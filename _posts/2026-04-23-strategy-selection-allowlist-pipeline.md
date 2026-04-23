---
layout: post
title: 전략 선택 아티팩트 빌드 — 실험 랭킹에서 Allowlist까지 4단계 필터
subtitle: Frozen·Research-only·Global Cut·Per-pair Gate 통과한 14개 조합만 SHA256으로 버전 관리
author: HyeongJin
date: 2026-04-23 10:00:00 +0900
categories: AI/LLM
tags: [Python, Trading, Strategy, DataEngineering, LiveTrading]
sidebar: []
published: true
---

`run_experiments.py`가 14개 전략 × 32개 심볼에 대해 백테스트를 돌리고 `ranking_all.csv`를 만든다. 이 448행짜리 CSV를 그대로 라이브 봇에 넣을 수는 없다. 어떤 전략이 어느 심볼에서 실제로 쓰일지 결정하는 두 가지 스크립트가 있다.

- `build_strategy_selection.py` — 핵심 필터링 로직
- `refresh_nonvix_selection.py` — SHA256 버전 관리 + meta/report 생성

## 입력 데이터 구조

`ranking_all.csv`는 전략·심볼 조합별 백테스트 결과를 담는다.

```
컬럼: oos_return, sharpe, mdd, profit_factor,
      instability_penalty, score, trades, strategy, symbol
```

`score`는 `metrics.py`의 selection_score — Sharpe + Profit Factor - 2.5×MDD - instability_penalty. 이 값으로 심볼별 최종 전략을 결정한다.

## 4단계 필터링

### 1) Frozen 전략 제거

```python
FROZEN_VIX_STRATEGIES = {"vix_prob_hybrid"}
d_nonvix = d[~d["strategy"].isin(frozen)].copy()
```

VIX 기반 전략은 별도 프로세스에서 독립적으로 관리한다. Non-VIX Allowlist 빌드 시 항상 제외한다.

### 2) Research-only 전략 제거

```python
research_only = _research_only_strategies(d)
d_nonvix = d_nonvix[~d_nonvix["strategy"].isin(research_only)].copy()
```

registry에서 `research_only=True`로 표시된 전략은 라이브에 투입하지 않는다. `inwcoin_martingale_strategy`가 여기 해당한다.

### 3) Global Cut

```python
stat = d_nonvix.groupby("strategy").agg(
    positive_ratio=("oos_return", lambda x: float((x > 0).mean())),
    mean_sharpe=("sharpe", "mean"),
)
globally_cut = set(
    stat[
        (stat["positive_ratio"] < 0.25) & (stat["mean_sharpe"] < 0.0)
    ].index.tolist()
)
```

전략 단위로 판단한다. 32개 심볼 중 OOS 수익이 플러스인 비율이 25% 미만이면서 평균 Sharpe도 음수인 전략은 심볼별 Gate 검사도 없이 전량 제외한다.

이번 실험 결과 6개 전략이 Global Cut됐다.

| 전략 | Positive OOS 비율 | 평균 Sharpe |
|------|-----------------|------------|
| berlin_candles | 0.0% | -5.44 |
| errorfunctions_library | 6.2% | -3.88 |
| stepped_trailing_tpsl | 6.2% | -7.81 |
| delta_volume_columns_pro_lucf | 6.2% | -3.02 |
| emd_trend_investorunknown | 18.8% | -2.05 |
| range_filter_dw | 15.6% | -1.95 |

`berlin_candles`는 32개 심볼 전부에서 OOS 수익이 음수였다. `stepped_trailing_tpsl`은 평균 Sharpe가 -7.81로 가장 나쁜 결과를 기록했다.

### 4) Per-pair Gate

```python
gate = (
    (d_nonvix["sharpe"] >= 1.0)
    & (d_nonvix["profit_factor"] >= 1.2)
    & (d_nonvix["mdd"] >= -0.12)
    & (d_nonvix["trades"] >= 30)
)
```

이 4개 조건을 모두 충족한 조합만 후보가 된다. 448행 중 26행이 통과했다(5.8%).

```
전략별 Gate 통과 수
──────────────────────────────────
golden_triangle_1h_setup1_2          10
inwcoin_martingale_strategy           4  ← research_only 제거 후 실질 0
pmax_explorer                         4
elastic_volume_weighted_student_t     3
emd_trend_investorunknown             2  ← global_cut 대상이나 Sharpe 조건만 보면 일부 통과
range_filter_dw                       2  ← global_cut 제거됨
stepped_trailing_tpsl                 1  ← global_cut 제거됨
```

