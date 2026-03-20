---
layout: post
title: 멀티에이전트 LLM 가격 검색 파이프라인 설계하기
subtitle: Orchestrator 패턴으로 6개 마켓플레이스를 fast/deep 모드로 병렬 탐색하고 MAD 기반 미끼 가격을 걸러내기까지
author: HyeongJin
date: 2026-03-19 10:00:00 +0900
categories: AI/LLM
tags: [Python, FastAPI, LLM, Multi-Agent, Langfuse]
sidebar: []
published: true
---

글로벌 최저가 탐색 플랫폼을 만들면서 가장 먼저 부딪힌 문제가 있었다.

단순히 여러 마켓플레이스를 크롤링하면 되는 게 아니었다. 공식몰·중고·해외직구가 섞인 결과에서 "진짜 최저가"를 뽑으려면 각 소스의 성격에 맞게 쿼리를 다르게 짜야 했고, 부품용·약정포함·빈 박스 같은 미끼 가격도 걸러야 했다. 그리고 이 모든 게 45초 안에 끝나야 했다.

결국 역할이 분리된 에이전트를 두고, Orchestrator가 타임아웃 내에 순서대로 실행하는 구조로 설계했다.

## 전체 구조

```
사용자 쿼리
    ↓
QueryExpander (LLM/휴리스틱 다국어 확장)
    ↓
SearchOrchestrator
    ├── [fast mode] 에이전트 2개, 45초
    └── [deep mode] 에이전트 4개, 160초
         │
         ├── official_retail_agent  → official 어댑터
         ├── used_market_agent      → used 어댑터
         ├── cross_region_agent     → 전체 어댑터
         └── verification_agent    → 전체 어댑터 (노이즈 제거)
              │
              ↓
         가격 정규화 (착지가격: 가격 + 배송 + 환율 + 수입세)
              ↓
         MAD 기반 이상가격 필터링
              ↓
         LLM 검증 (deep mode만)
              ↓
         가중치 스코어링 → 결과 반환
```

어댑터는 총 6개다. Bunjang, Mercari, eBay Browse API, eBay HTML, Web Retail Live, Web Used Live. 각 어댑터는 `source_type`으로 `official` / `used`를 선언하고, 에이전트의 `source_scope`에 따라 Orchestrator가 라우팅한다.

## 에이전트 프로필 정의

에이전트마다 역할(prompt), 담당 소스(scope), 페이지 스캔 배수(page_multiplier)가 다르다.

```python
@dataclass(frozen=True)
class SearchAgentProfile:
    agent_id: str
    source_scope: str       # official | used | all
    prompt: str
    page_multiplier: float  # 기본 페이지 수에 곱하는 배수
    prompt_source: str = "local"
    prompt_key: str = ""

role_defs = [
    ("official_retail_agent", "official", 1.0),   # 공식 채널, 기본 페이지
    ("used_market_agent",     "used",     1.35),  # 중고는 매물이 많으니 35% 더 스캔
    ("cross_region_agent",    "all",      1.05),  # 크로스보더, 전체 채널
    ("verification_agent",    "all",      0.8),   # 검증 목적, 빠르게 훑기
]
```

중고 에이전트가 page_multiplier 1.35인 건 이유가 있다. 번개장터·메르카리는 한 상품에 수십 개 매물이 올라오는데, 페이지를 충분히 안 보면 진짜 최저가를 놓친다.

프롬프트는 Langfuse에서 동적으로 가져오고, 없으면 로컬 `multi_agent_prompts.json`으로 폴백한다.

## fast / deep 모드 분기

```python
run_timeout_seconds = (
    self.deep_timeout_seconds   # 160초
    if state.scan_mode == "deep"
    else self.fast_timeout_seconds  # 45초
)
deadline = time.monotonic() + run_timeout_seconds

if state.scan_mode == "fast":
    plans = plans[: self.fast_mode_agent_limit]  # 에이전트 2개
else:
    plans = plans[: self.deep_mode_agent_limit]  # 에이전트 4개

for plan in plans:
    if time.monotonic() >= deadline:
        timeout_reached = True
        break
    # 어댑터 실행...
```

