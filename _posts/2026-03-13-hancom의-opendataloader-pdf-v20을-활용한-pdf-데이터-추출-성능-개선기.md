---
layout: post
title: Hancom의 OpenDataLoader PDF v2.0을 활용한 PDF 데이터 추출 성능 개선기
subtitle: 오픈 소스 PDF 데이터 추출 벤치마크에서 1위
author: HyeongJin
categories: Backend
tags: [PDF, 데이터 추출, 오픈 소스, Hancom, 벤치마크]
sidebar: []
published: true
---

최근 프로젝트에서 대량의 PDF 문서로부터 효율적으로 데이터를 추출해야 하는 상황이 발생했다. 기존의 방법으로는 처리 속도가 느리고, 추출한 데이터의 정확도가 낮아 새로운 대안이 필요했다.

Hancom에서 출시한 OpenDataLoader PDF v2.0이 오픈 소스 PDF 데이터 추출 벤치마크에서 1위를 차지했다는 소식을 듣고 이를 테스트해보기로 했다. 우선 이 도구의 설치는 매우 간단했다. 공식 깃허브 저장소에서 소스를 클론한 후, 요구 사항을 설치하면 바로 사용할 수 있었다.

```bash
# OpenDataLoader PDF v2.0 설치
$ git clone https://github.com/hancom-opendataloader/pdf-v2.0.git
$ cd pdf-v2.0
$ pip install -r requirements.txt
```

먼저 기본적인 기능을 테스트해보았다. 기존에 사용하던 PDF 추출 도구와 비교하여 속도와 정확도 측면에서 큰 차이를 보였다. 특히, 복잡한 레이아웃의 문서에서도 텍스트가 상당히 잘 추출되었고, 테이블 데이터도 예상보다 정확하게 파싱되었다.

하지만 모든 것이 매끄럽게 진행되지는 않았다. 일부 PDF 파일에서 특정 글꼴이 깨지는 문제가 발생했다. 이는 한글 폰트를 포함한 PDF 파일에서 주로 발생했으며, OpenDataLoader PDF의 기본 설정에서는 해당 폰트를 인식하지 못하여 발생한 문제였다.

```python
# PDF 파일 처리 및 데이터 추출 코드 예제
from opendataloader_pdf import PDFExtractor

extractor = PDFExtractor()
try:
    result = extractor.extract('example.pdf')
except FontError as e:
    print(f"폰트 에러 발생: {e}")
```

해당 문제의 경우, PDFExtractor의 설정 파일을 수정하여 지원하는 글꼴을 추가하는 방식으로 해결할 수 있었다. 이 작업을 통해 모든 파일에서 데이터 추출이 원활하게 이루어졌다.

추가적으로, 대량의 PDF 파일을 처리하기 위해 멀티쓰레딩을 도입했다. 이로 인해 처리 속도가 대폭 개선되었으며, 리소스 사용량 또한 효율적으로 관리할 수 있었다.

```python
from concurrent.futures import ThreadPoolExecutor

def process_pdf(file_path):
    extractor = PDFExtractor()
    return extractor.extract(file_path)

file_paths = ['file1.pdf', 'file2.pdf', 'file3.pdf']
with ThreadPoolExecutor(max_workers=5) as executor:
    results = list(executor.map(process_pdf, file_paths))
```

이번 프로젝트를 통해 OpenDataLoader PDF v2.0의 강력한 성능을 체감할 수 있었다. 속도와 정확도 면에서 경쟁 도구를 크게 앞서며, 오픈 소스로서의 접근성과 유연성도 높았다. 하지만 특정 폰트 문제와 같은 예외 상황에 대한 대처가 필요했고, 이것이 향후 개선의 여지가 있는 부분으로 보인다.

다음 단계로, 다른 형식의 문서와의 호환성을 테스트하고, 이 도구를 우리 기존 시스템에 통합하여 최종적인 성능 개선을 이루어낼 계획이다.