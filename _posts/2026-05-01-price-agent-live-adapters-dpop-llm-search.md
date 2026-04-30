---
layout: post
title: price-agent 라이브 어댑터 3종 — 번개장터 공개 API, 메루카리 DPoP JWT, LLM web_search_preview
subtitle: 파트너 토큰 없이 번개장터 크롤링, ES256 ECDSA 서명으로 메루카리 인증, OpenAI Responses API 2단계로 야후옥션·Amazon JP 수집
author: HyeongJin
date: 2026-05-01 10:00:00 +0900
categories: Backend
tags: [Python, FastAPI, OpenAI, Scraping, LLM]
sidebar: []
published: true
---

price-agent는 중고 상품 시세를 여러 플랫폼에서 수집해서 랭킹을 매긴다. 초기 구현에서는 mock 어댑터로 동작했다. 실제 데이터를 수집하기 위해 세 가지 라이브 어댑터를 추가했다. 번개장터 실시간 검색, 메루카리 JP, 그리고 커스텀 스크레이퍼가 없는 사이트를 LLM + web_search_preview로 커버하는 어댑터다.

## BunjangLiveAdapter — 공개 API 듀얼 경로

번개장터는 파트너 Open API를 제공하지만 파트너 토큰 없이도 공개 검색 API를 쓸 수 있다. 두 경로를 환경변수 하나로 분기한다.

```python
def search(self, canonical_product, queries, max_pages=1):
    if self.partner_token:
        return self._search_open_api(canonical_product, terms, max_pages)
    return self._search_find_api(canonical_product, terms, max_pages)
```

공개 API 엔드포인트는 `https://api.bunjang.co.kr/api/1/find_v2.json`이다.

```python
params = {
    "q": term,
    "page": page,
    "order": "score",      # score: 관련도, date: 최신
    "req_ref": "search",
    "stat_device": "w",    # 웹으로 위장
}
resp = client.get(FIND_API_URL, params=params)
# 응답: {"list": [...], "num_found": N, "n": 60}
```

응답에서 `num_found`와 페이지 크기 `n`으로 마지막 페이지를 판단한다.

```python
if (page + 1) * page_size >= num_found:
    break  # 더 이상 결과 없음
```

`status=0`인 것만 수집한다(0=판매중, 1=판매완료). `used=2`(새상품)이면 condition을 `new`로 처리한다.

### 상태 등급 키워드 파싱

번개장터는 상태 등급이 별도 필드가 아니라 상품명 텍스트에 섞여 있다. 키워드 매칭으로 파싱한다.

```python
_GRADE_S = ("s급", "s+급", "풀박", "풀구성", "미개봉", "미사용", "새상품급")
_GRADE_A = ("a급", "상급", "상태좋음", "깨끗", "흠집없음", "무각", "거의새것")
_GRADE_B = ("b급", "중급", "약간", "미세스크", "잔기스", "헤어라인")
_GRADE_C = ("c급", "하급", "파손", "고장", "부품용", "불량")
```

제목을 소문자로 정규화하고 각 그룹 키워드를 순서대로 확인한다. `used=2`(새상품 플래그)이면 키워드 검색 전에 `"new"`를 반환한다.

## MercariLiveAdapter — DPoP JWT 직접 구현

메루카리 JP API는 모든 요청에 **DPoP(Demonstration of Proof-of-Possession)** 토큰을 요구한다. 표준 Bearer 토큰과 달리 DPoP은 특정 HTTP 요청(URL + 메서드)에 바인딩된 서명 토큰이다. 훔쳐도 다른 요청에 재사용할 수 없다.

### ECDSA P-256 키 생성

어댑터 초기화 시 세션 단위로 EC 키 쌍을 생성한다.

```python
from cryptography.hazmat.primitives.asymmetric import ec

class MercariLiveAdapter(SourceAdapter):
    def __init__(self):
        # 세션마다 새 ECDSA P-256 키 쌍
        self._private_key = ec.generate_private_key(ec.SECP256R1())
        self._session_uuid = str(uuid.uuid4())
```

### DPoP JWT 생성

요청마다 DPoP 토큰을 새로 만든다. 구조는 `header.payload.signature`다.

```python
def _generate_dpop(url, method, private_key):
    pub = private_key.public_key()
    pub_numbers = pub.public_numbers()

    # 헤더: 알고리즘, 공개키 (JWK)
    header = {
        "alg": "ES256",
        "typ": "dpop+jwt",
        "jwk": {
            "crv": "P-256",
            "kty": "EC",
            "x": _int_to_b64url(pub_numbers.x, 32),
            "y": _int_to_b64url(pub_numbers.y, 32),
        }
    }

    # 페이로드: 요청 URL + 메서드 바인딩
    payload = {
        "iat": int(time.time()),       # 발급 시각
        "jti": str(uuid.uuid4()),      # 재사용 방지 UUID
        "htu": url,                    # 대상 URL
        "htm": method.upper(),         # HTTP 메서드
    }

    # ES256 서명 (DER → raw r||s 변환)
    signing_input = f"{header_b64}.{payload_b64}".encode()
    der_sig = private_key.sign(signing_input, ec.ECDSA(hashes.SHA256()))
    r, s = decode_dss_signature(der_sig)
    sig_bytes = r.to_bytes(32, "big") + s.to_bytes(32, "big")

    return f"{header_b64}.{payload_b64}.{_b64url(sig_bytes)}"
```

ES256 서명에서 주의할 점은 `cryptography` 라이브러리가 DER 인코딩 형식으로 서명을 반환한다는 것이다. JWT ES256은 raw `r || s` (각 32바이트) 형식이 필요하므로 `decode_dss_signature(der_sig)`로 r, s를 분리해서 연결한다.

