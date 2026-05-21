---
layout: post
title: 라이브 트레이딩에 RL 모델 배포 — 다단계 Fallback 설계
subtitle: 모델 미로드·observation 실패·예측 오류 각 시점에서 규칙 기반 전략으로 자동 전환
author: HyeongJin
date: 2026-05-21 10:00:00 +0900
categories: AI/LLM
tags: [Python, ReinforcementLearning, Trading, StableBaselines3, backend]
sidebar: []
published: true
---

RL 모델을 백테스팅 환경에서 훈련하는 것과 라이브 트레이딩에 실제로 배포하는 것은 다른 문제다. 훈련은 실패해도 재시도하면 되지만, 라이브에서는 모델이 없거나 예측이 실패했을 때 아무 행동도 안 하는 게 더 나쁠 수 있다.

이 구조에서는 RL 모델 실패 시 미리 작성해둔 규칙 기반 전략으로 자동으로 전환한다. 실패 지점이 세 군데인데 각각 다르게 처리한다.

## 실패 지점과 Fallback 흐름

```
run_step()
  ├─ 모델 없음 → fallback("RL model not loaded")
  ├─ observation 실패 → fallback("Failed to get valid RL observation")
  ├─ 예측 실패 → fallback("RL prediction error: ...")
  └─ 정상 → action 실행
```

`run_step()`은 매 스텝마다 호출되며, 각 실패 지점에서 `_trigger_conditional_fallback()`으로 위임한다.

```python
def run_step(self) -> None:
    if not self.model:
        self._trigger_conditional_fallback(reason="RL model not loaded")
        return

    observation = self._get_live_observation()
    if observation is None:
        self._trigger_conditional_fallback(reason="Failed to get valid RL observation")
        return

    try:
        action_raw, _states = self.model.predict(observation, deterministic=True)
        action = int(action_raw)
    except Exception as e:
        self._trigger_conditional_fallback(reason=f"RL prediction error: {e}")
        return

    # 정상: action 실행
    action_map = {0: "Hold", 1: "Buy", 2: "Sell"}
    ...
```

## Fallback — 규칙 기반 전략으로 전환

`_trigger_conditional_fallback()`은 별도로 작성해둔 규칙 기반 `check_entry_conditions()`를 호출한다. 이 함수는 RL 모델과 독립적으로 RSI, SMA 같은 기술 지표로 진입 조건을 판단한다.

```python
def _trigger_conditional_fallback(self, reason: str) -> None:
    if not conditional_strategy_available:
        logging.error("Conditional strategy not available. Cannot fallback.")
        return

    conditional_signal = check_entry_conditions(settings.CHALLENGE_SYMBOL)
    if not conditional_signal or 'side' not in conditional_signal:
        logging.info("Fallback: No conditional signal found.")
        return

    balance = get_futures_account_balance()
    ticker = get_symbol_ticker(settings.CHALLENGE_SYMBOL)
    current_price = float(ticker['price'])
    quantity = calculate_position_size(float(balance['availableBalance']), current_price)

    order_result = create_futures_order(
        symbol=settings.CHALLENGE_SYMBOL,
        side=conditional_signal['side'].upper(),
        quantity=quantity,
    )

    if order_result and order_result.get('orderId'):
        log_trade_to_db(
            strategy='challenge_fallback',
            symbol=settings.CHALLENGE_SYMBOL,
            side=conditional_signal['side'],
            quantity=quantity,
            entry_price=current_price,
            status='open_fallback',
            reason=f"Fallback: {reason}",
            order_id=order_result.get('orderId'),
        )
        send_slack_notification(
            f"⚠️ Fallback Trade Executed ({conditional_signal['side']})",
            f"Reason: {reason}",
            level="warning",
        )
```

fallback으로 체결된 거래는 `strategy='challenge_fallback'`으로 DB에 남긴다. 나중에 RL 체결과 fallback 체결을 구분해서 분석할 수 있다.

## Live Observation — 훈련 환경과 정규화 일치

RL 모델이 예측하려면 훈련 때 사용한 것과 동일한 형태의 observation이 필요하다. 훈련 환경(`TradingEnv`)에서 정규화한 방식 그대로 라이브 데이터에 적용해야 한다.

```python
def _get_live_observation(self) -> Optional[np.ndarray]:
    df = get_historical_data(settings.RL_ENV_SYMBOL, period="1d", interval="1m")
    if df is None or len(df) < 15:
        return None

    latest_price = df['Close'].iloc[-1]
    latest_rsi = calculate_rsi(df, window=14).iloc[-1]
    latest_sma = calculate_sma(df, window=7).iloc[-1]

    current_pnl = 0.0
    if self.current_position == 1 and self.entry_price > 0:
        current_pnl = (latest_price - self.entry_price) / self.entry_price

    # 훈련 환경과 동일한 정규화 방식 적용
    price_norm = (latest_price - df['Close'].mean()) / df['Close'].std()
    rsi_norm = (latest_rsi - 50) / 50           # RSI를 [-1, 1] 스케일로
    sma_norm = (latest_sma - df['Close'].mean()) / df['Close'].std()
    pnl_norm = np.clip(current_pnl * 10, -1, 1)

    state = np.array([
        price_norm, rsi_norm, sma_norm,
        float(self.current_position),
        pnl_norm,
    ], dtype=np.float32)

    if not np.all(np.isfinite(state)):
        return None  # NaN/Inf → fallback 트리거

    return state
```

정규화 방식이 훈련과 다르면 모델이 전혀 엉뚱한 action을 낸다. 훈련 환경의 scaler를 함께 저장해두고 라이브에서 불러오는 게 더 안전하다.

## 모델 로드 — 알고리즘 동적 선택

PPO, A2C, DDPG 중 어떤 모델을 썼는지 설정으로 결정한다.

```python
SB3_ALGORITHMS = {"PPO": PPO, "A2C": A2C, "DDPG": DDPG}

def load_model(self) -> None:
    AlgorithmClass = SB3_ALGORITHMS.get(settings.RL_ALGORITHM)
    if AlgorithmClass is None:
        self.model = None
        return

    if os.path.exists(settings.RL_MODEL_PATH):
        try:
            self.model = AlgorithmClass.load(settings.RL_MODEL_PATH)
        except Exception as e:
            logging.error(f"Model load failed: {e}")
            self.model = None
    else:
        self.model = None  # 파일 없으면 None → run_step에서 fallback
```

`self.model = None`이 fallback의 진입점이다. `run_step()`은 첫 줄에서 `if not self.model`로 체크하므로, 모델 파일이 없거나 손상돼도 시스템이 멈추지 않는다.

## 루프 실행

```python
def run_rl_trading_loop() -> None:
    strategy = RLTradingStrategy()
    # model 체크 불필요 — run_step이 내부에서 처리

    for i in range(5):
        try:
            strategy.run_step()
        except Exception as e:
            send_slack_notification("🚨 CRITICAL RL Loop Error", f"Step {i+1}: {e}", level="error")
            break
        time.sleep(10)
```

`run_step()` 하나가 모든 분기(정상 실행, 각 단계별 fallback)를 처리하므로 루프 코드는 단순하게 유지된다.
