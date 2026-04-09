---
layout: post
title: 퀀트 데이터 파이프라인 — Alpaca/Yahoo/FRED 폴백 체인과 ADR 프록시 피처
subtitle: 데이터 소스 이중화, Advance-Decline Ratio 계산, 시간 sin/cos 임베딩까지
author: HyeongJin
date: 2026-04-09 10:00:00 +0900
categories: AI/LLM
tags: [Python, Trading, DataEngineering, FeatureEngineering]
sidebar: []
published: true
---

라이브 트레이딩 시스템에서 데이터 파이프라인이 단일 소스에 의존하면 해당 소스가 내려갈 때 전체가 멈춘다. Alpaca가 주 소스고, Yahoo Finance가 폴백, FRED API가 VIX 공식 소스 역할을 한다. 각 소스에서 가져온 데이터로 레짐 분류에 쓸 피처를 만드는 과정을 정리한다.

## 멀티소스 폴백 체인

### Alpaca 데이터 클라이언트

Alpaca Data API v2는 페이지 토큰 기반 페이지네이션을 쓴다. `next_page_token`이 없을 때까지 반복 요청한다.

```python
def fetch_bars(self, req: BarRequest) -> pd.DataFrame:
    url = "https://data.alpaca.markets/v2/stocks/bars"
    params = {
        "symbols": req.symbol,
        "timeframe": self._tf(req.timeframe),
        "start": req.start,
        "end": req.end,
        "adjustment": "raw",
        "feed": req.feed,  # "iex" or "sip"
        "limit": 10000,
    }
    token = None
    rows: list[dict] = []
    while True:
        if token:
            params["page_token"] = token
        r = requests.get(url, headers=self._headers(), params=params, timeout=20)
        r.raise_for_status()
        data = r.json()
        rows.extend(data.get("bars", {}).get(req.symbol, []))
        token = data.get("next_page_token")
        if not token:
            break
```

타임프레임 문자열을 API 형식으로 변환한다. `"15m"` → `"15Min"`, `"60m"` → `"1Hour"`.

```python
@staticmethod
def _tf(tf: str) -> str:
    return {"1m": "1Min", "5m": "5Min", "15m": "15Min",
            "30m": "30Min", "60m": "1Hour", "1d": "1Day"}[tf]
```

응답의 컬럼명이 단축형(`t`, `o`, `h`, `l`, `c`, `v`)이라 rename이 필요하다.

```python
df = df.rename(columns={"t": "time", "o": "open", "h": "high",
                         "l": "low", "c": "close", "v": "volume"})
df["time"] = pd.to_datetime(df["time"], utc=True).dt.tz_convert("America/New_York")
```

타임존은 UTC로 받아서 뉴욕으로 변환한다. 이후 모든 인덱스는 `America/New_York` 기준으로 유지한다.

### Yahoo Finance 폴백

Alpaca가 안 될 때 Yahoo로 넘어간다.

```python
class YahooDataClient:
    def fetch_bars(self, symbol: str, timeframe: str, start: str, end: str) -> pd.DataFrame:
        df = yf.download(symbol, start=start, end=end,
                         interval=interval, auto_adjust=False, progress=False)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [c[0] for c in df.columns]   # MultiIndex 평탄화
        ...
        if out.index.tz is None:
            out.index = out.index.tz_localize("UTC")
        out.index = out.index.tz_convert("America/New_York")
```

`yf.download`의 컬럼이 `MultiIndex`일 때가 있다. 단일 심볼도 `(Close, SPY)` 형태로 나오는 경우가 있어서 평탄화 처리를 넣었다.

### VIX: FRED → Yahoo 폴백

VIX 공식 일봉 데이터는 FRED의 `VIXCLS` 시리즈다.

```python
def fetch_vix_series(start: str, end: str) -> pd.Series:
    try:
        return fetch_vix_fred(start, end)   # FRED_API_KEY 필요
    except Exception:
        y = YahooDataClient().fetch_bars("^VIX", "1d", start, end)
        s = y["close"].copy()
        s.index = pd.to_datetime(s.index.date)  # tz 제거 후 날짜만
        return s
```

FRED 호출이 실패하면 Yahoo의 `^VIX`로 자동 폴백된다. 두 소스 모두 날짜 인덱스(naive)를 반환하도록 맞춰서 호출부에서 소스를 신경 쓰지 않아도 된다.

한국 VKOSPI도 같은 패턴이다. 공식 KRX 피드는 인증이 복잡해서 사용자가 CSV를 직접 내려받아 넘기거나, 없으면 Yahoo의 `^KSVKOSPI`를 쓴다.

## ADR 프록시

ADR(Advance-Decline Ratio)은 유니버스 전체에서 오른 종목 수 / 내린 종목 수 비율이다. 시장 폭(breadth)을 나타낸다. S&P500 전체가 오르더라도 상위 10개 종목만 오르고 나머지가 내리면 ADR은 낮다.