### 요청 헤더에 DPoP 추가

```python
dpop = _generate_dpop(MERCARI_SEARCH_URL, "POST", self._private_key)
headers = {
    "DPoP": dpop,
    "X-Platform": "web",
    "Accept-Language": "ja-JP,ja;q=0.9",
    # ...
}
resp = client.post(MERCARI_SEARCH_URL, json=body, headers=headers)
```

401 응답이 오면 키를 새로 생성하고 한 번 더 시도한다.

```python
if resp.status_code == 401:
    self._private_key = ec.generate_private_key(ec.SECP256R1())
    dpop = _generate_dpop(MERCARI_SEARCH_URL, "POST", self._private_key)
    headers["DPoP"] = dpop
    resp = client.post(MERCARI_SEARCH_URL, json=body, headers=headers)
```

### 상태 코드 → 등급 매핑

메루카리는 `itemConditionId` 필드에 정수로 상태를 표시한다.

```python
_MERCARI_CONDITION_GRADE = {
    1: "new",  # 新品/未使用
    2: "S",    # 未使用に近い
    3: "A",    # 目立った傷や汚れなし
    4: "B",    # やや傷や汚れあり
    5: "C",    # 傷や汚れあり
    6: "C",    # 全体的に状態が悪い
}
```

번개장터가 텍스트 파싱이 필요한 것과 달리 메루카리는 구조화된 데이터로 제공하기 때문에 매핑이 간단하다.

## LLMWebSearchAdapter — web_search_preview 2단계 파이프라인

커스텀 스크레이퍼를 만들기 어려운 사이트(야후옥션 JP, Amazon JP, eBay)는 OpenAI Responses API의 `web_search_preview` 툴을 사용한다. 스크레이퍼 없이 사실상 모든 사이트를 커버한다는 게 핵심이다.

`deep_only = True`로 표시해서 빠른 검색 모드에서는 자동으로 건너뛴다. 검색에 LLM 호출이 2번 들어가기 때문이다.

```python
class LLMWebSearchAdapter(SourceAdapter):
    deep_only = True  # fast 모드에서 제외
    
    def __init__(self):
        self.model = os.getenv("LLM_WEBSEARCH_MODEL", "gpt-4.1")
        self.timeout = float(os.getenv("LLM_WEBSEARCH_TIMEOUT", "40"))
        sites_env = os.getenv("LLM_WEBSEARCH_SITES", "joonggonara,daangn,yahoo_auction")
        self.sites = [s.strip() for s in sites_env.split(",") if s.strip() in _SITE_PROMPTS]
```

### Step 1: web_search_preview로 검색 텍스트 수집

```python
resp = client.responses.create(
    model=self.model,
    tools=[{"type": "web_search_preview"}],
    instructions=_SEARCH_INSTRUCTIONS,
    input=site_prompt,  # 사이트별 프롬프트
)
# resp.output에서 message 블록의 텍스트 추출
```

사이트별 프롬프트 예:

```python
_SITE_PROMPTS = {
    "yahoo_auction": (
        "Search Yahoo! Auctions Japan (auctions.yahoo.co.jp) for used listings of: {query}\n"
        "Find multiple actual auction/buy-it-now listings with prices in JPY.\n"
        "Include at least 5 listings if available."
    ),
    "naver_used": (
        "Search Naver Shopping used goods (search.shopping.naver.com) for: {query} 중고\n"
        "Find used product listings from 중고나라, 번개장터, or other Korean sellers."
    ),
}
```

### Step 2: chat completion으로 텍스트 → JSON 파싱

Step 1에서 받은 자연어 텍스트를 구조화된 JSON으로 변환한다.

```python
parse_resp = client.chat.completions.create(
    model=self.model,
    response_format={"type": "json_object"},
    messages=[
        {"role": "system", "content": _PARSE_SYSTEM},  # JSON 스키마 명시
        {"role": "user", "content": search_text},
    ],
)
parsed = json.loads(parse_resp.choices[0].message.content)
```

출력 스키마:

```json
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
```

`currency`는 KRW/JPY/USD로 명시한다. 이후 pricing 서비스에서 환율 변환에 사용한다.

### JP/KO 쿼리 분기

야후옥션과 Amazon JP는 일본어 쿼리가 필요하다. 카탈로그에 등록된 언어별 검색어나 쿼리 변형(QueryVariant)에서 `lang=="ja"`인 것을 우선 사용한다.

```python
query_ko = ko_terms[0]
ja_terms = get_search_queries(canonical_product, "mercari") or [q.text for q in queries if q.lang == "ja"]
query_ja = ja_terms[0] if ja_terms else query_ko  # JP 쿼리 없으면 한국어 fallback

for site in self.sites[:3]:
    q = query_ja if site in ("yahoo_auction", "amazon_jp") else query_ko
    raw = self._search_site(_SITE_PROMPTS[site].format(query=q))
```

## 어댑터 등록 구조

세 어댑터는 `SourceAdapter` 기본 클래스를 상속한다. 오케스트레이터는 등록된 어댑터 목록을 순회하고 `deep_only=True`인 것은 fast 모드에서 건너뛴다.

```python
ADAPTERS = [
    BunjangLiveAdapter(),
    MercariLiveAdapter(),
    LLMWebSearchAdapter(),  # deep_only — fast 모드 제외
]

def run_search(query, fast=False):
    for adapter in ADAPTERS:
        if fast and getattr(adapter, "deep_only", False):
            continue
        offers, scanned = adapter.search(...)
```

세 어댑터가 커버하는 영역은 서로 겹치지 않는다. 번개장터는 국내 중고, 메루카리는 일본 중고 직접 API, LLMWebSearchAdapter는 스크레이퍼가 없는 해외 사이트 fallback이다.
