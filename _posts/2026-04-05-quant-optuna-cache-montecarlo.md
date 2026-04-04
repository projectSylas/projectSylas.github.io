---
layout: post
title: 퀀트 전략 파라미터 최적화 — Optuna + SQLite 캐시 + Monte Carlo 강건성 검증
subtitle: 그리드 서치 대신 Bayesian 최적화, 중복 계산 제거용 해시 캐시, 거래 셔플 시뮬레이션으로 파산 확률까지
author: HyeongJin
date: 2026-04-05 10:00:00 +0900
categories: AI/LLM
tags: [Python, Trading, Optuna, Backtesting, Statistics]
sidebar: []
published: true
---

전략별 최적 파라미터를 찾으면서 세 가지 문제가 생겼다.

1. 파라미터 공간이 넓으면 그리드 서치가 너무 느리다
2. 같은 파라미터 조합을 조건이 바뀔 때마다 재계산한다
3. 백테스트 Sharpe가 높아도 실제 운용에서 파산할 수 있다

각각 Optuna 베이지안 최적화, SQLite 해시 캐시, Monte Carlo 거래 셔플로 해결했다.

## 왜 그리드 서치가 안 되나

이동평균 기간 하나를 10~200 사이 정수로, 임계값을 0.1~2.0 사이 실수 20단계로 조합하면 이미 1,900가지다. 전략 파라미터가 5개면 100만 가지가 넘는다. 여기에 Walk-Forward 폴드 3개를 각 조합마다 돌리면 수백만 번의 백테스트가 필요하다.

Optuna는 이전 시도 결과를 보고 다음에 시도할 파라미터를 골라서 같은 트라이얼 수 대비 훨씬 좋은 값을 찾는다.

## Optuna + Walk-Forward 통합

```python
def run_optuna_search(
    strategy_name: str,
    df_15m: pd.DataFrame,
    df_60m: pd.DataFrame,
    search_space: dict[str, dict],
    market: str,
    symbol: str,
    bt_cfg: BacktestConfig,
    wf_cfg: WalkForwardConfig,
    cfg: OptunaConfig | None = None,
) -> tuple[dict, float]:
    oc = cfg or OptunaConfig()  # n_trials=80
    registry = load_registry()
    strategy = registry[strategy_name]

    def objective(trial: optuna.Trial) -> float:
        sampled = {k: _sample_param(trial, k, v) for k, v in search_space.items()}
        params = dict(strategy.default_params)
        params.update(sampled)

        wf = walkforward_backtest(
            df_15m=df_15m, df_60m=df_60m,
            strategy=strategy,
            param_candidates=[params],
            bt_cfg=bt_cfg, wf_cfg=wf_cfg,
            market=market, symbol=symbol,
        )

        rets = wf.test_equity.pct_change().fillna(0.0)
        sharpe = float(rets.mean() / rets.std() * (252 * wf_cfg.bars_per_day) ** 0.5) if rets.std() > 0 else 0.0
        mdd = float((wf.test_equity / wf.test_equity.cummax() - 1.0).min())
        pf = calc_profit_factor(wf.test_trades)

        return selection_score({"sharpe": sharpe, "profit_factor": pf, "mdd": mdd})

    study = optuna.create_study(direction="maximize")
    study.optimize(objective, n_trials=oc.n_trials)
    return dict(study.best_params), float(study.best_value)
```

파라미터 스펙은 타입별로 분기한다.

```python
def _sample_param(trial, name: str, spec: dict):
    kind = spec.get("type", "float")
    if kind == "float":
        return trial.suggest_float(name, spec["low"], spec["high"],
                                   log=spec.get("log", False))
    if kind == "int":
        return trial.suggest_int(name, spec["low"], spec["high"])
    if kind == "categorical":
        return trial.suggest_categorical(name, spec["choices"])
```

`log=True`는 스케일이 큰 파라미터(예: 기간 10~1000)에서 작은 값 쪽을 더 촘촘하게 탐색한다. 로그 스케일이 선형보다 탐색 효율이 높다.

## 선택 점수 공식

