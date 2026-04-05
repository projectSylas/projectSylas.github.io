---
layout: post
title: 라이브 트레이딩 리스크 오버레이 — Kill Switch, 오더 실패율, 익스포저 캡
subtitle: 일별/주별 손실 한도, 슬라이딩 윈도우 실패율, 섹터별 익스포저 비율 제어까지 계좌 레벨 안전장치
author: HyeongJin
date: 2026-04-07 10:00:00 +0900
categories: AI/LLM
tags: [Python, Trading, RiskManagement, LiveTrading]
sidebar: []
published: true
---

전략 레벨 stop loss와 별개로, 계좌 전체를 보호하는 리스크 오버레이가 필요하다. 전략이 여러 개 돌아갈 때 개별 전략 stop만으로는 전체 손실을 제어할 수 없다. 또 브로커 API 오류나 네트워크 장애가 연속으로 나면 잘못된 주문이 누적될 수 있다.

`RiskManager`는 세 가지를 본다.

1. 일별/주별 손실이 한도를 넘으면 kill switch
2. 주문 실패율이 임계치를 넘으면 kill switch
3. 포지션이 심볼/섹터/전체 한도를 초과하면 비율 조정

## Kill Switch 조건

```python
@dataclass
class RiskLimits:
    daily_loss_limit: float = -0.02        # 하루 -2%
    weekly_loss_limit: float = -0.05       # 주간 -5%
    failure_rate_limit: float = 0.20       # 5분 내 실패율 20%
    failure_window_minutes: int = 5
    min_orders_in_window_for_rate: int = 20
    consecutive_failures_limit: int = 3    # 연속 실패 3회
    absolute_failures_in_window_limit: int = 5  # 절대 실패 5건
```

### 손실 한도

```python
def mark_equity(self, ts: datetime, equity: float) -> None:
    day = ts.date()
    week = self._week_key(ts)

    if st.day_anchor is None or st.day_anchor.date() != day:
        st.day_anchor = ts
        st.day_start_equity = float(equity)

    if st.week_anchor is None or self._week_key(st.week_anchor) != week:
        st.week_anchor = ts
        st.week_start_equity = float(equity)

    day_pnl = float(equity / st.day_start_equity - 1.0)
    if day_pnl <= self.limits.daily_loss_limit:
        self._activate_kill_switch(ts, f"daily_loss_limit:{day_pnl:.4f}", "daily_loss_limit")

    week_pnl = float(equity / st.week_start_equity - 1.0)
    if week_pnl <= self.limits.weekly_loss_limit:
        self._activate_kill_switch(ts, f"weekly_loss_limit:{week_pnl:.4f}", "weekly_loss_limit")
```

봉이 들어올 때마다 `mark_equity`를 호출한다. 일/주 시작 기준 equity를 기억해 두고 현재 대비 낙폭을 계산한다. 날짜가 바뀌면 자동으로 앵커를 갱신한다.

### 주문 실패율

오더를 낼 때마다 결과를 기록한다. `deque`로 슬라이딩 윈도우를 관리한다.

```python
def record_order_result(self, ts: datetime, success: bool,
                        status_code: int | None = None,
                        error_type: str | None = None) -> None:
    self.state.order_events.append((ts, success, status_code, error_type))
    self._trim_order_events(ts)       # 윈도우 밖 이벤트 제거
    self._refresh_consecutive_failures()

    # 401/403은 즉시 kill switch (인증 오류는 일시적이 아님)
    if not success and status_code == 401:
        self._activate_kill_switch(ts, "fatal_order_error_status:401", "http_401")
        return
    if not success and status_code == 403 and error_type == "auth_error":
        self._activate_kill_switch(ts, "fatal_order_error_status:403", "http_403")
        return

    n, failures = self._failure_stats(ts)

    # 절대 실패 건수 초과
    if not success and failures >= self.limits.absolute_failures_in_window_limit:
        self._activate_kill_switch(ts, f"absolute_failures:{failures}", "absolute_failures")
        return

    # 연속 실패
    if not success and self.state.consecutive_failures >= self.limits.consecutive_failures_limit:
        self._activate_kill_switch(ts, f"consecutive_failures:{self.state.consecutive_failures}",
                                   "consecutive_failures")
        return

    # 비율 초과 (최소 20건 이상일 때만 평가)
    if n >= self.limits.min_orders_in_window_for_rate and n > 0:
        failure_rate = failures / n
        if failure_rate > self.limits.failure_rate_limit:
            self._activate_kill_switch(ts, f"order_failure_rate:{failure_rate:.4f}", "order_failure_rate")
```

`_trim_order_events`는 윈도우 밖 이벤트를 deque 앞에서 제거한다.

```python
def _trim_order_events(self, ts: datetime) -> None:
    cutoff = ts - timedelta(minutes=self.limits.failure_window_minutes)
    while self.state.order_events and self.state.order_events[0][0] < cutoff:
        self.state.order_events.popleft()
```

연속 실패 카운터는 최근 이벤트부터 역순으로 성공이 나올 때까지 실패를 센다.

```python
def _refresh_consecutive_failures(self) -> None:
    c = 0
    for _, ok, _, _ in reversed(self.state.order_events):
        if ok:
            break
        c += 1
    self.state.consecutive_failures = c
```

세 조건을 병렬로 본다.
- **비율**: 통계적 노이즈를 무시하려면 최소 20건이 필요
- **절대값**: 건수가 적어도 5건 연속 실패면 위험
- **연속**: 마지막 3건이 전부 실패하면 즉시 중단

