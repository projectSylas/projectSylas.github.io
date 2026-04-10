---
layout: post
title: 퀀트 전략 라우터 — 레짐 체인 결정, 차트 스냅샷, Softmax 가중치 배분
subtitle: 히스테리시스로 체인 전환 안정화, EMA 기반 차트 적합도 스코어, 온도 파라미터 Softmax까지
author: HyeongJin
date: 2026-04-10 10:00:00 +0900
categories: AI/LLM
tags: [Python, Trading, MachineLearning, FeatureEngineering]
sidebar: []
published: true
---

Transformer 레짐 분류기가 `p_trend_up / p_trend_down / p_chop / p_high_vol` 네 확률을 뱉는다. 이 확률로 "지금 어떤 전략을 켜고, 얼마나 태울까"를 결정하는 게 라우터 레이어다. 세 모듈이 파이프라인을 이룬다.

1. `chain.py` — 레짐 확률로 체인(defensive/chop/trend/neutral)을 결정
2. `chart_selector.py` — 차트 지표로 전략-차트 적합도를 계산
3. `interface.py` — 전략별 점수를 Softmax로 가중치로 변환

## 체인 결정 — `decide_chain`

레짐 확률이 들어오면 priority 순서로 체인을 고른다.

```python
# Priority: defensive → chop → trend → neutral
if p_hv >= hv_th and p_dn >= p_up:
    name = "defensive"
elif p_dn > p_up and p_dn >= 0.45:
    name = "defensive"
elif p_chop >= chop_th:
    name = "chop"
elif p_up >= trend_th and p_dn < trend_max_down:
    name = "trend"
else:
    name = "neutral"
```

defensive가 최우선이다. 고변동성(`p_hv`)이 높고 하락이 상승보다 강하면 먼저 방어 체인으로 간다. 고변동성이 없어도 하락 확률이 45% 이상이면 defensive로 분류한다. 횡보(`p_chop`)가 기준 이상이면 chop, 상승이 충분하면 trend, 어느 쪽도 아니면 neutral.

체인이 결정되면 해당 체인에 허용된 전략 목록(`strategies`)과 익스포저 배율(`exposure_multiplier`)을 꺼낸다. 전략이 하나도 매칭 안 되면 `fallback_default`로 떨어져서 전체 전략을 그대로 쓴다.

### 히스테리시스

레짐 확률은 매 봉마다 조금씩 흔들린다. 히스테리시스 없이 threshold만 쓰면 체인이 봉마다 defensive ↔ neutral을 왔다 갔다 할 수 있다.

```python
hyst = float(chain_cfg.get("hysteresis", 0.05))

if previous_chain_name == "defensive":
    hv_th -= hyst
elif previous_chain_name == "chop":
    chop_th -= hyst
elif previous_chain_name == "trend":
    trend_th -= hyst
```

현재 defensive 체인에 있으면 `hv_th`를 0.05 낮춘다. 즉 defensive에서 벗어나려면 더 강한 반대 신호가 필요하다. 이전 체인을 유지하는 쪽으로 threshold를 편향시켜서 불필요한 전환을 줄인다.

### 심볼/섹터 오버라이드

체인 전체 설정과 별개로 특정 심볼이나 섹터에 다른 전략 목록이나 익스포저를 줄 수 있다.

```python
def _get_chain_override(chain_cfg, chain_name, symbol, sector):
    overrides = chain_cfg.get("overrides", {})
    # Priority: symbol > sector
    source = overrides.get("symbol", {}).get(symbol)
    if source is None:
        source = overrides.get("sector", {}).get(sector)
    ...
    return ov_strategies, ov_exposure
```

심볼 오버라이드가 섹터보다 우선한다. 예를 들어 에너지 섹터 전체는 defensive 체인에서 익스포저 50%를 주되, XOM 한 종목은 30%를 주는 식으로 설정한다.

`vix_prob_hybrid`는 frozen 전략이다. 오버라이드 목록에 명시되어 있어도 다른 체인의 `allowed` 목록에서 걸러낸다. 항상 켜져 있어야 하는 VIX 기반 방어 전략이라 오버라이드로 끄지 못하게 막아뒀다.

## 차트 스냅샷 — `compute_chart_snapshot`

레짐은 Transformer가 학습한 확률이지만, 차트 스냅샷은 현재 봉 기준으로 즉시 계산하는 규칙 기반 지표다.

```python
trend_dir = _clamp(
    0.6 * _tanh_norm(trend60, 0.0035) + 0.4 * _tanh_norm(mom60, 0.015),
    -1.0, 1.0
)
trend_strength = _clamp(
    0.4 * abs(_tanh_norm(trend15, 0.0030))
    + 0.35 * abs(_tanh_norm(trend60, 0.0035))
    + 0.25 * abs(_tanh_norm(mom15 + mom60, 0.020)),
    0.0, 1.0
)
chop_score = _clamp(1.0 - trend_strength + 0.20 * vol_score, 0.0, 1.0)
overextension_score = _clamp(_tanh_norm(overext, 2.5), 0.0, 1.0)
```

- `trend_dir` — 60분봉 EMA 스프레드 + 모멘텀의 방향. +1이면 강한 상승, -1이면 강한 하락.
- `trend_strength` — 15분봉과 60분봉 EMA 스프레드의 절댓값 합성. 방향 없이 추세 강도만 본다.
- `chop_score` — `1 - trend_strength`. 추세가 약할수록 횡보 점수가 높다.
- `overextension_score` — 현재가가 EMA에서 얼마나 벗어났는지. ATR 대비 이격 거리.

