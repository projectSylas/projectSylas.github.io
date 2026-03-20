---
layout: post
title: 라이브 트레이딩 봇 레짐 분류 강화와 포지션별 손절 추가
subtitle: trend_threshold 0.45→0.55, trend_max_down 도입, high_vol 조기 감지, 그리고 진입가 추적 기반 5% 손절까지
author: HyeongJin
date: 2026-03-21 10:00:00 +0900
categories: AI/LLM
tags: [Python, Trading, Regime, StopLoss, Alpaca]
sidebar: []
published: true
---

자동매매 봇을 실제로 돌리다 보면 백테스트에서 잘 보이지 않던 문제들이 라이브에서 드러난다.

최근에 두 가지를 고쳤다. 하나는 레짐 체인 분류가 너무 쉽게 "trend" 체인으로 들어가는 문제였고, 다른 하나는 체인 수준의 손실 한도는 있는데 개별 포지션 단위 손절이 없다는 문제였다.

## 레짐 체인 구조

봇은 Transformer 모델이 예측한 레짐 확률 `(p_up, p_dn, p_chop, p_hv)`를 보고 4가지 체인 중 하나를 선택한다. 체인마다 활성 전략과 exposure_multiplier가 다르다.

```
defensive  → exposure 0.40  (방어적 전략만, 노출 낮춤)
chop       → exposure 0.40  (횡보 대응 전략만)
neutral    → exposure 0.65  (대부분 전략 허용)
trend      → exposure 0.90  (모멘텀 전략 풀가동)
```

우선순위는 `defensive → chop → trend → neutral` 순이다. 어디에도 해당 안 되면 neutral로 떨어진다.

판정 로직은 간단하다.

```python
def decide_chain(regime, selected_strategies, chain_cfg, ...):
    p_up   = regime.get("p_trend_up", 1/3)
    p_dn   = regime.get("p_trend_down", 1/3)
    p_chop = regime.get("p_chop", 1/3)
    p_hv   = regime.get("p_high_vol", 0.5)

    hv_th      = chain_cfg.get("high_vol_threshold", 0.65)
    chop_th    = chain_cfg.get("chop_threshold", 0.50)
    trend_th   = chain_cfg.get("trend_threshold", 0.45)
    trend_max_down = chain_cfg.get("trend_max_down", 1.0)

    if p_hv >= hv_th and p_dn >= p_up:   # 고변동 + 하락 우세
        name = "defensive"
    elif p_dn > p_up and p_dn >= 0.45:   # 완만한 하락도 방어
        name = "defensive"
    elif p_chop >= chop_th:
        name = "chop"
    elif p_up >= trend_th and p_dn < trend_max_down:
        name = "trend"
    else:
        name = "neutral"
```

## 이번에 바꾼 것

### 1. trend_threshold 0.45 → 0.55

기존에는 p_up이 0.45만 넘으면 trend 체인으로 들어갔다. 1/3이 균등 확률이고, 0.45는 균등 대비 겨우 35% 높은 수준이다. 상승 신호가 약한데도 모멘텀 전략을 풀가동하는 경우가 생겼다.

0.55로 올렸다. 상승 확률이 55% 이상 나와야 trend 체인으로 진입한다. 비교적 확실한 신호일 때만 노출을 높이겠다는 판단이다.

### 2. trend_max_down 추가 (0.40)

기존엔 p_up이 threshold만 넘으면 p_dn이 얼마든 trend로 들어갔다. 예컨대 `p_up=0.56, p_dn=0.38`이면 상승 우세처럼 보이지만 하락 확률도 꽤 높은 불안정한 구간이다.

```python
elif p_up >= trend_th and p_dn < trend_max_down:  # p_dn이 0.40 미만일 때만 trend
    name = "trend"
```

`trend_max_down=0.40`을 추가해서, p_dn이 0.40 이상이면 p_up이 충분해도 trend로 진입하지 않는다. neutral로 떨어지게 된다.

### 3. 방어 조건 확장

기존 defensive 진입 조건은 "고변동 AND 하락 우세"였다. VIX 급등 같은 명확한 이벤트가 없는 완만한 하락은 감지하지 못했다.

```python
# 기존
if p_hv >= hv_th and p_dn >= p_up:
    name = "defensive"

# 추가
elif p_dn > p_up and p_dn >= 0.45:
    name = "defensive"
```

고변동이 아니더라도 하락 확률이 우세하고 0.45 이상이면 defensive로 간다. 느린 하락장에서 노출을 줄이지 못하는 문제를 잡기 위해 추가했다.

### 4. high_vol_threshold 0.65 → 0.40