`research_only`와 `globally_cut`을 먼저 제거하고 Gate를 적용하면 실질 후보는 golden_triangle(10) + pmax(4) + elastic_t(3) = 17개 쌍이다.

## 심볼별 최고 스코어 1개 선택

```python
cand = d_nonvix[gate].copy().sort_values(["symbol", "score"], ascending=[True, False])
chosen = cand.groupby("symbol", as_index=False).head(1)
```

`max_strategies_per_symbol=1`이면 심볼당 1개, 2이면 2개까지 허용한다. `refresh_nonvix_selection.py`는 single_best 모드용으로 항상 1개를 선택한다.

최종 선택된 14개 쌍:

| 심볼 | 전략 | Score |
|------|------|------:|
| IWM | golden_triangle_1h_setup1_2 | 5.15 |
| IYR | golden_triangle_1h_setup1_2 | 5.34 |
| KRE | golden_triangle_1h_setup1_2 | 4.39 |
| MDY | golden_triangle_1h_setup1_2 | 3.53 |
| SOXX | golden_triangle_1h_setup1_2 | 2.98 |
| TAN | golden_triangle_1h_setup1_2 | 3.01 |
| XBI | elastic_volume_weighted_student_t_tension | 4.47 |
| XLB | pmax_explorer | 5.41 |
| XLF | golden_triangle_1h_setup1_2 | 4.08 |
| XLI | golden_triangle_1h_setup1_2 | 5.26 |
| XLP | golden_triangle_1h_setup1_2 | **10.41** |
| XLRE | golden_triangle_1h_setup1_2 | 6.15 |
| XLU | pmax_explorer | 5.20 |
| XLY | pmax_explorer | 4.41 |

XLP의 score 10.41이 압도적으로 높다. golden_triangle이 10개 심볼을 독식하고 pmax가 3개, elastic_t가 1개를 가져갔다.

## 아티팩트 출력

```
config/allowlist_ls32_nonvix.csv    ← symbol,strategy,enabled,reason
config/scores_ls32_nonvix.csv       ← symbol,strategy,score
config/enabled_symbols_ls32_nonvix.txt ← 활성 심볼 목록
config/selection_ls32_nonvix_meta.json ← generation_id + selected_pairs
reporting/ls32_nonvix_single_best_refresh_latest.md  ← Markdown 리포트
```

`allowlist.csv`는 모든 심볼 × 전략 조합을 담는다. Gate를 통과한 쌍은 `enabled=1`, 나머지는 `enabled=0`. 라이브 봇은 이 파일을 읽어 해당 사이클에서 실행할 전략을 결정한다.

## SHA256 generation_id

```python
def _generation_id(ranking_path: Path, payload: dict[str, Any]) -> str:
    h = hashlib.sha256()
    h.update(str(ranking_path.resolve()).encode("utf-8"))
    h.update(ranking_path.read_bytes())     # ranking 파일 내용
    h.update(json.dumps(payload, sort_keys=True).encode("utf-8"))  # gate + selected_pairs
    return "sha256:" + h.hexdigest()
```

ranking 파일 내용과 Gate 파라미터, 선택 결과를 모두 포함한 SHA256이다. ranking이 바뀌거나 Gate 파라미터가 달라지면 generation_id가 달라진다.

```json
// selection_ls32_nonvix_meta.json
{
  "generation_id": "sha256:2549a5f1c93b22d5...",
  "selected_pairs": [...],
  "enabled_symbols": [...]
}
```

라이브 봇은 이 `generation_id`로 현재 실행 중인 포지션이 어느 세대의 선택 결과인지 추적한다. Cutover 시 이전 세대 포지션을 `legacy_hold`로 분류하는 기준이 된다.

## Allowlist 리프레시 트리거

실험 결과가 업데이트됐거나, Gate 파라미터를 조정했거나, 새 전략이 추가됐을 때 아래 명령으로 리프레시한다.

```bash
python3 -m trading.cli.refresh_nonvix_selection \
  --ranking results_experiments_all_strategies_20260306_001/ranking_all.csv \
  --min-sharpe 1.0 \
  --min-profit-factor 1.2 \
  --max-drawdown-abs 0.12 \
  --min-trades 30
```

출력된 `generation_id`가 바뀌면 다음 라이브 봇 재기동 시 새 세대로 전환된다.
