---
layout: post
title: 한국 시장 레짐 분류기 — Transformer 단일 헤드 정정 확률 예측
subtitle: US 멀티태스크와의 구조 비교, 일봉 기반 binary 분류, 15분봉 리샘플링 데이터 번들까지
author: HyeongJin
date: 2026-04-12 10:00:00 +0900
categories: AI/LLM
tags: [Python, PyTorch, Trading, MachineLearning, Transformer]
sidebar: []
published: true
---

이전에 만든 US Transformer는 멀티태스크다. 레짐 분류(3-class)와 고변동성 예측(binary)을 하나의 인코더로 처리하고 심볼·섹터 임베딩을 달았다. 한국 시장 버전은 구조를 단순하게 가져갔다. 이유는 두 가지다.

1. 한국 레짐 분류의 목적이 다르다. "지금 추세 상승/하락/횡보"가 아니라 "근미래에 시장이 조정을 받을 가능성"이다.
2. 심볼별 특성 차이가 미국보다 좁다. KOSPI 구성 종목은 S&P500 대비 섹터 분산이 작고 외국인 수급이라는 공통 변수에 강하게 묶인다.

결과적으로 단일 이진 출력(`correction_prob`)을 내는 단순한 구조가 됐다.

## 모델 구조

```python
class KRTransformerModel(nn.Module):
    def __init__(self, input_dim: int, cfg: KRTransformerConfig) -> None:
        super().__init__()
        self.proj = nn.Linear(input_dim, cfg.d_model)
        enc_layer = nn.TransformerEncoderLayer(
            d_model=cfg.d_model,        # 64
            nhead=cfg.nhead,            # 4
            dim_feedforward=cfg.d_model * 4,
            dropout=cfg.dropout,        # 0.1
            batch_first=True,
        )
        self.encoder = nn.TransformerEncoder(enc_layer, num_layers=cfg.num_layers)  # 2
        self.norm = nn.LayerNorm(cfg.d_model)
        self.head = nn.Linear(cfg.d_model, 1)
```

US 버전과 비교하면:

| | US Transformer | KR Transformer |
|--|--|--|
| 헤드 수 | 2 (regime 3-class + high_vol binary) | 1 (correction binary) |
| 임베딩 | symbol + sector | 없음 |
| 손실 함수 | 0.7 × CE + 0.3 × BCE | BCEWithLogitsLoss |
| 입력 데이터 | 15분봉 (장중) | 일봉 |

`forward`는 단순하다.

```python
def forward(self, x: torch.Tensor) -> torch.Tensor:
    h = self.proj(x)
    h = self.encoder(h)
    h = self.norm(h[:, -1, :])
    return self.head(h).squeeze(-1)   # (B,) logit
```

출력이 단일 logit이라 sigmoid를 씌우면 바로 확률이 된다. 심볼 임베딩이 없으니 입력 피처만 맞으면 어느 종목 시계열에나 적용할 수 있다.

## 시퀀스 빌드

US 버전은 심볼별로 그룹을 나눠서 각각 슬라이딩 윈도우를 만들었다. KR 버전은 단일 시리즈라 더 단순하다.

```python
def build_sequences(df, feature_cols, target_col, seq_len):
    x = df[list(feature_cols)].astype(float).values
    y = df[target_col].astype(float).values
    idx = df.index

    xs, ys, ts = [], [], []
    for i in range(seq_len, len(df)):
        xs.append(x[i - seq_len : i])
        ys.append(y[i])
        ts.append(idx[i])

    return np.asarray(xs, dtype=np.float32), np.asarray(ys, dtype=np.float32), pd.Index(ts)
```

`target_col`은 미래 N봉 내 최대 낙폭이 임계값 이하면 1인 이진 레이블이다. 일봉 기준으로 5~10일 선행 레이블을 쓴다.

## 학습

```python
def train_kr_transformer(train_df, valid_df, feature_cols, target_col, cfg):
    x_tr, y_tr, _ = build_sequences(train_df, feature_cols, target_col, cfg.seq_len)
    x_va, y_va, _ = build_sequences(valid_df, feature_cols, target_col, cfg.seq_len)

    tr_loader = DataLoader(TensorDataset(torch.from_numpy(x_tr), torch.from_numpy(y_tr)),
                           batch_size=cfg.batch_size, shuffle=True)

    model = KRTransformerModel(input_dim=len(feature_cols), cfg=cfg).to(cfg.device)
    opt = torch.optim.AdamW(model.parameters(), lr=cfg.lr)
    loss_fn = nn.BCEWithLogitsLoss()

    best_valid = 1e18
    best_state = None

    for _ in range(cfg.epochs):
        model.train()
        for xb, yb in tr_loader:
            opt.zero_grad()
            loss = loss_fn(model(xb.to(cfg.device)), yb.to(cfg.device))
            loss.backward()
            opt.step()

        model.eval()
        with torch.no_grad():
            losses = [float(loss_fn(model(xb.to(cfg.device)), yb.to(cfg.device)).cpu())
                      for xb, yb in va_loader]
        valid_loss = float(np.mean(losses))
        if valid_loss < best_valid:
            best_valid = valid_loss
            best_state = {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}

    if best_state:
        model.load_state_dict(best_state)
    return model, {"valid_loss": best_valid}
```

Early stopping 없이 전 에포크 돌고 best validation loss 상태를 복원한다. 에포크가 30개로 짧아서 과최적화 위험이 낮다.

