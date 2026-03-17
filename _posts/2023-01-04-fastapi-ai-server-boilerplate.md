---
layout: post
title: FastAPI AI 서버 보일러플레이트 구성하기
subtitle: Poetry + Docker + pre-commit + semantic-version — AI 모델 서빙 서버의 표준 설정
author: HyeongJin
date: 2023-01-04 10:00:00 +0900
categories: Backend
tags: [Python, FastAPI, Docker, DevOps, backend]
sidebar: []
published: true
---

AI 모델 서빙 서버를 반복해서 만들다 보면 매번 같은 설정을 처음부터 하게 된다. Poetry 패키지 관리, Docker 구성, pre-commit 코드 품질, 버전 관리까지 한 번에 잡아둔 FastAPI 보일러플레이트를 만들었다.

## 전체 구조

```
src/
  main.py       # FastAPI 앱, 미들웨어, 라우트
  interface.py  # 요청/응답 Pydantic 모델
  util.py       # CamelCase 응답 유틸
Dockerfile.pip      # pip 기반 Docker 이미지
Dockerfile.poetry   # poetry 기반 Docker 이미지
docker-compose.yml
pyproject.toml      # Poetry + taskipy 설정
.pre-commit-config.yaml
.versionrc          # standard-version 버전 규칙
```

## FastAPI 앱

```python
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from src.interface import DemoCamelResponse

app = FastAPI(
    title=os.getenv("API_TITLE"),
    version=os.getenv("API_VERSION"),
)

# 정적 파일 서빙 (모델 결과물 등 임시 파일)
app.mount("/static", StaticFiles(directory="tmp"), name="static")

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("ALLOW_ORIGINS").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/predict", response_model=DemoCamelResponse)
def predict():
    pass  # AI 모델 추론 로직 삽입
```

타이틀, 버전, CORS 허용 도메인 모두 환경변수로 관리한다.

## CamelCase 응답 모델

Python은 snake_case, JSON은 camelCase 관례가 다르다. `pydantic-humps`의 `camelize`로 자동 변환한다.

```python
# util.py
from humps import camelize
from pydantic import BaseModel

class CamelModel(BaseModel):
    class Config:
        alias_generator = camelize
        allow_population_by_field_name = True
```

```python
# interface.py
class DemoSnakeResponse(BaseModel):
    id: int
    is_success: bool  # JSON: is_success (그대로)

class DemoCamelResponse(CamelModel):
    id: int
    is_success: bool  # JSON: isSuccess (camel 변환)
```

`CamelModel`을 상속하면 응답 JSON이 자동으로 camelCase로 나온다. 프론트엔드와 API 필드명 규칙을 맞출 때 유용하다.

## Poetry로 패키지 관리

pip 대신 Poetry를 쓰면 의존성 버전 충돌 관리가 훨씬 편하다. `poetry.lock`으로 팀 전체가 동일한 패키지 버전을 쓸 수 있다.

```bash
# 의존성 설치
poetry install

# 개발 서버 실행 (pyproject.toml의 taskipy 설정 사용)
poetry shell
task devel  # uvicorn src.main:app --port 8080 --reload
```

`pyproject.toml`에 `taskipy`로 자주 쓰는 명령을 단축키처럼 등록했다.

```toml
[tool.taskipy.tasks]
devel = "uvicorn src.main:app --port 8080 --reload"
test = "pytest tests/"
lint = "flake8 src/"
```

## Docker 두 가지 — pip vs poetry

pip 방식과 poetry 방식 두 가지 Dockerfile을 둬서 환경에 맞게 선택한다.

```dockerfile
# Dockerfile.pip — 심플한 pip 방식
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8080"]
```

```yaml
# docker-compose.yml
services:
  app:
    build:
      context: .
      dockerfile: Dockerfile.pip  # 또는 Dockerfile.poetry
    ports:
      - "8080:8080"
    env_file:
      - .env
    volumes:
      - ./models:/app/models  # 모델 파일 마운트
      - ./tmp:/app/tmp        # 임시 결과물
```

모델 파일은 이미지에 포함하지 않고 볼륨으로 마운트한다. 이미지 크기를 줄이고, 모델 교체 시 재빌드 없이 파일만 바꾸면 된다.

## pre-commit 코드 품질

커밋 전 자동으로 코드 스타일을 검사한다.

```yaml
# .pre-commit-config.yaml
repos:
  - repo: https://github.com/psf/black
    rev: 23.1.0
    hooks:
      - id: black

  - repo: https://github.com/pycqa/isort
    rev: 5.12.0
    hooks:
      - id: isort

  # flake8은 선택적으로 활성화
  # - repo: https://github.com/pycqa/flake8
  #   rev: 6.0.0
  #   hooks:
  #     - id: flake8
```

```bash
pip install pre-commit
pre-commit install  # git hook 등록
```

이후 `git commit` 시 자동으로 black 포매팅과 isort가 실행된다.

## semantic versioning — CHANGELOG 자동화

버전 관리는 `standard-version`(Node.js 패키지)으로 처리한다. 커밋 메시지 컨벤션(`feat:`, `fix:`, `BREAKING CHANGE:`)에 따라 버전을 자동으로 올리고 CHANGELOG를 생성한다.

```bash
# 버전 올리기
yarn standard-version --release-as 1.1.0
```

실행하면:
1. `package.json` 버전 업데이트
2. `CHANGELOG.md`에 변경 내역 자동 추가
3. git commit + tag 생성

`.versionrc`에 커밋 타입별 CHANGELOG 섹션을 정의해둔다.

## 쓰면서 느낀 점

AI 서버는 모델 파일 관리와 추론 환경 재현이 핵심이다. Docker 볼륨으로 모델을 분리하면 모델 버전 교체가 간편하고, 이미지를 가볍게 유지할 수 있다.

CamelModel은 처음 쓸 때 alias 설정이 헷갈릴 수 있는데, `allow_population_by_field_name = True`를 같이 설정해야 Python 코드에서 snake_case로 접근할 수 있다.
