---
layout: post
title: 탈락 전략 해부 — Global Cut·trades 부족·신호 미발화 세 가지 실패 유형
subtitle: support_resistance_channels 전량 0거래 버그, bear_market_probability_model trades<30, trendlines 희귀 발화까지
author: HyeongJin
date: 2026-04-24 10:00:00 +0900
categories: AI/LLM
tags: [Python, Trading, Strategy, Backtesting, QuantFinance]
sidebar: []
published: true
---

14개 전략 × 32개 심볼 실험에서 최종 Allowlist에 들어간 전략은 `golden_triangle`, `pmax_explorer`, `elastic_student_t` 세 가지뿐이다. 나머지 11개가 왜 탈락했는지를 세 가지 실패 유형으로 나눠서 살펴본다. 버그 하나, 설계 문제 하나, 파라미터 문제 하나다.

## 유형 1 — 신호 자체가 발화하지 않는 버그

### `support_resistance_channels`

32개 심볼 전부에서 trades=0, Sharpe=0, OOS return=0이었다.

```python
DEFAULT_PARAMS = {"channel_window": 96, "break_buffer": 0.0005}

res = d["high"].rolling(int(p["channel_window"])).max()
sup = d["low"].rolling(int(p["channel_window"])).min()

long_cond = d["close"] > (res * (1.0 + buf))
short_cond = d["close"] < (sup * (1.0 - buf))
```

`res`는 96봉 rolling **high** 최댓값이다. 그런데 `long_cond`는 `close > res`를 요구한다. 문제는 `rolling().max()`가 **현재 봉을 포함**한다는 것이다. 현재 봉의 `high`는 `close`보다 항상 크거나 같다. 따라서 rolling max에는 반드시 `current_high >= current_close`인 값이 포함된다. `close > rolling_max_of_high`는 수학적으로 불가능하다.

```
현재 봉: high=105, close=104
res(96봉 rolling high max) >= 105  → close(104) > res(≥105): 항상 False
```

`long_cond`는 단 한 번도 True가 될 수 없다. 버그다. `res`를 `shift(1)`로 이전 봉의 최댓값과 비교했어야 한다.

```python
# 의도된 코드였다면:
res = d["high"].rolling(channel_window).max().shift(1)
long_cond = d["close"] > (res * (1.0 + buf))
```

Global Strategy Stats에서 Mean Sharpe가 정확히 0.000으로 나온 것이 이 버그의 직접적인 흔적이다.

## 유형 2 — 매매 횟수 부족 (trades < 30)

### `bear_market_probability_model`

Global Cut은 통과했다. OOS 양수 비율 65.6%, 평균 Sharpe 1.275로 12개 전략 중 2위였다. 그런데 최종 선택에서 제외됐다. 이유는 per-pair gate의 `trades >= 30` 조건이다.

```python
GATE = dict(sharpe=1.0, pf=1.2, mdd=-0.12, trades=30)
```

32개 심볼 중 trades ≥ 30을 넘긴 것은 단 1개(SLV, 85거래)다. 나머지는 대부분 한 자릿수다.

| 심볼 | Sharpe | Trades | MDD |
|------|--------|--------|-----|
| XLP | 6.43 | **2** | -1.8% |
| GLD | 4.55 | **24** | -3.8% |
| XLI | 4.35 | **3** | -1.8% |
| SLV | 3.87 | 85 | **-17.5%** |
| DIA | 3.45 | **6** | -1.6% |

Sharpe가 높은 심볼들이 trades가 너무 적다. XLP의 Sharpe 6.43은 2번 거래한 결과다 — 통계적으로 신뢰할 수 없다. SLV는 85번 거래해서 통계적으로 유의미하지만 MDD -17.5%가 -12% 한계를 초과한다.

왜 거래 횟수가 이렇게 적을까. 전략의 진입 조건을 보면 이해된다.

