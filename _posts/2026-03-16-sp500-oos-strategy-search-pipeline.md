---
layout: post
title: S&P500 444종목 전략 탐색 파이프라인
subtitle: Train/Test 분리 + 파라미터 그리드 서치로 OOS 검증된 종목만 allowlist 편입 — 과최적화 없이 실거래 가능한 조합 선별
author: HyeongJin
date: 2026-03-16 10:00:00 +0900
categories: AI/LLM
tags: [Python, Trading, Backtesting, Statistics, Strategy]
sidebar: []
published: true
---

라이브 트레이딩 allowlist를 ETF 14쌍에서 S&P500 전체로 확장하면서, 종목별로 전략과 파라미터 조합을 자동으로 선별하는 파이프라인을 만들었다. 핵심은 Train 기간에서 최적화하고 Test 기간에서 독립적으로 검증하는 OOS 분리 구조다.

## 왜 파이프라인이 필요했나

전략을 전체 기간 데이터로 최적화하면 과최적화가 된다. 백테스트 Sharpe가 2.0이어도 실제 라이브에서 음수가 나오는 경우가 많다. Train/Test 분리가 필수다.

444종목에 4개 전략을 각각 수십~수백 개 파라미터 조합으로 돌리면 수만 번의 백테스트가 필요하다. 수작업으로 할 수 없어서 `search_low_corr_strategies.py`로 자동화했다.

## 구조

```
Train: 2024-01-01 ~ 2025-06-30  (18개월)
Test:  2025-07-01 ~ 2026-03-03  (8개월)
```

Train에서 최적 파라미터를 찾고, Test 기간에서 그 파라미터로 독립 검증한다. Test 데이터는 최적화 과정에 전혀 관여하지 않는다.

## Gate 조건

두 구간 모두 아래 조건을 통과해야 allowlist 편입 후보가 된다.

```python
GATE = dict(sharpe=1.0, pf=1.2, mdd=-0.12, trades=20)

def gate_pass(s: dict) -> bool:
    return (s["sharpe"] >= GATE["sharpe"] and
            s["mdd"]    >= GATE["mdd"] and
            s["pf"]     >= GATE["pf"] and
            s["trades"] >= GATE["trades"])
```

- **Sharpe ≥ 1.0**: 리스크 조정 수익 기준
- **Profit Factor ≥ 1.2**: 수익 거래 합계 / 손실 거래 합계
- **MDD ≥ -12%**: 최대 낙폭 제한
- **거래 수 ≥ 20**: 통계적 유의미성 확보 (거래가 너무 적으면 운이 좋아서 나온 수치일 수 있다)

## 탐색 대상 전략과 파라미터 그리드

4개 전략에 대해 파라미터 조합을 정의했다.

```python
GRIDS = {
    "roc_dual_momentum": [
        {"fast_roc": fr, "slow_roc": sr, "smooth": sm, "threshold": thr}
        for fr  in [5, 10, 15]
        for sr  in [30, 40, 60]
        for sm  in [3, 5, 8]
        for thr in [0.0, 0.002]
        if fr < sr
    ],
    "zscore_mean_reversion": [
        {"lookback": lb, "entry_z": ez, "exit_z": xz, "vol_ratio_cap": vc, "vol_short": vs}
        for lb in [30, 50, 70]
        for ez in [1.2, 1.5, 2.0]
        for xz in [0.2, 0.3, 0.5]
        for vc in [1.0, 1.2, 1.5]
        for vs in [8, 10]
        # 3×3×3×3×2 = 162 조합
    ],
    # bb_rsi_reversion, elastic_student_t ...
}
```

`roc_dual_momentum`은 54개 조합, `zscore_mean_reversion`은 162개 조합. 종목 129개 × 조합 수 = 심볼당 수천 번의 백테스트가 돌아간다.

## Train 최적화 로직

심볼별로 파라미터 그리드를 전수 탐색해서 Train gate를 통과하는 조합 중 Sharpe 최고 파라미터를 선택한다.

```python
def best_params_on_train(bundles_train, strategy, param_grid):
    results = {}
    for sym, bundle in bundles_train.items():
        best_sharpe, best_p = -999, None
        for p in param_grid:
            r = run_backtest(bundle.df_15m, bundle.df_60m, strategy, p, symbol=sym, cfg=BT_CFG)
            s = summarize(r)
            if gate_pass(s) and s["sharpe"] > best_sharpe:
                best_sharpe, best_p = s["sharpe"], p
        if best_p is not None:
            results[sym] = (best_sharpe, best_p)
    return results
```

Train에서 Sharpe가 가장 높은 단일 파라미터만 추출한다. 여러 파라미터를 앙상블하지 않는다 — 단일 파라미터로 Test를 통과해야 과최적화가 아님을 확인할 수 있다.

## Test 검증

Train 최적 파라미터를 Test 기간에 그대로 적용해서 성능을 측정한다.

```python
for sym, (tr_sharpe, best_p) in best_train.items():
    r_te = run_backtest(bundle_te.df_15m, bundle_te.df_60m, strategy, best_p, symbol=sym, cfg=BT_CFG)
    s_te = summarize(r_te)
    both = gate_pass(s_te)
    print(f"{sym:6s} Tr={tr_sharpe:.2f} Te={s_te['sharpe']:.2f} MDD={s_te['mdd']*100:.1f}% {'✅' if both else '❌'}")
```

Test gate까지 통과한 조합만 `both_pass=True`로 표시된다.

## 결과

S&P500 개별주 + ETF 포함 약 444종목을 돌린 결과, **92개 종목**이 Test까지 통과해서 allowlist에 편입됐다.

전략별 양쪽 통과 현황:

| 전략 | Train 통과 | Test까지 통과 | 통과율 |
|------|-----------|-------------|-------|
| zscore_mean_reversion | ~160 | ~48 | 30% |
| roc_dual_momentum | ~120 | ~31 | 26% |
| elastic_student_t | ~95 | ~22 | 23% |
| bb_rsi_reversion | ~80 | ~15 | 19% |

Train 통과가 많아도 Test 통과율은 20~30%. 나머지 70%는 Train 기간에 과최적화된 것이다.

과최적화 패턴의 특징: Train Sharpe ≥ 2.0인데 Test Sharpe가 음수인 경우. 파라미터를 Train 데이터에 지나치게 맞춘 것이다.

## allowlist 편입 기준

Test 통과 조합 중 추가 필터를 적용했다.

1. **최소 거래 수 30개 이상**: gate의 20개보다 기준을 높였다
2. **레짐 분류 적합성**: trend 종목에는 모멘텀 전략, chop/defensive 종목에는 평균회귀 전략을 우선
3. **상관관계 확인**: 이미 allowlist에 있는 종목과 수익 곡선 상관관계가 0.7 이상이면 제외 (저상관 분산)

이렇게 선별된 92종목이 실거래 allowlist에 추가됐다. ETF 14쌍이었던 allowlist가 ETF + 개별주 124개 총합으로 확장됐다.

## 실행 방법

```bash
cd trading/
python cli/search_low_corr_strategies.py
# 전체 결과는 results_low_corr_search.csv로 저장
```

실행 시간은 전체 약 2~3시간. 결과는 CSV로 저장되고, 양쪽 통과 조합과 최적 파라미터가 함께 출력된다.

```
전략별 양쪽통과 수:
strategy
bb_rsi_reversion                                15
elastic_volume_weighted_student_t_tension       22
roc_dual_momentum                               31
zscore_mean_reversion                           48
```

이 출력에서 통과한 심볼별 파라미터를 allowlist 설정에 반영한다.
