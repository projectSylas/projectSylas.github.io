---
layout: post
title: 백테스트 엔진 내부 — 바 단위 루프, Stop/TP 처리, 비용 모델
subtitle: latency_bars로 체결 지연 시뮬레이션, candle high/low Stop-TP 판정, turnover 기반 수수료·슬리피지 반영
author: HyeongJin
date: 2026-04-18 10:00:00 +0900
categories: AI/LLM
tags: [Python, Trading, Backtesting, QuantFinance]
sidebar: []
published: true
---

Walk-Forward나 Optuna 최적화 포스트에서 `run_backtest()`를 블랙박스처럼 썼는데, 그 안에서 실제로 무슨 일이 일어나는지 정리한다. 핵심은 세 가지다.

1. 신호 지연 — 현재 바의 신호가 다음 바에 체결되는 구조
2. Stop / Take-Profit — 바 내부 high/low로 터치 여부 판단
3. 비용 모델 — 포지션 변화량(turnover)에 수수료·슬리피지 부과

## 설정 — `BacktestConfig`

```python
@dataclass(frozen=True)
class BacktestConfig:
    fee_bps_side: float = 2.0       # 편도 수수료 (bps)
    slippage_bps_side: float = 1.0  # 편도 슬리피지 (bps)
    latency_bars: int = 1           # 신호→체결 지연 바 수
    allow_short: bool = True
    initial_equity: float = 1.0
```

`fee_bps_side=2.0`은 편도 기준 2bp. 왕복으로 4bp가 발생한다. `slippage_bps_side`는 market impact + spread 추정값이다. 두 값을 합산해 `cost_rate`로 쓴다.

```python
cost_rate = (bt.fee_bps_side + bt.slippage_bps_side) / 10_000.0  # = 0.0003
```

`latency_bars=1`이 기본값이다. 전략이 15분봉 종가를 보고 신호를 냈다면 다음 바 시가에 체결된다고 가정한다.

## 신호 정규화 — `_prepare_signals`

전략이 반환하는 DataFrame이 항상 컬럼을 다 갖고 있지는 않다.

```python
def _prepare_signals(raw: pd.DataFrame, index: pd.Index, allow_short: bool) -> pd.DataFrame:
    sig = raw.reindex(index).copy()
    for col, default in [("signal", 0), ("size", 0.0), ("sl", np.nan), ("tp", np.nan)]:
        if col not in sig:
            sig[col] = default

    sig["signal"] = sig["signal"].fillna(0).astype(int).clip(-1, 1)
    sig["size"] = sig["size"].fillna(0.0).astype(float).clip(0.0, 1.0)

    target = sig["signal"] * sig["size"]
    if not allow_short:
        target = target.clip(lower=0.0)
    sig["target"] = target
    return sig
```

`signal`은 방향(-1/0/1), `size`는 투입 비율(0~1). 곱하면 실제 포지션 크기가 된다. `allow_short=False`이면 음수 포지션을 0으로 잘라낸다.

## 바 루프 — 신호 지연과 비용 계산

```python
desired = sig["target"].shift(int(bt.latency_bars)).fillna(0.0)
ret = d15["close"].pct_change().fillna(0.0)
```

`shift(1)`로 신호를 한 바 뒤로 민다. 인덱스 i의 포지션은 인덱스 i-1의 신호로 결정된다. 이 한 줄이 look-ahead bias를 차단한다.

루프는 단순하다.

```python
for i, ts in enumerate(d15.index):
    row = d15.iloc[i]
    target = float(desired.iloc[i])

    # Stop/TP 체크
    if pos != 0:
        ...

    turnover = abs(target - pos)
    cost = turnover * cost_rate
    bar_ret = pos * float(ret.iloc[i]) - cost
    eq = eq * (1.0 + bar_ret)

    pos = target
    equity.append(eq)
```

