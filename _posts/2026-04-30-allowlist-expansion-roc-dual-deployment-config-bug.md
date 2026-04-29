---
layout: post
title: Allowlist 확장과 roc_dual_momentum 배포 — 수작업 24종목 추가와 설정 키 오타 10일 방치
subtitle: 57종목 실험 상위 24종목 수동 선별, per-symbol 파라미터 JSON 배포, symbol_overrides → by_symbol 키 오류로 파라미터 묵살됐던 과정
author: HyeongJin
date: 2026-04-30 10:00:00 +0900
categories: AI/LLM
tags: [Python, Trading, Strategy, DataEngineering, LiveTrading]
sidebar: []
published: true
---

OOS 검증을 통과한 전략과 심볼을 라이브 봇에 실제로 투입하려면 세 가지 작업이 필요하다. allowlist에 추가하고, 심볼별 최적 파라미터를 설정 파일에 넣고, 해당 전략이 어느 체인에서 활성화될지 chain config를 수정하는 것이다. 이 포스트는 S&P500 57종목 실험 결과를 수작업으로 정리해 allowlist를 확장하고, `roc_dual_momentum` 12쌍을 배포하는 과정을 다룬다. 그리고 배포한 지 10일이 지나서야 발견한 설정 키 오류도 함께 기록한다.

## 24종목 수작업 선별 — 왜 자동화하지 않았나

S&P500 전체 파이프라인(`search_sp500_oos.py`)은 수백 종목을 자동으로 검증하고 Gate를 통과한 결과를 CSV로 뽑는다. 그런데 이 파이프라인과 별개로, 57개 "잘 알려진 대형주"를 따로 묶어서 `golden_triangle` 전략으로 OOS 검증을 돌렸다.

자동화 파이프라인의 결과를 그대로 allowlist에 넣지 않은 이유는 유동성이다. 자동화 파이프라인은 OOS Sharpe와 Gate 기준만 보기 때문에, Alpaca paper trading에서 슬리피지가 크거나 호가 스프레드가 넓은 소형주가 들어올 수 있다. 57종목 실험에서는 처음부터 유동성이 검증된 대형주만 범위를 제한했다.

실험 결과(OOS Sharpe 상위):

| 심볼 | OOS Sharpe | Train Sharpe |
|------|-----------|-------------|
| JNJ  | 7.87 | — |
| TMO  | 7.20 | — |
| NOW  | 5.95 | — |
| MRK  | 5.29 | — |
| CMCSA | 4.98 | — |
| SLB  | — | — |
| AAPL | — | — |
| MSFT | — | — |

OOS Sharpe가 양수인 24종목을 뽑고, 음수인 BAC, JPM, ABBV 등은 제외했다.

```csv
# allowlist_ls32_nonvix.csv 추가 분
JNJ,golden_triangle_1h_setup1_2,1,oos_validated_stock
TMO,golden_triangle_1h_setup1_2,1,oos_validated_stock
NOW,golden_triangle_1h_setup1_2,1,oos_validated_stock
MRK,golden_triangle_1h_setup1_2,1,oos_validated_stock
CMCSA,golden_triangle_1h_setup1_2,1,oos_validated_stock
# ... 19개 더
AAPL,golden_triangle_1h_setup1_2,1,oos_validated_stock
MSFT,golden_triangle_1h_setup1_2,1,oos_validated_stock
META,golden_triangle_1h_setup1_2,1,oos_validated_stock
```

이 추가로 총 심볼 수가 **148개**가 됐다. ETF 14쌍 + S&P500 자동 파이프라인 92종목 + 수작업 24종목이다.

allowlist의 `reason` 컬럼을 `oos_validated_stock`으로 표시한다. 라이브 봇은 이 필드를 읽지 않지만, 나중에 어떤 기준으로 추가됐는지 추적할 수 있다.

## roc_dual_momentum 배포 — per-symbol 파라미터