목적 함수의 핵심은 `selection_score`다. Sharpe 하나만 쓰면 손실 빈도는 낮아도 큰 손실이 나는 전략이 살아남는다.

```python
def selection_score(summary: dict, instability_penalty: float = 0.0) -> float:
    sharpe = float(summary.get("sharpe", 0.0))
    pf = float(summary.get("profit_factor", 0.0))
    mdd = abs(float(summary.get("mdd", 0.0)))
    return sharpe + pf - 2.5 * mdd - float(instability_penalty)
```

- `pf` (Profit Factor): 총 수익 / 총 손실. 1.0 이상이어야 이익이다.
- `mdd` (Max Drawdown): 2.5배 패널티. MDD가 20%면 점수에서 0.5 깎인다.
- `instability_penalty`: 수익이 특정 구간에만 집중되어 있으면 추가 감점. 전체 수익 계열을 5구간으로 나눠서 구간별 평균의 표준편차로 계산한다.

```python
def _instability_penalty(returns: pd.Series) -> float:
    chunks = np.array_split(returns.values, 5)
    mus = [float(np.mean(c)) for c in chunks if len(c) > 0]
    return float(np.std(mus))
```

## SQLite 해시 캐시

같은 전략 + 파라미터 + 데이터 조합을 다시 돌리는 경우가 많다. Optuna 탐색을 멈췄다가 재개하거나, 동일 파라미터를 다른 심볼에 적용할 때.

캐시 키를 해시로 만들어서 SQLite에 저장한다.

```python
@dataclass(frozen=True)
class CacheKey:
    dataset_hash: str    # 데이터프레임 앞뒤 5행 + shape 해시
    strategy_hash: str   # 전략명 + default_params 해시
    param_hash: str      # 파라미터 조합 해시
    fold: str            # "wf_252_63_63"

    def as_text(self) -> str:
        return f"{self.dataset_hash}:{self.strategy_hash}:{self.param_hash}:{self.fold}"
```

데이터프레임 전체를 해시하면 느리다. 대신 인덱스 앞뒤 5행, shape, close 앞뒤 5개만 쓴다. 충돌 가능성은 낮고 속도는 빠르다.

```python
def hash_dataframe(df: pd.DataFrame) -> str:
    payload = {
        "index_head": [str(x) for x in df.index[:5]],
        "index_tail": [str(x) for x in df.index[-5:]],
        "shape": df.shape,
        "close_head": [float(x) for x in df["close"].head(5).tolist()] if "close" in df else [],
        "close_tail": [float(x) for x in df["close"].tail(5).tolist()] if "close" in df else [],
    }
    return hash_obj(payload)  # JSON → SHA256 앞 16자
```

캐시 히트 시 SQLite에서 경로를 꺼내서 Parquet를 읽는다.

```python
def cache_lookup(db_path: Path, key: CacheKey) -> tuple[Path, dict] | None:
    with sqlite3.connect(db_path) as con:
        cur = con.execute(
            "SELECT result_path, summary_json FROM experiment_cache WHERE cache_key=?",
            (key.as_text(),)
        )
        row = cur.fetchone()
    if not row:
        return None
    return Path(row[0]), json.loads(row[1])
```

결과는 folds / equity / trades 세 테이블을 `_table` 컬럼으로 구분해서 Parquet 하나에 담는다. Parquet가 실패하면 CSV로 폴백.

```python
merged = pd.concat([fold_data, eq, tr], ignore_index=True, sort=False)
try:
    merged.to_parquet(out, index=False)
except Exception:
    out = out.with_suffix(".csv")
    merged.to_csv(out, index=False)
```

## 병렬 실험 실행

심볼별 실험 잡을 `ProcessPoolExecutor`로 병렬 처리한다.

```python
@dataclass(frozen=True)
class ExperimentJob:
    strategy_name: str
    symbol: str
    market: str
    param_space: dict[str, list]

def run_jobs_parallel(jobs, df_15m, df_60m, cfg=None) -> list[ExperimentResult]:
    exp_cfg = cfg or ExperimentConfig()
    workers = max(1, exp_cfg.workers)  # os.cpu_count() - 2

    with ProcessPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_run_single, j, df_15m, df_60m, exp_cfg): j for j in jobs}
        for fut in as_completed(futs):
            out.append(fut.result())
```

