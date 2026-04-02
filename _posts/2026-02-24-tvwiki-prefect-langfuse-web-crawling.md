---
layout: post
title: 동적 웹 크롤링 Prefect 플로우 + Langfuse 게이트 연동
subtitle: 메달리온 아키텍처로 미디어 경력 데이터를 수집하고 LLM 게이트로 품질을 잡는 방법
author: HyeongJin
date: 2026-02-24 09:00:00 +0900
categories: AI/LLM
tags: [Python, Prefect, AI, Langfuse, backend]
sidebar: []
published: true
---

KOBIS, TMDB, 나무위키에 이어 드라마 스태프 정보가 구조화된 형태로 잘 정리된 미디어 정보 사이트 크롤링 플로우를 추가했다. 드라마/영화 서비스에 필요한 경력 데이터를 보완하기 위해서다.

이번에는 Langfuse 게이트를 붙여서 LLM이 추출한 데이터 품질을 파이프라인 내에서 검증하도록 했다.

## 플로우 구조

```
미디어 정보 페이지 크롤링 (patchright)
    ↓
Bronze: 원본 HTML 파싱 → 구조화
    ↓
Silver: LLM 경력 추출 + Langfuse 게이트
    ↓
Gold: DB 적재
```

기존 KOBIS/TMDB 플로우와 동일한 메달리온 아키텍처를 따른다.

## patchright로 동적 크롤링

대상 사이트는 JavaScript 렌더링이 필요한 페이지라 requests만으로는 안 됐다. Playwright fork인 patchright를 썼다. 봇 감지 우회 처리가 추가된 버전이다.

```python
from patchright.sync_api import sync_playwright

@task
def fetch_media_page(url: str) -> str:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()
        page.goto(url, wait_until="networkidle")
        html = page.content()
        browser.close()
    return html
```

`wait_until="networkidle"` 옵션으로 JS 렌더링이 완료된 후 HTML을 가져온다. 동적 사이트에서 빠진 데이터 없이 전체 DOM을 확보하는 데 필수적이다.

## Langfuse 게이트

LLM이 경력 데이터를 추출한 뒤, 결과물 품질을 자동으로 검증하는 게이트를 넣었다. Langfuse의 `score` API로 품질 점수를 기록하고, 임계값 아래면 해당 레코드를 Silver 단계에서 걸러낸다.

```python
from langfuse import Langfuse

langfuse = Langfuse()

@task
def quality_gate(trace_id: str, extracted_careers: list, threshold: float = 0.7) -> list:
    passed = []
    for career in extracted_careers:
        score = evaluate_career_quality(career)
        langfuse.score(
            trace_id=trace_id,
            name="career_extraction_quality",
            value=score,
        )
        if score >= threshold:
            passed.append(career)
        else:
            logger.warning(f"게이트 탈락: {career.get('name')} score={score:.2f}")
    return passed
```

Langfuse UI에서 각 플로우 실행마다 품질 점수 분포를 볼 수 있다. 어떤 조건에서 추출 실패율이 올라가는지 트래킹하기 좋다.

## Prefect 플로우 조합

```python
from prefect import flow, task

@flow(name="media-career-web-flow")
def media_career_flow(media_title: str):
    trace_id = create_langfuse_trace(media_title)

    # Bronze: 크롤링 + 파싱
    html = fetch_media_page(build_search_url(media_title))
    raw_careers = parse_staff_table(html)

    # Silver: LLM 정제 + 게이트
    extracted = llm_extract_careers(raw_careers, trace_id=trace_id)
    passed = quality_gate(trace_id, extracted)

    # Gold: DB 적재
    if passed:
        upsert_careers_to_db(passed)
        notify_naverworks(f"{media_title}: {len(passed)}건 적재")

    return passed
```

각 단계를 독립 태스크로 분리해서 Prefect UI에서 태스크별 실행 상태와 재시도 여부를 확인할 수 있다.

## 실제로 쓰면서 느낀 것

드라마별 스태프 테이블이 깔끔하게 정리돼 있는 편이지만, 작품마다 테이블 구조가 조금씩 달라서 파서가 예외를 많이 던졌다. HTML 파싱 로직에 방어 코드를 많이 넣어야 했다.

Langfuse 게이트는 생각보다 유용했다. LLM 추출 결과가 스키마에 맞긴 한데 내용이 이상한 경우를 직접 확인할 수 있어서 프롬프트를 빠르게 개선할 수 있었다. 프롬프트 버전 관리도 Langfuse에서 같이 되니까 어떤 버전에서 품질이 올라갔는지 바로 비교된다.
