---
layout: post
title: KOBIS-TMDB 인물 매칭 ETL 파이프라인
subtitle: 한국영화진흥원 API + TMDB 이름·필모·직무·성별 4개 조건 AND 매칭, 필모 교집합 기반 신뢰도 3단계 분류
author: HyeongJin
date: 2025-12-19 10:00:00 +0900
categories: Backend
tags: [Python, Prefect, ETL, PostgreSQL, DataEngineering]
sidebar: []
published: true
---

드라마·영화 크루 프로필 데이터를 구축하면서 KOBIS(한국영화진흥원)와 TMDB(The Movie Database) 인물 데이터를 병합해야 했다. 두 소스의 인물 ID가 달라서 동명이인 구분, 활동 작품 교집합, 직무 정규화를 거쳐 같은 인물인지 판단하는 매칭 로직이 필요했다.

## 문제 정의

KOBIS에는 한국 인물 데이터가 풍부하고, TMDB에는 이미지·영문 이름·글로벌 활동 이력이 있다. 두 소스를 연결하면 더 완성도 높은 프로필을 만들 수 있다.

단순히 이름으로만 매칭하면 동명이인 오매칭이 생긴다. "이정재"는 배우도 있고 감독도 있다. 같은 이름이어도 직무, 성별, 활동 작품이 다르면 다른 인물이다.

## 매칭 조건 4개 AND

```
1. 이름 일치 (한글명 정규화)
2. 필모 교집합 1개 이상
3. 직무 그룹 일치 (11개 그룹으로 분류)
4. 성별 일치 (양쪽 모두 있을 때만 필수)
```

4개 조건을 AND로 적용한다. 이름만 맞아도 나머지가 하나라도 불일치하면 매칭하지 않는다.

## 이름 정규화

KOBIS는 한글 이름, TMDB는 `name` 필드(영문 또는 한글)와 `also_known_as` 배열(다국어 별칭)을 가진다.

```python
def get_tmdb_korean_names(record: dict) -> set[str]:
    names = set()
    name = record.get("name")
    if name:
        names.add(MatchingHelper.normalize(name))

    also_known_as = record.get("also_known_as")
    if isinstance(also_known_as, str):
        also_known_as = json.loads(also_known_as)
    for aka in (also_known_as or []):
        names.add(MatchingHelper.normalize(aka))

    return names
```

`also_known_as`에 한글 이름이 있는 경우가 많다. TMDB의 "Lee Jung-jae" → also_known_as에 "이정재"가 포함되어 있어서 KOBIS 이름과 매칭된다.

## 필모 교집합

같은 이름이어도 활동 작품이 겹치지 않으면 다른 인물일 가능성이 높다.

```python
kobis_works = self.get_kobis_works(kobis_record)  # 출연/스태프 작품 set
tmdb_works  = self.get_tmdb_works(tmdb_record)    # known_for 작품 set

intersection = kobis_works & tmdb_works
intersection_count = len(intersection)

if intersection_count == 0:
    return (False, 0)
```

작품명도 정규화한다. 특수문자 제거, 공백 통일, 소문자 변환 등을 거쳐 "기생충"과 "Parasite"가 같은 작품으로 인식되도록 한다.

## 신뢰도 3단계

필모 교집합 개수로 매칭 신뢰도를 분류한다.

```python
@staticmethod
def get_confidence_level(intersection_count: int) -> str:
    if intersection_count >= 3:
        return "high"
    elif intersection_count == 2:
        return "medium"
    elif intersection_count == 1:
        return "low"
    else:
        return "none"
```

신뢰도에 따라 직무 조건 적용을 달리한다.

```python
is_high_confidence = intersection_count >= 3

if is_high_confidence:
    # 고신뢰도: 직무 그룹 매핑이 없는 경우만 제외, 불일치는 허용
    if kobis_role_group is None:
        return (False, 0)
else:
    # 일반 매칭: 직무 그룹 일치 필수
    if kobis_role_group != tmdb_role_group:
        return (False, 0)
```

필모가 3개 이상 겹치면 같은 인물임이 거의 확실하다. 이 경우 직무가 두 소스에서 다르게 기록되어 있어도 매칭을 허용한다. 반면 필모 교집합이 1~2개뿐이면 직무까지 일치해야 통과시킨다.

## 직무 그룹 정규화

KOBIS와 TMDB의 직무 체계가 다르다. KOBIS는 "촬영감독", "조명감독" 같은 세부 직무를 쓰고, TMDB는 "Camera", "Lighting" 같은 영문 카테고리를 쓴다. 11개 공통 그룹으로 매핑해서 비교한다.

```
감독 계열    → directing
촬영 계열    → camera
조명 계열    → lighting
미술 계열    → art
음향/사운드  → sound
편집 계열    → editing
배우         → acting
작가         → writing
제작         → production
VFX/CG      → vfx
기타         → other
```

KOBIS의 "스테디캠", "포커스펄러"는 모두 `camera`로, TMDB의 "Camera Operator", "Director of Photography"도 `camera`로 통일한다.

## Prefect 배치 플로우

전체 인물 데이터를 메모리에 올리면 OOM이 발생한다. 1,000건 단위로 배치 파일(JSONL)에 저장하고 순차 처리한다.

```python
@task(tags=["etl", "db-load"], retries=3, retry_delay_seconds=5, timeout_seconds=600)
async def load_kobis_from_db() -> str:
    """DB → 배치 JSONL 파일"""
    temp_dir = script_dir / "temp" / f"kobis_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    batch_data = []
    batch_num = 0

    async with connector.get_connection() as conn:
        result = await conn.execute(text("SELECT * FROM merge.career_kobis_etl ORDER BY id"))
        for row in result:
            batch_data.append(dict(row._mapping))
            if len(batch_data) >= 1000:
                save_batch_jsonl(batch_data, temp_dir / f"batch_{batch_num:04d}.jsonl")
                batch_num += 1
                batch_data = []

    return str(temp_dir)
```

로드 → 매칭 → 병합 → DB 저장을 Prefect task로 분리해서 각 단계를 독립적으로 재시도할 수 있다. 매칭 단계에서 실패해도 로드를 다시 할 필요 없이 배치 파일에서 이어서 처리한다.

## 결과 저장

매칭된 인물은 `merge.career_kobis_tmdb_merged` 테이블에 신뢰도 레벨과 함께 저장된다.

```python
merged_record = merge_kobis_tmdb_record(
    kobis_record=kobis_rec,
    tmdb_record=tmdb_rec,
    confidence_level=CareerMatcher.get_confidence_level(intersection_count),
)
```

`confidence_level`을 함께 저장해두면 이후 API에서 high 신뢰도 매칭만 쓰거나, low 신뢰도는 수동 검토 대상으로 표시하는 식으로 활용할 수 있다.
