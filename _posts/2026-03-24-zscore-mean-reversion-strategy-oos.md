---
layout: post
title: Z-Score 평균회귀 전략 설계와 OOS 검증
subtitle: bb_rsi_reversion 실패 원인 분석 → 60m z-score + 거래량 필터 + 레짐 게이트로 재설계, Train/Test 분리 검증까지
author: HyeongJin
date: 2026-03-24 10:00:00 +0900
categories: AI/LLM
tags: [Python, Trading, Strategy, Backtesting, Statistics]
sidebar: []
published: true
---

기존에 운용하던 `bb_rsi_reversion` 전략이 계속 손실을 냈다. 백테스트에서는 괜찮았는데 라이브에서 실패하는 패턴이었다. 원인을 분석하고 z-score 기반으로 전략을 재설계했다.

## bb_rsi_reversion 실패 원인

세 가지가 문제였다.

**1. 타임프레임이 너무 짧았다 (15m)**
15분봉은 노이즈가 많다. 볼린저밴드 하단에 닿았다고 반등이 오는 게 아닌 경우가 많았다.

**2. 레짐 필터가 없었다**
추세장에서도 평균회귀를 시도했다. 강한 하락 추세에서 "저가에 진입"하면 계속 물린다. 평균회귀는 횡보·방어 구간에서만 작동해야 한다.

**3. BB 중선 청산**
볼린저밴드 중선(20일 이동평균)에서 청산하다 보니 포지션을 너무 오래 들고 있었다. 과매매가 되는 구간에서 보유 기간이 길어지면 손실이 커진다.

## 재설계: zscore_mean_reversion

### z-score 기반 진입

볼린저밴드 대신 z-score를 쓴다. 의미는 비슷하지만 표준화 방식이 다르다.

```
z = (close - mean(close, lookback)) / std(close, lookback)
```

z-score가 -1.5 이하면 "과도하게 하락", +1.5 이상이면 "과도하게 상승"으로 본다. 볼린저밴드는 고정 2σ 기준인데, z-score는 파라미터(entry_z)로 임계값을 조정할 수 있다.

### 거래량 필터 (vol_ratio_cap)

거래량이 급증할 때는 진입하지 않는다. 거래량 폭발은 추세 돌파 신호인 경우가 많기 때문이다.

```python
vol_sma_short = vol.rolling(vol_short, min_periods=1).mean()   # 10봉 SMA
vol_sma_long  = vol.rolling(lookback, min_periods=1).mean()    # 50봉 SMA
vol_ratio = vol_sma_short / vol_sma_long                       # 단기/장기 비율
```

`vol_ratio < 1.2` 일 때만 진입한다. 단기 거래량이 장기 평균의 1.2배를 넘으면 건너뛴다.

### 레짐 게이트

추세장(trend 확률이 높을 때)에는 진입 자체를 막는다.

```python
regime_trend = float(regime_prob.get("trend", 0.5))
regime_ok = regime_trend < regime_max  # regime_max = 0.55

long_entry  = zs < -entry_z and vol_ok and regime_ok
short_entry = zs > +entry_z and vol_ok and regime_ok and allow_short
```

Transformer 레짐 모델이 "추세 확률 55% 이상"을 내놓으면 신호를 무시한다. 평균회귀가 추세장에서 역방향 배팅이 되는 걸 차단하기 위한 조건이다.

### 조기 청산 (exit_z = 0.3)

z-score가 0으로 완전히 회귀하기 전에 -0.3 수준에서 청산한다.

```python
long_exit  = zs > -exit_z   # 롱 청산: z가 -0.3 이상으로 올라오면
short_exit = zs < +exit_z   # 숏 청산: z가 +0.3 이하로 내려오면
```

완전 회귀(z=0)까지 기다리면 보유 기간이 길어지고 다음 진입 기회를 놓친다. 70% 정도 회귀한 시점에서 정리하는 방식이다.

### ATR 기반 사이징 + 손절

```python
v_atr  = atr(d, 14)
atr_pct = v_atr / d["close"]
size   = (0.01 / atr_pct).clip(0.1, 1.0)   # 1% risk 기준 포지션 크기

sl_long  = close - 2.0 * v_atr
sl_short = close + 2.0 * v_atr
```

변동성이 높은 종목일수록 포지션을 작게 잡는다. 손절은 ATR 2배. TP는 따로 없다 — z-score 청산 조건이 그 역할을 한다.

