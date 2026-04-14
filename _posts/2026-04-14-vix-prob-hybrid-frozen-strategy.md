---
layout: post
title: VIX Prob Hybrid — 항상 켜진 VIX 리스크온 확률 전략
subtitle: VIX z-score와 EMA 모멘텀으로 risk-on 확률 추정, frozen 전략 설계, 60분봉→15분봉 리인덱싱
author: HyeongJin
date: 2026-04-14 10:00:00 +0900
categories: AI/LLM
tags: [Python, Trading, Backtesting]
sidebar: []
published: true
---

포트폴리오 전략들이 체인 라우터에 의해 켜고 꺼지는 동안 `vix_prob_hybrid`는 항상 켜져 있다. 라우터 설정에서 `frozen` 전략으로 지정되어 오버라이드 목록에 포함돼도 필터링되지 않는다.

```python
_FROZEN_OVERRIDE_STRATEGIES = {"vix_prob_hybrid"}

def _sanitize_override_strategies(values):
    out = []
    for v in values:
        s = str(v).strip()
        if not s or s in _FROZEN_OVERRIDE_STRATEGIES:
            continue    # frozen 전략은 오버라이드로 끌 수 없음
        out.append(s)
    return out
```

이 전략이 항상 켜져 있어야 하는 이유는 VIX 상태를 기반으로 시장 전체의 리스크온/오프 여부를 판단하는 역할 때문이다. 다른 전략들이 개별 심볼의 가격 패턴을 보는 동안, `vix_prob_hybrid`는 시장 공포지수를 직접 입력으로 받아서 리스크온 확률을 실시간으로 계산한다.

## 입력: VIX 60분봉

다른 전략들이 15분봉 종가를 입력으로 받는 것과 달리 이 전략은 60분봉 VIX 데이터를 입력으로 쓴다.

```python
d60 = ensure_ohlcv(get_df_60m(inp))
```

VIX 15분봉은 일중 노이즈가 심하다. 60분봉으로 집계하면 의미 있는 변화만 남는다. 실제로 VIX가 장중에 급등했다가 한 시간 안에 복귀하는 경우는 전략 신호를 바꿀 이유가 아니다.

## VIX → 리스크온 확률

```python
def _proxy_prob(d: pd.DataFrame) -> pd.Series:
    vix = d["close"].rename("vix")
    z = (
        (vix - vix.rolling(24).mean())
        / vix.rolling(24).std().replace(0.0, pd.NA)
    ).fillna(0.0)
    px_mom = ema(vix.pct_change().fillna(0.0), 8)
    p = (0.5 - 0.18 * z - 0.45 * px_mom).clip(0.02, 0.98)
    return p
```

두 신호를 결합한다.

**VIX z-score (`z`)**: 최근 24봉(24시간) 대비 현재 VIX가 얼마나 높은지다. VIX 절댓값은 시장 국면마다 기준이 달라서 상대적 위치가 중요하다. VIX 20은 조용한 장세에서 높고, 공포장에서는 낮다. 24봉 롤링 z-score로 현재 VIX가 최근 분포 어느 위치인지를 본다.

**VIX 모멘텀 (`px_mom`)**: `vix.pct_change()`의 EMA 8봉. VIX의 방향성이다. 현재 VIX 수준이 낮아도 빠르게 오르는 중이면 공포가 확산되는 신호다. 반대로 VIX가 높아도 빠르게 내려가면 공포가 완화 중이다.

조합 공식: `0.5 - 0.18 * z - 0.45 * px_mom`

- 기본값 0.5 (중립)에서 시작
- z가 높으면(VIX 상대적으로 높음) 확률 감소
- px_mom이 양수면(VIX 상승 중) 확률 감소
- z가 낮고 px_mom이 음수(VIX 하락 중)이면 확률 증가

`clip(0.02, 0.98)`은 확률이 0이나 1에 붙지 않도록 한다. 극단 확률에서 사이징이 0이나 1이 되는 것을 방지한다.

