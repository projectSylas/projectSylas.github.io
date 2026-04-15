---
layout: post
title: 라이브 트레이딩 모니터링 대시보드 — 순수 Python HTTP 서버와 TTL 캐시
subtitle: ThreadingHTTPServer 단일 파일 서버, 인메모리 TTL 캐시, Alpaca→Yahoo 가격 폴백, CSV 실시간 로그 파싱까지
author: HyeongJin
date: 2026-04-15 10:00:00 +0900
categories: AI/LLM
tags: [Python, Trading, LiveTrading, DataEngineering]
sidebar: []
published: true
---

라이브 트레이딩 봇이 돌아가는 동안 현재 상태를 한눈에 볼 수 있어야 한다. 레짐이 어느 체인으로 분류됐는지, 어느 전략이 활성화됐는지, 마지막 주문이 성공했는지. Flask나 FastAPI를 쓰면 의존성이 늘어난다. 순수 Python 표준 라이브러리의 `http.server`로 구현했다.

## 서버 구조

```python
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

class DashboardHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)
        ...
```

`ThreadingHTTPServer`는 요청마다 새 스레드를 만든다. 브라우저가 `/api/state`와 `/api/price`를 동시에 요청해도 블로킹 없이 처리된다. 대시보드 하나에 여러 탭이 열려 있어도 문제없다.

엔드포인트는 세 가지다.
- `GET /` — 단일 HTML 파일 (CSS, JavaScript 모두 인라인)
- `GET /api/state` — 봇 상태 JSON (레짐, 포지션, 실행 이력, 성능)
- `GET /api/price` — 가격 시계열 JSON (Alpaca → Yahoo 폴백)

외부 의존성 없이 단일 `.py` 파일로 서버와 프론트엔드를 함께 제공한다. 배포가 `python dashboard_server.py --port 8080` 한 줄이다.

## TTL 인메모리 캐시

API 요청마다 외부 API를 호출하면 느리고 rate limit에 걸린다. 결과를 메모리에 캐시하고 TTL이 지나면 새로 가져온다.

```python
_PRICE_CACHE: dict[str, tuple[float, dict]] = {}
_ALLOWLIST_CACHE: dict[str, tuple[float, dict]] = {}
_ALPACA_ORDERS_CACHE: dict[str, tuple[float, list]] = {}
_ALPACA_SNAP_CACHE: dict[str, tuple[float, dict]] = {}
```

딕셔너리 값의 첫 번째 원소가 `time.time()` 타임스탬프다.

```python
def fetch_market_price_bundle(symbol, limit=300, timeframe="15Min"):
    cache_key = f"{symbol.upper()}:{timeframe}:{int(limit)}"
    now_ts = time.time()
    cached = _PRICE_CACHE.get(cache_key)
    if cached and now_ts - cached[0] < 300:   # 5분 TTL
        return cached[1]

    # 캐시 미스 → 실제 fetch
    bundle = _fetch_fresh(symbol, limit, timeframe)
    _PRICE_CACHE[cache_key] = (now_ts, bundle)
    return bundle
```

캐시 키는 `심볼:타임프레임:limit`이다. 같은 심볼이라도 타임프레임이나 limit이 다르면 별도로 캐시한다.

TTL은 데이터 종류마다 다르게 설정한다.
- 가격 데이터: 5분 (15분봉 기준으로 충분히 신선)
- Alpaca 주문 목록: 30초 (빠르게 바뀔 수 있음)
- allowlist CSV: 파일 수정 시각 기반 (mtime이 바뀌지 않으면 캐시 유지)

allowlist 캐시는 TTL 대신 파일 mtime을 비교한다.

```python
def _load_allowlist_enabled(path):
    mtime = p.stat().st_mtime
    cached = _ALLOWLIST_CACHE.get(key)
    if cached and cached[0] >= mtime:   # mtime이 안 바뀌면 캐시 유효
        return cached[1]
    ...
    _ALLOWLIST_CACHE[key] = (mtime, out)
    return out
```

CSV 파일이 수정되지 않으면 계속 캐시에서 읽는다. 시간 기반 TTL이면 파일이 바뀌지 않아도 주기적으로 재읽는 낭비가 생긴다.

## Alpaca → Yahoo 가격 폴백

실시간 가격은 Alpaca IEX 피드를 쓰다가 최신 봉이 2시간 이상 지났으면 Yahoo로 전환한다.

```python
def fetch_market_price_bundle(symbol, limit=300, timeframe="15Min"):
    alp = _fetch_market_price_series_alpaca(symbol, limit, timeframe)
    alp_age = _points_age_minutes(alp)
    use_yahoo = (not alp) or (alp_age is None) or (alp_age > 120.0)

    source = "alpaca_iex"
    points = alp
    if use_yahoo:
        y = _fetch_market_price_series_yahoo(symbol, limit)
        y_age = _points_age_minutes(y)
        if y and (not alp or (y_age is not None and (alp_age is None or y_age < alp_age))):
            points = y
            source = "yahoo_prepost"
    ...
```

`_points_age_minutes`는 마지막 봉 타임스탬프와 현재 시각의 차이를 분으로 반환한다. Alpaca 봉이 없거나 120분 이상 오래됐으면 Yahoo로 넘어간다. Yahoo에서도 봉을 가져왔을 때 Alpaca보다 더 최신이면 Yahoo를 쓴다.

