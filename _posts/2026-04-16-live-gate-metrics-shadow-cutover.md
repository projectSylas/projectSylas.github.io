---
layout: post
title: 라이브 전환 게이트 — Metrics, LiveGate, Shadow Mode 검증 파이프라인
subtitle: Sharpe·MDD·Profit Factor 계산부터 LiveGate 통과 조건, Shadow Guard 자동 루프, Cutover 절차까지
author: HyeongJin
date: 2026-04-16 10:00:00 +0900
categories: AI/LLM
tags: [Python, Trading, LiveTrading, RiskManagement, DataEngineering]
sidebar: []
published: true
---

백테스트 성능이 좋다고 바로 라이브에 올리면 안 된다. 백테스트 과적합, 체결 슬리피지, 브로커 오류율, 레이턴시 같은 실운영 변수를 다 고려해야 한다. 직접 구현한 라이브 전환 파이프라인은 세 단계로 나뉜다.

1. `metrics.py` — 백테스트 지표 계산 및 selection score
2. `gates.py` — 실시간 LiveGate 통과 여부 판정
3. Shadow Guard Loop — 실제 라이브 환경에서 5영업일 자동 검증 후 Cutover

## 지표 계산 — `metrics.py`

### MDD (Max Drawdown)

```python
def max_drawdown(equity: pd.Series) -> float:
    if equity.empty:
        return 0.0
    peak = equity.cummax()
    dd = equity / peak - 1.0
    return float(dd.min())
```

cummax로 rolling peak를 구하고 현재 자본을 나누면 각 시점의 drawdown이 나온다. `dd.min()`은 음수 — 절대값이 클수록 낙폭이 크다.

### Sharpe Ratio

```python
def sharpe_ratio(returns: pd.Series, bars_per_year: int = 252 * 26) -> float:
    mu = float(returns.mean())
    sd = float(returns.std())
    if sd <= 0:
        return 0.0
    return (mu / sd) * math.sqrt(bars_per_year)
```

15분봉 기준으로 연환산한다. 1년에 252 거래일, 하루 26개 바 → `bars_per_year=6552`. 표준 일봉 기준 `sqrt(252)`와 동일한 논리다.

### Profit Factor

```python
def profit_factor(trade_returns: pd.Series) -> float:
    gross_profit = float(trade_returns[trade_returns > 0].sum())
    gross_loss = float(-trade_returns[trade_returns < 0].sum())
    if gross_loss <= 0:
        return 999.0 if gross_profit > 0 else 0.0
    return gross_profit / gross_loss
```

총 수익 ÷ 총 손실. 1.0 미만이면 손실이 이익을 초과한다. 손실 거래가 없으면 999를 반환해 정렬 기준으로 쓸 수 있게 한다.

### Selection Score

```python
def selection_score(summary: dict[str, float], instability_penalty: float = 0.0) -> float:
    sharpe = float(summary.get("sharpe", 0.0))
    pf = float(summary.get("profit_factor", 0.0))
    mdd = abs(float(summary.get("mdd", 0.0)))
    return sharpe + pf - 2.5 * mdd - float(instability_penalty)
```

단일 숫자로 전략을 줄 세울 때 쓴다. MDD에 2.5 페널티를 줘서 낙폭이 큰 전략을 적극적으로 걸러낸다. Walk-forward OOS 여러 윈도우에서 결과가 들쭉날쭉한 전략에는 `instability_penalty`를 추가로 부여한다.

`summarize_backtest`가 이 지표들을 한 번에 묶어준다.

```python
def summarize_backtest(equity, returns, trades) -> dict[str, float]:
    return {
        "oos_return": float(equity.iloc[-1] - 1.0),
        "mdd": max_drawdown(equity),
        "sharpe": sharpe_ratio(returns),
        "profit_factor": profit_factor(trades["return"]),
        "trades": float(len(trades)),
        "win_rate": float((trades["return"] > 0).mean()),
    }
```

## 라이브 게이트 — `gates.py`

백테스트 기준만으로는 부족하다. 라이브 봇이 실제로 돌기 시작한 후에도 최소 조건을 넘었는지 주기적으로 확인해야 한다.

```python
@dataclass(frozen=True)
class LiveGate:
    min_trading_days: int = 20        # 최소 20 거래일
    min_sharpe: float = 1.0           # Sharpe ≥ 1.0
    min_profit_factor: float = 1.2    # Profit Factor ≥ 1.2
    max_drawdown_abs: float = 0.12    # MDD ≤ 12%
    max_error_rate: float = 0.005     # 오더 오류율 ≤ 0.5%
    max_latency_p95_sec: float = 3.0  # p95 레이턴시 ≤ 3초
```

