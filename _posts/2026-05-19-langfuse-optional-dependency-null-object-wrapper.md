---
layout: post
title: Langfuse를 선택적 의존성으로 만들기 — Null Object 래퍼 설계
subtitle: 환경변수가 없거나 패키지가 없어도 동일하게 작동하는 텔레메트리 래퍼
author: HyeongJin
date: 2026-05-19 10:00:00 +0900
categories: AI/LLM
tags: [Python, Langfuse, LLM, DesignPattern, backend]
sidebar: []
published: true
---

멀티에이전트 LLM 파이프라인에 Langfuse 트레이싱을 붙이면서 문제가 생겼다. 로컬 개발 환경에는 Langfuse API 키가 없고, CI 환경에도 없다. 그렇다고 코드 곳곳에 `if langfuse_enabled:` 분기를 넣으면 비즈니스 로직이 오염된다.

해결 방법은 **Null Object Pattern**이다. Langfuse가 없을 때는 메서드를 호출해도 아무것도 하지 않는 빈 객체를 반환하게 만들면, 호출 코드는 Langfuse 활성화 여부를 신경 쓰지 않아도 된다.

## 구조

```
TelemetryClient          ← Langfuse 초기화, start_trace() 진입점
    └── TelemetrySpan    ← trace/span/generation 노드 래퍼, end() 호출
```

`TelemetryClient`는 Langfuse가 활성화됐을 때 실제 trace를, 비활성화됐을 때 빈 `TelemetrySpan()`을 반환한다. `TelemetrySpan`은 항상 동일한 인터페이스를 제공하므로 호출 코드는 분기 없이 쓸 수 있다.

## TelemetrySpan — Null Object

```python
from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Dict

@dataclass
class TelemetrySpan:
    node: Any = None  # None이면 Null Object

    def span(self, name: str, input_payload=None, metadata=None) -> "TelemetrySpan":
        if self.node is None:
            return TelemetrySpan()  # 빈 span 반환, 에러 없음
        try:
            child = self.node.span(name=name, input=input_payload, metadata=metadata)
            return TelemetrySpan(child)
        except Exception:
            return TelemetrySpan()

    def generation(self, name: str, model: str, input_payload=None, metadata=None) -> "TelemetrySpan":
        if self.node is None:
            return TelemetrySpan()
        try:
            child = self.node.generation(
                name=name, model=model, input=input_payload, metadata=metadata
            )
            return TelemetrySpan(child)
        except Exception:
            return TelemetrySpan()

    def end(self, output: Any = None, metadata=None) -> None:
        if self.node is None:
            return  # 아무것도 하지 않음
        try:
            end_fn = getattr(self.node, "end", None) or getattr(self.node, "update", None)
            if callable(end_fn):
                payload = {k: v for k, v in {"output": output, "metadata": metadata}.items() if v is not None}
                end_fn(**payload)
        except Exception:
            return
```

`node=None`이면 모든 메서드가 아무 작업 없이 빈 `TelemetrySpan`이나 `None`을 돌려준다. 호출 코드는 반환값을 그대로 체이닝해도 안전하다.

## TelemetryClient — 초기화 및 활성화 제어

```python
import os

try:
    from langfuse import Langfuse
except Exception:
    Langfuse = None  # 패키지 없어도 import 에러 없음


class TelemetryClient:
    def __init__(self) -> None:
        self.public_key = os.getenv("LANGFUSE_PUBLIC_KEY", "").strip()
        self.secret_key = os.getenv("LANGFUSE_SECRET_KEY", "").strip()
        self.host = os.getenv("LANGFUSE_HOST", "https://cloud.langfuse.com").strip()
        self.environment = os.getenv("APP_ENV", "development").strip()

        # 두 조건 모두 충족해야 활성화
        self.enabled = bool(self.public_key and self.secret_key and Langfuse is not None)
        self.client = None

        if self.enabled:
            try:
                self.client = Langfuse(
                    public_key=self.public_key,
                    secret_key=self.secret_key,
                    host=self.host,
                    environment=self.environment or None,
                )
            except Exception:
                self.client = None
                self.enabled = False

    def start_trace(self, name: str, session_id=None, input_payload=None, metadata=None) -> TelemetrySpan:
        if not self.enabled or self.client is None:
            return TelemetrySpan()  # 항상 TelemetrySpan 반환
        try:
            trace = self.client.trace(
                name=name,
                session_id=session_id,
                input=input_payload,
                metadata=metadata,
            )
            return TelemetrySpan(trace)
        except Exception:
            return TelemetrySpan()

    def flush(self) -> None:
        if not self.enabled or self.client is None:
            return
        try:
            flush_fn = getattr(self.client, "flush", None)
            if callable(flush_fn):
                flush_fn()
        except Exception:
            return
```

`Langfuse = None` fallback으로 패키지 자체가 없어도 `import` 시 크래시가 없다. `enabled` 플래그는 키 존재 여부와 패키지 가용성을 동시에 체크한다.

## 실제 사용 코드

호출 코드는 `telemetry.enabled` 체크 없이 그대로 쓴다.

```python
# 초기화 (한 번만)
telemetry = TelemetryClient()

# 검색 오케스트레이터에서 trace 시작
trace = telemetry.start_trace(
    name="search_orchestrator.run",
    session_id=session_id,
    input_payload={"query": query, "scan_mode": "deep"},
)

# LLM 호출마다 generation 기록
generation = trace.generation(
    name="llm.extract.attempt_1",
    model="gpt-4.1",
    input_payload={"prompt": prompt[:1600]},
)

# ... LLM 호출 ...

generation.end(output={"keys": list(result.keys())}, metadata={"success": True})
trace.end(metadata={"total_offers": len(offers)})
telemetry.flush()
```

Langfuse 키가 없는 환경에서는 `start_trace()`가 빈 `TelemetrySpan()`을 반환하고, 이후 `.generation()`, `.end()` 호출도 모두 no-op이 된다. 코드 변경 없이 개발/스테이징/프로덕션 환경을 전환할 수 있다.

## 헬스체크 노출

`/health` 엔드포인트에서 Langfuse 활성화 여부를 확인할 수 있다.

```python
@app.get("/health")
async def health():
    return {
        "status": "ok",
        "langfuse_enabled": telemetry.enabled,
    }
```

환경변수 설정 여부를 배포 후 즉시 확인할 수 있다.
