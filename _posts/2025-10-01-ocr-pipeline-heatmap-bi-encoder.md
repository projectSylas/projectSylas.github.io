---
layout: post
title: 영상 크레딧 OCR 파이프라인 — 히트맵 크롭 + Clova OCR + Bi-Encoder 추천
subtitle: HeatmapCropper로 크레딧 영역 추출, 다중 OCR 엔진 전처리, Bi-Encoder 파인튜닝
author: HyeongJin
date: 2025-10-01 10:00:00 +0900
categories: AI/LLM
tags: [Python, AI, backend]
sidebar: []
published: true
---

영상에서 스태프 크레딧을 자동으로 추출하는 파이프라인을 구축했다. 크레딧 구간의 프레임에서 텍스트 영역을 감지하고, OCR로 텍스트를 추출한 뒤, Bi-Encoder로 직군 매칭 추천까지 이어지는 end-to-end 구조다.

## 전체 파이프라인

```
영상 프레임 입력
    ↓
FilterPipeline (EAST 텍스트 감지 → 히트맵 생성)
    ↓
HeatmapCropper (히트맵 기반 크레딧 영역 크롭)
    ↓
OCRProcessor (Clova OCR / Tesseract)
    ↓
후처리 (텍스트 정제, 컬럼 클러스터링)
    ↓
Bi-Encoder (직군 분류 추천)
```

## HeatmapCropper — 히트맵 기반 영역 크롭

여러 프레임의 텍스트 감지 결과를 누적해서 히트맵을 만들고, 히트맵에서 크레딧이 몰려 있는 영역을 크롭한다.

```python
class HeatmapCropper(BaseCropper):
    def __init__(
        self,
        method: CropMethod,
        data_list: list[OCRImage],
        pipeline: FilterPipeline,
        *,
        threshold: int = 5,
        y_gap_threshold: int = 75,
        x_gap_threshold: int = 17,
        x_padding: int = 40,
        y_padding: int = 20,
        exclude_top_height: int = 0,  # 상단 로고/타이틀 영역 제외
    ):
        ...

    def run(self, output_dir: str) -> str:
        if self.method is CropMethod.GAP:
            paths = self._run_global_gap(self.data_list, self.pipeline, output_dir)
        elif self.method is CropMethod.BINARY:
            paths = self._run_per_frame_binary(self.data_list, self.pipeline, output_dir)

        output_path = os.path.join(output_dir, "..", "merged.png")
        self._merge_vertical(paths, output_path)
        return output_path
```

`GAP` 방식은 전체 프레임의 히트맵을 합산해서 한 번에 크롭한다. `BINARY` 방식은 프레임별로 이진화해서 크롭한다. 크레딧 롤 유형에 따라 방식을 선택한다.

```python
def _run_global_gap(self, ...):
    processed = pipeline.run(data_list)
    heatmaps = [d.heatmap for d in processed if d.heatmap is not None]

    # 여러 프레임 히트맵 누적
    heatmap = np.add.reduce(heatmaps)

    # 상단 영역 마스킹 (타이틀/로고 텍스트가 크레딧으로 잡히는 것 방지)
    if self.exclude_top_height > 0:
        heatmap[:self.exclude_top_height, :] = 0

    y_min, y_max, x_min, x_max = self._crop_range_gap_based(heatmap)

    # 크롭 후 세로로 병합
    for idx, data in enumerate(data_list):
        cropped = data.image[y_min:y_max, x_min:x_max]
        cv2.imwrite(f"{output_dir}/{idx:04d}.png", cropped)
```

`exclude_top_height`는 크레딧 상단에 나오는 영화 제목이나 로고가 텍스트로 잡혀서 노이즈가 되는 문제를 해결하기 위해 추가했다.

## OCR 엔진 — Clova vs Tesseract

OCR 엔진을 교체 가능하도록 설계했다. `OCREngineType` enum으로 선택한다.

