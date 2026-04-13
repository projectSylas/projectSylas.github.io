---
layout: post
title: PMAX 전략 구현 — EMA + ATR 래칫 트레일링 스톱
subtitle: 추세 전환 시 PMAX 라인 리셋, ATR 이격 기반 포지션 사이징, SL/TP 자동 계산까지
author: HyeongJin
date: 2026-04-13 10:00:00 +0900
categories: AI/LLM
tags: [Python, Trading, Backtesting]
sidebar: []
published: true
---

PMAX(Progressive Moving Average with ATR Trailing Stop)는 EMA 기반 이동평균선과 ATR 밴드를 결합한 추세 추종 전략이다. 핵심은 래칫(ratchet) 구조다. 상승 추세에서는 손절선이 위로만 올라가고 내려오지 않는다. 하락 추세에서는 반대로 아래로만 내려간다. 이 단방향 이동이 추세 추종과 손실 제한을 동시에 달성한다.

## 알고리즘

PMAX 라인은 두 밴드로 구성된다.

```python
def _pmax(close, ma, atr_v, mult):
    upper = ma + mult * atr_v
    lower = ma - mult * atr_v

    trend = np.ones(len(close), dtype=int)
    pmax  = np.full(len(close), np.nan, dtype=float)
```

`upper`는 저항선, `lower`는 지지선이다. EMA(`ma`)에서 `atr_mult * ATR` 만큼 위아래로 벌린다.

래칫 계산:

```python
    for i in range(1, len(close)):
        prev = pmax[i - 1]
        if np.isnan(prev):
            prev = float(lower.iloc[i - 1])

        # 1. 추세 방향 결정
        if close.iloc[i] > prev:
            trend[i] = 1
        elif close.iloc[i] < prev:
            trend[i] = -1
        else:
            trend[i] = trend[i - 1]

        # 2. PMAX 라인 계산 (래칫)
        if trend[i] > 0:
            pmax[i] = max(float(lower.iloc[i]),
                          prev if trend[i - 1] > 0 else float(lower.iloc[i]))
        else:
            pmax[i] = min(float(upper.iloc[i]),
                          prev if trend[i - 1] < 0 else float(upper.iloc[i]))
```

추세 결정은 현재 종가가 이전 PMAX 라인 위에 있으면 상승(1), 아래면 하락(-1)이다.

PMAX 라인 계산에서 래칫이 작동한다.

**상승 추세일 때:**
- 이전 봉도 상승이면: `max(lower[i], prev)` → 하한선과 이전 PMAX 중 높은 값. PMAX는 올라갈 수 있어도 내려오지 않는다.
- 추세가 이번 봉에 상승으로 전환됐으면: `lower[i]`로 리셋. 지지선에서 새로 시작.

**하락 추세일 때:**
- 이전 봉도 하락이면: `min(upper[i], prev)` → 상한선과 이전 PMAX 중 낮은 값. PMAX는 내려갈 수 있어도 올라오지 않는다.
- 추세가 이번 봉에 하락으로 전환됐으면: `upper[i]`로 리셋. 저항선에서 새로 시작.

추세 전환이 일어나는 순간 PMAX 라인이 반대 밴드로 점프한다. 그게 전환 신호이자 새 추세의 시작점이다.

## 신호 및 포지션 사이징

```python
def compute_signals(inp, params):
    p = with_default_params(params, DEFAULT_PARAMS)
    d = ensure_ohlcv(get_df_15m(inp))

    ma    = ema(d["close"], int(p["ma_len"]))   # EMA 20
    atr_v = atr(d, int(p["atr_len"]))           # ATR 10
    pmax_line, trend = _pmax(d["close"], ma, atr_v, float(p["atr_mult"]))

    sig = trend.astype(int)
    if not bool(p["allow_short"]):
        sig = sig.clip(lower=0)   # 롱 온리 모드
```

`trend`가 바로 신호다. +1이면 롱, -1이면 숏.

포지션 크기는 PMAX 라인과 종가의 이격 거리로 계산한다.

```python
    size = (
        (d["close"] - pmax_line).abs()
        / (atr_v.replace(0.0, np.nan) * float(p["atr_mult"]))
    ).fillna(0.0).clip(0.1, 1.0)
```