Yahoo `yf.download`의 멀티인덱스 컬럼 처리는 데이터 파이프라인과 동일하다.

```python
if isinstance(df.columns, pd.MultiIndex):
    df.columns = [c[0] for c in df.columns]
```

`prepost=True`로 프리마켓/애프터마켓 데이터도 포함한다. 대시보드에서 장 외 시간에도 최신 가격을 볼 수 있다.

## CSV 실시간 로그 파싱

봇은 매 봉마다 상태를 CSV에 append한다. 대시보드가 이 CSV를 읽어서 상태를 표시한다. 문제는 봇과 대시보드가 동시에 파일에 접근한다는 점이다.

봇이 한 행을 쓰는 중간에 대시보드가 읽으면 마지막 행이 잘려 있을 수 있다. `pandas.read_csv`가 파싱 오류를 낸다.

```python
def safe_read_csv(path):
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame()
    try:
        return pd.read_csv(path, dtype=str, low_memory=False)
    except Exception:
        try:
            return pd.read_csv(
                path, dtype=str, low_memory=False,
                engine="python",
                on_bad_lines="skip",    # 파싱 오류 행 스킵
            )
        except Exception:
            return pd.DataFrame()
```

첫 시도가 실패하면 `on_bad_lines="skip"`으로 재시도한다. 잘린 행만 버리고 나머지를 읽는다. `dtype=str`로 전체를 문자열로 읽어서 타입 추론 오류도 피한다.

봇이 컬럼을 추가하면서 스키마가 바뀌는 경우도 있다. 오래된 로그 파일에는 새 컬럼이 없고 최신 행에는 있다. `dtype=str` + `on_bad_lines="skip"` 조합이면 스키마가 섞인 파일도 안전하게 읽힌다.

## 동적 심볼/전략 목록 발견

대시보드를 띄울 때 어떤 심볼과 전략이 활성화돼 있는지를 로그에서 자동으로 파악한다.

```python
def discover_options(engine_log, monitor_log, legacy_signal_files, default_symbol, state_file):
    eng = safe_read_csv(engine_log)
    mon = safe_read_csv(monitor_log)
    state_obj = safe_read_json(state_file)

    symbols: set[str] = set()
    # 엔진 로그의 targets 컬럼에서 심볼 추출
    symbols.update(_extract_symbols_from_targets(eng))
    # monitor CSV에서 추출
    if not mon.empty and "symbol" in mon.columns:
        for s in mon["symbol"].dropna().tail(500):
            symbols.add(str(s).upper())
    # 런타임 상태 파일에서 추출
    for s in to_list_of_str(state_obj.get("runtime_symbols", [])):
        symbols.add(str(s).upper())
    ...
```

세 소스에서 심볼을 모은다. 봇 설정 파일을 읽는 게 아니라 실제 로그에 기록된 심볼만 포함한다. 봇이 새 심볼을 추가하면 대시보드를 재시작하지 않아도 다음 폴링 사이클에서 자동으로 목록에 나타난다.

## 체인 전환 이벤트 추출

엔진 로그에서 전략이나 체인이 바뀐 시점을 이벤트로 추출한다.

```python
def build_strategy_switch_events(eng, selected_strategy="ALL"):
    prev_active = None
    prev_chain = None
    out = []
    for _, r in eng.tail(500).iterrows():
        active = str(r.get("active_strategies", "") or "")
        chain = str(r.get("chain_name", "") or "")
        if active != prev_active or chain != prev_chain:
            out.append({
                "time": str(r.get("time", "")),
                "event": "strategy_switch",
                "note": f"chain={chain} active={active}",
                ...
            })
            prev_active = active
            prev_chain = chain
    return out
```

로그를 순서대로 읽으면서 `active_strategies`나 `chain_name`이 바뀌는 순간만 기록한다. 이 이벤트를 차트 위에 수직선으로 표시하면 "이 구간에서 defensive 체인으로 전환됐고 그 이후 포지션이 줄었다"를 한눈에 볼 수 있다.

## 실제 포지션 비율 계산

봇이 기록하는 `position_qty`는 주식 수다. 계좌 대비 실제 비율은 현재가와 equity가 있어야 계산된다.

```python
def build_actual_weight_series(mon, price_points):
    px_values = pd.Series(
        [x[1] for x in px_rows],
        index=pd.DatetimeIndex([x[0] for x in px_rows])
    ).sort_index()

    out = []
    for _, r in mon.tail(500).iterrows():
        ts = parse_time(r.get("time"))
        qty = to_float_or_none(r.get("position_qty"))
        eq = to_float_or_none(r.get("equity"))
        if qty is None or eq is None or eq == 0.0:
            continue
        px = px_values.asof(ts)   # 해당 시각 이전 가장 최근 가격
        w = float((qty * float(px)) / eq)
        out.append({"t": str(r.get("time")), "v": w})
    return out
```

`pd.Series.asof(ts)`는 주어진 타임스탬프 이전의 가장 최근 값을 반환한다. 모니터 로그의 타임스탬프와 가격 시리즈의 타임스탬프가 정확히 일치하지 않아도 올바른 가격을 찾는다.

실제 포지션 비율 = `qty × price / equity`다. 목표 비율(`target`)과 실제 비율(`actual`)을 나란히 차트에 그리면 목표와 실제가 얼마나 빠르게 수렴하는지 볼 수 있다.
