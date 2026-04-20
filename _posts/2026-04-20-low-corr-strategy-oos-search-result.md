---
layout: post
title: 4개 전략 × 84 심볼 OOS 검증 — roc_dual_momentum만 살아남았다
subtitle: Train/Test 분리 + LiveGate 이중 필터로 평균회귀 3종을 탈락시키고 모멘텀 12개 조합을 발굴한 과정
author: HyeongJin
date: 2026-04-20 10:00:00 +0900
categories: AI/LLM
tags: [Python, Trading, Backtesting, QuantFinance, Strategy]
sidebar: []
published: true
---

새 전략을 추가할 때 가장 위험한 실수는 Train 성능만 보는 것이다. 좋은 Train Sharpe가 OOS에서 반전되면 라이브에서 그대로 손실이 된다. `search_low_corr_strategies.py`는 4개 전략을 84개 심볼에 걸쳐 Train/Test로 분리해 검증하고, 양쪽 모두 LiveGate를 통과한 조합만 선별한다.

## 실험 설계

**기간 분리**

| 구간 | 기간 | 역할 |
|------|------|------|
| Train | 2024-01-01 ~ 2025-06-30 | 파라미터 최적화 |
| Test | 2025-07-01 ~ 2026-03-03 | OOS 검증 |

Train에서 최고 Sharpe 파라미터를 고른 뒤 Test에서 동일 파라미터로 재검증한다. 두 구간 모두 아래 Gate를 통과해야 "양쪽 통과" 판정을 받는다.

```python
GATE = dict(sharpe=1.0, pf=1.2, mdd=-0.12, trades=20)
```

- Sharpe ≥ 1.0, Profit Factor ≥ 1.2, MDD ≥ -12%, 거래 수 ≥ 20

**대상 전략**

| 전략 | 성격 | 파라미터 조합 수 |
|------|------|---------------|
| `bb_rsi_reversion` | 평균회귀 (볼린저 + RSI) | ~540 |
| `zscore_mean_reversion` | 평균회귀 (Z-score) | 162 |
| `elastic_volume_weighted_student_t_tension` | 평균회귀 (Student-t) | 48 |
| `roc_dual_momentum` | 추세 추종 (ROC 이중 가속도) | 54 |

**백테스트 비용 가정**: 편도 수수료 2bp + 슬리피지 1bp, 1바 지연 체결.

## 결과 요약

```
전략별 Train 통과 → Test 통과 (양쪽)
──────────────────────────────────────
bb_rsi_reversion                  5 → 0
elastic_student_t                 6 → 0
zscore_mean_reversion            35 → 0
roc_dual_momentum                61 → 12
```

84개 심볼 중 Train Gate를 통과한 조합이 총 107개. 그 중 양쪽 통과는 12개 — 전부 `roc_dual_momentum`이다.

## 평균회귀 전략이 무너진 이유

### bb_rsi_reversion

Train Sharpe 평균 1.11이었지만 Test에서는 -1.02로 반전됐다. Train 5개 중 Test 통과 0개.

볼린저 밴드 이탈→복귀 패턴은 레인지 장세에서 잘 작동한다. 2024년 초~2025년 중반은 상승 추세 속 횡보 구간이 많았다. 그러나 2025-07 이후 Test 구간에서 추세성이 강해지면서 밴드 이탈 후 복귀 없이 추세를 이어가는 케이스가 늘었다.

### zscore_mean_reversion

Train에서 35개를 통과했지만 Test MDD -12% 초과 탈락이 8개, 전체적으로 Test Sharpe 평균 0.16이었다. 낙폭을 제어하지 못한 케이스가 많았다.

Z-score 기반 전략은 vol이 급등하면 진입 기준선이 흔들린다. Test 구간에서 VIX가 반복적으로 스파이크하면서 "통계적으로 과도한 이탈"이 실제로는 방향성 변화였던 케이스가 많았다.

### elastic_student_t

