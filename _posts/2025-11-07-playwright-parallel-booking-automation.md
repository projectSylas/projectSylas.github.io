---
layout: post
title: Playwright로 예약 자동화 봇 만들기 - 병렬 처리와 Shadow DOM 대응
subtitle: React 기반 예약 위젯에서 Shadow DOM을 뚫고 병렬로 여러 시간대를 동시에 시도하는 방법
author: HyeongJin
date: 2025-11-07 11:00:00 +0900
categories: Backend
tags: [Python, Playwright, automation, backend]
sidebar: []
published: true
---

예약 자동화 봇을 Playwright로 만들었다. 구체적인 내용은 생략하고, 만들면서 부딪혔던 기술적 문제들만 정리한다.

## Shadow DOM 문제

최신 React 기반 예약 위젯은 Shadow DOM을 쓰는 경우가 많다. 일반 `page.locator`로는 Shadow DOM 안쪽 요소에 접근이 안 된다.

Playwright는 `pierce` CSS selector로 Shadow DOM을 뚫을 수 있다.

```python
# 일반 locator — Shadow DOM 안쪽 접근 불가
page.locator("button.booking-confirm")  # 작동 안 함

# pierce로 Shadow DOM 관통
page.locator("pierce/button.booking-confirm")  # 작동함
```

아니면 `evaluate`로 직접 DOM을 파고드는 방식도 있다.

```python
page.evaluate("""
    document.querySelector('booking-widget')
        .shadowRoot
        .querySelector('button.confirm')
        .click()
""")
```

## 병렬 처리

여러 시간대를 동시에 시도해야 했다. Python `asyncio`로 Playwright async API를 쓰면 된다.

```python
import asyncio
from playwright.async_api import async_playwright

async def try_booking(context, time_slot: str) -> bool:
    page = await context.new_page()
    try:
        await page.goto(BOOKING_URL)
        await page.wait_for_timeout(1000)

        # 시간대 선택
        slot = page.locator(f"pierce/[data-time='{time_slot}']")
        if not await slot.is_visible():
            return False

        await slot.click()
        await page.locator("pierce/button.next").click()
        # ... 예약 진행
        return True
    finally:
        await page.close()

async def run():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()

        tasks = [
            try_booking(context, slot)
            for slot in TIME_SLOTS
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        # 먼저 성공한 슬롯 처리
```

여러 시간대를 `asyncio.gather`로 동시에 시도한다. 하나라도 성공하면 나머지는 취소.

## 마감 날짜 자동 감지

예약 가능한 날짜인지 미리 확인하는 로직이 필요했다.

```python
async def is_date_available(page, date: str) -> bool:
    date_cell = page.locator(f"pierce/td[data-date='{date}']")
    classes = await date_cell.get_attribute("class") or ""

    # full_day 클래스가 있으면 마감
    if "full_day" in classes:
        return False
    # _day_item 클래스가 없으면 선택 불가 날짜
    if "_day_item" not in classes:
        return False
    return True
```

## 테스트 모드

결제 직전까지만 진행하는 테스트 모드를 넣어뒀다. 실수로 실제 결제가 나가는 걸 방지하기 위해.

```python
async def run_booking(test_mode: bool = True):
    # ...예약 폼 입력...

    if test_mode:
        print("테스트 모드: 결제 직전에서 중단")
        await browser.close()
        return

    await page.locator("pierce/button.payment-confirm").click()
```

## uv로 가볍게 패키징

패키지 관리는 uv를 썼다. `requirements.txt`나 Poetry보다 훨씬 빠르고 가볍다.

```toml
# pyproject.toml
[project]
name = "autobooking"
requires-python = ">=3.11"
dependencies = [
    "playwright>=1.40.0",
]

[tool.uv]
dev-dependencies = ["pytest>=7.0"]
```

```bash
uv sync
uv run python main.py
```

## 정리

Playwright는 Shadow DOM 지원, 비동기 병렬 처리, 헤드리스 실행 모두 잘 된다. React 기반 SPA에서 동적으로 렌더링되는 요소들은 `wait_for_selector`나 `wait_for_timeout`으로 로딩을 기다린 뒤 처리해야 한다. 타임아웃을 너무 빡빡하게 잡으면 빠른 처리를 위해 값을 줄였다가 요소를 못 찾는 케이스가 생기니 주의.
