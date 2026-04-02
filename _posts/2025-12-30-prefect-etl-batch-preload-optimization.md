---
layout: post
title: Prefect ETL 배치 처리와 preload 최적화
subtitle: 수십만 건 Career DB ETL — 전체 로드 OOM 문제를 배치 인덱싱 + 참조 ID preload 패턴으로 해결
author: HyeongJin
date: 2025-12-30 10:00:00 +0900
categories: Backend
tags: [Python, Prefect, ETL, PostgreSQL, DataEngineering]
sidebar: []
published: true
---

`career_db_etl_flow`는 운영 DB의 커리어 로우데이터(`staging.career_raw`)와 KOBIS-TMDB 병합 ETL 결과(`merge.career_generation_etl`)를 매칭해서 최종 병합 테이블(`merge.career_db_etl`)을 만드는 플로우다.

초기 구현에서는 ETL 데이터 전체를 메모리에 올린 다음 DB 레코드와 매칭했다. 데이터가 수십만 건으로 늘어나면서 OOM이 발생했다.

## 문제: 전체 로드 방식의 한계

```python
# 기존 방식 — 전체 ETL 데이터를 한 번에 로드
etl_df = await load_all_etl_records()   # 수십만 건 → OOM
match_result = match_careers(db_df, etl_df)
```

ETL 결과가 많아질수록 `etl_df` 크기가 선형으로 늘어난다. 전체를 pandas DataFrame으로 들고 있으면 프로세스 메모리를 압도한다.

## 해결: 배치 인덱싱 + preload

전략은 두 단계로 나뉜다.

1. **인덱스만 배치로 구축** — 전체 ETL을 50,000건 단위로 읽어서 매칭 키(작품명 + 이름) → ETL ID 매핑만 메모리에 유지
2. **참조되는 ID만 preload** — DB 레코드가 실제로 매칭할 ETL ID 목록을 수집하고, 그것만 다시 DB에서 로드

```python
BATCH_SIZE = 10_000      # DB 저장 배치
ETL_BATCH_SIZE = 50_000  # ETL 인덱스 구축 배치
```

## 1단계: 배치 인덱싱

```python
etl_index = {}

for offset in range(0, total_etl, ETL_BATCH_SIZE):
    etl_batch_df = await load_etl_career_batch(offset, ETL_BATCH_SIZE)
    batch_index = build_etl_index_batch(etl_batch_df, offset)

    for key, ids in batch_index.items():
        if key not in etl_index:
            etl_index[key] = []
        etl_index[key].extend(ids)

    del etl_batch_df
    del batch_index  # 배치 처리 후 즉시 해제
```

`build_etl_index_batch`는 배치 DataFrame을 읽어서 `(작품명_정규화, 이름_정규화)` → `[etl_id, ...]` 딕셔너리만 만든다. 처리 후 `del`로 DataFrame을 즉시 해제한다.

`etl_index`는 ID 목록만 갖는 딕셔너리라 전체 레코드보다 훨씬 작다. 100만 건이어도 메모리에 올릴 수 있다.

## 2단계: 참조 ID 수집 + preload

```python
# DB 레코드가 실제로 참조하는 ETL ID만 수집
referenced_etl_ids = collect_referenced_etl_ids(db_df, etl_index)

# 필요한 것만 로드
etl_df = await load_referenced_etl_records(referenced_etl_ids)
```

`collect_referenced_etl_ids`는 DB 레코드의 `(작품명, 이름)` 키로 인덱스를 조회해서 매칭 후보 ETL ID 목록을 만든다. 전체 ETL 중 실제로 DB에서 참조되는 것만 추려낸다.

대부분의 경우 ETL 레코드 중 DB와 매칭되는 비율은 60~80% 수준이다. 나머지 20~40%는 로드할 필요가 없다.

`load_referenced_etl_records`는 10,000개씩 배치로 ID를 `IN` 쿼리에 넣어서 로드한다.

```python
for i in range(0, len(etl_ids_list), 10000):
    batch_ids = etl_ids_list[i:i+10000]
    batch_df = await load_etl_records_by_ids(batch_ids)
    all_dfs.append(batch_df)
```

## 매칭 키 설계

인덱스 키는 `(작품명 정규화, 이름 정규화)` 튜플이다.

```python
def normalize_text(text) -> str:
    if pd.isna(text) or text is None or text == "":
        return ""
    return str(text).strip().replace(" ", "").lower()
```

공백 제거 + 소문자 변환. "기생충"과 " 기 생 충 "이 같은 키가 된다.

이름 매칭은 KOBIS 이름과 TMDB 이름을 모두 키로 넣는다.

```python
def get_etl_user_names(etl_row: pd.Series) -> list[str]:
    names = []
    for field in ['kobis_api_people_nm', 'kobis_web_people_nm']:
        value = etl_row.get(field)
        if value and not pd.isna(value):
            names.append(str(value).strip())

    tmdb_name = etl_row.get('tmdb_name')
    if tmdb_name and not pd.isna(tmdb_name):
        names.append(str(tmdb_name).strip())

    # also_known_as에서 한글 이름 추가
    also_known_as = etl_row.get('tmdb_also_known_as')
    korean_names = get_korean_names_from_also_known_as(also_known_as)
    names.extend(korean_names)

    return names
```

ETL 레코드 하나에서 여러 이름이 나올 수 있다. KOBIS 이름, TMDB 영문명, TMDB also_known_as 한글명 등 가능한 모든 이름으로 인덱스 키를 만들어서 DB 이름과 교차 매칭된다.

## Prefect task 분리

각 단계를 Prefect `@task`로 분리해서 독립적으로 재시도할 수 있게 했다.

```python
@task(name="load-etl-career-batch", retries=2, retry_delay_seconds=5, timeout_seconds=300)
async def load_etl_career_batch(offset: int, limit: int) -> pd.DataFrame:
    ...

@task(name="build-etl-index-batch")
def build_etl_index_batch(etl_df: pd.DataFrame, start_offset: int = 0) -> dict:
    ...

@task(name="match-careers-optimized")
def match_careers_optimized(db_df, etl_df, etl_index) -> dict:
    ...
```

DB 연결 오류로 배치 로드가 실패해도 해당 배치만 재시도한다. 전체 플로우를 다시 돌릴 필요가 없다. `timeout_seconds`로 특정 배치가 오래 걸릴 때 행이 풀리도록 했다.

## 결과

전체 인덱스 구축 → 참조 ID 수집 → preload → 매칭 → 10,000건 배치 저장 순서로 플로우가 돌아간다. 수십만 건에서 OOM 없이 처리되고, Prefect UI에서 각 단계별 소요 시간과 처리 건수를 확인할 수 있다.

배치 처리 패턴은 단순하다. 전체를 한 번에 올리는 대신, 인덱스만 배치로 구축하고 실제로 필요한 것만 가져온다. ETL 데이터가 더 커져도 `ETL_BATCH_SIZE`만 조정하면 메모리 사용량을 제어할 수 있다.