캐시가 있으면 DB 조회 + Parquet 읽기만 하므로 재실행 시 워커당 수십 ms다. 첫 실행에서만 실제 백테스트를 돌린다.

결과는 `selection_score` 기준으로 정렬된 DataFrame으로 반환된다.

```python
def rank_results(results: list[ExperimentResult]) -> pd.DataFrame:
    rows = [dict(r.summary) | {"strategy": r.job.strategy_name, "symbol": r.job.symbol} for r in results]
    return pd.DataFrame(rows).sort_values("score", ascending=False).reset_index(drop=True)
```

## Monte Carlo 강건성 검증

백테스트 Sharpe가 좋아도 거래 순서가 달랐다면 결과가 달라졌을 수 있다. 거래를 무작위로 셔플해서 1,000번 시뮬레이션한다.

```python
def trade_shuffle_simulation(
    trades: pd.DataFrame,
    n_iter: int = 1000,
    seed: int = 42,
    ruin_threshold: float = -0.20,
) -> MonteCarloSummary:
    rng = random.Random(seed)
    tr = trades["return"].astype(float).tolist()
    rets = []

    for _ in range(n_iter):
        x = tr[:]
        rng.shuffle(x)
        eq = 1.0
        min_eq = 1.0
        for r in x:
            eq *= (1.0 + float(r))
            min_eq = min(min_eq, eq)
        rets.append((eq - 1.0, min_eq - 1.0))

    final_returns = np.array([r for r, _ in rets])
    min_path_dd = np.array([d for _, d in rets])

    return MonteCarloSummary(
        p10_return=float(np.percentile(final_returns, 10)),
        p50_return=float(np.percentile(final_returns, 50)),
        p90_return=float(np.percentile(final_returns, 90)),
        ruin_prob=float(np.mean(min_path_dd <= ruin_threshold)),
    )
```

`ruin_prob`은 1,000번 시뮬레이션 중 최소 자산이 -20% 이하로 내려간 비율이다. 이 값이 0.1 이상이면 운용 중 파산 가능성이 10% 이상이라는 뜻으로, 파라미터를 보수적으로 조정하거나 포지션 크기를 줄인다.

p10/p50/p90은 최악-중간-최선 시나리오 수익률이다. p10이 음수면 운이 나쁠 때 손실이 날 수 있다는 뜻이다.

## 파라미터 민감도 검증

최적 파라미터 근방 ±15%를 무작위로 흔들어서 결과가 안정적인지 확인한다.

```python
def perturb_params(base: dict, pct: float = 0.15, seed: int = 42, n: int = 20) -> list[dict]:
    rng = np.random.default_rng(seed)
    out = []
    for _ in range(n):
        p = dict(base)
        for k, v in p.items():
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                lo, hi = v * (1 - pct), v * (1 + pct)
                p[k] = int(round(rng.uniform(lo, hi))) if isinstance(v, int) else float(rng.uniform(lo, hi))
        out.append(p)
    return out
```

이 20개 변형을 `run_jobs_parallel`에 넣어서 score 분포를 본다. 최적 파라미터에서 조금 벗어나도 score가 유지되면 강건한 파라미터고, 급격히 떨어지면 과최적화 의심이다.

## 실제 흐름

```
Optuna 80회 탐색 → best_params 도출
    ↓
perturb_params(best_params) → 20개 변형
    ↓
run_jobs_parallel (캐시 활용)
    ↓
score 분포 확인 (민감도)
    ↓
trade_shuffle_simulation → ruin_prob 확인
    ↓
ruin_prob < 0.05 이면 allowlist 편입
```

Optuna로 방향을 잡고, 민감도와 Monte Carlo로 실전 투입 여부를 결정하는 구조다. 백테스트 단일 지표만 보고 파라미터를 고르는 것보다 실패 케이스를 미리 걸러낼 수 있다.
