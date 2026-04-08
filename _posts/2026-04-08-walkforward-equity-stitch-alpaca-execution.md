---
layout: post
title: Walk-Forward 폴드 equity 스티칭과 Alpaca 주문 실행 레이어
subtitle: 폴드별 equity를 연결하는 scaled stitch, 포지션 역전 분리, 오류 분류 재시도까지
author: HyeongJin
date: 2026-04-08 10:00:00 +0900
categories: AI/LLM
tags: [Python, Trading, Backtesting, Alpaca, LiveTrading]
sidebar: []
published: true
---

Walk-Forward 백테스트를 만들면서 두 가지가 까다로웠다.

1. 폴드별로 나온 equity curve를 하나로 이어 붙이는 방법
2. 백테스트 포지션 비율을 실제 주문 수량으로 변환하고, Alpaca API 오류를 분류해서 재시도하는 방법

## Walk-Forward 구조

폴드 하나는 train 구간과 test 구간으로 구성된다.

```
| ← train (252일) → | ← test (63일) → |
                    | ← train (252일) → | ← test (63일) → |
                                        ...
```

`step_days`마다 윈도우를 앞으로 밀면서 폴드를 만든다.

```python
train_bars = wf_cfg.train_days * wf_cfg.bars_per_day   # 252 * 26 = 6552봉
test_bars  = wf_cfg.test_days  * wf_cfg.bars_per_day   # 63  * 26
step_bars  = wf_cfg.step_days  * wf_cfg.bars_per_day

i = 0
while i + train_bars + test_bars <= len(idx):
    tr_15 = df_15m.iloc[i : i + train_bars]
    te_15 = df_15m.iloc[i + train_bars : i + train_bars + test_bars]
    ...
    i += step_bars
```

train 구간에서 파라미터 후보를 모두 돌려서 `selection_score`가 가장 높은 파라미터를 고른다. 그 파라미터로만 test 구간을 백테스트한다. test 데이터는 최적화에 관여하지 않는다.

```python
best_params = strategy.default_params
best_train_score = -1e18

for params in param_candidates:
    p = dict(strategy.default_params)
    p.update(params)
    train_res = run_backtest(tr_15, tr_60, strategy, p, ...)
    sc = selection_score(train_res.summary)
    if sc > best_train_score:
        best_train_score = sc
        best_params = p

test_res = run_backtest(te_15, te_60, strategy, best_params, ...)
```

## Equity Stitch

폴드마다 equity가 1.0에서 시작한다. 이걸 하나의 연속 곡선으로 만들어야 누적 수익률이 제대로 보인다.

단순 concat은 안 된다. 폴드 경계에서 equity가 1.0으로 리셋되면 곡선이 끊긴다.

`_stitch_scaled`는 이전 폴드의 마지막 값을 다음 폴드의 시작 스케일로 쓴다.

```python
def _stitch_scaled(series_list: list[pd.Series]) -> pd.Series:
    out_parts: list[pd.Series] = []
    scale = 1.0
    for i, s in enumerate(series_list):
        ss = s.astype(float) * scale
        if i > 0 and not ss.empty:
            ss = ss.iloc[1:]        # 경계 첫 봉 중복 제거
        out_parts.append(ss)
        if not s.empty:
            scale *= float(s.iloc[-1])   # 다음 폴드 스케일 업데이트
    return pd.concat(out_parts).sort_index()
```

예를 들어 폴드 1이 1.0 → 1.08로 끝나면 `scale = 1.08`. 폴드 2의 equity(1.0 시작)에 1.08을 곱해서 이어 붙인다. 폴드 2가 1.0 → 0.95로 끝나면 다음 스케일은 `1.08 * 0.95 = 1.026`.

경계 첫 봉을 `iloc[1:]`로 자르는 이유는 직전 폴드의 마지막 봉과 현재 폴드의 첫 봉이 같은 타임스탬프일 수 있기 때문이다.

## 포지션 → 주문 변환

백테스트 포지션은 비율이다 (예: SPY 30%). 실제 주문은 수량이다 (예: SPY 12주 매수).

```python
def generate_orders(
    target_positions: dict[str, float],   # 목표 비율
    current_positions: dict[str, float],  # 현재 비율
    prices: dict[str, float],
    account_equity: float = 100_000.0,
    min_delta: float = 0.005,             # 0.5% 미만 변화는 무시
    position_multiplier: float = 1.0,
    flatten_on_reversal: bool = True,
) -> list[OrderRequest]:
```

delta가 min_delta 미만이면 주문을 내지 않는다. 0.5% 변화에 매번 주문을 내면 수수료만 나간다.

