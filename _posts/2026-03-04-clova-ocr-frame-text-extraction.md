---
layout: post
title: Clova OCR API로 영상 크레딧 텍스트 자동 추출
subtitle: 엔딩 크레딧에서 스태프 정보를 뽑는 파이프라인
author: HyeongJin
categories: AI/LLM
tags: [AI, LLM, Python]
sidebar: []
published: true
---

영화나 드라마 엔딩 크레딧에는 모든 스태프가 나온다. 이걸 자동으로 읽어서 DB에 넣을 수 있으면 KOBIS에 없는 스태프 데이터를 보완할 수 있다.

방식: 영상에서 프레임 추출 → OCR로 텍스트 인식 → LLM으로 역할/이름 파싱.

## OCR 선택 과정

처음엔 Tesseract를 썼다. 무료고 Python 바인딩이 있다.

한국어 텍스트 인식률이 낮았다. 특히 영상 크레딧처럼 배경이 어둡고 폰트가 작은 경우. 인식률이 30~40% 수준.

Google Vision API도 테스트했는데 한국어 세로쓰기나 특수 폰트에서 틀리는 경우가 있었다.

Clova OCR(Naver)이 한국어 인식률이 가장 높았다. 영상 크레딧 특성상 폰트가 다양하고 배경과 대비가 낮은 경우가 많은데, Clova가 이걸 잘 처리했다.

## 프레임 추출

```python
import cv2
from pathlib import Path

def extract_credit_frames(video_path: str, output_dir: str, fps: float = 0.5) -> list[str]:
    """
    영상에서 크레딧 구간 프레임을 추출한다.
    fps=0.5 → 2초에 1장
    """
    cap = cv2.VideoCapture(video_path)
    video_fps = cap.get(cv2.CAP_PROP_FPS)
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    duration = total_frames / video_fps

    # 영상 마지막 20% 구간이 크레딧 가능성 높음
    start_time = duration * 0.8
    start_frame = int(start_time * video_fps)
    interval = int(video_fps / fps)

    cap.set(cv2.CAP_PROP_POS_FRAMES, start_frame)
    saved_paths = []
    frame_idx = start_frame

    while True:
        ret, frame = cap.read()
        if not ret:
            break
        if (frame_idx - start_frame) % interval == 0:
            path = Path(output_dir) / f"frame_{frame_idx:08d}.jpg"
            cv2.imwrite(str(path), frame)
            saved_paths.append(str(path))
        frame_idx += 1

    cap.release()
    return saved_paths
```

## Clova OCR API 호출

```python
import requests
import base64

def ocr_with_clova(image_path: str) -> list[dict]:
    with open(image_path, 'rb') as f:
        image_data = base64.b64encode(f.read()).decode()

    payload = {
        "version": "V2",
        "requestId": str(uuid.uuid4()),
        "timestamp": int(time.time() * 1000),
        "lang": "ko",
        "images": [
            {
                "format": "jpg",
                "name": Path(image_path).name,
                "data": image_data,
            }
        ]
    }

    resp = requests.post(
        settings.CLOVA_OCR_URL,
        headers={
            "X-OCR-SECRET": settings.CLOVA_OCR_SECRET,
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=30,
    )
    resp.raise_for_status()

    result = resp.json()
    fields = result["images"][0].get("fields", [])
    return [{"text": f["inferText"], "confidence": f["inferConfidence"]} for f in fields]
```

## 텍스트 파싱

OCR 결과가 나오면 LLM으로 역할-이름 쌍을 추출.

```python
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate

PARSE_PROMPT = """다음은 영화/드라마 엔딩 크레딧에서 추출한 텍스트입니다.
역할과 이름 쌍을 JSON 배열로 추출하세요.
텍스트가 역할명이면 role, 이름이면 name으로 분류하고,
역할과 이름이 같은 줄에 있으면 같은 entry로 묶으세요.

텍스트: {credit_text}

출력 형식: [{{"role": "...", "name": "..."}}]
"""

def parse_credit_text(ocr_texts: list[str]) -> list[dict]:
    combined = "\n".join(ocr_texts)
    prompt = ChatPromptTemplate.from_template(PARSE_PROMPT)
    chain = prompt | ChatOpenAI(model="gpt-4o-mini", temperature=0)
    result = chain.invoke({"credit_text": combined})
    return json.loads(result.content)
```

## 중복 제거

같은 프레임에서 텍스트가 겹치거나, 연속 프레임에서 같은 내용이 반복 추출되는 문제가 있었다.

```python
def deduplicate_credits(entries: list[dict]) -> list[dict]:
    seen = set()
    unique = []
    for entry in entries:
        key = (entry.get("role", ""), entry.get("name", ""))
        if key not in seen and entry.get("name"):
            seen.add(key)
            unique.append(entry)
    return unique
```

## 결과

Tesseract 대비 Clova OCR 인식률이 크레딧 텍스트 기준 30% → 78%로 올랐다. 완벽하진 않지만 KOBIS에서 누락된 스태프를 상당수 채울 수 있었다.

비용은 Clova OCR이 이미지당 약 3원. 영화 한 편에 프레임 100~200장이면 300~600원. 작품 수 늘어나면 부담이 되지만, 데이터 품질 개선 효과가 크다고 판단.

다음 개선: 크레딧 구간 자동 감지. 현재는 영상 후반 20%를 통으로 처리하는데, 실제 크레딧은 더 짧다. 텍스트 밀도 기반으로 크레딧 시작/끝 지점을 자동으로 찾는 로직 추가 예정.