매 바마다 `pos × 수익률 - 비용`이 그 바의 손익이다. 포지션이 0이거나 변동이 없으면 비용이 0이다.

## Stop-Loss / Take-Profit 처리

```python
if pos != 0:
    sl = float(sig["sl"].iloc[i - 1]) if pd.notna(...) else np.nan
    tp = float(sig["tp"].iloc[i - 1]) if pd.notna(...) else np.nan
    hit = False
    if pos > 0:
        if not np.isnan(sl) and float(row["low"]) <= sl:
            target = 0.0; hit = True
        if not np.isnan(tp) and float(row["high"]) >= tp:
            target = 0.0; hit = True
    else:
        if not np.isnan(sl) and float(row["high"]) >= sl:
            target = 0.0; hit = True
        if not np.isnan(tp) and float(row["low"]) <= tp:
            target = 0.0; hit = True
    if hit:
        sig.at[ts, "stop_tp_exit"] = 1
```

stop/tp 수준은 이전 바(`i-1`)의 컬럼에서 읽는다. 현재 바 candle의 `low`가 long stop 이하이거나 `high`가 long TP 이상이면 해당 바에서 청산이 일어난 것으로 처리한다. 캔들 내부 정확한 체결가는 모르지만, 실제 라이브 결과와 대체로 일치한다.

stop/tp가 hit되면 `target=0.0`으로 강제 설정되고 기존 루프의 turnover 계산으로 이어진다. 별도 분기 없이 청산 비용도 자동으로 반영된다.

## 포지션 역전 처리

롱→숏 전환은 한 바에 처리하지 않는다.

```python
elif pos != 0.0 and np.sign(target) != np.sign(pos):
    # 역전 시 진입가·시간 갱신
    entry_time = ts
    entry_price = exit_price
```

역전이 감지되면 현재 포지션을 청산 기록에 남기고 즉시 새 포지션을 진입으로 기록한다. 수량 계산에서 `flatten_on_reversal=True`를 쓰면 실행 레이어에서는 이걸 두 루프로 나누어 처리하지만, 백테스트에서는 같은 바에서 처리해 보수적인 비용이 두 번 나온다.

## 거래 기록

포지션이 0→0이 아닌 매매가 있을 때마다 `trade_rows`에 누적한다.

```python
{
    "entry_time": entry_time,
    "exit_time": ts,
    "entry_price": entry_price,
    "exit_price": exit_price,
    "side": 1 또는 -1,
    "return": float(trade_ret),
}
```

`trade_ret`은 가격 변동분에서 왕복 비용(`2.0 * cost_rate`)을 빼서 계산한다.

```python
trade_ret = side * (exit_price / entry_price - 1.0) - 2.0 * cost_rate
```

이 DataFrame이 `metrics.py`의 `profit_factor()`, `win_rate` 계산 입력이 된다.

## 최종 반환

```python
return BacktestResult(
    equity=equity_s,
    returns=returns_s,
    positions=positions_s,
    signals=sig,
    trades=trades,
    summary=summarize_backtest(equity_s, returns_s, trades),
)
```

`summary`에는 OOS 수익률, MDD, Sharpe, Profit Factor, 승률이 들어간다. Walk-Forward 폴드 루프는 이 결과를 받아 스코어로 변환하고 파라미터 선택에 쓴다.

## 설계 의도

백테스트 엔진이 이 정도로 간단한 이유가 있다.

- `latency_bars=1` 하나로 look-ahead bias 전부 차단
- candle high/low stop 판정으로 봉 내 터치 시뮬레이션
- turnover 기반 비용으로 잦은 매매 전략에 실제적 페널티
- `flatten_on_reversal`은 실행 레이어와 동일한 롤오버 로직

복잡한 order book 시뮬레이션은 없다. 슬리피지를 고정값으로 치환한 대신 파라미터 최적화 단계에서 보수적인 비용을 가정해 라이브 결과와의 갭을 줄였다.
