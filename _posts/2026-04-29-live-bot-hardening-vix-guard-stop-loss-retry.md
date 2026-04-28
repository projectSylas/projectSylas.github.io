---
layout: post
title: 라이브 봇 강화 — 레짐 임계 재조정·VIX 롱 차단·포지션 손절·403 재시도 폭풍 억제
subtitle: trend_threshold 0.45→0.55, p_dn≥0.45 defensive 조기 전환, entry_prices 추적 5% 손절, 403 per-symbol cooldown 3 poll
author: HyeongJin
date: 2026-04-29 10:00:00 +0900
categories: AI/LLM
tags: [Python, Trading, LiveTrading, RiskManagement, Architecture]
sidebar: []
published: true
---

paper trading을 실제로 돌려보면 백테스트 설계 단계에서 보이지 않던 문제가 나온다. 레짐 체인이 너무 늦게 defensive로 전환하거나, VIX 급등 시 롱 진입이 막히지 않거나, Alpaca API가 403을 반환할 때 같은 심볼로 주문이 계속 재시도되는 문제들이다. 이 포스트는 실전 가동 중 발생한 네 가지 문제를 수정한 커밋들을 순서대로 정리한다.

## 1. 403 재시도 폭풍 — per-symbol 주문 실패 쿨다운

Alpaca에서 403 + `insufficient_qty` (또는 `held_for_orders`) 에러가 나면, 다음 60초 사이클에서 같은 심볼로 또 주문이 들어간다. 포지션이 이미 청산 중이거나 매수 주문이 pending인데 새 주문을 보내면 같은 에러가 반복되면서 `errors` 카운트가 쌓인다.

```python
# Per-symbol order failure cooldown
_order_fail_until: dict[str, datetime] = {}
_ORDER_FAIL_COOLDOWN_POLLS = 3
```

루프 시작 시 만료된 항목을 정리하고, 현재 주문 목록에서 쿨다운 중인 심볼을 제거한다.

```python
# 쿨다운 만료 항목 제거
_order_fail_until = {k: v for k, v in _order_fail_until.items() if v > now}

# 쿨다운 중인 심볼 주문 건너뜀
orders = [o for o in orders if o.symbol.upper() not in _order_fail_until]
```

주문 결과 처리 시 403이 나오면 해당 심볼을 3 poll 동안 잠근다.

```python
if r.error_type in {"insufficient_qty", "auth_error"} and r.status_code == 403:
    _order_fail_until[sym] = now + timedelta(seconds=args.poll_seconds * _ORDER_FAIL_COOLDOWN_POLLS)
```

`poll_seconds=60`이면 3 poll = 180초 동안 해당 심볼로 주문을 보내지 않는다. 이 시간 안에 기존 주문이 체결되거나 취소되면 자연스럽게 다음 사이클에서 정상 처리된다.

이와 함께 chain config도 수정됐다. `golden_triangle`이 `neutral`과 `chop` 체인에 들어가 있었는데, 이 전략은 추세장 전용 설계라 횡보·혼조 구간에서 성과가 나쁘다. 해당 체인에서 제거했다.

```json
// 수정 전 — chop 체인에 golden_triangle 포함
"chop": {
  "strategies": ["golden_triangle_1h_setup1_2", "bear_market_probability_model", ...]
}

// 수정 후 — chop에서 제거
"chop": {
  "strategies": ["bear_market_probability_model", "support_resistance_trendlines_strategy"]
}
```

## 2. VIX guard — VIX > 20 신규 롱 차단

VIX가 급등하는 구간에서 신규 롱 진입을 차단한다.

```python
_vix_latest = float(vix.iloc[-1]) if vix is not None and not vix.empty else 0.0
_vix_block_above = float(runtime.get("vix_entry_block_above", 20.0))

if _vix_latest > _vix_block_above:
    for _sym in list(target_positions.keys()):
        _cur = float(current_positions.get(_sym, 0.0))
        _tgt = float(target_positions[_sym])
        if abs(_cur) < float(selector_cfg.get("flat_position_abs", 0.003)) and _tgt > 0:
            del target_positions[_sym]
```

핵심은 두 조건이 모두 충족될 때만 삭제한다는 것이다.

