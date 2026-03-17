---
layout: post
title: Prefect + LangChain으로 영화 시나리오 AI 분석 파이프라인 만들기
subtitle: 5개 LLM 에이전트 멀티스테이지 오케스트레이션과 Kling AI 프리비즈 영상 자동 생성
author: HyeongJin
date: 2026-03-16 14:00:00 +0900
categories: AI/LLM
tags: [Python, AI, LangChain, Prefect, Langfuse]
sidebar: []
published: true
---

영화·드라마 시나리오 원문을 넣으면 캐릭터, 세계관, 스토리, 액션, 비주얼 방향을 LLM이 분석하고, 그 결과로 Kling AI가 프리비즈 영상을 자동으로 만들어주는 파이프라인이다.

파일 파싱부터 영상 생성·병합까지 엔드투엔드로 돌아가도록 Prefect 메달리온 아키텍처로 설계했다.

## 전체 구조

```
입력: .pdf / .txt / .html / .docx

Bronze  → 파싱 + MD5 캐싱
Silver  → 5 LLM 에이전트 분석
Gold    → 통합 JSON + 리포트
Previs  → intent별 샷 리스트 저장
VideoGen → Kling AI 호출 + FFmpeg 병합

출력: outputs/{title}/{timestamp}/
    gold.json, report.md
    previs/{intent}/*.json
    video/{intent}.mp4
```

## Bronze: 파일 파싱 + 캐싱

다양한 포맷을 지원해야 했다. HTML(IMSDB), TXT, PDF, DOCX가 주요 포맷이다.

```python
import hashlib
from pathlib import Path
import pdfplumber

def parse_file(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return _parse_pdf(path)
    elif suffix in (".html", ".htm"):
        return _parse_html(path)
    elif suffix == ".txt":
        return _parse_txt(path)
    elif suffix == ".docx":
        return _parse_docx(path)
    raise ValueError(f"미지원 포맷: {suffix}")

def _get_cache_path(file_path: Path) -> Path:
    md5 = hashlib.md5(file_path.read_bytes()).hexdigest()
    return Path(f"outputs/scenario_bronze/cache/{md5}.json")
```

MD5 해시 기반으로 캐싱한다. 같은 파일을 다시 분석 요청하면 파싱을 건너뛴다. 시나리오 파일이 크면 파싱만 몇 초 걸리는 경우도 있어서 유용하다.

## Silver: 5 에이전트 멀티스테이지

분석 에이전트 5개를 3단계로 나눠서 실행한다.

```python
from prefect import flow
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate

@flow(name="silver-analysis")
def silver_flow(script: str, run_ts: str):
    # Phase 1: 병렬 (독립적인 분석)
    character, world, story, action = run_phase1_parallel(script)

    # Phase 2: 순차 (앞 결과를 참조)
    visual = run_visual_direction(script, character, world, story, action)

    # Phase 3: 병렬 (4개 intent별 샷 구성)
    shots = run_shot_composers_parallel(
        script, character, world, story, action, visual
    )
    return shots
```

Phase 1은 캐릭터/세계관/스토리/액션 에이전트가 독립적으로 원문 전체를 분석한다. 청크 분할 없이 원문을 통째로 넣는다. GPT-4o 기준 context window가 충분해서 이 방식이 청크 분할보다 일관성이 높았다.

Phase 2의 `visual_direction`은 Phase 1 결과를 참조해서 촬영 스타일, 조명, 컬러 톤을 제안한다.

Phase 3는 `ad / trailer / summary / cinematic` 4가지 intent별로 shot_composer가 각각 다른 샷 리스트를 만든다.

## LangChain LCEL + Pydantic Structured Output

에이전트 응답을 구조화된 형태로 받는 게 핵심이다.

```python
from pydantic import BaseModel
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI

class CharacterAnalysis(BaseModel):
    characters: list[Character]
    relationships: list[Relationship]

def run_character_agent(script: str, trace_id: str) -> CharacterAnalysis:
    llm = ChatOpenAI(model="gpt-4o", temperature=0)
    chain = prompt | llm.with_structured_output(CharacterAnalysis)

    with langfuse_context(trace_id=trace_id, name="character-agent"):
        return chain.invoke({"script": script})
```

`with_structured_output`으로 Pydantic 모델을 직접 반환받는다. JSON 파싱 오류를 LangChain이 처리해줘서 별도 파싱 코드가 필요 없다.

## Kling AI 영상 생성

샷 리스트가 나오면 Kling AI API로 각 샷을 영상으로 만든다.

```python
import httpx
import time

class KlingClient:
    def __init__(self, api_key: str):
        self.base_url = "https://api.piapi.ai/api/kling/v1"
        self.headers = {"x-api-key": api_key}

    def generate_shot(self, prompt: str, duration: int = 5) -> str:
        response = httpx.post(
            f"{self.base_url}/video/text2video",
            json={"prompt": prompt, "duration": duration},
            headers=self.headers,
            timeout=30
        )
        task_id = response.json()["data"]["task_id"]
        return self._wait_for_result(task_id)

    def _wait_for_result(self, task_id: str, max_wait: int = 300) -> str:
        for _ in range(max_wait // 5):
            time.sleep(5)
            status = self._check_status(task_id)
            if status["state"] == "completed":
                return status["video_url"]
            if status["state"] == "failed":
                raise RuntimeError(f"Kling 생성 실패: {task_id}")
        raise TimeoutError(f"타임아웃: {task_id}")
```

PiAPI 프록시를 통해 Kling API를 호출한다. 영상 생성에 보통 1~3분 걸려서 폴링으로 완료를 기다린다.

## FFmpeg 샷 병합

```python
import subprocess
from pathlib import Path

def merge_shots(shot_paths: list[Path], output_path: Path):
    # ffmpeg concat 리스트 파일 생성
    concat_list = output_path.parent / "concat.txt"
    concat_list.write_text(
        "\n".join(f"file '{p.absolute()}'" for p in shot_paths)
    )

    subprocess.run([
        "ffmpeg", "-f", "concat", "-safe", "0",
        "-i", str(concat_list),
        "-c", "copy", str(output_path)
    ], check=True)
```

각 샷 영상을 ffmpeg concat으로 이어 붙인다. 인코딩 없이 스트림을 복사(`-c copy`)하므로 빠르다.

## 결과 저장 구조

```
outputs/{title}/{YYYYMMDD_HHMMSS}/
    gold.json          # 전체 분석 결과
    report.md          # 읽기 쉬운 요약
    agents/
        character.json
        world.json
        story.json
        action.json
        visual_direction.json
    previs/
        ad/shots.json
        trailer/shots.json
        summary/shots.json
        cinematic/shots.json
    video/
        ad.mp4
        trailer.mp4
```

타임스탬프별로 실행 이력이 쌓인다. 같은 시나리오로 프롬프트를 수정해서 다시 돌리면 이전 결과와 비교할 수 있다.

## 실제로 쓰면서

가장 손이 많이 간 부분은 `shot_composer`의 캐릭터 ID 검증이었다. LLM이 존재하지 않는 캐릭터 ID를 샷에 할당하는 케이스가 있어서, silver 단계 후처리로 invalid ID를 자동으로 제거하는 로직을 넣어야 했다.

Langfuse 트레이스를 보면 각 에이전트가 얼마나 걸리는지, 어느 프롬프트 버전에서 품질이 올라가는지 바로 확인된다. LLM 파이프라인 디버깅에 이게 없으면 매우 불편할 것 같다.
