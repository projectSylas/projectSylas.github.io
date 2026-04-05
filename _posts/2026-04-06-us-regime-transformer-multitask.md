---
layout: post
title: Transformer로 시장 레짐 분류하기 — 심볼 임베딩 + 이중 헤드 멀티태스크
subtitle: VIX z-score·EMA 스프레드·ADR로 피처 설계, trend/chop/고변동성을 동시에 예측하는 PyTorch 모델
author: HyeongJin
date: 2026-04-06 10:00:00 +0900
categories: AI/LLM
tags: [Python, PyTorch, Trading, MachineLearning, Transformer]
sidebar: []
published: true
---

규칙 기반 레짐 감지(이동평균 교차, VIX 임계값)는 파라미터를 수동으로 튜닝해야 하고 시장 환경이 바뀌면 다시 맞춰야 한다. Transformer 인코더로 레짐을 학습하게 하면 피처 조합의 비선형 패턴을 직접 학습한다.

목표는 두 가지다.

1. 현재가 **추세 상승 / 추세 하락 / 횡보** 중 어디인가 (3-class)
2. 향후 변동성이 높을 것인가 (binary)

두 태스크를 하나의 인코더로 처리하는 멀티태스크 구조를 만들었다.

## 피처 설계

```python
FEATURE_COLS = [
    "ret_1",          # 1봉 수익률
    "ret_4",          # 4봉 수익률
    "ret_8",          # 8봉 수익률
    "vol_20",         # 20봉 변동성
    "vol_60",         # 60봉 변동성
    "atr_14_norm",    # ATR / 현재가 (정규화)
    "ema_spread_20_60",  # (EMA20 - EMA60) / 현재가
    "adr_z",          # ADR z-score
    "vix_z",          # VIX z-score
    "vix_chg_5",      # VIX 5봉 변화율
]
```

`vix_z`와 `vix_chg_5`는 크로스-애셋 신호다. VIX가 자신의 최근 분포 대비 높은지, 급격히 움직이는지를 함께 본다. 일봉 VIX를 15분봉 인덱스에 맞게 forward fill한다.

```python
def _vix_intraday(vix_daily: pd.Series, idx: pd.Index) -> pd.Series:
    vix = vix_daily.copy()
    vix.index = pd.to_datetime(vix.index, utc=True).tz_convert(None).normalize()
    target_dates = pd.DatetimeIndex(pd.to_datetime(idx)).tz_convert(None).normalize()
    vi = vix.reindex(target_dates, method="ffill")
    vi.index = idx
    return vi.astype(float)
```

`adr_z`는 ADR(Average Daily Range)의 log z-score다. 종목 특유의 변동성 수준이 최근 120봉 대비 얼마나 높은지 나타낸다.

```python
adr = adr_15m.reindex(d.index).ffill().fillna(1.0)
out["adr_z"] = _zscore(np.log(adr.clip(lower=1e-6)), 120)
```

## 레이블 구성

레짐 레이블은 미래 N봉(기본 20봉) 수익률로 만든다. 데이터셋 빌드 시에만 쓰이고 실시간 추론에는 불필요하다.

```python
horizon_bars = 20
trend_threshold = 0.0035  # 0.35%

fwd_ret = close.shift(-horizon_bars) / close - 1.0
out["trend_up"]   = (fwd_ret >= trend_threshold).astype(float)
out["trend_down"] = (fwd_ret <= -trend_threshold).astype(float)
out["chop"]       = ((out["trend_up"] == 0.0) & (out["trend_down"] == 0.0)).astype(float)
```

세 클래스는 상호 배타적이고 합이 1이다. 정합성 체크를 명시적으로 한다.

```python
out = out[(out["trend_up"] + out["trend_down"] + out["chop"]).between(0.99, 1.01)]
```

고변동성 레이블은 미래 변동성의 상위 80%ile로 분류한다.

```python
fut_vol = ret_1.shift(-1).rolling(horizon_bars).std().shift(-(horizon_bars - 1))
hv_cut = fut_vol.quantile(0.80)
out["high_vol"] = (fut_vol >= hv_cut).astype(float)
```

## 모델 구조

```python
class USTransformerModel(nn.Module):
    def __init__(self, input_dim: int, cfg: USTransformerConfig) -> None:
        super().__init__()
        self.num_proj = nn.Linear(input_dim, cfg.d_model)
        self.sym_emb  = nn.Embedding(cfg.symbol_vocab, cfg.embed_dim)
        self.sec_emb  = nn.Embedding(cfg.sector_vocab, cfg.embed_dim)
        self.fuse     = nn.Linear(cfg.d_model + cfg.embed_dim * 2, cfg.d_model)

        enc_layer = nn.TransformerEncoderLayer(
            d_model=cfg.d_model,        # 64
            nhead=cfg.nhead,            # 4
            dim_feedforward=cfg.d_model * 4,
            dropout=cfg.dropout,        # 0.1
            batch_first=True,
        )
        self.encoder = nn.TransformerEncoder(enc_layer, num_layers=cfg.num_layers)  # 2
        self.norm = nn.LayerNorm(cfg.d_model)

        self.head_regime = nn.Linear(cfg.d_model, 3)   # up/down/chop
        self.head_hv     = nn.Linear(cfg.d_model, 1)   # 고변동성
```

심볼 ID와 섹터 ID를 임베딩해서 수치 피처와 합친다. 같은 피처값이어도 심볼마다 해석이 다를 수 있다는 가정이다 — tech 종목과 utility 종목의 `ema_spread_20_60`이 같은 의미가 아니다.