- `abs(_cur) < flat_position_abs` — 현재 포지션이 없어야 함 (flat 상태)
- `_tgt > 0` — 신규 롱 진입이어야 함

기존에 들고 있는 포지션은 건드리지 않는다. VIX가 폭등해도 이미 진입한 포지션을 강제청산하면 오히려 저점에서 손절하는 꼴이 될 수 있기 때문이다. 신규 진입만 막고 기존 포지션은 전략 신호에 따라 자연 청산되도록 둔다.

`vix_entry_block_above`는 `runtime.json`에서 읽는다. 초기 구현에서 `cfg.get(...)`으로 잘못 참조해 NameError가 발생했다.

```python
# 버그 — cfg는 argparse Namespace, .get() 없음
_vix_block_above = float(cfg.get("vix_entry_block_above", 20.0))

# 수정 — runtime은 dict
_vix_block_above = float(runtime.get("vix_entry_block_above", 20.0))
```

`cfg`는 `argparse.Namespace`라 `.get()` 메서드가 없다. `runtime`은 `_load_json_if_exists()`로 로드한 `dict`다.

## 3. 레짐 임계 재조정 — defensive 조기 전환

레짐 체인은 `p_trend_up`, `p_trend_down`, `p_chop`, `p_high_vol` 네 가지 확률값으로 체인을 결정한다. 초기 임계값이 너무 낙관적으로 설정돼 있어 하락장에서 defensive로 전환이 늦었다.

### 변경 전후 비교

| 파라미터 | 수정 전 | 수정 후 | 의미 |
|----------|---------|---------|------|
| `trend_threshold` | 0.45 | 0.55 | trend 체인 진입 기준 강화 |
| `trend_max_down` | — (없음) | 0.40 | p_dn이 높으면 trend 차단 |
| `high_vol_threshold` | 0.65 | 0.40 | 변동성 감지 더 예민하게 |
| `hysteresis` | 0.07 | 0.07 | 변경 없음 |

`trend_threshold`를 0.45에서 0.55로 올렸다. 이전에는 상승 확률이 45%만 돼도 trend 체인으로 분류됐다 — 사실상 약간의 우세만 있어도 trend가 됐다. 0.55로 올리면 "뚜렷한 상승 우세"가 있어야만 trend 전략이 돌아간다.

`trend_max_down=0.40`은 신규 추가된 조건이다. 상승 확률이 아무리 높아도 하락 확률이 40%를 넘으면 trend 체인을 차단한다.

```python
# 수정 전 — p_up만 보면 trend
elif p_up >= trend_th:
    name = "trend"

# 수정 후 — p_up 높아도 p_dn이 크면 trend 아님
elif p_up >= trend_th and p_dn < trend_max_down:
    name = "trend"
```

`high_vol_threshold`는 0.65에서 0.40으로 낮췄다. 이전에는 `p_high_vol >= 0.65`여야 defensive로 분류됐는데, 변동성이 실제로 문제가 되는 시점은 `p_high_vol >= 0.40` 수준에서 이미 나타난다.

### defensive 조기 전환 조건 추가

기존 defensive 전환은 `p_high_vol >= hv_th AND p_dn >= p_up` 두 조건이 모두 필요했다. 고변동성 없이 천천히 하락하는 구간은 잡지 못했다.

```python
# 수정 전 — 고변동성 필수
if p_hv >= hv_th and p_dn >= p_up:
    name = "defensive"

# 수정 후 — 하락 지배만으로도 defensive
if p_hv >= hv_th and p_dn >= p_up:
    name = "defensive"
elif p_dn > p_up and p_dn >= 0.45:   # 추가 조건
    name = "defensive"
```

`p_dn > p_up and p_dn >= 0.45` — 하락 확률이 상승 확률을 넘으면서 절댓값도 45% 이상이면 VIX 급등 없이도 defensive로 전환한다. 완만한 하락 추세를 더 일찍 잡기 위한 조건이다.

같은 커밋에서 neutral 체인의 `exposure_multiplier`는 0.45에서 0.65로 올렸다. 하락 쪽 조건이 강화됐으니 neutral은 진짜 방향성이 불분명한 구간에만 남는다 — 그 구간에서 노출을 조금 늘려도 된다는 판단이다.

## 4. 포지션 손절 — entry_prices 추적 + 5% 손절

