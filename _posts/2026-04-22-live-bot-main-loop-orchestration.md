---
layout: post
title: 라이브 봇 메인 루프 — 레짐에서 주문까지 60초 사이클 전체 흐름
subtitle: 데이터 수집→레짐 예측→체인 결정→Allowlist 필터→전략 선택→Churn Guard→포지션 정책→주문 실행까지
author: HyeongJin
date: 2026-04-22 10:00:00 +0900
categories: AI/LLM
tags: [Python, Trading, LiveTrading, Architecture, RiskManagement]
sidebar: []
published: true
---

지금까지 레짐 분류기, 전략 라우터, 리스크 오버레이, 백테스트 엔진, 실행 레이어를 각각 다뤘다. `run_live_bot.py`는 이 컴포넌트들을 60초마다 하나의 사이클로 묶는 메인 루프다. 각 사이클에서 무슨 일이 일어나는지 순서대로 정리한다.

## 사이클 개요

```
[매 60초]
 1. 데이터 로드 (Alpaca / Yahoo / FRED)
 2. 데이터 freshness 체크 (stale / outside_rth / missing)
 3. 레짐 피처 빌드 → 레짐 예측
 4. 체인 결정 (defensive / chop / trend / neutral)
 5. Allowlist 필터 (허용 전략만 통과)
 6. 활성 전략 신호 계산
 7. 전략 선택 (weighted / single_best)
 8. 포지션 타겟 계산
 9. Churn Guard 적용
10. Position Policy 결정
11. 주문 생성 → open order 필터 → Alpaca 실행
12. RiskManager 상태 업데이트
13. State JSON 저장 / CSV 로그 기록
```

## 1. 데이터 freshness 체크

데이터를 받아온 뒤 즉시 freshness를 확인한다.

```python
freshness = assess_data_freshness(
    now=now,
    last_15m=last_15,
    last_60m=last_60,
    stale_15m_bars=2,
    stale_60m_bars=1,
    trade_regular_only=trade_regular_only,
)
```

상태는 네 가지다.

| 상태 | 의미 | 처리 |
|------|------|------|
| `active` | 정상 | 신호 계산 진행 |
| `outside_rth` | 정규장 외 시간 | 주문 0건, 관측만 |
| `stale` | 마지막 봉이 너무 오래됨 | 심볼 스킵 |
| `missing` | 데이터 없음 | 심볼 스킵 |

심볼 과반수가 `outside_rth`이면 해당 사이클의 `market_phase="outside_rth"`로 기록한다.

## 2. 레짐 예측 → 체인 결정

```python
feats = build_us_regime_features(bundle.df_15m, vix_daily=vix, adr_15m=bundle.adr_15m)
regime, regime_meta = predict_regime_with_meta(feats, market="US", symbol=symbol, regime_cfg=regime_cfg)
rp = regime.iloc[-1].to_dict()  # {p_trend_up, p_trend_down, p_chop, p_high_vol}

decision = decide_chain(rp, selected, chain_cfg, symbol=symbol, ...)
```

`regime_cfg.mode`는 세 가지다.

- `heuristic` — 규칙 기반 레짐 (Transformer 없음)
- `ml` — Transformer 모델 추론
- `shadow` — ml로 추론하되 실행엔 heuristic 결과 사용 (Shadow Mode 검증용)

체인 결정 결과인 `decision.active_strategies`가 그 사이클에서 실행 가능한 전략 목록이 된다.

## 3. Allowlist 필터

체인이 활성화한 전략 중 Allowlist에 없는 것을 제거한다.

```python
if allowlist is not None:
    active_for_symbol = [s for s in active_for_symbol if allowlist.is_allowed(symbol, s)]
    if before > 0 and not active_for_symbol:
        disabled_symbols.add(symbol)
```

Allowlist는 `(symbol, strategy)` 쌍 단위로 관리한다. 특정 심볼에서 특정 전략만 허용하거나 막을 수 있다. 예를 들어 OOS 검증을 통과하지 못한 조합은 Allowlist에서 `enabled=0`으로 처리한다.

## 4. 전략 선택 — weighted vs single_best

활성화된 전략이 여러 개일 때 두 가지 모드로 처리한다.

### weighted 모드

모든 전략의 신호를 Softmax 가중치로 합산한다.

```python
w = route_weights(rp, strategy_scores, router_cfg)
tgt = sum(w[s] * strategy_targets[s] for s in w)
```

### single_best 모드

레짐·차트 피트 점수가 가장 높은 전략 하나만 선택한다.

```python
best = max(pool, key=lambda s: adjusted_scores[s])
```

전략을 바꾸는 것(switch)을 억제하는 세 가지 조건이 있다.

```python
switch_edge_buffer = 0.15      # 현재 전략 대비 0.15 이상 앞서야 전환
cooldown_minutes = 120.0       # 마지막 전환 후 120분 동안 전환 불가
switch_only_when_flat = True   # 포지션이 있으면 전환 불가
```