가장 보수적인 grid(48개 조합)로 Train 6개 통과, Test 0개.

## roc_dual_momentum만 살아남은 이유

`roc_dual_momentum`은 short-term ROC와 long-term ROC의 가속도 차이로 진입한다.

```python
fast_roc = d60["close"].pct_change(fast_n)
slow_roc = d60["close"].pct_change(slow_n)

# 단기 ROC가 장기 ROC를 초과할 때 롱
long_cond = (fast_sm > slow_sm) & (fast_sm > threshold)
```

Test 구간(2025-07~2026-03)은 트럼프 당선 이후 섹터 순환과 방향성 장세가 반복됐다. 모멘텀 전략에 유리한 환경이다. 평균회귀는 "올랐으니 내려온다"고 베팅하지만, 모멘텀은 "올랐으니 더 오른다"고 본다 — Test 구간에서는 후자가 맞았다.

Train 61개 → Test 12개 생존률은 약 20%. 생각보다 낮다. Train Sharpe ≥ 1.0만으로는 OOS 안정성을 보장할 수 없다.

## 양쪽 통과 12개 조합

| 심볼 | Train Sharpe | Test Sharpe | Test MDD | Profit Factor |
|------|-------------|-------------|----------|---------------|
| DHR  | 1.16 | 2.71 | -3.5% | 1.39 |
| BMY  | 1.01 | 2.65 | -2.4% | 2.11 |
| EIX  | 1.36 | 2.20 | -3.8% | 1.85 |
| COP  | 1.05 | 1.80 | -3.0% | 1.78 |
| WM   | 2.38 | 1.85 | -2.3% | 1.32 |
| CSCO | 1.61 | 1.68 | -2.9% | 2.43 |
| AMAT | 1.05 | 1.47 | -7.6% | 1.86 |
| GLD  | 2.20 | 1.47 | -1.2% | 1.62 |
| PEG  | 1.12 | 1.21 | -2.2% | 1.30 |
| LMT  | 1.29 | 1.12 | -2.4% | 1.36 |
| CNX  | 1.41 | 1.42 | -9.0% | 1.43 |
| WBD  | 1.06 | 1.05 | -3.3% | 2.84 |

대부분 MDD가 -4% 이하로 낮다. 유일하게 AMAT(-7.6%)와 CNX(-9.0%)가 상대적으로 크지만 Gate 기준(-12%) 이내다.

파라미터 경향도 눈에 띈다. 상위 5개 조합이 모두 `fast_roc=10, slow_roc=30` 또는 `fast_roc=5, slow_roc=60` 계열에 `threshold=0.002`를 쓴다. 노이즈 필터(threshold)가 있는 조합이 OOS에서 더 안정적이었다.

```python
# 대표 파라미터 (DHR, EIX)
{"fast_roc": 10, "slow_roc": 30, "smooth": 3, "threshold": 0.002}

# BMY
{"fast_roc": 10, "slow_roc": 30, "smooth": 8, "threshold": 0.0}
```

## Train 통과 심볼이 많은 이유

`roc_dual_momentum`이 Train에서 61개 통과한 이유는 파라미터 grid가 추세 강도가 다른 심볼에도 유연하게 맞아들기 때문이다. `fast_roc=[5,10,15]`, `slow_roc=[30,40,60]`, `smooth=[3,5,8]`로 조합하면 54개 — 심볼별로 모멘텀 속도에 맞는 파라미터가 적어도 하나는 존재할 가능성이 높다. 반면 bb_rsi는 평균회귀가 성립하는 심볼 자체가 적어 Train 통과조차 5개에 불과했다.

## 다음 단계

12개 조합은 기존 allowlist에 추가 후보로 올린다. allowlist에 이미 포함된 심볼의 경우 기존 전략과 roc_dual의 correlation을 먼저 확인한다 — 포트폴리오 분산 효과가 없으면 추가 의미가 없다.