`BCEWithLogitsLoss`는 내부적으로 sigmoid + binary cross entropy를 수치적으로 안정하게 합친 것이다. 출력 레이어를 sigmoid로 끝내지 않아도 되고 수치 안정성도 높다.

## 추론

```python
def predict_probs(model, df, feature_cols, seq_len, device="cpu"):
    x, _, ts = build_sequences(df.assign(_dummy=0), feature_cols, "_dummy", seq_len)
    if len(x) == 0:
        return pd.Series(dtype=float)

    model.eval()
    with torch.no_grad():
        logits = model(torch.from_numpy(x).to(device)).cpu().numpy()
    probs = 1.0 / (1.0 + np.exp(-logits))
    return pd.Series(probs, index=ts, name="correction_prob")
```

레이블이 필요 없는 추론 시에는 `df.assign(_dummy=0)`으로 더미 타깃 컬럼을 끼워 넣어서 `build_sequences` 시그니처를 재활용한다. 출력은 인덱스가 달린 `correction_prob` 시리즈다.

## 체크포인트

```python
def save_checkpoint(model, cfg, feature_cols, path):
    torch.save({
        "state_dict": model.state_dict(),
        "cfg":         cfg.__dict__,
        "feature_cols": list(feature_cols),
    }, path)

def load_checkpoint(path, device="cpu"):
    ckpt = torch.load(path, map_location=device)
    cfg = KRTransformerConfig(**ckpt["cfg"])
    feature_cols = list(ckpt["feature_cols"])
    model = KRTransformerModel(input_dim=len(feature_cols), cfg=cfg)
    model.load_state_dict(ckpt["state_dict"])
    model.eval()
    return model, cfg, feature_cols
```

US 버전은 체크포인트에 symbol_id_map, sector_id_map, scaler까지 함께 저장했다. KR 버전은 임베딩이 없으니 `feature_cols`만 저장하면 된다. 로드 시 `cfg.__dict__`로 저장해 둔 하이퍼파라미터로 모델을 재구성하고 가중치를 복원한다.

## 데이터 번들

KR Transformer에 넣을 일봉 피처를 준비하는 과정에서 `MarketDataBundle`을 쓴다. 미국 시장용으로 설계했지만 15분봉을 60분봉으로 리샘플링하는 패턴이 한국 데이터에도 동일하게 적용된다.

```python
def _resample_60m(df_15m: pd.DataFrame) -> pd.DataFrame:
    out = pd.DataFrame()
    out["open"]   = df_15m["open"].resample("60min").first()
    out["high"]   = df_15m["high"].resample("60min").max()
    out["low"]    = df_15m["low"].resample("60min").min()
    out["close"]  = df_15m["close"].resample("60min").last()
    out["volume"] = df_15m["volume"].resample("60min").sum()
    return out.dropna(subset=["close"])
```

Pandas `resample`의 집계 규칙: OHLCV를 60분 단위로 묶을 때 open은 첫 봉, high/low는 max/min, close는 마지막 봉, volume은 합산이다. 이 네 규칙을 틀리면 백테스트와 라이브 추론의 OHLCV 값이 달라진다.

```python
@dataclass(frozen=True)
class MarketDataBundle:
    symbol: str
    df_15m: pd.DataFrame
    df_60m: pd.DataFrame
    adr_15m: pd.Series
    adr_60m: pd.Series
```

`load_market_bundle`은 심볼 하나에 대해 15분봉과 60분봉, 그리고 ADR 시리즈를 묶어서 반환한다. 60분봉은 15분봉에서 리샘플링하므로 소스가 하나다. ADR 유니버스 종목도 동시에 fetch해서 메인 심볼과 타임라인을 맞춘다.

```python
def load_market_bundle(symbol, start, end, adr_universe, prefer_alpaca=True):
    universe = list(dict.fromkeys([symbol, *list(adr_universe)]))
    bars = {s: fetch_symbol_bars(s, start, end, prefer_alpaca) for s in universe}

    df_15m = bars[symbol].copy().sort_index()
    df_60m = _resample_60m(df_15m)
    adr_15m = build_adr_proxy(bars, universe, timeframe="15m")
    bars_60 = {k: _resample_60m(v) for k, v in bars.items()}
    adr_60m = build_adr_proxy(bars_60, universe, timeframe="60m")

    return MarketDataBundle(symbol=symbol, df_15m=df_15m, df_60m=df_60m,
                            adr_15m=adr_15m, adr_60m=adr_60m)
```

`dict.fromkeys`로 심볼 중복 제거 시 순서를 유지한다. 메인 심볼이 ADR 유니버스에 포함되어 있어도 한 번만 fetch된다.

## correction_prob의 역할

`correction_prob`는 체인 라우터의 defensive 체인 판단에 입력으로 들어간다. US Transformer의 `p_trend_down`이나 `p_high_vol`과 유사한 역할이지만 한국 시장에 특화된 신호다.

```
correction_prob >= 0.65  →  defensive 체인 트리거 강화
correction_prob < 0.35   →  trend 체인 진입 조건 완화
```

두 모델을 분리한 이유는 학습 데이터 특성이 다르기 때문이다. US 모델은 S&P500 구성 종목 일중 15분봉으로 학습하고, KR 모델은 KOSPI/KOSDAQ 일봉으로 학습한다. 동일한 모델 구조에 다른 데이터를 섞으면 두 시장의 레짐 신호가 희석된다. 분리해서 각각 fine-tuning하는 편이 정확도가 높다.