```python
# 60분봉 기준
peak = d60["close"].rolling(dd_window=120).max()
dd = d60["close"] / peak - 1.0
vol = d60["close"].pct_change().rolling(vol_window=60).std()

dd_rank = percentile_rank(dd.abs(), 120)   # 120봉 이내 낙폭 순위
vol_rank = percentile_rank(vol, 120)       # 120봉 이내 변동성 순위

bear_prob = 0.65 * dd_rank + 0.35 * vol_rank
long_cond = bear_prob <= 0.28   # 매우 낮은 낙폭 + 낮은 변동성
short_cond = bear_prob >= 0.72  # 매우 높은 낙폭 + 높은 변동성
```

`bear_enter=0.72` — 전체 120봉 중 낙폭+변동성 복합 점수가 상위 28% 이내여야 숏을, 하위 28%여야 롱을 낸다. 60분봉으로 OOS 기간(약 9개월)에 120봉 lookback이면 기준이 매우 까다롭다. 극단적인 조건에서만 발화하도록 설계돼 있어서 60분봉으로는 거래가 거의 안 나온다.

15분봉 데이터로 바꾸거나 `bear_enter` 임계를 낮추면 다른 결과가 나올 수 있지만, 현재 파라미터로는 실전 투입 기준을 통과하지 못했다.

## 유형 3 — 희귀 발화 조건 + 극소 수익

### `support_resistance_trendlines_strategy`

OOS 양수 비율 31.2%, 평균 Sharpe -1.224로 Global Cut됐다. Global Cut 기준은 `positive_ratio < 0.25 AND mean_sharpe < 0.0`인데, 31.2%는 25%를 넘어서 기술적으로 Global Cut은 피했다. 하지만 per-pair gate에서 전부 탈락했다.

```python
slope = _rolling_slope(d["close"], slope_window=40)

near_sup = d["close"] <= (sup * 1.003)   # 80봉 최저점의 0.3% 이내
near_res = d["close"] >= (res * 0.997)   # 80봉 최고점의 0.3% 이내

# 지지선 근처에서 상승 추세
long_cond = ((near_sup & (slope > 0)) | (d["close"] > res))
# 저항선 근처에서 하락 추세
short_cond = ((near_res & (slope < 0)) | (d["close"] < sup))
```

`near_sup & slope > 0` 조건은 가격이 80봉 저점의 0.3% 이내에 있으면서 동시에 40봉 선형 기울기가 양수인 경우다. 두 조건이 겹치는 경우는 매우 드물다. 가격이 저점에 있으면 기울기가 음수인 경우가 많기 때문이다.

실제로 Sharpe가 가장 높았던 심볼들의 거래 수를 보면:

| 심볼 | Sharpe | Trades | OOS return |
|------|--------|--------|-----------|
| SLV | 2.49 | **6** | +0.29% |
| XLB | 1.79 | **5** | +0.03% |
| ARKK | 1.61 | **8** | +0.14% |
| QQQ | 0.84 | 31 | +0.12% |

대부분 한 자릿수 거래에 OOS return이 0.1~0.3% 수준이다. 전략이 발화해도 실질적인 수익이 너무 작다. `(near_sup & slope > 0)` 조건이 너무 희귀하게 충족되고, `close > res` (채널 돌파)를 대안으로 넣었지만 이 경우도 드물다.

## 세 가지 실패 유형 정리

| 전략 | 탈락 단계 | 실패 원인 |
|------|-----------|----------|
| `support_resistance_channels` | Global Cut (mean_sharpe=0.0) | `close > rolling_high_max` 항상 False — 구현 버그 |
| `bear_market_probability_model` | Per-pair Gate (trades) | 60분봉 기준 발화 빈도 극히 낮음, 통계 신뢰 부족 |
| `support_resistance_trendlines_strategy` | Per-pair Gate (score 부족) | 희귀 발화 + 극소 수익, OOS return 합산 미미 |

실험에서 탈락한 전략이라도 버그를 수정하거나 타임프레임을 조정하면 다른 결과가 나올 수 있다. `support_resistance_channels`는 `shift(1)` 한 줄 수정으로 논리를 바로잡을 수 있다. `bear_market_probability_model`은 15분봉으로 내리거나 `bear_enter` 임계를 완화하면 거래 횟수 문제를 해결할 여지가 있다.