`(close - pmax_line) / (ATR × mult)`는 밴드 폭 대비 현재 이격 비율이다. 종가가 PMAX 라인에서 멀수록 추세가 강하고 신뢰도가 높다. 최솟값 0.1으로 클리핑해서 최소 포지션을 보장하고, 1.0으로 상한을 두어 과잉 진입을 막는다.

## SL / TP

```python
    sl = pmax_line
    tp = d["close"] + 2.0 * atr_v
    short_sl = pmax_line
    short_tp = d["close"] - 2.0 * atr_v

    return compact_output_frame(
        d.index, sig, size,
        sl.where(sig >= 0, short_sl),
        tp.where(sig >= 0, short_tp),
    )
```

손절은 PMAX 라인 자체다. 종가가 PMAX 라인을 반대로 돌파하면 추세 전환이므로 동시에 stop-loss 조건이 된다. 별도의 손절 계산이 필요 없다.

익절은 `close ± 2 × ATR`로 고정이다. ATR 기반이라 변동성이 클수록 익절 목표도 넓어진다. 숏의 경우 SL과 TP 방향이 반대이므로 `sig >= 0` 조건으로 분기한다.

## 유틸리티

`atr`은 EWM 방식으로 계산한다.

```python
def atr(df, period=14):
    prev_close = d["close"].shift(1)
    tr = pd.concat([
        d["high"] - d["low"],
        (d["high"] - prev_close).abs(),
        (d["low"]  - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1.0 / max(1, period), adjust=False).mean()
```

True Range의 세 값 중 최댓값을 EWM으로 평활화한다. `alpha = 1/period`는 Wilder 방식이다. 표준 EMA(`span=period`)와 다르게 수렴이 느려서 극단값 영향이 줄어든다.

`ema`는 pandas EWM이다.

```python
def ema(s, length):
    return s.ewm(span=max(1, length), adjust=False).mean()
```

## 파라미터 기본값

```python
DEFAULT_PARAMS = {
    "ma_len":   20,    # EMA 기간
    "atr_len":  10,    # ATR 기간
    "atr_mult": 3.0,   # 밴드 폭 배수
    "allow_short": True,
}
```

`atr_mult`가 클수록 밴드가 넓어져서 노이즈에 덜 민감하지만 손절 범위도 커진다. Walk-Forward Optuna 탐색에서 `atr_mult`는 2.0~5.0 범위로 탐색하고, 최적값은 심볼과 타임프레임마다 달라진다. 15분봉 SPY 기준으로 OOS 검증에서 `atr_mult=3.0`이 안정적이었다.

## 전략 인터페이스 연결

```python
def strategy_fn(inp: StrategyInput, params: dict) -> StrategyOutput:
    return last_output_from_frame(compute_signals(inp, params))
```

`compute_signals`는 전체 시리즈를 반환한다. 백테스트 엔진은 이 시리즈 전체를 쓰고, 라이브 추론은 `last_output_from_frame`으로 마지막 봉만 꺼낸다.

```python
def last_output_from_frame(frame):
    row = frame.iloc[-1]
    return {
        "signal": int(row.get("signal", 0)),
        "size":   float(row.get("size", 0.0)),
        "sl":     float(row["sl"]) if pd.notna(row.get("sl")) else None,
        "tp":     float(row["tp"]) if pd.notna(row.get("tp")) else None,
        "tags":   {},
    }
```

라이브 루프에서 15분봉 한 봉이 닫힐 때마다 `strategy_fn`을 호출해서 `signal`을 받는다. `signal`과 현재 포지션을 비교해서 주문이 필요하면 `generate_orders`로 넘긴다.

## 래칫의 실제 동작

추세가 강할수록 PMAX 라인은 종가에 바짝 따라붙는다. ATR이 줄어들면 밴드 폭이 좁아지고 PMAX 라인이 촘촘하게 상승하기 때문이다. 반대로 변동성이 크면 밴드가 넓어져서 PMAX 라인이 멀리서 추적한다. 변동성에 따라 자동으로 추적 감도가 바뀌는 점이 고정 비율 트레일링 스톱과의 차이다.

추세 전환 신호가 나오면 PMAX 라인이 반대 밴드로 즉시 점프하고 새 방향에서 래칫이 다시 시작된다. 포지션 역전 처리는 실행 레이어의 `flatten_on_reversal`이 맡는다.
