---
layout: post
title: OpenAI web_search_preview로 크롤링 안 되는 사이트 긁기
subtitle: 야후옥션·Amazon JP·네이버 중고 — 스크래퍼 없이 Responses API 한 번으로 가격 데이터 JSON 추출
author: HyeongJin
date: 2026-03-23 10:00:00 +0900
categories: AI/LLM
tags: [Python, OpenAI, Crawling, LLM]
sidebar: []
published: true
---

가격 비교 플랫폼을 만들면서 야후옥션 일본, Amazon JP, 네이버 중고를 크롤링해야 했다. 문제는 이 사이트들이 전통적인 스크래퍼로 긁기가 까다롭다는 점이다. JS 렌더링이 필요하거나, 봇 감지가 빡빡하거나, DOM 구조가 자주 바뀐다.

Selenium이나 Playwright를 붙이는 대신 OpenAI Responses API의 `web_search_preview` 툴을 써봤다.

## Responses API와 web_search_preview

OpenAI Responses API는 Chat Completions와 다르게 툴을 모델이 직접 실행할 수 있다. `web_search_preview` 툴을 주면 LLM이 웹 검색을 하고 결과 텍스트를 가져온다.

```python
from openai import OpenAI

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

resp = client.responses.create(
    model="gpt-4.1",
    tools=[{"type": "web_search_preview"}],
    instructions="You are a shopping price research assistant. Search the web for the given product and site. Summarize ALL listings you find including: title, price, url/link, condition, site name.",
    input="Search Yahoo! Auctions Japan (auctions.yahoo.co.jp) for used listings of: iPhone 15 128GB\nFind at least 5 listings with prices in JPY.",
)
```

응답은 `resp.output` 리스트로 온다. `type == "message"` 블록 안의 `content`에서 텍스트를 꺼내면 된다.

```python
search_text = ""
for block in resp.output:
    if getattr(block, "type", None) == "message":
        for content in getattr(block, "content", []):
            text = getattr(content, "text", "") or ""
            if text:
                search_text = text
                break
```

## 2단계 파이프라인

LLM이 가져온 텍스트를 바로 쓸 수는 없다. 자연어 설명이 섞여 있어서 가격·URL·조건을 파싱하기 어렵다. 두 번째 chat completion으로 JSON으로 변환한다.

```python
parse_resp = client.chat.completions.create(
    model="gpt-4.1",
    messages=[
        {"role": "system", "content": _PARSE_SYSTEM},
        {"role": "user", "content": f"Search results:\n\n{search_text[:4000]}"},
    ],
    response_format={"type": "json_object"},
    temperature=0,
    max_tokens=1200,
)
```

`_PARSE_SYSTEM` 프롬프트에서 출력 스키마를 고정한다.

```python
_PARSE_SYSTEM = """
Convert the product search results text into a JSON object.
Return ONLY valid JSON, no markdown, no explanation.

Output format:
{"offers": [
  {
    "title": "listing title",
    "price": 350000,
    "currency": "KRW",
    "url": "https://...",
    "source": "중고나라",
    "origin_country": "KR",
    "condition": "used",
    "shipping_cost": 0
  }
]}

Rules:
- price must be a plain number (remove currency symbols and commas)
- currency: "KRW" / "JPY" / "USD"
- Only include listings with a real URL
- Maximum 10 offers
"""
```

`temperature=0`, `json_object` response format 조합이면 구조가 꽤 안정적으로 나온다.

## 사이트별 프롬프트 분기

사이트마다 검색 URL과 언어가 다르기 때문에 site prompt를 분리했다.

```python
_SITE_PROMPTS = {
    "yahoo_auction": (
        "Search Yahoo! Auctions Japan (auctions.yahoo.co.jp) for used listings of: {query}\n"
        "Find multiple actual auction/buy-it-now listings with prices in JPY.\n"
        "Include at least 5 listings if available."
    ),
    "amazon_jp": (
        "Search Amazon Japan (amazon.co.jp) for listings of: {query}\n"
        "Find new and used product listings with prices in JPY."
    ),
    "naver_used": (
        "Search Naver Shopping used goods (search.shopping.naver.com) for: {query} 중고\n"
        "Find used product listings from 중고나라, 번개장터, or other Korean sellers."
    ),
}
```

야후옥션·Amazon JP는 일본어 쿼리를 넘기고, 네이버는 한국어 쿼리를 쓴다.

```python
for site in self.sites[:3]:
    q = query_ja if site in ("yahoo_auction", "amazon_jp") else query_ko
    site_prompt = _SITE_PROMPTS[site].format(query=q)
    raw = self._search_site(site_prompt)
```

## JSON 파싱 방어 처리

LLM이 가끔 코드 블록으로 감싸거나 텍스트를 앞뒤에 붙이는 경우가 있다. 정규식으로 걷어내고 파싱한다.

```python
@staticmethod
def _extract_json(text: str) -> List[dict]:
    text = re.sub(r"```json\s*", "", text)
    text = re.sub(r"```\s*", "", text)
    try:
        data = json.loads(text.strip())
        offers = data.get("offers") or [] if isinstance(data, dict) else data
        return [o for o in offers if isinstance(o, dict)]
    except (json.JSONDecodeError, ValueError):
        # 실패하면 텍스트 안에서 JSON 블록을 직접 찾아봄
        m = re.search(r"\{.*\"offers\".*\}", text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0)).get("offers", [])
            except Exception:
                pass
    return []
```

## 통화 감지

LLM이 통화 단위를 항상 명확하게 내놓지는 않는다. 원문 가격 문자열에 `¥`, `円`, `원` 같은 기호가 남아 있는 경우가 있어서 그걸로 추론한다.

```python
currency_raw = str(item.get("currency") or "").strip().upper()
if not currency_raw:
    orig_price_str = str(item.get("price") or "")
    if "원" in orig_price_str or "₩" in orig_price_str:
        currency_raw = "KRW"
    elif "¥" in orig_price_str or "円" in orig_price_str:
        currency_raw = "JPY"
    else:
        currency_raw = "JPY" if origin_country == "JP" else "KRW"
```

## fast 모드에서 제외

`web_search_preview` 호출은 응답이 느리다. 사이트 3개를 순차적으로 돌리면 20~40초가 쉽게 나온다. fast 모드(45초 제한)에서 쓰면 다른 에이전트들이 전부 타임아웃에 걸린다.

```python
class LLMWebSearchAdapter(SourceAdapter):
    deep_only = True  # fast 모드에서는 제외
```

Orchestrator에서 어댑터를 로드할 때 `deep_only` 플래그를 보고 fast 모드에서는 이 어댑터를 건너뛴다.

## 장단점

**장점**
- 스크래퍼 코드가 없다. DOM 구조가 바뀌어도 유지보수할 게 없다.
- 봇 감지를 우회할 필요가 없다.
- 야후옥션 일본처럼 한국에서 직접 크롤링이 느린 사이트도 커버된다.

**단점**
- 느리다. 사이트당 8~15초.
- API 비용이 든다. 검색 횟수가 많아지면 비용이 선형으로 늘어난다.
- 결과 수가 적다. 한 번에 10개 내외. 전통적인 스크래퍼처럼 수십 개를 긁기 어렵다.
- LLM이 URL을 잘못 만들거나 없는 매물을 생성하는 경우가 드물게 있다. URL 유효성 검사 필수.

커스텀 스크래퍼가 필요한 자리를 완전히 대체하긴 어렵지만, deep 모드 보조 소스로는 충분히 쓸만하다.
