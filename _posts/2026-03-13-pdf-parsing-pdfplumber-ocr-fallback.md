---
layout: post
title: 스캔 PDF 처리 - pdfplumber + tesseract OCR fallback 구현
subtitle: AI 시나리오 파이프라인에서 다양한 PDF를 파싱하는 방법
author: HyeongJin
categories: AI/LLM
tags: [Python, AI, backend]
sidebar: []
published: true
---

시나리오 분석 파이프라인을 만들면서 PDF 지원이 필요했다. 영화 시나리오가 PDF로 오는 경우가 많다.

문제는 PDF가 두 종류라는 것. 텍스트 레이어가 있는 일반 PDF, 그리고 종이를 스캔한 이미지 PDF. 전자는 텍스트 추출이 간단하지만 후자는 OCR을 써야 한다.

## pdfplumber 기본 추출

```python
import pdfplumber

def _parse_pdf(path: Path) -> str:
    texts: list[str] = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text()
            if page_text:
                texts.append(page_text)
    return "\n".join(texts)
```

pdfplumber는 PyMuPDF나 pdfminer보다 한국어 텍스트 레이아웃 처리가 나은 편이다. 특히 컬럼이 있는 레이아웃에서 줄 순서가 덜 섞인다.

## 스캔 PDF 감지

텍스트 레이어가 없는 스캔 PDF를 pdfplumber로 열면 `page.extract_text()`가 `None`이나 빈 문자열을 반환한다. 이걸 기준으로 fallback을 결정.

```python
def _parse_pdf(path: Path) -> str:
    texts: list[str] = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text()
            if page_text:
                texts.append(page_text)

    result = "\n".join(texts)

    # 텍스트가 너무 적으면 스캔 PDF로 판단 → OCR
    if len(result.strip()) < 100:
        print(f"[PDF OCR] 텍스트 부족 ({len(result.strip())}자) → OCR 시도: {path.name}")
        result = _parse_pdf_ocr(path)

    return result
```

100자 기준은 경험적으로 정했다. 실제로 테스트하다 보면 일부 PDF가 50~70자 정도의 메타데이터 텍스트만 갖고 있는 경우가 있어서, 너무 낮은 기준을 잡으면 OCR 호출이 불필요하게 많아진다.

## tesseract OCR fallback

```python
import fitz  # pymupdf
import pytesseract
from PIL import Image
import io

def _parse_pdf_ocr(path: Path) -> str:
    texts: list[str] = []
    doc = fitz.open(path)

    for page in doc:
        # 2x 해상도로 렌더링 — 해상도 낮으면 인식률 급락
        mat = fitz.Matrix(2.0, 2.0)
        pix = page.get_pixmap(matrix=mat)

        img = Image.open(io.BytesIO(pix.tobytes("png")))
        text = pytesseract.image_to_string(img, lang="kor+eng")

        if text.strip():
            texts.append(text)

    doc.close()
    return "\n".join(texts)
```

PyMuPDF(`fitz`)로 PDF 페이지를 이미지로 렌더링한 뒤 Tesseract OCR에 넘기는 방식.

`fitz.Matrix(2.0, 2.0)`이 중요하다. 기본 해상도(1.0)에서 OCR을 돌리면 한국어 인식률이 50% 이하로 떨어졌다. 2배 스케일로 올리니 80%대로 올라갔다. 3배는 속도가 느려지는 것 대비 인식률 개선이 미미했다.

`lang="kor+eng"` — 한국어+영어 동시 인식. 영어만 지정하면 한글을 아예 못 읽고, 한국어만 지정하면 영어 단어를 엉터리로 읽는다.

## NUL 문자 문제

PDF에서 텍스트를 추출하다 보면 `\x00`(NUL 문자)가 섞여 나오는 경우가 있다. PostgreSQL에 저장하거나 Prefect flow 파라미터로 넘길 때 에러.

```python
def sanitize_text_for_db(text: str) -> str:
    """PostgreSQL/JSON에서 허용하지 않는 NUL 제거"""
    if not text:
        return text
    return text.replace("\x00", " ")
```

pdfplumber가 출력한 텍스트를 무조건 이 함수에 통과시키는 게 안전하다. 에러가 발생한 뒤에 추가한 코드인데, 처음부터 넣어뒀으면 시간 낭비가 없었을 것이다.

## 인코딩 문제

txt 파일도 같이 처리하는 파서가 있는데, 여기서 인코딩 문제가 빈번했다. 오래된 한국어 문서는 euc-kr이나 cp949인 경우가 많다.

```python
def _parse_txt(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ("utf-8", "euc-kr", "cp949", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")
```

순서가 중요하다. utf-8 → euc-kr → cp949 → latin-1 순으로 시도. utf-8을 먼저 시도하지 않으면 utf-8 문서가 euc-kr로 잘못 디코딩되는 경우가 생긴다. euc-kr과 cp949는 거의 같지만 일부 특수문자에서 차이가 있어서 둘 다 시도.

## 결과

파이프라인에서 처리한 PDF 유형별 결과:
- 일반 텍스트 PDF: pdfplumber로 95%+ 정확도
- 스캔 PDF (해상도 높음): OCR fallback으로 80%대
- 스캔 PDF (저해상도, 손글씨 메모 등): 50~60%, 실질적으로 사용 불가

한글 스캔 PDF에서 완벽한 인식률을 원한다면 Clova OCR 같은 상용 API가 필요하다. Tesseract는 무료지만 한국어에서 한계가 명확하다.