```python
class OCRProcessor:
    def __init__(self, engine_type: OCREngineType = OCREngineType.CLOVA):
        self.engine_type = engine_type

    def _single_ocr(self, img_path: str) -> str:
        img = cv2.imread(img_path)
        match self.engine_type:
            case OCREngineType.CLOVA:
                return ClovaOCRModel().get_text(img=img)
            case OCREngineType.TESSERACT:
                return TesseractModel().get_text(img=img)

    def _postprocess(self, text: str) -> str:
        # 크레딧 텍스트 정제 — 특수문자 제거
        return (
            text.replace("  ", " ")
                .replace("|", "")
                .replace("#", "")
                .replace("•", " ")
                .replace("/", " ")
                .replace("-", "")
        )
```

한국어 크레딧에서는 Clova OCR이 Tesseract보다 정확도가 높았다. Tesseract는 오픈소스라 로컬 테스트용으로 유지했다.

## 컬러/흑백 판별

크레딧 배경이 컬러인지 흑백인지에 따라 전처리 방식이 달라진다. HSV 채도와 채널 차이로 판별한다.

```python
def _is_color_image(img: np.ndarray, *, std_thresh: float = 2.5, sat_thresh: float = 0.12) -> bool:
    # 1) RGB 채널 간 차이 — 채널이 비슷하면 흑백
    b, g, r = img[...,0].astype(float), img[...,1].astype(float), img[...,2].astype(float)
    std_mean = (np.std(r-g) + np.std(g-b) + np.std(r-b)) / 3.0
    channel_diff_is_color = std_mean > std_thresh

    # 2) HSV 채도 — 채도 높은 픽셀 비율
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    s = hsv[..., 1].astype(float) / 255.0
    sat_is_color = (s > sat_thresh).mean() > 0.01

    return bool(channel_diff_is_color or sat_is_color)
```

두 조건 중 하나라도 만족하면 컬러로 판단한다. 흑백 크레딧은 Otsu 이진화로 더 선명하게 처리했다.

## Bi-Encoder 파인튜닝 — 직군 추천

크레딧에서 추출한 직군 텍스트를 DB의 직군 코드와 매핑하는 추천 모델이다. Bi-Encoder(쌍둥이 인코더)로 쿼리-후보 임베딩을 학습했다.

```python
class BiEncoderCollator:
    def __init__(
        self,
        tok: PreTrainedTokenizerBase,
        pad_to_multiple_of: int | None = 8,
        dense_k: int = 0,   # Hard negative 수 (dense retrieval용)
        sparse_k: int = 0,  # Hard negative 수 (sparse retrieval용)
    ):
        self.tok = tok
        # multiprocessing.Value로 동적 변경 가능하도록
        self._dense_k = mp.Value("i", int(dense_k))
        self._sparse_k = mp.Value("i", int(sparse_k))
```

학습 중 Hard negative 비율을 동적으로 조정할 수 있도록 `multiprocessing.Value`로 설계했다. 학습 후반부에 Hard negative를 늘려 모델이 어려운 케이스를 더 잘 구분하도록 했다.

```python
def get_best_device() -> torch.device:
    # CUDA > MPS(Apple Silicon) > CPU 순으로 선택
    if torch.cuda.is_available():
        return torch.device("cuda")
    elif torch.backends.mps.is_available():
        return torch.device("mps")
    return torch.device("cpu")
```

Apple Silicon Mac에서도 MPS로 GPU 가속이 되도록 처리했다.

## 만들면서 겪은 것

히트맵 크롭에서 가장 어려운 부분은 크레딧 영역 경계를 정확히 잡는 것이었다. 프레임마다 크레딧 위치가 미묘하게 달라서, 단일 프레임 기준이 아닌 누적 히트맵 기준으로 잡는 방식이 훨씬 안정적이었다.

OCR 후처리도 중요했다. 특히 `|`, `·`, `/` 같은 구분자 문자가 이름이나 직군 사이에 섞여 들어오는 경우가 많았다. 후처리 규칙을 쌓아가면서 정확도를 높였다.

Bi-Encoder 학습에서 Hard negative 선택이 성능 향상에 핵심이었다. 랜덤 negative보다 Cosine similarity 상위 후보 중 오답인 것들을 Hard negative로 쓰면 모델이 훨씬 세밀하게 구분하는 것을 확인했다.
