---
layout: post
title: FastAPI + SQLite 동시 읽기/쓰기 타임아웃, WAL 모드로 해결
subtitle: 검색 요청 중 DB 저장이 겹치면서 발생한 livelock — journal_mode=WAL 한 줄로 잡기
author: HyeongJin
date: 2026-03-23 09:00:00 +0900
categories: Backend
tags: [Python, FastAPI, SQLite, SQLAlchemy]
sidebar: []
published: true
---

FastAPI 서버에서 검색 요청이 들어올 때 결과를 SQLite에 저장하는 구조였다. 로컬에서는 잘 돌았는데 Railway에 올리고 나서 간헐적으로 요청이 타임아웃으로 죽기 시작했다.

## 상황

검색 파이프라인이 끝나면 결과를 DB에 flush하는 구조다.

```python
def _persist_normalized_offers(self, db: Session, run_id: str, offers: List[Offer]):
    rows = []
    for idx, offer in enumerate(offers[:200], start=1):
        row = NormalizedOfferRow(run_id=run_id, offer_rank=idx, ...)
        db.add(row)
        rows.append(row)
    db.flush()  # 여기서 블로킹
    return {row.url: row.id for row in rows}
```

동시에 폴링 요청(`GET /api/v1/search/runs/{run_id}`)이 들어오면 같은 DB를 읽으려 한다. SQLite 기본 모드(DELETE journal)에서는 **쓰기 락이 걸린 동안 읽기도 블로킹**된다. 검색이 오래 걸릴수록 폴링이 쌓이고, 결국 타임아웃.

## SQLite 기본 journal 모드 문제

SQLite는 기본적으로 `journal_mode=DELETE`를 쓴다. 이 모드에서는 트랜잭션이 시작되면 전체 DB 파일에 락이 걸린다.

```
쓰기 트랜잭션 시작
  → DB 파일 EXCLUSIVE 락
  → 다른 커넥션 읽기/쓰기 전부 대기
  → 트랜잭션 커밋
  → 락 해제
```

FastAPI는 비동기로 요청을 처리하지만 SQLite IO는 동기라서, 쓰기 트랜잭션이 길어지면 그 사이에 들어온 읽기 요청들이 전부 블로킹된다. 30초 `timeout`을 줘도 검색이 40초 걸리면 그냥 죽는다.

## WAL 모드

`journal_mode=WAL`(Write-Ahead Logging)로 바꾸면 쓰기와 읽기가 서로를 블로킹하지 않는다.

```
WAL 모드:
  쓰기 → WAL 파일에 변경사항 기록 (DB 파일은 건드리지 않음)
  읽기 → 마지막 커밋된 체크포인트 기준으로 읽기 (쓰기와 무관)
```

읽기는 항상 일관된 스냅샷을 보고, 쓰기는 WAL 파일에 먼저 쌓인 다음 나중에 DB에 병합된다. 동시 요청이 들어와도 서로 기다리지 않는다.

## 적용 방법

SQLAlchemy에서는 커넥션 생성 이벤트에 훅을 달아서 적용한다.

```python
from sqlalchemy import create_engine, event

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./app.db")
IS_SQLITE = DATABASE_URL.startswith("sqlite")

connect_args = {"check_same_thread": False, "timeout": 30} if IS_SQLITE else {}
engine = create_engine(DATABASE_URL, pool_pre_ping=True, connect_args=connect_args)

if IS_SQLITE:
    @event.listens_for(engine, "connect")
    def _set_wal_mode(dbapi_conn, _):
        dbapi_conn.execute("PRAGMA journal_mode=WAL")
        dbapi_conn.execute("PRAGMA synchronous=NORMAL")
```

`event.listens_for(engine, "connect")`는 커넥션 풀에서 새 커넥션이 생성될 때마다 실행된다. 모든 커넥션이 WAL 모드로 동작하도록 보장할 수 있다.

`synchronous=NORMAL`도 같이 설정했다. 기본값인 `FULL`은 매 트랜잭션마다 fsync를 호출해서 느리다. `NORMAL`은 WAL 체크포인트 시점에만 fsync하기 때문에 쓰기 성능이 개선된다. 전원이 갑자기 끊기면 최근 트랜잭션이 날아갈 수 있지만, 서버 프로세스 크래시 수준에서는 안전하다.

## 배치 flush도 같이 수정

기존엔 row마다 `db.flush()`를 호출하고 있었다. 200개 오퍼를 저장하면 flush를 200번 호출하는 셈이다. 각 flush가 WAL에 쓰기를 기록하는 왕복이니까 불필요한 오버헤드가 있었다.

```python
# 변경 전: 루프 안에서 flush
for offer in offers:
    db.add(NormalizedOfferRow(...))
    db.flush()  # 매번 호출

# 변경 후: 전부 add한 다음 한 번만 flush
rows = []
for offer in offers:
    row = NormalizedOfferRow(...)
    db.add(row)
    rows.append(row)
db.flush()  # 한 번만
```

`db.flush()`는 SQLAlchemy 세션 캐시를 DB로 내보내는 것이다. 한 트랜잭션 안에서 여러 번 flush해도 커밋은 되지 않는다. 모두 추가한 다음 한 번만 flush하면 WAL에 한 번의 배치 쓰기로 처리된다.

## 결과

간헐적 타임아웃이 사라졌다. 검색이 45초 걸려도 폴링이 블로킹되지 않는다.

PostgreSQL을 쓰면 애초에 이 문제가 없다. WAL이 기본이고 행 단위 락을 쓰기 때문이다. 로컬/스테이징에서 SQLite를 쓰고 프로덕션에서 PostgreSQL을 쓰는 구조라면, SQLite도 WAL 모드로 맞춰두는 게 환경 간 동작 차이를 줄이는 데 도움이 된다.