에이전트 루프 안에서, 그리고 어댑터 루프 안에서도 매번 deadline을 체크한다. 타임아웃이 지나면 그 시점까지 모은 결과를 그대로 반환한다. 결과가 부분적이더라도 아무것도 없는 것보단 낫다.

어댑터 하나가 예외를 던져도 `try/except`로 잡아서 다음 어댑터로 넘어간다. 개별 소스 실패가 전체 검색을 죽이면 안 된다.

## 에이전트별 쿼리 분기

LLM 플래너가 켜져 있으면 각 에이전트가 역할에 맞는 쿼리 변형을 LLM으로 만든다. 꺼져 있거나 fast 모드면 규칙 기반 폴백을 쓴다.

```python
suffix_map = {
    "official_retail_agent": {
        "en": "official store new genuine",
        "ko": "정품 새상품 공식",
        "ja": "公式 新品 正規",
        "zh": "官方 全新 正品",
    },
    "used_market_agent": {
        "en": "used pre-owned second hand",
        "ko": "중고 실사용",
        "ja": "中古 使用済み",
        "zh": "二手 已使用",
    },
    "verification_agent": {
        "en": "exact model no accessories no parts",
        "ko": "정확한 모델 액세서리 제외 부품용 제외",
        "ja": "型番一致 アクセサリ除外 部品除外",
        "zh": "型号一致 排除配件 排除零件",
    },
}
```

쿼리 확장기(QueryExpander)가 한국어 상품명을 영어·일어·중국어로 변환해두면, 각 에이전트가 그 위에 역할별 서픽스를 붙여서 마켓플레이스에 던진다.

## 미끼 가격 필터링: MAD 기반 3층 방어

이게 가장 공을 많이 들인 부분이다. 가격 비교 서비스에서 제일 위험한 건 "부품용 5만원"이나 "약정포함 0원" 같은 미끼 가격이 최저가로 뜨는 경우다.

```python
def _filter_price_bait_offers(self, offers):
    all_prices = [offer.landed_price_krw for offer in offers]
    used_prices = [o.landed_price_krw for o in offers if o.source_class == "used_market"]
    verified_prices = [
        o.landed_price_krw for o in offers
        if o.source_class in {"trusted_retail", "brand_official"}
    ]

    median_all      = median(all_prices)
    median_used     = median(used_prices) if used_prices else median_all
    median_verified = median(verified_prices) if verified_prices else median_all

    # 1층: 소스 신뢰도별 floor 분리
    overall_floor  = max(13500, median_all      * 0.35)
    used_floor     = max(13500, median_used     * 0.35)
    verified_floor = max(13500, median_verified * 0.28)  # 공식몰은 더 관대하게

    # 2층: MAD(Median Absolute Deviation) 기반 robust floor
    mad = median([abs(p - median_all) for p in all_prices]) or 0.0
    robust_floor = median_all - (3.0 * mad)
    if robust_floor > 0:
        overall_floor  = max(overall_floor,  robust_floor)
        used_floor     = max(used_floor,     robust_floor * 0.95)
        verified_floor = max(verified_floor, robust_floor * 0.90)

    # 3층: 제목·URL 기반 휴리스틱 필터
    bait_tokens = [
        "for parts", "parts only", "empty box", "broken",
        "박스만", "고장", "부품용", "요금제", "약정", "개통",
    ]
    bait_url_tokens = ["/search", "search=", "/category", "/sch/i.html"]
```

**1층 — 소스 신뢰도별 floor**: 공식몰(brand_official)과 중고(used_market)의 정상 가격대가 다르기 때문에 분리했다. 중고 최저가의 35% 미만이면 의심, 공식몰은 28% 미만으로 좀 더 관대하게 잡는다.

**2층 — MAD**: 평균 대신 중앙값을 쓰는 이유는 이상값에 덜 민감하기 때문이다. MAD는 `median(|xi - median|)`이고, `median - 3*MAD` 이하면 통계적 이상값으로 본다. 데이터가 편향되어 있는 가격 분포에서 단순 표준편차보다 훨씬 안정적이다.

**3층 — 휴리스틱**: 아무리 가격이 정상 범위여도 제목에 "부품용"이 있거나 URL이 검색 결과 페이지면 제외한다.