## 진입/청산 조건

```python
long_cond = (
    (d60["close"] <= float(p["entry_vix"])) &   # VIX <= 15
    (proxy_prob >= float(p["enter_prob"]))        # 리스크온 확률 >= 0.55
)
exit_cond = (
    (d60["close"] >= float(p["exit_vix"])) |     # VIX >= 20
    (proxy_prob <= float(p["exit_prob"]))          # 리스크온 확률 <= 0.45
)
```

VIX 절댓값(15, 20)과 확률(0.55, 0.45) 두 조건을 동시에 쓴다. 절댓값만 쓰면 VIX 15가 상대적으로 높은 구간에서도 진입하고, 확률만 쓰면 VIX가 급등 중에도 확률이 아직 높으면 진입한다. 두 조건의 AND/OR 조합으로 노이즈를 걸러낸다.

진입에는 AND, 청산에는 OR를 쓴다. 진입은 더 엄격하게, 청산은 더 민감하게.

`allow_short = False`가 기본값이다. VIX 기반 전략에서 숏은 "시장이 빠질 것"에 베팅하는 게 아니라 "리스크오프 상태"를 나타내는 것이므로 단순히 포지션을 0으로 두는 게 더 명확하다.

## 포지션 사이징

```python
size = (proxy_prob - float(p["exit_prob"])).clip(0.0, 1.0)
```

리스크온 확률이 청산 기준(0.45)을 얼마나 초과하는지로 사이즈를 정한다. 확률이 0.45이면 사이즈 0, 0.95이면 사이즈 0.5, 1.45이면 사이즈 1.0(but clip으로 1.0 상한). 확률이 높을수록 더 많이 태우는 구조다.

## 60분봉 → 15분봉 리인덱싱

전략 출력은 60분봉 기준이지만 실행 레이어는 15분봉 루프에서 돌아간다. 60분봉 신호를 15분봉 인덱스에 맞춰야 한다.

```python
frame60 = compact_output_frame(d60.index, sig, size, sl, tp)
return frame60.reindex(inp["df_15m"].index, method="ffill").fillna({
    "signal": 0,
    "size": 0.0
})
```

`reindex(method="ffill")`은 60분봉 신호가 없는 15분봉 위치에 직전 60분봉 값을 채운다. 09:30 봉의 신호가 10:15 봉까지 이어지다가 10:30 봉에 새 신호로 교체되는 식이다. `fillna`는 60분봉 이전의 초기 구간을 0으로 채운다.

## SL / TP

```python
sl = d60["close"] * 0.97   # VIX 기준 3% 손절
tp = d60["close"] * 1.04   # VIX 기준 4% 익절
```

VIX 데이터가 입력이라 SL/TP도 VIX 기준으로 계산된다. 실제로 이 SL/TP는 참고값 수준이다. `vix_prob_hybrid`는 `exit_cond`가 True가 되면 신호가 0으로 바뀌어 실행 레이어가 포지션을 청산한다. SL/TP가 먼저 발동할 가능성은 낮다.

## 다른 전략과의 역할 분리

| 전략 | 입력 | 역할 |
|------|------|------|
| PMAX | 15분봉 종가 | 개별 심볼 추세 추종 |
| BB+RSI | 15분봉 종가 | 개별 심볼 평균 회귀 |
| Z-Score MR | 60분봉 종가 | 저상관 쌍 스프레드 |
| vix_prob_hybrid | 60분봉 VIX | 시장 전체 리스크온 필터 |

`vix_prob_hybrid`는 개별 심볼 신호가 아니라 시장 전체의 리스크 환경을 판단한다. 다른 전략들이 개별 종목에서 신호를 낼 때 VIX 기반 리스크온 확률이 낮으면 포트폴리오 전체의 익스포저 배율(`exposure_multiplier`)을 낮추는 데 쓰인다.

frozen 설계는 이 역할 때문이다. 리스크 필터를 특정 체인에서 꺼버리면 그 체인에서 시장 공포를 무시하게 된다.