앞서 OOS 검증에서 Train/Test 양쪽을 통과한 12쌍을 `roc_dual_momentum` 전략으로 배포한다. 이 전략은 심볼마다 최적 파라미터(`fast_roc`, `slow_roc`, `smooth`, `threshold`)가 다르기 때문에 per-symbol 파라미터를 JSON에 정의한다.

```json
// strategy_params_us_ls32.json
"roc_dual_momentum": {
  "by_symbol": {
    "DHR": { "fast_roc": 10, "slow_roc": 30, "smooth": 3, "threshold": 0.002, "allow_short": false },
    "BMY": { "fast_roc": 10, "slow_roc": 30, "smooth": 8, "threshold": 0.0,   "allow_short": false },
    "EIX": { "fast_roc": 10, "slow_roc": 30, "smooth": 3, "threshold": 0.002, "allow_short": false },
    "WM":  { "fast_roc": 5,  "slow_roc": 60, "smooth": 3, "threshold": 0.002, "allow_short": false },
    "COP": { "fast_roc": 15, "slow_roc": 40, "smooth": 8, "threshold": 0.002, "allow_short": false },
    "CSCO":{ "fast_roc": 15, "slow_roc": 60, "smooth": 3, "threshold": 0.0,   "allow_short": false },
    "GLD": { "fast_roc": 5,  "slow_roc": 60, "smooth": 8, "threshold": 0.002, "allow_short": false },
    "AMAT":{ "fast_roc": 15, "slow_roc": 40, "smooth": 8, "threshold": 0.0,   "allow_short": false },
    "CNX": { "fast_roc": 15, "slow_roc": 30, "smooth": 8, "threshold": 0.0,   "allow_short": false },
    "PEG": { "fast_roc": 15, "slow_roc": 60, "smooth": 8, "threshold": 0.002, "allow_short": false },
    "LMT": { "fast_roc": 5,  "slow_roc": 60, "smooth": 8, "threshold": 0.0,   "allow_short": false },
    "WBD": { "fast_roc": 10, "slow_roc": 40, "smooth": 5, "threshold": 0.002, "allow_short": false }
  }
}
```

파라미터 패턴을 보면 상위 5개(DHR, BMY, EIX)는 `fast_roc=10, slow_roc=30` 조합이고, 나머지는 `slow_roc=60`처럼 더 느린 long-term ROC를 쓴다. OOS 실험에서 안정적으로 통과한 파라미터를 그대로 심볼별로 할당한 것이다.

### 체인 배포

`roc_dual_momentum`은 추세 추종 전략이므로 trend와 neutral 체인에만 넣는다.

```json
"trend": {
  "strategies": ["golden_triangle_1h_setup1_2", "roc_dual_momentum", "pmax_explorer", ...]
},
"neutral": {
  "strategies": ["golden_triangle_1h_setup1_2", "roc_dual_momentum", "zscore_mean_reversion", ...]
}
```

chop과 defensive에는 넣지 않는다. 횡보장이나 하락장에서 모멘텀 전략을 돌리면 추세가 아닌 방향으로 포지션을 잡을 수 있다. `zscore_mean_reversion`이 chop/defensive를 담당한다.

## 설정 키 오류 — symbol_overrides vs by_symbol

배포 후 10일이 지나도록 아무도 눈치채지 못한 버그가 있었다. `strategy_params_us_ls32.json`에서 per-symbol 파라미터를 담는 키 이름이 잘못됐다.

```json
// 잘못된 배포 상태 (10일간)
"roc_dual_momentum": {
  "symbol_overrides": {   // ← 이 키는 _resolve_strategy_params가 모른다
    "DHR": { "fast_roc": 10, ... },
    ...
  }
}
```

`_resolve_strategy_params` 함수는 `"by_symbol"` 키만 인식한다.