### 타임프레임: 60m

15m에서 60m으로 올렸다. 60분봉으로 계산한 z-score가 1.5σ까지 벌어지는 건 의미 있는 이탈이다. 15m 노이즈에 반응하지 않는다.

```python
d = ensure_ohlcv(get_df_60m(inp))
# ...
# 60m 신호 → 15m 인덱스로 forward-fill
frame60.reindex(inp["df_15m"].index, method="ffill")
```

백테스트 엔진이 15m 기준으로 돌아가기 때문에 마지막에 reindex로 변환한다.

## OOS 검증

Train/Test를 고정 구간으로 나눠서 검증했다.

```
Train: 2024-01-01 ~ 2025-06-30
Test:  2025-07-01 ~ 2026-03-03
```

파라미터는 Train 구간에서 Optuna로 찾고, Test 구간 결과로 실제 성능을 판단했다. OOS에서 Sharpe가 양수인 종목만 선정했다.

결과 일부:

| Symbol | Train Sharpe | OOS Sharpe | OOS MDD | OOS 수익률 | 통과 |
|--------|-------------|-----------|---------|----------|------|
| ETR    | 1.27        | 2.04      | -2.5%   | +13.1%   | ✅   |
| GLD    | 1.73        | 0.66      | -4.9%   | +4.3%    | ✅   |
| XBI    | -           | 양수      | -       | -        | ✅   |
| XLI    | -           | 양수      | -       | -        | ✅   |
| ALL    | 1.76        | -0.23     | -11.7%  | -2.3%    | ❌   |
| TRV    | 1.20        | -0.61     | -12.4%  | -4.9%    | ❌   |

Train Sharpe가 1.5 이상이어도 OOS에서 음수가 나오는 경우가 많다. `ALL`이 대표적이다. Train에서만 잘 맞춘 과최적화다. `ETR`은 Train보다 OOS Sharpe가 오히려 높다 — 전략이 구조적으로 작동한다는 신호다.

OOS에서 음수가 나온 종목(`ALL`, `TRV`, `T`, `BAC`, `JPM`, `ABBV` 등)은 allowlist에서 제외했다.

## 배포 체인

trend 체인에는 배포하지 않았다. 추세장에서 평균회귀는 맞지 않는다. defensive, chop, neutral 체인에만 추가했다.

```json
"defensive": {
    "strategies": ["golden_triangle_1h_setup1_2", "zscore_mean_reversion", ...]
},
"chop": {
    "strategies": ["zscore_mean_reversion", ...]
},
"neutral": {
    "strategies": ["golden_triangle_1h_setup1_2", "zscore_mean_reversion", ...]
},
"trend": {
    "strategies": ["roc_dual_momentum", "pmax_explorer", ...]
    // zscore_mean_reversion 없음
}
```

레짐 게이트를 코드에서 한 번, 체인 배포로 한 번 — 이중으로 막는다.

## VIX guard와의 조합

같은 봇에 VIX guard도 돌아가고 있다. VIX가 20을 넘으면 신규 롱 진입 자체를 차단한다.

```python
_vix_latest      = float(vix.iloc[-1])
_vix_block_above = float(runtime.get("vix_entry_block_above", 20.0))

if _vix_latest > _vix_block_above:
    for sym in list(target_positions.keys()):
        cur = float(current_positions.get(sym, 0.0))
        tgt = float(target_positions[sym])
        if abs(cur) < POSITION_EPS and tgt > 0:   # 현재 포지션 없고 + 신규 롱이면
            del target_positions[sym]              # 제거
```

기존 포지션은 건드리지 않는다. 이미 들고 있는 건 VIX 게이트 대상이 아니다. 신규 진입만 막는다. z-score 전략이 아무리 강한 신호를 내도, VIX가 20 이상이면 실행되지 않는다.

레짐 체인 → 레짐 게이트(코드 내) → VIX guard 순서로 필터가 세 겹으로 쌓여 있다.

## 결론

평균회귀 전략이 라이브에서 실패하는 가장 흔한 이유는 추세장에서도 돌리기 때문이다. 레짐 필터를 빼면 백테스트 성과가 좋아 보이지만 실제로는 위험하다. Train/Test 분리 검증에서 OOS 음수가 나오는 종목을 솎아내는 것도 중요하다 — Train Sharpe가 높다고 OOS에서 같은 성과를 기대하면 안 된다.