```python
delta = tgt - cur
if abs(delta) < min_delta:
    continue

notional = abs(delta) * account_equity * position_multiplier
qty = int(math.floor(notional / px))
if qty <= 0:
    continue

side = "buy" if delta > 0 else "sell"
```

### 포지션 역전 처리

롱 → 숏 전환(또는 반대)은 한 루프에서 처리하면 안 된다. 기존 포지션을 청산하는 매도 주문과 숏을 여는 매도 주문이 충돌해서 실제 수량이 맞지 않을 수 있다.

```python
if flatten_on_reversal and cur != 0.0 and tgt != 0.0:
    cur_sign = 1 if cur > 0 else -1
    tgt_sign = 1 if tgt > 0 else -1
    if cur_sign != tgt_sign:
        tgt = 0.0    # 이번 루프는 청산만
```

역전이 감지되면 목표를 0으로 바꿔서 청산 주문만 낸다. 다음 루프에서 현재 포지션이 0에 가깝게 됐을 때 목표 방향으로 진입한다.

## Alpaca 주문 실행

```python
def execute_orders_alpaca(
    orders: list[OrderRequest],
    mode: Literal["paper", "live"],
    retries: int = 2,
) -> list[OrderResult]:
    url = _base_url(mode) + "/v2/orders"
    headers = _alpaca_headers()

    for order in orders:
        for attempt in range(retries + 1):
            t0 = time.perf_counter()
            try:
                r = requests.post(url, headers=headers, json=asdict(order), timeout=10)
                latency_ms = (time.perf_counter() - t0) * 1000.0
                if 200 <= r.status_code < 300:
                    # 성공
                    break
                last_error = f"status={r.status_code}"
            except Exception as e:
                last_error = str(e)

            if attempt < retries:
                time.sleep(0.5 * (attempt + 1))   # 0.5s, 1.0s 백오프
```

각 주문에 latency_ms를 기록한다. 이 값을 `RiskManager.record_order_result`에 넘겨서 API 응답이 느려지는 추세를 잡을 수 있다.

## 오류 분류

주문 실패의 원인을 분류해서 kill switch 조건에 넘긴다.

```python
def _classify_order_error(status_code: int | None, error: str | None) -> str:
    msg = (error or "").lower()

    # Alpaca 403은 인증 외에 수량 부족도 반환 — 메시지로 구분
    if "insufficient qty available" in msg:
        return "insufficient_qty"
    if "insufficient buying power" in msg:
        return "insufficient_buying_power"

    if status_code == 401:
        return "auth_error"
    if status_code == 403:
        return "auth_error"
    if status_code in {400, 404, 409, 422}:
        return "request_error"
    if status_code is not None and status_code >= 500:
        return "server_error"
    if "timed out" in msg or "timeout" in msg or "connection" in msg:
        return "network_error"
    return "unknown_error"
```

Alpaca는 403을 인증 오류와 수량 부족 양쪽에 쓴다. status code만 보면 kill switch가 잘못 발동된다. 메시지 내용으로 먼저 분기한다.

`auth_error`는 `RiskManager`가 즉시 kill switch를 걸고 자동 복구를 허용하지 않는다. `insufficient_qty` / `server_error` / `network_error`는 일시적 오류로 분류돼서 재시도 후 실패율이 낮아지면 자동 복구된다.

## 오픈 주문 필터

직전 루프에서 낸 주문이 아직 체결되지 않았는데 같은 심볼에 새 주문을 내면 중복이 된다.

```python
def filter_orders_against_open(
    orders: list[OrderRequest],
    open_orders: list[dict],
) -> tuple[list[OrderRequest], int]:
    blocked_symbols = {
        str(o.get("symbol", "")).upper()
        for o in open_orders if o.get("symbol")
    }
    out = [o for o in orders if o.symbol.upper() not in blocked_symbols]
    skipped = len(orders) - len(out)
    return out, skipped
```

주문 실행 직전에 `fetch_open_orders`로 미체결 목록을 가져와서 겹치는 심볼은 건너뛴다.

## 전체 실행 루프

```
봉 수신
    ↓
전략 신호 생성
    ↓
apply_exposure_caps(target)        ← 비율 캡
    ↓
generate_orders(target, current, prices, equity)  ← 수량 계산
    ↓
fetch_open_orders()
filter_orders_against_open(orders, open_orders)   ← 중복 제거
    ↓
can_trade() 확인                   ← kill switch
    ↓
execute_orders_alpaca(orders, mode)
    ↓
record_order_result(success, status_code, error_type)
    ↓
maybe_auto_recover()
```

백테스트는 비율로 동작하고 실행 레이어가 수량으로 변환한다. 이 경계를 명확히 나누면 전략 코드에서 브로커 API를 신경 쓰지 않아도 된다.