```python
def forward(self, x, symbol_id, sector_id):
    h = self.num_proj(x)                                # (B, T, d_model)
    s = self.sym_emb(symbol_id).unsqueeze(1).expand(-1, x.shape[1], -1)
    c = self.sec_emb(sector_id).unsqueeze(1).expand(-1, x.shape[1], -1)
    h = self.fuse(torch.cat([h, s, c], dim=-1))         # 피처 + 임베딩 통합
    h = self.encoder(h)
    h = self.norm(h[:, -1, :])                          # 마지막 타임스텝만 사용
    return self.head_regime(h), self.head_hv(h).squeeze(-1)
```

인코더 출력의 마지막 타임스텝(`h[:, -1, :]`)을 분류 헤드에 넣는다. BERT의 [CLS] 토큰 대신 마지막 위치를 사용하는 GPT 스타일이다.

## 시퀀스 빌드

심볼별로 그룹을 나눠서 슬라이딩 윈도우로 (seq_len=32) 시퀀스를 만든다.

```python
for _, g in df.sort_index().groupby("symbol", sort=False):
    gg = g.sort_index()
    if len(gg) <= seq_len:
        continue
    x_all = gg[list(feature_cols)].astype(float).values

    for i in range(seq_len, len(gg)):
        xs.append(x_all[i - seq_len : i])
        syms.append(int(sid[i]))
        secs.append(int(cid[i]))
        # 레이블: i 위치의 미래 레짐
        cls = int(np.argmax([yup[i], ydn[i], ycp[i]]))
        ys_reg.append(cls)
        ys_hv.append(float(yhv[i]))
```

심볼 경계를 넘어서 시퀀스를 만들지 않는다. 심볼별로 독립적으로 슬라이딩한다.

## 학습

```python
loss = 0.7 * ce(logit_reg, yb_reg) + 0.3 * bce(logit_hv, yb_hv)
```

레짐 분류(CrossEntropy)에 0.7, 고변동성 예측(BCEWithLogits)에 0.3 가중치를 뒀다. 레짐 분류가 더 중요한 태스크라 가중치를 높였다. 두 태스크의 그래디언트가 인코더를 공유하면서 서로 regularization 효과를 준다.

Early stopping은 validation loss 기준이다.

```python
if valid_loss < best_valid:
    best_valid = valid_loss
    best_state = {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}
    no_improve = 0
else:
    no_improve += 1
    if no_improve >= cfg.patience:  # patience=5
        break

if best_state is not None:
    model.load_state_dict(best_state)
```

best epoch 상태를 복원해서 최종 모델로 쓴다.

## 추론

```python
def predict_regime_probs(model, df, feature_cols, seq_len, scaler, device="cpu"):
    xdf = apply_standard_scaler(df.copy(), feature_cols, scaler)
    x, sym, sec, _, _, ts = build_us_sequences(xdf, feature_cols, seq_len, require_labels=False)

    model.eval()
    with torch.no_grad():
        logit_reg, logit_hv = model(
            torch.from_numpy(x).to(device),
            torch.from_numpy(sym).to(device),
            torch.from_numpy(sec).to(device),
        )
        reg_p = torch.softmax(logit_reg, dim=1).cpu().numpy()
        hv_p  = torch.sigmoid(logit_hv).cpu().numpy()

    return pd.DataFrame({
        "p_trend_up":   reg_p[:, 0],
        "p_trend_down": reg_p[:, 1],
        "p_chop":       reg_p[:, 2],
        "p_high_vol":   hv_p,
    }, index=ts)
```

출력은 각 바에 대한 확률이다. `p_trend_up > 0.6` 이면 추세 상승으로 분류하는 식으로 전략에서 필터링 조건으로 쓴다.

## 체크포인트 저장

모델 상태 외에 scaler, feature_cols, symbol/sector 매핑을 함께 저장한다. 추론 환경에서 학습 환경과 동일한 전처리를 재현하기 위해서다.

```python
torch.save({
    "state_dict":     model.state_dict(),
    "cfg":            cfg.__dict__,
    "feature_cols":   list(feature_cols),
    "symbol_id_map":  {str(k): int(v) for k, v in symbol_id_map.items()},
    "sector_id_map":  {str(k): int(v) for k, v in sector_id_map.items()},
    "scaler":         scaler,
    "model_version":  "us_transformer_v1",
}, path)
```

로드 시 `USTransformerConfig`를 dict에서 재구성하고, symbol/sector vocab 크기를 실제 데이터에 맞게 조정한다.

```python
ckpt = torch.load(path, map_location=device)
cfg  = USTransformerConfig(**ckpt["cfg"])
model = USTransformerModel(input_dim=len(feature_cols), cfg=cfg)
model.load_state_dict(ckpt["state_dict"])
model.eval()
```

## 설계상 선택 이유

**왜 Transformer인가**: 시계열에서 어떤 과거 바가 현재 레짐에 영향을 주는지 attention이 직접 학습한다. EMA 같은 수동 집계와 달리 거리 가중치를 데이터로 배운다.

**왜 마지막 타임스텝만 쓰나**: 레짐은 "지금 어떤 상태인가"를 맞추는 태스크다. 시퀀스 전체를 평균하면 과거 정보에 희석된다. `h[:, -1, :]`이 가장 직관적이다.

**왜 심볼 임베딩인가**: 종목마다 베타, 유동성, 섹터 특성이 다르다. 같은 VIX z-score가 tech 대형주와 소형 바이오에 다른 의미를 갖는다. 임베딩이 이 차이를 흡수한다.
