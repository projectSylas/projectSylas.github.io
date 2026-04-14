---
layout: post
title: 볼린저 밴드 + RSI 평균회귀 전략 — 상태머신 루프와 ATR 포지션 사이징
subtitle: BB 하단+RSI 과매도 진입, 중심선 청산, numpy 배열 상태머신, ATR 역수 사이징까지
author: HyeongJin
date: 2026-04-14 10:00:00 +0900
categories: AI/LLM
tags: [Python, Trading, Backtesting]
sidebar: []
published: true
---

평균회귀 전략은 추세 전략과 반대 방향 신호를 낸다. PMAX나 ROC Dual Momentum이 상승 돌파에 롱을 잡을 때, 볼린저 밴드 + RSI 전략은 BB 하단 이탈 구간에서 롱을 잡는다. 두 전략을 포트폴리오에 함께 넣으면 상관관계가 낮아져서 전체 변동성이 줄어든다.

진입 조건:
- 롱: `close < BB 하단` AND `RSI < 35` (과매도)
- 숏: `close > BB 상단` AND `RSI > 65` (과매수)

청산 조건:
- 롱 청산: `close >= BB 중심선` (EMA로 복귀)
- 숏 청산: `close <= BB 중심선`

목표가를 고정 수익률이 아니라 BB 중심선으로 잡은 이유는 "밴드 이탈 → 평균 복귀"라는 평균회귀 가정 자체가 목표를 자연스럽게 정의하기 때문이다.

## 볼린저 밴드 계산

```python
bb_p = int(p["bb_period"])   # 20
bb_s = float(p["bb_std"])    # 2.0

mid   = d["close"].rolling(bb_p).mean()
std   = d["close"].rolling(bb_p).std()
upper = mid + bb_s * std
lower = mid - bb_s * std
```

표준편차 2배 밴드는 정규분포 가정 하에 종가의 약 95%가 밴드 안에 들어온다. 밴드 밖으로 나가는 건 통계적으로 이례적이라는 가정이 평균회귀 전략의 근거다.

## 상태머신 루프

pandas 조건 연산으로 신호를 만들면 상태 추적이 안 된다. `close < lower` 조건이 True → False → True로 왔다 갔다 할 때 포지션을 그대로 유지해야 하는지 새로 진입해야 하는지 구분이 안 된다.

상태변수 `pos`를 유지하는 루프로 해결한다.

```python
sig_a = np.zeros(n, dtype=np.int8)
pos = 0
for i in range(n):
    if np.isnan(mid_a[i]):
        sig_a[i] = 0
        continue

    long_entry  = close_a[i] < lower_a[i] and rv_a[i] < rsi_os
    short_entry = close_a[i] > upper_a[i] and rv_a[i] > rsi_ob and allow
    long_exit   = close_a[i] >= mid_a[i]
    short_exit  = close_a[i] <= mid_a[i]

    if long_entry:
        pos = 1
    elif short_entry:
        pos = -1
    elif pos == 1 and long_exit:
        pos = 0
    elif pos == -1 and short_exit:
        pos = 0

    sig_a[i] = pos
```

진입 조건이 청산 조건보다 우선순위가 높다. 이미 롱인 상태에서 또 `long_entry`가 True여도 `pos = 1`로 덮어써서 중복 진입이 생기지 않는다.

**numpy 배열 전환 이유**: pandas Series는 요소 접근이 느리다. 루프가 수만 번 돌아가는 백테스트에서 `.iloc[i]`보다 C 배열 인덱싱이 수십 배 빠르다.

```python
close_a = d["close"].values   # numpy ndarray
mid_a   = mid.values
lower_a = lower.values
rv_a    = rv.values
```

`.values`로 numpy 배열을 꺼낸 뒤 루프 안에서는 파이썬 기본 타입으로만 접근한다. pandas 오버헤드가 없다.

## ATR 역수 사이징

포지션 크기를 변동성 역수로 계산한다.

```python
v_atr   = atr(d, 14)
atr_pct = (v_atr / d["close"].replace(0.0, pd.NA)).fillna(0.02)
size    = (0.01 / atr_pct).clip(0.1, 1.0)
```

`atr_pct`는 ATR을 현재가 대비 비율로 정규화한 값이다. `0.01 / atr_pct`는 "ATR이 1%일 때 전체 자본의 1%를 리스크로 잡으면 사이즈가 얼마냐"를 의미한다. 변동성이 크면(atr_pct가 크면) 사이즈가 줄어들고, 변동성이 작으면 사이즈가 늘어난다.

`fillna(0.02)`는 초기 봉에서 ATR이 계산 안 될 때 기본 변동성 2%를 가정한다.

PMAX 전략과 비교하면:

| 전략 | 사이징 기준 |
|------|------------|
| PMAX | PMAX 라인 이격 거리 / ATR 밴드 폭 |
| BB+RSI | 0.01 / (ATR / close) |

PMAX는 추세 강도로 사이즈를 키우고, BB+RSI는 변동성이 작을 때 사이즈를 키운다. 둘을 함께 포트폴리오에 넣으면 사이징 방향도 반대라 리스크가 상쇄된다.

## SL / TP

```python
sl       = d["close"] - 1.5 * v_atr   # 롱 손절
tp       = mid                          # 롱 목표 (BB 중심선)
short_sl = d["close"] + 1.5 * v_atr   # 숏 손절
short_tp = mid                          # 숏 목표 (BB 중심선)
```

손절은 진입가 기준 1.5 ATR. 평균회귀 전략에서 "진입 이후 반대 방향으로 1.5 ATR 이상 이탈"이면 평균회귀 가설이 깨진 것으로 본다.

목표는 BB 중심선(`mid`). 중심선 복귀가 이 전략이 기대하는 수익이다. 중심선을 넘어서 추세가 이어진다면 그 이후 수익은 포기한다.

## 전략 스타일과 체인 배치

라우터의 `chart_selector`에서 이 전략은 `chop` 스타일로 분류된다. 이름에 "bb_rsi"나 "reversion"이 없어도 `style_by_strategy` 맵에 명시적으로 등록한다.

```python
# chain config
"style_by_strategy": {
    "bb_rsi_reversion": "chop"
}
```

`chop` 스타일 전략의 적합도 점수는 횡보 점수(`p_chop`, `chop_score`)가 높고 추세 강도(`trend_strength`)가 낮을 때 올라간다. 추세장에서는 점수가 낮아져 가중치가 줄어들고, 체인이 `chop`으로 결정될 때 가중치가 최대가 된다. 전략이 잘 맞는 구간에 자동으로 집중되는 구조다.