## 자동 복구

kill switch가 걸려도 원인에 따라 자동 복구를 허용한다.

```python
def maybe_auto_recover(self, ts: datetime) -> bool:
    fatal_reason_codes = {"daily_loss_limit", "weekly_loss_limit", "http_401", "http_403"}
    if self.state.reason_code in fatal_reason_codes:
        return False   # 손실 한도 / 인증 오류는 수동 해제만

    cooldown = timedelta(minutes=self.limits.recover_cooldown_minutes)
    if ts - self.state.last_kill_switch_ts < cooldown:
        return False   # 5분 쿨다운

    n, failures = self._failure_stats(ts)
    self._refresh_consecutive_failures()
    if failures == 0 and self.state.consecutive_failures == 0:
        # 윈도우 내 실패가 사라졌으면 복구
        self.state.kill_switch = False
        ...
        return True
```

손실 한도와 인증 오류는 자동 복구 금지다. 운영자가 직접 확인하고 `clear_kill_switch()`를 호출해야 한다. 일시적인 주문 실패율 초과는 5분 쿨다운 후 윈도우에서 실패가 사라지면 자동 복구된다.

## 익스포저 캡

목표 포지션을 직접 실행하기 전에 `apply_exposure_caps`를 거친다.

```python
@dataclass
class RiskLimits:
    max_symbol_exposure: float = 0.15    # 종목당 최대 15%
    max_total_exposure: float = 0.60     # 전체 포지션 최대 60%
    max_sector_exposure: float = 0.20    # 섹터당 최대 20%
```

```python
def apply_exposure_caps(self, target_positions: dict[str, float]) -> dict[str, float]:
    # 1단계: 종목별 캡
    capped = {}
    for sym, w in target_positions.items():
        capped[sym] = max(-self.limits.max_symbol_exposure,
                          min(self.limits.max_symbol_exposure, float(w)))

    # 2단계: 섹터별 캡
    capped = self._apply_sector_caps(capped)

    # 3단계: 전체 총합 캡
    total = sum(abs(w) for w in capped.values())
    if total > self.limits.max_total_exposure and total > 0:
        scale = self.limits.max_total_exposure / total
        capped = {k: v * scale for k, v in capped.items()}

    return capped
```

섹터 캡은 종목별 캡 이후에 적용된다. 섹터 내 총 익스포저가 한도를 넘으면 섹터 내 모든 종목을 비율 축소한다.

```python
def _apply_sector_caps(self, capped: dict[str, float]) -> dict[str, float]:
    for sec, syms in by_sector.items():
        sec_cap = float(overrides.get(sec, self.limits.max_sector_exposure))
        gross = sum(abs(capped[sym]) for sym in syms)
        if gross > sec_cap and gross > 0:
            scale = sec_cap / gross
            for sym in syms:
                capped[sym] = capped[sym] * scale
    return capped
```

`sector_exposure_overrides`로 섹터별로 다른 한도를 줄 수 있다. 에너지 섹터는 10%, 기술 섹터는 30%처럼.

## 데이터 신선도 체크

주문 전에 시장 데이터가 최신인지 확인한다.

```python
def assess_data_freshness(
    now: datetime,
    last_15m: datetime | None,
    last_60m: datetime | None,
    stale_15m_bars: int = 2,
    stale_60m_bars: int = 1,
) -> FreshnessStatus:
    if last_15m is None or last_60m is None:
        return FreshnessStatus(status="missing", can_trade=False, reason="missing_bar_data")

    if now_utc - last_15m > timedelta(minutes=15 * stale_15m_bars):
        return FreshnessStatus(status="stale", can_trade=False, reason="stale_15m")

    if now_utc - last_60m > timedelta(minutes=60 * stale_60m_bars):
        return FreshnessStatus(status="stale", can_trade=False, reason="stale_60m")

    return FreshnessStatus(status="fresh", can_trade=True, reason="fresh")
```

15분봉은 2봉(30분), 60분봉은 1봉(60분) 이상 오지 않으면 stale로 판단한다. 데이터 피드가 끊겼을 때 오래된 신호로 주문이 나가는 걸 막는다.

US 정규장 여부도 별도로 체크한다.

```python
def is_us_regular_hours(now: datetime) -> bool:
    ts = now.astimezone(ZoneInfo("America/New_York"))
    if ts.weekday() >= 5:
        return False
    mins = ts.hour * 60 + ts.minute
    return 570 <= mins < 960  # 09:30 ~ 16:00
```

## 실제 적용 흐름

```
봉 수신
    ↓
mark_equity(ts, current_equity)   ← 손실 한도 체크
    ↓
assess_data_freshness(...)         ← 데이터 신선도 체크
    ↓
can_trade() == True?               ← kill switch 확인
    ↓
전략 신호 생성
    ↓
apply_exposure_caps(raw_positions) ← 익스포저 조정
    ↓
주문 실행
    ↓
record_order_result(ts, success, ...)  ← 실패율 업데이트
    ↓
maybe_auto_recover(ts)             ← 복구 가능 여부 확인
```

kill switch가 걸리면 신규 주문을 내지 않고 기존 포지션 청산 로직만 실행한다. `reason_code`를 로그에 남겨두면 kill switch가 왜 발동됐는지 사후에 추적할 수 있다.
