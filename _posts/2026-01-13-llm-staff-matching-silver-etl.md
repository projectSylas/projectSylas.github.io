---
layout: post
title: LLM으로 영화 스태프 데이터 Silver ETL 만들기
subtitle: 비정형 스태프 명단을 구조화된 DB 레코드로 변환하는 파이프라인
author: HyeongJin
date: 2026-01-13 09:00:00 +0900
categories: AI/LLM
tags: [AI, LLM, Python]
sidebar: []
published: true
---

클레딧의 핵심 데이터는 영화/드라마 스태프 이력이다. 감독, 배우, 촬영감독 등이 어떤 작품에 참여했는지.

KOBIS 공식 API에서 스태프 데이터를 내려받으면 이름과 직무가 오는데, 문제는 품질이다. 같은 사람인데 이름 표기가 다르거나("박준영" vs "박준영 B"), 직무명이 표준화되지 않거나("촬영" vs "촬영감독" vs "Dir. of Photography").

이걸 정제하는 Silver ETL을 LLM으로 만들었다.

## 문제 정의

```json
// KOBIS Raw 스태프 데이터 (Bronze)
{
  "movieCd": "20251234",
  "directors": [{"peopleNm": "홍길동", "peopleNmEn": "Gildong Hong"}],
  "staffs": [
    {"peopleNm": "김철수", "staffRoleGroup": "촬영팀", "staffRole": "촬영"},
    {"peopleNm": "이영희", "staffRoleGroup": "음악팀", "staffRole": "음악 감독"},
    {"peopleNm": "박민준", "staffRoleGroup": "기타", "staffRole": ""}
  ]
}

// 원하는 Silver 출력
{
  "movie_id": "20251234",
  "staff": [
    {"name": "홍길동", "role": "director", "role_detail": "감독"},
    {"name": "김철수", "role": "cinematographer", "role_detail": "촬영감독"},
    {"name": "이영희", "role": "composer", "role_detail": "음악감독"},
    {"name": "박민준", "role": "unknown", "role_detail": null}
  ]
}
```

규칙 기반으로 만들면 직무 사전을 계속 업데이트해야 한다. LLM이 더 유연하다.

## LangChain LCEL 체인

```python
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel
from typing import Optional

class StaffRecord(BaseModel):
    name: str
    role: str  # director, actor, cinematographer, editor, composer, etc.
    role_detail: Optional[str]
    confidence: float  # 0~1

class StaffList(BaseModel):
    staff: list[StaffRecord]

prompt = ChatPromptTemplate.from_messages([
    ("system", """영화/드라마 스태프 데이터를 정제합니다.
- staffRoleGroup, staffRole 정보를 기반으로 표준 role 코드를 할당하세요.
- 이름이 불명확하거나 역할 파악이 불가능하면 confidence를 낮게 설정하세요.
- 표준 role 코드: director, actor, producer, cinematographer, editor, composer,
  art_director, costume, makeup, visual_effects, sound, script, unknown
"""),
    ("user", "작품: {movie_title}\n스태프 목록:\n{raw_staff}")
])

chain = prompt | ChatOpenAI(model="gpt-4o-mini", temperature=0).with_structured_output(StaffList)
```

## Prefect Flow

```python
from prefect import flow, task

@task(retries=2)
def process_movie_staff(movie: dict) -> list[StaffRecord]:
    raw_staff_str = format_staff_for_llm(movie["staffs"])
    result = chain.invoke({
        "movie_title": movie["title"],
        "raw_staff": raw_staff_str,
    })
    return result.staff

@task
def save_silver_staff(movie_id: str, staff: list[StaffRecord]):
    with transaction.atomic():
        # 기존 silver 데이터 삭제 후 재적재
        SilverStaff.objects.filter(movie_id=movie_id).delete()
        SilverStaff.objects.bulk_create([
            SilverStaff(
                movie_id=movie_id,
                name=s.name,
                role=s.role,
                role_detail=s.role_detail,
                confidence=s.confidence,
            )
            for s in staff
            if s.confidence >= 0.6  # 신뢰도 낮은 건 저장 안 함
        ])

@flow(name="silver-staff-etl")
def silver_staff_flow(movie_ids: list[str] | None = None):
    movies = get_bronze_movies(movie_ids)

    for batch in chunked(movies, 10):
        staff_results = process_movie_staff.map(batch)
        for movie, staff in zip(batch, staff_results):
            save_silver_staff(movie["movie_id"], staff)
```

## 성별 데이터 부재 대응

스태프 데이터에 성별 정보가 없는 경우가 많았다. 매칭 로직에서 성별로 필터링하는 코드가 있었는데, 성별 데이터가 없을 때 아예 매칭이 안 됐다.

```python
# 기존 - 성별 필수 조건
def match_staff_to_user(staff_name: str, role: str, gender: str) -> User | None:
    return User.objects.filter(name=staff_name, gender=gender).first()

# 수정 - 성별은 있을 때만 조건으로
def match_staff_to_user(staff_name: str, role: str, gender: str | None = None) -> User | None:
    qs = User.objects.filter(name=staff_name)
    if gender:
        qs = qs.filter(gender=gender)
    return qs.first()
```

성별 데이터가 있으면 필터 조건으로, 없으면 이름만으로 매칭. 복수 결과가 나오면 팔로워 수 기준으로 가장 유명한 사용자를 선택.

## LLM 비용

GPT-4o-mini 기준 스태프 100명 처리에 토큰이 약 3,000개 소비됐다. 전체 DB 50,000개 작품 처리 시 예상 비용이 꽤 됐다.

배치 처리 전 confidence 0.9+ 케이스는 규칙 기반으로 먼저 처리하고 LLM은 애매한 케이스만 처리하도록 최적화했다. 전체 비용 40% 감소.