`evaluate_live_gate`는 summary dict를 받아 어느 조건이 실패했는지 리스트로 반환한다.

```python
def evaluate_live_gate(
    summary: dict[str, float],
    trading_days: int,
    gate: LiveGate | None = None,
) -> tuple[bool, list[str]]:
    g = gate or LiveGate()
    failed: list[str] = []

    if trading_days < g.min_trading_days:
        failed.append(f"trading_days<{g.min_trading_days}")
    if float(summary.get("sharpe", 0.0)) < g.min_sharpe:
        failed.append(f"sharpe<{g.min_sharpe}")
    if float(summary.get("profit_factor", 0.0)) < g.min_profit_factor:
        failed.append(f"profit_factor<{g.min_profit_factor}")
    if abs(float(summary.get("mdd", 0.0))) > g.max_drawdown_abs:
        failed.append(f"mdd>{g.max_drawdown_abs}")
    if float(summary.get("error_rate", 1.0)) >= g.max_error_rate:
        failed.append(f"error_rate>={g.max_error_rate}")
    if float(summary.get("latency_p95_sec", 999.0)) > g.max_latency_p95_sec:
        failed.append(f"latency_p95>{g.max_latency_p95_sec}")

    return len(failed) == 0, failed
```

반환값은 `(pass: bool, failed_reasons: list[str])`. 어느 조건이 미달인지 명시적으로 알 수 있어서 로그 분석이 쉽다.

```json
{
  "pass": false,
  "failed": ["trading_days<20", "sharpe<1.0"]
}
```

## Shadow Mode 검증

백테스트에서 LiveGate를 통과한 전략도 실제 라이브 환경에서 다시 검증한다. Shadow mode는 주문을 실제로 내지 않고 레짐 분류, 전략 선택, 오더 생성까지는 다 거치되 체결만 건너뛴다.

5 거래일 동안 아래 기준을 충족해야 Cutover로 진행한다.

| 지표 | 기준 |
|------|------|
| fallback ratio | < 5% |
| regime prediction missing | = 0 |
| order error rate | < 0.5% |
| kill switch activation | = 0 |

### Shadow Guard Loop

셸 스크립트 `run_shadow_guard_loop.sh`가 일정 주기로 shadow_guard 체크를 실행하고 결과를 두 곳에 기록한다.

- `shadow_status_latest.json` — 최신 상태 (대시보드가 읽음)
- `shadow_status_history.jsonl` — 히스토리 누적 (트렌드 분석용)

```json
// shadow_status_latest.json
{
  "pass": false,
  "failed": ["trading_days<5"],
  "summary": {
    "rows_total": 1295,
    "rows_shadow": 1295,
    "trading_days_observed": 1,
    "fallback_ratio": 0.0,
    "regime_missing_count": 0,
    "order_error_rate": 0.0,
    "kill_switch_triggered": false,
    "latest_regime_source": "none"
  }
}
```

초기에는 `trading_days_observed < 5`이라 당연히 실패한다. 5 거래일 후 나머지 지표가 기준을 모두 넘으면 Cutover 프로세스로 진입한다.

## Cutover 절차

Shadow 검증이 통과되면 아래 순서로 전환한다. 미국 정규장 종료 후 실행이 원칙이다.

1. 기존 프로세스 PID 확인 → kill
2. 기존 state/log를 `.bak`로 백업
3. 신규 세대 allowlist 대상 심볼 open order 전량 cancel
4. 신규 런처로 봇 기동
5. 대시보드 재기동 (신규 state/log 경로로 교체)
6. 첫 30분 집중 모니터링:
   - `selection_generation`이 state와 대시보드 일치
   - legacy 포지션에 `legacy_hold` / `unwind_only` 정책 적용
   - `outside_rth` 구간에서 주문 0건
   - 함께 돌아가는 다른 프로세스 생존 확인

롤백이 필요하면 신규 프로세스 중지 → open order cancel → 이전 런처 재기동으로 5분 내에 되돌릴 수 있다.

## 운영 검증 지표

Cutover 이후에도 운영 모니터링 기준은 유지된다.

| 지표 | 기준 |
|------|------|
| allowlist_hit_rate | 100% |
| 심볼당 active strategy | 최대 1개 |
| disabled_symbol 신규 진입 | 0건 |
| outside_rth 주문 | 0건 |
| 오더 오류율 | < 0.5% |
| kill switch | 미발동 |

## 정리

백테스트 성능 지표 계산(metrics) → 정적 기준 게이트(gates) → 라이브 환경 동적 검증(shadow guard) → Cutover의 3단 파이프라인으로 실운영 리스크를 단계적으로 줄인다. 어느 단계에서 실패해도 이유가 명시적인 문자열로 기록되기 때문에 디버깅이 빠르다.