고변동 감지 임계값을 낮췄다. 0.65면 모델이 "이건 확실히 고변동"이라고 말할 때만 반응했는데, 시장이 흔들리기 시작하는 초입을 놓치는 경우가 있었다. 0.40으로 낮춰서 변동성이 올라오는 조짐이 보이면 일찍 defensive로 전환하도록 했다.

### 5. neutral exposure_multiplier 0.45 → 0.65

defensive나 trend가 아닌 "중간 상태"에서 exposure를 너무 낮게 잡고 있었다. 중립적인 시장에서 기회를 너무 많이 놓치고 있어서 0.65로 올렸다.

---

현재 런타임 설정값이다.

```json
{
    "high_vol_threshold": 0.40,
    "chop_threshold":     0.50,
    "trend_threshold":    0.55,
    "trend_max_down":     0.40,
    "hysteresis":         0.07
}
```

**hysteresis**는 체인 전환이 너무 자주 일어나는 것을 막는 장치다. 현재 체인에 머물러 있으면 threshold를 0.07 낮춰서 판정한다. trend 체인에 있으면 p_up이 0.48(= 0.55 - 0.07)까지 내려와도 trend를 유지한다.

## 포지션별 손절 추가

체인 수준의 손실 한도(daily -2%, weekly -5%)는 있었는데 개별 포지션 단위 손절이 없었다. 레짐 분류가 늦게 반응하는 동안 한 종목이 크게 빠지는 경우를 대비해서 추가했다.

### 진입가 추적

주문 체결 시점에 진입가를 state에 기록한다.

```python
_entry_prices = state.get("entry_prices", {})

if abs(_prev_pos) < POSITION_EPS and r.side == "buy":
    _entry_prices[sym] = float(prices.get(sym, 0.0))   # 신규 롱 진입
elif abs(_prev_pos) < POSITION_EPS and r.side == "sell":
    _entry_prices[sym] = float(prices.get(sym, 0.0))   # 신규 숏 진입
elif r.side == "sell" and _prev_pos > 0:
    _entry_prices.pop(sym, None)                        # 롱 청산
elif r.side == "buy" and _prev_pos < 0:
    _entry_prices.pop(sym, None)                        # 숏 청산

state["entry_prices"] = _entry_prices
```

포지션이 0에서 새로 진입할 때만 기록하고, 청산 시 삭제한다. state 파일에 저장되기 때문에 봇이 재시작돼도 진입가가 유지된다.

### 손절 판정

매 루프마다 타겟 포지션을 확정하기 전에 체크한다.

```python
_stop_loss_pct = float(runtime.get("position_stop_loss_pct", 0.0))  # 0.05 (5%)

if _stop_loss_pct > 0:
    for _sym in list(target_positions.keys()):
        _cur_w = float(current_positions.get(_sym, 0.0))
        if abs(_cur_w) < POSITION_EPS:
            continue

        _px_now   = float(prices.get(_sym, 0.0))
        _px_entry = float(_entry_prices.get(_sym, 0.0))
        if _px_entry <= 0 or _px_now <= 0:
            continue

        if _cur_w > 0:
            _pnl_pct = (_px_now / _px_entry) - 1.0      # 롱: 현재가 / 진입가 - 1
        else:
            _pnl_pct = 1.0 - (_px_now / _px_entry)      # 숏: 1 - 현재가 / 진입가

        if _pnl_pct <= -_stop_loss_pct:
            target_positions[_sym] = 0.0                 # 강제 청산
```

손실이 -5% 이하면 target_position을 0으로 만든다. 이후 리스크 오버레이와 주문 생성 로직을 그대로 통과하면서 청산 주문이 나간다. 별도 청산 경로를 만들지 않고 기존 주문 파이프라인을 재활용했다.

롱/숏 모두 처리한다. 숏 포지션의 손실은 `1 - (현재가/진입가)`로 계산한다. 현재가가 진입가보다 높을수록 손실이 커지는 구조다.

## 결론

레짐 분류 하나를 바꾸는 것도 단순히 숫자 하나를 올리는 게 아니다. threshold를 올리면 trend 진입이 줄어들고 중립 구간이 늘어난다. 그 상태에서 exposure가 너무 낮으면 기회를 놓치니까 neutral exposure도 함께 올렸다. 방어 조건을 넓히면서 hysteresis도 확인해야 했다. 변경이 연쇄적으로 이어진다.

포지션 손절은 구조 자체는 단순한데, 진입가 추적을 state에 영속화한 부분이 핵심이다. 봇이 재시작되거나 장중에 예외가 나도 진입가를 잃어버리지 않아야 한다.