전략 신호가 청산 신호를 내도 시장이 빠르게 움직이면 손실이 커질 수 있다. 전략 신호와 무관하게 진입 가격 대비 -5% 이상 손실이 나면 강제 청산한다.

### entry_prices 추적

주문이 체결될 때 진입 가격을 state에 기록한다.

```python
_entry_prices = state.get("entry_prices", {}) if isinstance(state.get("entry_prices"), dict) else {}
_prev_pos = float(current_positions.get(sym, 0.0))

if abs(_prev_pos) < POSITION_EPS and r.side == "buy":
    # 신규 롱 진입 — 현재 가격을 진입 가격으로 기록
    _entry_prices[sym] = float(prices.get(sym, 0.0))
elif abs(_prev_pos) < POSITION_EPS and r.side == "sell":
    # 신규 숏 진입 — 현재 가격을 진입 가격으로 기록
    _entry_prices[sym] = float(prices.get(sym, 0.0))
elif r.side == "sell" and _prev_pos > 0:
    # 롱 청산 — 진입 가격 삭제
    _entry_prices.pop(sym, None)
elif r.side == "buy" and _prev_pos < 0:
    # 숏 청산 — 진입 가격 삭제
    _entry_prices.pop(sym, None)

state["entry_prices"] = _entry_prices
```

신규 진입(이전 포지션이 flat)이면 기록하고, 반대 방향 주문으로 청산하면 삭제한다.

### 매 사이클 손절 평가

```python
_stop_loss_pct = float(runtime.get("position_stop_loss_pct", 0.0))
if _stop_loss_pct > 0:
    _entry_prices = state.get("entry_prices", {}) if isinstance(state.get("entry_prices"), dict) else {}
    for _sym in list(target_positions.keys()):
        _cur_w = float(current_positions.get(_sym, 0.0))
        if abs(_cur_w) < POSITION_EPS:
            continue  # 포지션 없으면 건너뜀

        _px_now = float(prices.get(_sym, 0.0))
        _px_entry = float(_entry_prices.get(_sym, 0.0))
        if _px_entry <= 0 or _px_now <= 0:
            continue  # 진입 가격 없으면 건너뜀

        if _cur_w > 0:
            _pnl_pct = (_px_now / _px_entry) - 1.0        # 롱 PnL
        else:
            _pnl_pct = 1.0 - (_px_now / _px_entry)        # 숏 PnL

        if _pnl_pct <= -_stop_loss_pct:
            target_positions[_sym] = 0.0                  # 강제 청산
```

`position_stop_loss_pct=0.05`면 진입 가격 대비 -5% 손실 시 해당 심볼의 target을 0으로 만든다. 다음 단계에서 Churn Guard와 주문 생성이 이 target=0을 처리해 청산 주문으로 이어진다.

`position_stop_loss_pct=0.0` (기본값)이면 손절 로직 자체가 실행되지 않는다. 런타임 설정으로 손절 비율을 조절할 수 있다.

## 변경 내용 요약

| 커밋 | 날짜 | 내용 |
|------|------|------|
| `97961b1` | 2026-03-17 | 403 재시도 폭풍 억제 (3 poll cooldown), chain config golden_triangle 정리 |
| `997b336` | 2026-03-17 | VIX guard: VIX > 20 신규 롱 차단 |
| `ec988ef` | 2026-03-18 | VIX guard NameError 수정 (`cfg` → `runtime`) |
| `cbbf9e2` | 2026-03-18 | 레짐 임계 강화, defensive 조기 전환, 포지션 손절 5% |

네 커밋이 이틀 안에 연속으로 나온 건 paper trading 가동 중 실시간으로 문제를 발견하고 수정했다는 뜻이다. 403 재시도 폭풍은 에러 로그에서, VIX 차단 미작동은 NameError 트레이스백에서, 레짐 전환 지연은 실제 chain 레이블 CSV를 보고 발견했다.

라이브 봇이 실제로 돌아가면 백테스트로는 잡히지 않는 운영 레이어 버그가 반드시 나온다. 코드가 아무리 잘 설계돼 있어도 실제 API 에러 처리, 설정 파일 key 오타, 임계값 보수성 판단은 운영하면서만 알 수 있다.
