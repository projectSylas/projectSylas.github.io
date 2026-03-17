---
layout: post
title: EAST 텍스트 감지 모델로 영상 엔딩 크레딧 구간 자동 감지하기
subtitle: OpenCV + EAST로 OTT 플랫폼별 엔딩 크레딧 시작 지점을 찾는 방법
author: HyeongJin
date: 2024-09-12 11:00:00 +0900
categories: Backend
tags: [Python, OpenCV, AI, backend]
sidebar: []
published: true
---

영화·드라마 엔딩 크레딧에서 스태프 정보를 뽑아야 했다. 크레딧이 언제 시작하는지를 먼저 찾아야 하는데, 사람이 직접 타임코드를 찍는 방식은 콘텐츠가 늘어날수록 현실적이지 않았다.

OTT 플랫폼(넷플릭스, 웨이브, 티빙, 디즈니+ 등)마다 플레이어가 다르고, 크레딧 구간을 자동으로 감지하는 방법이 필요했다.

## 핵심 아이디어

엔딩 크레딧은 텍스트가 가득한 구간이다. 일반 영상 씬과 비교하면 프레임 안에 텍스트 박스 개수가 훨씬 많다. EAST(Efficient and Accurate Scene Text Detector) 모델로 프레임당 텍스트 영역 수를 세면 크레딧 구간을 찾을 수 있다고 판단했다.

## EAST 모델 세팅

```python
import cv2
import numpy as np
from imutils.object_detection import non_max_suppression

class EASTModel:
    def __init__(self, model_path: str, min_confidence: float = 0.5):
        self.layer_names = [
            "feature_fusion/Conv_7/Sigmoid",
            "feature_fusion/concat_3"
        ]
        self.net = cv2.dnn.readNet(model_path)
        self.min_confidence = min_confidence

    def get_boxes(self, image: np.ndarray, width: int, height: int):
        h, w = image.shape[:2]
        blob = cv2.dnn.blobFromImage(
            image, 1.0, (width, height),
            (123.68, 116.78, 103.94),
            swapRB=True, crop=False
        )
        self.net.setInput(blob)
        scores, geometry = self.net.forward(self.layer_names)
        return self._decode(scores, geometry, w, h, width, height)
```

입력 이미지를 640×640으로 resize해서 넣고, 출력된 score/geometry 맵에서 텍스트 박스를 뽑는다. NMS(Non-Maximum Suppression)로 겹치는 박스를 정리한다.

## 중복 프레임 제거

크레딧은 텍스트가 스크롤되면서 내려오는 경우가 많다. 연속 프레임이 거의 같은 내용이면 중복으로 판단해서 건너뛴다.

```python
class ROIExtractor:
    def is_redundant(self, base64_frame: str) -> bool:
        frame = ImageConverter.base64_to_cv2_img(base64_frame)
        boxes = self.model.get_boxes(frame, 640, 640)
        # 텍스트 박스가 2개 미만이면 크레딧 프레임이 아님
        return boxes is None or len(boxes) < 2

    def count_words(self, base64_frame: str) -> int:
        frame = ImageConverter.base64_to_cv2_img(base64_frame)
        boxes = self.model.get_boxes(frame, 640, 640)
        return len(boxes)
```

## 플랫폼별 대응

OTT마다 플레이어 구조가 달랐다. 디즈니+는 영상이 시작되면 플레이어가 화면을 줄이는 동작이 있어서 별도 처리가 필요했고, 웨이브는 초기 로딩 방식이 달라서 핫픽스를 여러 번 냈다.

```python
# 플랫폼별 크레딧 감지 채널 분리
class DisneyChannel(BaseChannel):
    def handle_credit_start(self):
        # 디즈니+: 크레딧 시작 시 shrinking 동작 대응
        self.wait_for_stable_frame()
        ...

class WavveChannel(BaseChannel):
    def handle_credit_start(self):
        # 웨이브: HLS 스트림 기반 remux 후 처리
        ...
```

각 채널을 독립 클래스로 분리하고, `BaseChannel`에서 공통 로직을 처리하는 구조로 정리했다.

## 스크롤 감지

크레딧이 스크롤되고 있는지도 판단해야 했다. 연속 프레임 간 centroid 이동 벡터를 구해서 수직 이동이 일정 임계값을 넘으면 스크롤 중으로 판단했다.

```python
def detect_scroll(prev_boxes, curr_boxes, threshold=5.0):
    if not prev_boxes or not curr_boxes:
        return False
    prev_centroid = np.mean(prev_boxes, axis=0)
    curr_centroid = np.mean(curr_boxes, axis=0)
    shift = curr_centroid - prev_centroid
    return abs(shift[1]) > threshold  # y축 이동
```

## 결과

EAST 모델이 조명이 어둡거나 폰트가 특이한 경우에 박스를 놓치는 케이스가 있었다. 이 부분은 이후에 Clova Vision OCR을 병렬로 쓰는 이중 파이프라인으로 보완했다.

크레딧 구간 감지 자체는 플랫폼별 채널을 분리하고 스크롤 감지 로직을 넣고 나서 정확도가 크게 올라갔다. 실제 스태프 추출까지 이어지는 파이프라인의 첫 단계가 안정화된 셈이다.