```python
def build_adr_proxy(
    bars: dict[str, pd.DataFrame],
    universe: Iterable[str],
    timeframe: str,
) -> pd.Series:
    advances = None
    declines = None

    for sym in universe:
        d = bars.get(sym)
        if d is None or d.empty:
            continue
        ret = d["close"].pct_change().fillna(0.0)
        adv = (ret > 0).astype(int)
        dec = (ret < 0).astype(int)
        advances = adv if advances is None else advances.add(adv, fill_value=0)
        declines = dec if declines is None else declines.add(dec, fill_value=0)

    adr = (advances + 1.0) / (declines + 1.0)
    return adr.astype(float)
```

분모에 1을 더해서 0으로 나누는 걸 방지한다. `advances + 1 / declines + 1`이면 모든 종목이 내려도 0이 아니라 `1/N+1` 정도 값이 나온다.

ADR을 바로 피처로 쓰는 대신 log를 씌우고 z-score를 만든다. ADR의 분포가 right-skewed라 raw 값은 정규화 후 비교가 어렵다.

```python
out["adr_log"] = np.log(out["adr"].clip(lower=1e-6))
out["adr_z"] = _zscore(out["adr_log"], 120)  # 120봉 롤링
```

## 피처 엔지니어링

### 수익률과 변동성

```python
out["ret_1"] = out["close"].pct_change().fillna(0.0)
out["ret_8"] = out["close"].pct_change(8).fillna(0.0)
out["vol_20"] = out["ret_1"].rolling(20).std().fillna(0.0)
```

단기(1봉)와 중기(8봉) 수익률을 모두 넣는다. 전략에 따라 모멘텀이나 평균 회귀 신호가 다른 주기에서 나오기 때문이다.

### VIX z-score

VIX 절대값은 시장 국면마다 기준이 다르다. 2020년에 VIX 30은 낮은 편이고, 2023년에 VIX 30은 높은 편이다. 롤링 z-score로 현재 VIX가 최근 분포 대비 얼마나 높은지를 본다.

```python
vix_intraday = vix_daily.reindex(idx.date, method="ffill")
vix_intraday.index = idx   # 15분봉 인덱스에 맞춤
out["vix_z"] = _zscore(out["vix"], 80)
out["vix_chg_5"] = out["vix"].pct_change(5).fillna(0.0)
```

`vix_chg_5`는 VIX의 단기 방향성이다. VIX가 높더라도 내려가는 추세면 공포가 완화 중이고, 낮더라도 급등 중이면 경계 신호다.

### 시간 sin/cos 임베딩

시간을 원형으로 인코딩한다. `hour=0`과 `hour=23`이 수치상으로 멀지만 실제로는 30분 차이다. 선형 수치로 넣으면 모델이 이 연속성을 학습하기 어렵다.

```python
out["hour"] = idx.hour + idx.minute / 60.0
out["hour_sin"] = np.sin(2.0 * np.pi * out["hour"] / 24.0)
out["hour_cos"] = np.cos(2.0 * np.pi * out["hour"] / 24.0)
```

`(sin, cos)` 쌍이면 어떤 두 시각 사이의 거리도 유클리드 거리로 바르게 표현된다. 장 시작(09:30)과 장 마감(15:45) 부근의 변동성 패턴을 모델이 포착하는 데 도움이 된다.

한국 데이터에는 요일 임베딩을 쓴다. 일봉 데이터라 시간 정보가 없고 대신 요일별 효과(월요일 약세 등)를 반영한다.

```python
day = out.index.dayofweek  # 0=월 ~ 4=금
out["dow_sin"] = np.sin(2.0 * np.pi * day / 7.0)
out["dow_cos"] = np.cos(2.0 * np.pi * day / 7.0)
```

### 급락 레이블

레짐 분류 외에 급락 예측 레이블도 만든다. 향후 N봉 내 최저가가 현재 대비 임계값 이하로 내려가면 1이다.

```python
def build_drawdown_label(close: pd.Series, horizon: int = 5,
                          threshold: float = -0.03) -> pd.Series:
    future_min = close.shift(-1).rolling(horizon).min().shift(-(horizon - 1))
    dd = future_min / close - 1.0
    return (dd <= threshold).astype(int).dropna()
```

`shift(-1).rolling(horizon).min().shift(-(horizon-1))`은 현재 봉 이후 `horizon`봉 내 최솟값을 현재 인덱스에 정렬한다. 이 레이블을 분류 모델로 학습하면 급락 직전 패턴을 잡는 데 쓸 수 있다.

## 캘린더 정렬

여러 소스 시리즈를 합칠 때 인덱스 불일치가 생긴다. 주식 바와 VIX, ADR의 거래일이 다를 수 있다.

```python
def align_on_calendar(series_map: dict[str, pd.Series], ffill: bool = True) -> pd.DataFrame:
    idx = None
    for s in series_map.values():
        idx = s.index if idx is None else idx.union(s.index)

    out = pd.DataFrame(index=idx)
    for name, s in series_map.items():
        out[name] = s.reindex(idx)

    if ffill:
        out = out.sort_index().ffill()
    return out
```

전체 인덱스의 합집합을 만들고 각 시리즈를 reindex한 뒤 forward fill한다. VIX 휴장일에는 직전 거래일 값이 채워진다.