세 조건 중 하나라도 걸리면 드롭한다. 그리고 이 필터 때문에 유효한 오퍼가 0개가 되면 필터를 적용하지 않고 원본을 그대로 반환한다.

## 가중치 스코어링

필터를 통과한 오퍼는 4개 축으로 점수를 매긴다.

```python
DEFAULT_WEIGHTS = {
    "price":     0.5,   # 착지가격 기준 상대 점수
    "trust":     0.2,   # 소스 신뢰도
    "match":     0.2,   # 스펙 매칭 신뢰도 (용량, 색상)
    "freshness": 0.1,   # 등록 신선도
}

SOURCE_TRUST_BASE = {
    "brand_official":       95.0,
    "trusted_retail":       88.0,
    "used_market":          70.0,
    "private_or_unverified": 55.0,
}
```

가격 점수는 최저가에 가까울수록 100점에 가깝고, 중앙값에서 멀어질수록 25점까지 떨어진다. 중고 최저가가 공식몰보다 30% 저렴해도, 신뢰도 점수 때문에 최종 스코어는 비슷하게 나오는 경우도 생긴다.

## Variant Fallback

용량(128GB/256GB)이나 색상 조건을 지정하면 조건 불일치 오퍼는 별도 큐에 담아둔다.

```python
if variant_status == "mismatch":
    relaxed_confidence = max(0.3, round(offer.match_confidence - 0.22, 2))
    relaxed_variant_offers.append(
        offer.model_copy(update={"match_confidence": relaxed_confidence})
    )
    continue

normalized_offers.append(offer)

# 모든 에이전트가 돌고 나서...
if not normalized_offers and relaxed_variant_offers:
    variant_fallback_used = True
    normalized_offers = self._dedupe_offers(relaxed_variant_offers)
```

조건에 딱 맞는 오퍼가 0개면, confidence를 낮춘 불일치 오퍼들을 폴백으로 쓴다. 아무 결과도 없는 것보다 낫고, match_confidence가 낮아졌기 때문에 스코어링에서 페널티를 받는다.

## Langfuse 옵저버빌리티

프로덕션에서 LLM 파이프라인을 운영하다 보면 "왜 이 결과가 나왔는지"를 추적하는 게 생각보다 중요하다.

```python
trace = self.telemetry.start_trace(
    name="search_orchestrator.run",
    input_payload={"target_key": ..., "scan_mode": ...},
)

# 에이전트마다 span
agent_span = trace.span(name=f"agent:{agent_name}", ...)

# 끝나면
trace.end(output={"offers": len(normalized_offers), ...})
```

run 단위 trace → 에이전트 단위 span → LLM call 단위 generation 트레이스가 중첩 구조로 Langfuse에 쌓인다. 어떤 에이전트가 몇 페이지 스캔했는지, 프롬프트를 로컬에서 가져왔는지 Langfuse에서 가져왔는지, 이상 가격 필터에서 몇 개 걸렸는지를 전부 coverage_stats로 기록한다.

```python
coverage_stats = CoverageStats(
    scan_mode=state.scan_mode,
    adapters_used=len(self.adapters),
    agent_pages_scanned=agent_pages_scanned,       # 에이전트별 페이지 수
    agent_raw_candidates=agent_raw_candidates,     # 에이전트별 원본 후보 수
    agent_prompt_sources=agent_prompt_sources,     # local vs langfuse
    outlier_filtered_count=outlier_filtered,       # MAD 필터 제거 수
    llm_validation_status=validation_note,
)
```

## 결론

멀티에이전트를 쓴다고 해서 단순히 "에이전트 여러 개 붙이기"가 아니다. 역할을 나누고, 소스 범위를 분리하고, 쿼리를 역할에 맞게 다르게 만들어야 실제로 의미가 생긴다.

그리고 LLM을 붙이면 결과의 설명 가능성이 올라가는 대신 속도가 느려진다. fast/deep 모드로 분리한 것도 그 트레이드오프를 그대로 설계에 반영한 거다.

가격 필터에서 MAD를 쓴 이유도 비슷하다. 가격 데이터는 왜곡이 심해서 단순 표준편차보다 중앙값 기반의 robust statistics가 훨씬 안정적으로 동작한다.