```python
def _resolve_strategy_params(strategy_name, symbol, default_params, overrides):
    raw = overrides.get(strategy_name, {})

    by_symbol = raw.get("by_symbol")       # "by_symbol"만 읽는다
    default_override = raw.get("default")

    if isinstance(by_symbol, dict):
        # by_symbol이 있으면 심볼별 override 적용
        if isinstance(default_override, dict):
            params.update(default_override)
        sym_override = by_symbol.get(symbol)
        if isinstance(sym_override, dict):
            params.update(sym_override)
        return params

    # by_symbol이 없으면 legacy flat mapping — raw 전체를 params에 덮어씀
    params.update(raw)
    return params
```

`"symbol_overrides"` 키로 저장했기 때문에 `raw.get("by_symbol")`은 None을 반환한다. 코드는 **legacy flat path**로 떨어진다. `params.update(raw)`를 실행하면 `"symbol_overrides"` 딕셔너리 자체가 파라미터로 덮어씌워진다 — 전략은 `fast_roc`, `slow_roc` 같은 숫자 대신 `{"DHR": {...}, "BMY": {...}}` 형태의 딕셔너리를 파라미터로 받게 된다.

`zscore_mean_reversion`도 같은 오류가 있었다.

```json
// zscore_mean_reversion 오류 상태
"zscore_mean_reversion": {
  "default": { "entry_z": 1.5, "allow_short": true },   // 잘못된 default
  "symbol_overrides": { "XBI": {...}, "ETR": {...} }     // 역시 "symbol_overrides"
}
```

legacy flat path에서는 `default`와 `symbol_overrides` 키가 모두 파라미터로 덮어씌워진다. `entry_z=1.5`(OOS 검증 기준 1.0)와 `allow_short=True`(숏 허용 안 하는 설계)가 실수로 실제 파라미터로 적용됐다.

### 수정

한 글자 수정으로 해결됐다.

```json
// 수정 전
"symbol_overrides": { ... }

// 수정 후
"by_symbol": { ... }
```

두 전략(`roc_dual_momentum`, `zscore_mean_reversion`) 모두 동일하게 수정했다.

### 왜 10일이나 지났나

실수가 10일간 지속된 이유는 전략이 겉으로는 "작동하는 것처럼" 보였기 때문이다. 파라미터가 잘못 됐어도 전략 신호 자체는 나왔다 — 다만 OOS에서 검증한 파라미터가 아닌 코드 내부 `DEFAULT_PARAMS`가 그대로 쓰였다. 

`zscore_mean_reversion`의 경우 검증된 `entry_z=1.0` 대신 `entry_z=1.5`가 쓰이면 진입 기준이 더 까다로워진다. 신호가 덜 나오지만 틀린 건 아니라 눈에 잘 안 띈다. `allow_short=True`가 됐어도 실제로 숏 신호가 그 구간에 없으면 드러나지 않는다.

설정 파일 오타는 에러를 던지지 않는다. 코드가 조용히 다른 동작을 한다. 파라미터가 실제로 적용됐는지 확인하려면 로그에서 `entry_z`, `allow_short` 같은 값을 명시적으로 출력하거나, dryrun 사이클에서 파라미터를 덤프해보는 수밖에 없다.

## 정리

| 커밋 | 내용 |
|------|------|
| `0bba418` | 57종목 실험 상위 24종목 수작업 allowlist 추가, 총 148 심볼 |
| `ee37735` | `roc_dual_momentum` 12쌍 per-symbol 파라미터 배포, trend·neutral 체인에 추가 |
| `2538c91` | `symbol_overrides` → `by_symbol` 키 오류 수정, 10일 만에 발견 |

OOS 검증 → 파라미터 최적화 → 배포까지 했는데 설정 키 하나 때문에 10일간 잘못된 파라미터로 돌았다. 설정 파일을 추가할 때는 실제로 파라미터가 적용됐는지 dryrun 로그에서 확인하는 습관이 필요하다.
