---
layout: post
title: TimesFM + FinBERT로 AI 자동매매 시스템 만들기
subtitle: 가격 예측 모델과 뉴스 감성 분석을 결합한 자동화 트레이딩 실험
author: HyeongJin
date: 2025-04-28 18:00:00 +0900
categories: AI/LLM
tags: [Python, AI, MachineLearning, backend]
sidebar: []
published: true
---

자동매매 시스템을 한번 만들어보고 싶었다. 규칙 기반 전략만으로는 시장 변화에 대응하기 어렵고, AI 모델을 붙이면 어느 정도 보완이 될 것 같았다.

실제 주문 연동보다는 파이프라인 설계와 AI 모델 실험에 집중했다. Backtrader로 백테스팅을 돌려서 전략을 검증하는 구조까지 만드는 게 목표였다.

## 전체 구조

```
가격 데이터 수집 (yfinance/CCXT)
    ↓
TimesFM / LSTM 가격 예측
    ↓
FinBERT 뉴스 감성 분석
    ↓
신호 결합 → 매매 판단
    ↓
Backtrader 백테스팅
```

## 가격 예측 — TimesFM

Google이 공개한 TimesFM은 시계열 데이터에 특화된 파운데이션 모델이다. 기존 LSTM보다 zero-shot 예측 성능이 좋다는 평을 보고 실험해봤다.

```python
from src.ai_models.price_predictor import PricePredictor

predictor = PricePredictor(model_type="timesfm")
forecast = predictor.predict(
    ticker="BTC-USD",
    lookback_days=30,
    forecast_horizon=5
)
```

LSTM도 같이 구현했다. TimesFM은 모델 로딩 자체가 무거워서 로컬 환경에서 LSTM이 더 빠른 경우가 있었다.

## 감성 분석 — FinBERT

뉴스 헤드라인에서 시장 심리를 읽는 데 FinBERT를 썼다. 금융 도메인에 특화된 BERT 계열 모델이라 일반 감성 모델보다 정확하다.

```python
from transformers import pipeline

sentiment_pipeline = pipeline(
    "sentiment-analysis",
    model="ProsusAI/finbert"
)

headlines = fetch_news_headlines(ticker="AAPL", limit=10)
results = sentiment_pipeline(headlines)

# positive/negative/neutral 비율 계산
score = sum(
    1 if r["label"] == "positive" else -1 if r["label"] == "negative" else 0
    for r in results
) / len(results)
```

감성 점수를 -1~1 스케일로 정규화해서 가격 예측 신호와 결합했다.

## Backtrader 전략 검증

실제 주문 전에 Backtrader로 백테스팅을 먼저 돌렸다.

```python
import backtrader as bt

class ChallengeStrategy(bt.Strategy):
    params = dict(
        rsi_period=14,
        rsi_oversold=30,
        ai_weight=0.4,
        sentiment_weight=0.3,
    )

    def next(self):
        rsi = self.rsi[0]
        ai_signal = self.p.ai_weight * self.ai_forecast
        sentiment_signal = self.p.sentiment_weight * self.sentiment_score

        combined = ai_signal + sentiment_signal
        if rsi < self.p.rsi_oversold and combined > 0.5:
            self.buy()
        elif combined < -0.5:
            self.sell()
```

RSI 기반 기술적 분석과 AI 신호를 조합하는 방식. 파라미터 최적화까지 돌렸는데 모든 조합에서 거래가 발생하지 않는 문제가 있어서 전략 조건을 더 완화해야 했다.

## 실험하면서 배운 것

TimesFM은 예측 성능이 좋지만 모델 크기가 커서 서버 스펙이 받쳐줘야 한다. FinBERT는 영문 뉴스에 최적화돼 있어서 한국어 경제 기사에는 번역을 거쳐야 의미 있는 결과가 나왔다.

가장 까다로운 건 두 신호를 어떻게 결합하냐였다. 단순 가중합보다 동적 가중치를 쓰는 게 더 나을 것 같아서 RL 에이전트로 가중치 자체를 학습시키는 방향도 실험했다.

실제 주문 연동은 아직 안 됐다. 백테스팅에서 전략을 더 검증하고 나서 연동할 계획이다.
