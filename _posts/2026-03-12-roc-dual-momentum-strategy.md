---
layout: post
title: ROC 이중 모멘텀 전략 설계
subtitle: EMA 크로스오버 대신 가격 변화율 직접 비교 — 단기/장기 ROC 가속도 기반 진입 + 모멘텀 강도 사이징
author: HyeongJin
date: 2026-03-12 10:00:00 +0900
categories: AI/LLM
tags: [Python, Trading, Strategy, Backtesting, Statistics]
sidebar: []
published: true
---

`golden_triangle` 전략이 EMA 크로스오버 기반이라면, `roc_dual_momentum`은 가격 변화율(ROC)을 직접 비교한다. 이동평균의 후행성 없이 모멘텀의 방향과 가속도를 직접 잡는다.

## ROC 이중 모멘텀이란

**ROC(Rate of Change)**는 N봉 전 가격 대비 현재 가격의 변화율이다.

```
ROC(n) = (현재가 - n봉 전가) / n봉 전가
```

이중 모멘텀은 단기 ROC와 장기 ROC를 동시에 본다.

- `fast_roc`(10봉): 최근 모멘텀
- `slow_roc`(40봉): 중기 모멘텀

단기 ROC가 장기 ROC보다 크다는 건 모멘텀이 가속 중이라는 뜻이다. 반대면 감속.

```python
fast_roc = d60["close"].pct_change(fast_n).fillna(0.0)
slow_roc = d60["close"].pct_change(slow_n).fillna(0.0)

fast_sm = fast_roc.rolling(sm_n).mean().fillna(0.0)  # 5봉 스무딩
slow_sm = slow_roc.rolling(sm_n).mean().fillna(0.0)
```

`pct_change(n)`으로 ROC를 구하고, `rolling(sm_n).mean()`으로 5봉 스무딩한다. EMA가 아니라 SMA라서 계산이 단순하고 라그가 예측 가능하다.

## 진입 조건

```python
long_cond  = (fast_sm > slow_sm) & (fast_sm > thr)
short_cond = (fast_sm < slow_sm) & (fast_sm < -thr)
```

롱 조건 두 가지:
1. `fast_sm > slow_sm` — 단기 모멘텀이 장기보다 크다 (가속)
2. `fast_sm > threshold` — 절대 모멘텀이 양수다 (상승 방향)

숏은 반대. 두 조건이 동시에 만족될 때만 진입한다.

`threshold=0.0`이 기본값이다. 노이즈 필터가 필요하면 0.001 정도로 올린다.

## 모멘텀 강도 기반 사이징

포지션 크기를 고정으로 쓰지 않고 현재 모멘텀 강도에 비례해 조정한다.

```python
momentum_abs = fast_sm.abs()
roll_max = momentum_abs.rolling(60).max().replace(0.0, pd.NA)
size = (momentum_abs / roll_max).fillna(0.0).clip(0.1, 1.0)
```

최근 60봉 모멘텀 절댓값 최대치 대비 현재 모멘텀 비율로 사이즈를 결정한다. 모멘텀이 강할수록 크게 들어가고, 약하면 최소 0.1까지만 내려간다.

이렇게 하면 추세가 명확한 구간에서 자연스럽게 사이즈가 커지고, 횡보에서는 줄어든다.

## ATR 기반 SL/TP

```python
v_atr = atr(d60, 14)
sl = d60["close"] - 1.5 * v_atr          # 롱 손절
tp = d60["close"] + 2.5 * v_atr          # 롱 익절
short_sl = d60["close"] + 1.5 * v_atr    # 숏 손절
short_tp = d60["close"] - 2.5 * v_atr    # 숏 익절
```

손절 1.5×ATR, 익절 2.5×ATR. 리스크/리워드 비율 1:1.67.

zscore_mean_reversion과 다른 점은 TP가 있다는 것이다. 모멘텀 전략은 목표가에 도달하면 청산하고 재진입을 노린다. 평균회귀처럼 z-score 조건 청산을 쓰면 추세가 살아있는데 너무 일찍 빠져나오게 된다.

## 타임프레임

```python
d60 = ensure_ohlcv(get_df_60m(inp))
# ...
frame60.reindex(inp["df_15m"].index, method="ffill").fillna({"signal": 0, "size": 0.0})
```

60분봉으로 신호를 계산하고 15분봉 인덱스로 reindex한다. 엔진이 15m 기준으로 포지션을 관리하기 때문이다. `ffill`로 앞 신호를 유지하다가 다음 60m 봉에서 업데이트된다.

15m 대신 60m을 쓰는 이유는 ROC가 노이즈에 민감하기 때문이다. 15m 봉의 ROC는 뉴스 이벤트 한 번에 급등락해서 가짜 신호를 많이 낸다.

## golden_triangle과의 차이

| 항목 | golden_triangle | roc_dual_momentum |
|------|----------------|------------------|
| 진입 기준 | EMA 크로스오버 | ROC 가속도 비교 |
| 후행성 | 있음 (EMA 평활화) | 적음 (ROC 직접 계산) |
| 청산 | ATR TP 또는 EMA 반전 | ATR TP 고정 |
| 사이징 | 고정 or ATR 기반 | 모멘텀 강도 비례 |
| 적합 레짐 | 추세 + 중간 | 강한 추세 |

EMA 크로스오버는 평활화 덕분에 False signal이 적지만 진입이 늦다. ROC는 빠르지만 노이즈를 타면 whipsaw가 난다. 스무딩(5봉 SMA)과 60m 타임프레임으로 이를 억제한다.

## 레짐 체인 배포

trend 체인에 배포했다. zscore_mean_reversion과 반대로, 추세가 강한 구간에서 모멘텀을 따라가는 전략이기 때문이다.

```json
"trend": {
    "strategies": ["roc_dual_momentum", "pmax_explorer", ...]
}
```

defensive, chop 구간에서는 모멘텀이 없거나 방향이 자주 바뀌어서 whipsaw가 심해진다. trend 전용으로만 쓴다.

## OOS 결과 일부

`search_low_corr_strategies.py`로 S&P500 444종목에 대해 Train/Test 분리 검증을 했다. roc_dual_momentum은 모멘텀이 강한 섹터 ETF와 성장주에서 OOS Sharpe가 잘 나왔다.

- **QQQ**: Train 1.43 → OOS 1.18
- **XLK**: Train 1.61 → OOS 0.94
- **NVDA**: Train 2.1 → OOS 1.3 (변동성 큰 종목 — 사이징이 중요)

반면 유틸리티(XLU), 채권(TLT) 같은 저변동성 종목은 OOS에서 음수로 떨어졌다. 모멘텀이 약한 자산에는 맞지 않는 전략이다.