이 조건들이 동시에 충족돼야 전략이 교체된다. 잦은 전략 교체는 실행 비용과 슬리피지를 유발하기 때문에 보수적으로 관리한다.

## 5. Churn Guard

포지션 수준의 과매매를 억제한다. 전략 선택과 별개 레이어다.

```python
churn_cfg = {
    "enabled": True,
    "min_target_abs": 0.03,          # 3% 미만 목표는 0으로 처리
    "rebalance_deadband": 0.02,      # 현재 포지션과 2% 이내 차이면 유지
    "side_change_cooldown_minutes": 60,  # 롱↔숏 전환 쿨다운
    "min_hold_minutes": 60,          # 체결 후 최소 보유 시간
}
```

세 가지를 막는다.

1. **deadband** — 현재 포지션과 목표 차이가 `rebalance_deadband` 이내면 현재 포지션 유지
2. **side cooldown** — 이전에 롱이었다가 숏으로 전환 요청이 오면 60분 기다림
3. **min hold** — 실제 체결이 일어난 뒤 60분 이내에 반대 방향 요청이 오면 무시

```python
if abs(tgt - cur) < rebalance_deadband:
    tgt = cur
    deadband_clamped += 1
```

Churn Guard는 outside_rth 구간에도 상태를 유지한다. 장 종료 직후 빈 `churn_targets`로 상태가 초기화되는 버그가 있었는데, 현재 사이클 심볼 외에도 이전에 추적된 심볼 상태를 보존하도록 수정했다.

## 6. Position Policy

Allowlist에서 빠진 심볼, Cutover로 생긴 이전 세대 포지션 등을 어떻게 처리할지 결정한다.

```python
def _resolve_position_policy(*, symbol, current_position, selected_strategy,
                               enabled_symbols, owner_map, generation_map,
                               selection_generation, flat_position_abs):
    if sym not in enabled_symbols:
        return "unwind_only", "disabled_symbol"

    if generation == selection_generation and owner:
        if owner == selected:
            return "normal", ""
        return "legacy_hold", "owner_mismatch"

    return "legacy_hold", "pre_cutover_position"
```

| 정책 | 의미 | 동작 |
|------|------|------|
| `normal` | 정상 거래 | target_position 그대로 적용 |
| `legacy_hold` | 이전 세대 포지션 | 목표가 현재보다 크면 현재 유지, 작으면 축소 |
| `unwind_only` | allowlist 제외 심볼 | 목표를 0으로 강제, 청산만 |

Cutover 시 새 세대가 시작되면 이전 세대에서 진입한 포지션은 `legacy_hold`로 잡힌다. 신규 매수는 막고 기존 포지션은 전략 신호에 따라 자연 청산된다.

## 7. 주문 실행 파이프라인

```python
orders = generate_orders(
    target_positions, current_positions,
    prices, account_equity,
    min_delta=order_min_delta,
)
open_orders = fetch_open_orders(mode)
orders, skipped = filter_orders_against_open(orders, open_orders)
results = execute_orders_alpaca(orders, mode=args.mode)
```

`filter_orders_against_open`이 이미 주문이 걸려 있는 심볼을 제거한다. 같은 심볼에 두 건이 중복으로 들어가는 걸 방지한다.

## 8. 로그 구조

매 사이클마다 두 개의 CSV에 기록한다.

**`live_log.csv`** — 사이클 단위 요약

주요 컬럼: `time, mode, action, equity, chain_name, active_strategies, regime_source, regime_confidence, allowlist_hit_rate, disabled_symbol_count, churn_blocked_side_cooldown, position_policy_by_symbol, market_phase`

**`live_log_events.csv`** — 주문 단위 이벤트

주요 컬럼: `time, symbol, side, qty, success, status_code, error_type, latency_ms, order_id, chain_name`

`_json_dumps_safe`가 NaN, Inf 같은 JSON 직렬화 불가능 값을 None으로 치환한다. numpy scalar를 Python 기본형으로 변환하는 `hasattr(v, "item")` 분기도 포함한다.

## 전체 컴포넌트 의존 관계

```
run_live_bot
  ├── data.pipeline          (데이터 로드)
  ├── features.engineering   (레짐 피처)
  ├── regime.interface       (레짐 예측)
  ├── router.chain           (체인 결정)
  ├── router.chart_selector  (차트 적합도)
  ├── router.interface       (가중치 배분)
  ├── risk.overlay           (Kill Switch, freshness)
  ├── execution.interface    (Alpaca 주문)
  └── reporting.metrics      (성능 집계)
```

각 컴포넌트를 독립적으로 테스트하고 교체할 수 있는 구조다. `regime_cfg.mode`를 바꾸면 레짐 소스만 전환되고, `selector_cfg.mode`를 바꾸면 전략 선택 방식만 달라진다. 메인 루프는 이 설정들을 런타임 JSON에서 읽어 반영한다.
