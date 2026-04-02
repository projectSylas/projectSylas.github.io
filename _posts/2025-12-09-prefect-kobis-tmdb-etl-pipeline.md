---
layout: post
title: Prefect로 KOBIS/TMDB 영화 데이터 ETL 파이프라인 구축
subtitle: 공공 API 데이터를 DB에 안정적으로 적재하는 파이프라인 설계
author: HyeongJin
date: 2025-12-09 11:00:00 +0900
categories: AI/LLM
tags: [AI, Python, DevOps]
sidebar: []
published: true
---

영화/드라마 데이터를 자동으로 수집하고 적재하는 파이프라인이 필요했다. KOBIS(영화진흥위원회)와 TMDB(The Movie Database)를 주 소스로 선택했다.

Prefect를 오케스트레이터로 쓰기로 했다. Airflow도 검토했는데 Prefect는 로컬 실행이 간단하고 태스크 단위 재시도 설정이 직관적이었다.

## 기본 구조

메달리온 아키텍처로 3단계.

```
Raw (API 응답 그대로) → Staging (정제) → Production DB
```

```python
from prefect import flow, task
from prefect.tasks import task_input_hash
from datetime import timedelta

@task(
    retries=3,
    retry_delay_seconds=60,
    cache_key_fn=task_input_hash,
    cache_expiration=timedelta(hours=6),
)
def fetch_kobis_movies(page: int) -> list[dict]:
    """KOBIS API에서 영화 목록 페이지를 가져온다"""
    client = KobisApiClient()
    return client.get_movie_list(page=page)

@task(retries=2)
def upsert_to_staging(movies: list[dict]) -> int:
    """staging 테이블에 upsert"""
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.executemany("""
                INSERT INTO staging_movies (movie_cd, title, year, updated_at)
                VALUES (%(movie_cd)s, %(movieNm)s, %(prdtYear)s, NOW())
                ON CONFLICT (movie_cd) DO UPDATE SET
                    title = EXCLUDED.title,
                    year = EXCLUDED.year,
                    updated_at = NOW()
            """, movies)
    return len(movies)
```

## API 키 로테이션

KOBIS 일일 쿼터가 3,000건. 초기 적재는 수만 건이 필요해서 키 로테이션이 필수.

```python
class KobisApiClient:
    def __init__(self):
        self._keys = settings.KOBIS_API_KEYS  # 리스트
        self._idx = 0
        self._quota_exhausted = set()

    def _get_key(self) -> str:
        for _ in range(len(self._keys)):
            key = self._keys[self._idx]
            self._idx = (self._idx + 1) % len(self._keys)
            if key not in self._quota_exhausted:
                return key
        raise RuntimeError("모든 API 키 쿼터 소진")

    def get_movie_list(self, page: int) -> list[dict]:
        while True:
            key = self._get_key()
            resp = requests.get(
                "https://kobis.or.kr/.../searchMovieList.json",
                params={"key": key, "curPage": page, "itemPerPage": 100},
                timeout=30,
            )
            data = resp.json()
            if "faultInfo" in data:
                self._quota_exhausted.add(key)
                continue
            return data["movieListResult"]["movieList"]
```

## TMDB 연동

KOBIS는 국내 데이터에 강하고 TMDB는 국제 메타데이터(영문 제목, 포스터 URL 등)에 강하다. 두 소스를 매칭해서 쓰기로 했다.

```python
@task
def fetch_tmdb_movie(tmdb_id: int) -> dict:
    resp = requests.get(
        f"https://api.themoviedb.org/3/movie/{tmdb_id}",
        headers={"Authorization": f"Bearer {settings.TMDB_ACCESS_TOKEN}"},
        params={"language": "ko-KR"},
    )
    return resp.json()

@flow
def sync_tmdb_flow():
    """staging_movies에서 tmdb_id가 있는 것들 상세 정보 보강"""
    movies_without_detail = get_movies_without_tmdb_detail()

    # rate limit: 40 requests/10s
    for batch in chunked(movies_without_detail, 40):
        results = fetch_tmdb_movie.map(
            [m['tmdb_id'] for m in batch]
        )
        save_tmdb_details(results)
        time.sleep(10)
```

TMDB rate limit가 40req/10s라 배치 처리 후 슬립.

## DB 커넥션 풀 초과 문제

태스크를 병렬로 실행하면서 DB 커넥션이 부족해지는 문제가 생겼다.

```
psycopg2.OperationalError: FATAL: remaining connection slots are reserved for non-replication superuser connections
```

Prefect의 ConcurrencyLimit 태그를 써서 DB 태스크의 동시 실행 수를 제한.

```python
@task(tags=["db"], task_run_concurrency_limit=5)
def upsert_batch(batch: list[dict]) -> int:
    # 최대 5개까지만 동시 실행
    ...
```

그리고 큰 데이터는 청크 SQL로 나눠서 커밋.

```python
def bulk_upsert_chunked(data: list[dict], chunk_size: int = 500):
    for i in range(0, len(data), chunk_size):
        chunk = data[i:i+chunk_size]
        with get_db_connection() as conn:
            # 커넥션을 청크마다 열고 닫음
            execute_upsert(conn, chunk)
```

## 나머지 흐름

```python
@flow(name="kobis-full-sync")
def kobis_full_sync_flow():
    # 1. 영화 목록 수집
    pages = list(range(1, 200))
    movie_lists = fetch_kobis_movies.map(pages)

    # 2. staging 적재
    counts = upsert_to_staging.map(movie_lists)

    # 3. 상세 정보 보강 (다음 플로우에서)
    trigger_detail_flow(wait_for=[counts])
```

`map()`이 Prefect에서 태스크를 병렬 실행하는 방법이다. `wait_for`로 의존 관계 표현.

초기 데이터 적재 2일 + 이후 매일 신규 데이터 추가 크론으로 운영 중.