`tanh`로 정규화하는 이유는 EMA 스프레드가 절댓값으로 비교 불가능하기 때문이다. SPY와 소형주의 스프레드 절댓값은 다르지만 tanh scale로 맞추면 -1~+1로 표준화된다.

## 전략-차트 적합도 스코어 — `score_strategy_chart_fit`

전략마다 성격이 다르다. 추세 추종 전략은 trend 체인에서 잘 맞고, 평균 회귀 전략은 chop 체인에서 잘 맞는다. 이 적합도를 수치로 계산한다.

```python
style = _strategy_style(strategy, selector_cfg.get("style_by_strategy", {}))
```

전략 이름에 "pmax", "golden_triangle", "emd" 등이 포함되면 `trend` 스타일, "support_resistance", "elastic" 등이면 `chop` 스타일, "bear_market", "vix"는 `defensive` 스타일로 분류한다. 명시적으로 `style_by_strategy` 맵에 등록하면 이름 규칙을 오버라이드한다.

스타일별 점수 공식:

```python
# trend 전략
score = (
    0.50 * align            # 신호 방향 × 차트 방향
    + 0.30 * trend_strength
    + 0.10 * (p_up if sig > 0 else p_dn)
    - 0.20 * p_chop         # 횡보 페널티
    - 0.10 * p_hv
    - 0.10 * overext        # 과열 페널티
)

# chop(평균 회귀) 전략
score = (
    0.35 * p_chop
    + 0.25 * chop_score
    + 0.20 * (1.0 - trend_strength)
    + 0.20 * mean_revert_align   # 차트 방향 반대
    - 0.15 * p_hv
)
```

`align`은 전략 신호 방향 × `trend_dir`. 신호가 롱이고 차트가 상승 방향이면 +, 신호는 롱인데 차트가 하락이면 -. 평균 회귀 전략은 반대로 역방향이 유리하다 (`mean_revert_align = -align`).

## Softmax 가중치 배분 — `route_weights`

체인이 허용한 전략들의 점수를 가중치로 바꾼다.

```python
def route_weights(regime_row, strategy_scores, cfg):
    temp = float(cfg.get("temperature", 0.75))
    max_w = float(cfg.get("max_single_weight", 0.45))

    adjusted = adjusted_strategy_scores(regime_row, strategy_scores, cfg)
    weights = _softmax(adjusted, temperature=temp)

    clipped = {k: min(max_w, v) for k, v in weights.items()}
    s = sum(clipped.values())
    return {k: v / s for k, v in clipped.items()}
```

Softmax 이전에 레짐 bias를 점수에 더한다.

```python
regime_boost = (
    alpha_up * p_up
    + alpha_dn * p_dn
    + alpha_chop * p_chop
    + alpha_hv * p_hv
)
adjusted[name] = score + regime_boost
```

전략 설정에 `trend_up: 0.3`이 있으면 상승 레짐 확률 × 0.3이 해당 전략의 점수에 더해진다. ROC Dual Momentum 전략은 `trend_up` 가중치가 높고, Z-Score Mean Reversion은 `chop` 가중치가 높다.

### 온도(temperature)

`temperature = 0.75`에서 Softmax는 상위 전략에 집중되지만 완전히 winner-take-all은 아니다.

```python
def _softmax(d, temperature=1.0):
    t = max(1e-6, float(temperature))
    mx = max(d.values())
    exps = {k: math.exp((v - mx) / t) for k, v in d.items()}
    den = sum(exps.values())
    return {k: v / den for k, v in exps.items()}
```

`temperature=1.0`이면 표준 Softmax, `temperature < 1.0`이면 점수 격차가 증폭되어 더 집중된다. `max_single_weight=0.45`로 단일 전략이 가중치 45% 이상을 가져가는 걸 막고, 초과분은 재정규화로 나머지에 분배한다.

## 전체 파이프라인 연결

```
레짐 분류기 출력 (p_trend_up, p_trend_down, p_chop, p_high_vol)
    ↓
decide_chain()
  → 체인명 (defensive/chop/trend/neutral)
  → 허용 전략 목록
  → exposure_multiplier
    ↓
compute_chart_snapshot(df_15m, df_60m)
  → trend_dir, trend_strength, chop_score, vol_score, overextension_score
    ↓
build_selector_edges()
  score_strategy_chart_fit() per strategy
    ↓
route_weights()
  adjusted_strategy_scores() → softmax → cap → renormalize
    ↓
전략별 포지션 비율 = 전략 신호 × weight × exposure_multiplier
    ↓
apply_exposure_caps() → generate_orders()
```

레짐 분류기와 리스크 오버레이 사이에 라우터가 끼어서 "어떤 전략을 켜고 얼마나 태울지"를 결정한다. 레짐 확률과 차트 상태를 동시에 보는 이유는 Transformer 레짐이 과거 32봉을 보는 지연 특성을 갖는 반면, 차트 스냅샷은 현재 봉 기준의 즉각적인 상태를 반영하기 때문이다. 둘을 결합하면 레짐의 큰 그림과 차트의 현재 상태가 모두 반영된 전략 선택이 된다.
