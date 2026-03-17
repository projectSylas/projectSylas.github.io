---
layout: post
title: Docker로 Apache Airflow 로컬 환경 구성하기
subtitle: CeleryExecutor + PostgreSQL + Redis — docker-compose로 Airflow 2.5.1 띄우기
author: HyeongJin
date: 2024-03-22 09:00:00 +0900
categories: DevOps
tags: [Docker, DevOps, Airflow, Python]
sidebar: []
published: true
---

Apache Airflow를 로컬에서 돌리기 위한 Docker Compose 구성을 정리한다. 공식 문서에서 제공하는 `docker-compose.yaml` 기반이고, CeleryExecutor로 Worker를 여러 개 띄울 수 있는 구성이다.

## 사전 준비

- Docker 설치
- Docker Compose v1.29.1 이상

## 구성 요소

CeleryExecutor 구성에서 돌아가는 서비스들:

| 서비스 | 역할 |
|--------|------|
| `postgres` | Airflow 메타데이터 DB |
| `redis` | Celery 브로커 (태스크 큐) |
| `airflow-webserver` | Web UI (포트 8080) |
| `airflow-scheduler` | DAG 스케줄링 |
| `airflow-worker` | 태스크 실행 |
| `airflow-triggerer` | Deferrable Operator 처리 |
| `airflow-init` | 초기화 (DB 마이그레이션 + 관리자 계정 생성) |
| `flower` | Celery Worker 모니터링 UI (선택) |

## docker-compose.yaml

```yaml
version: '3'
x-airflow-common:
  &airflow-common
  image: ${AIRFLOW_IMAGE_NAME:-apache/airflow:2.5.1}
  environment:
    &airflow-common-env
    AIRFLOW__CORE__EXECUTOR: CeleryExecutor
    AIRFLOW__DATABASE__SQL_ALCHEMY_CONN: postgresql+psycopg2://airflow:airflow@postgres/airflow
    AIRFLOW__CELERY__RESULT_BACKEND: db+postgresql://airflow:airflow@postgres/airflow
    AIRFLOW__CELERY__BROKER_URL: redis://:@redis:6379/0
    AIRFLOW__CORE__FERNET_KEY: ''
    AIRFLOW__CORE__DAGS_ARE_PAUSED_AT_CREATION: 'true'
    AIRFLOW__CORE__LOAD_EXAMPLES: 'true'
    AIRFLOW__API__AUTH_BACKENDS: 'airflow.api.auth.backend.basic_auth,airflow.api.auth.backend.session'
    _PIP_ADDITIONAL_REQUIREMENTS: ${_PIP_ADDITIONAL_REQUIREMENTS:-}
  volumes:
    - ${AIRFLOW_PROJ_DIR:-.}/dags:/opt/airflow/dags
    - ${AIRFLOW_PROJ_DIR:-.}/logs:/opt/airflow/logs
    - ${AIRFLOW_PROJ_DIR:-.}/plugins:/opt/airflow/plugins
  user: "${AIRFLOW_UID:-50000}:0"
  depends_on:
    &airflow-common-depends-on
    redis:
      condition: service_healthy
    postgres:
      condition: service_healthy

services:
  postgres:
    image: postgres:13
    environment:
      POSTGRES_USER: airflow
      POSTGRES_PASSWORD: airflow
      POSTGRES_DB: airflow
    volumes:
      - postgres-db-volume:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD", "pg_isready", "-U", "airflow"]
      interval: 5s
      retries: 5
    restart: always

  redis:
    image: redis:latest
    expose:
      - 6379
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 30s
      retries: 50
    restart: always

  airflow-webserver:
    <<: *airflow-common
    command: webserver
    ports:
      - 8080:8080
    healthcheck:
      test: ["CMD", "curl", "--fail", "http://localhost:8080/health"]
      interval: 10s
      timeout: 10s
      retries: 5
    restart: always
    depends_on:
      <<: *airflow-common-depends-on
      airflow-init:
        condition: service_completed_successfully

  airflow-scheduler:
    <<: *airflow-common
    command: scheduler
    healthcheck:
      test: ["CMD-SHELL", 'airflow jobs check --job-type SchedulerJob --hostname "$${HOSTNAME}"']
      interval: 10s
      timeout: 10s
      retries: 5
    restart: always
    depends_on:
      <<: *airflow-common-depends-on
      airflow-init:
        condition: service_completed_successfully

  airflow-worker:
    <<: *airflow-common
    command: celery worker
    environment:
      <<: *airflow-common-env
      DUMB_INIT_SETSID: "0"
    restart: always
    depends_on:
      <<: *airflow-common-depends-on
      airflow-init:
        condition: service_completed_successfully

  airflow-triggerer:
    <<: *airflow-common
    command: triggerer
    restart: always
    depends_on:
      <<: *airflow-common-depends-on
      airflow-init:
        condition: service_completed_successfully

  airflow-init:
    <<: *airflow-common
    entrypoint: /bin/bash
    command:
      - -c
      - |
        mkdir -p /sources/logs /sources/dags /sources/plugins
        chown -R "${AIRFLOW_UID}:0" /sources/{logs,dags,plugins}
        exec /entrypoint airflow version
    environment:
      <<: *airflow-common-env
      _AIRFLOW_DB_UPGRADE: 'true'
      _AIRFLOW_WWW_USER_CREATE: 'true'
      _AIRFLOW_WWW_USER_USERNAME: ${_AIRFLOW_WWW_USER_USERNAME:-airflow}
      _AIRFLOW_WWW_USER_PASSWORD: ${_AIRFLOW_WWW_USER_PASSWORD:-airflow}
    user: "0:0"
    volumes:
      - ${AIRFLOW_PROJ_DIR:-.}:/sources

  # Celery Worker 모니터링 — docker-compose --profile flower up 으로 선택 실행
  flower:
    <<: *airflow-common
    command: celery flower
    profiles:
      - flower
    ports:
      - 5555:5555
    restart: always
    depends_on:
      <<: *airflow-common-depends-on
      airflow-init:
        condition: service_completed_successfully

volumes:
  postgres-db-volume:
```

## 실행

```bash
# AIRFLOW_UID 설정 (Linux 필수, macOS는 생략 가능)
echo -e "AIRFLOW_UID=$(id -u)" > .env

# 초기화 + 실행
docker-compose up airflow-init
docker-compose up -d

# Flower 모니터링 포함
docker-compose --profile flower up -d
```

실행 후 `http://localhost:8080`에서 Web UI 접속. 기본 계정은 `airflow` / `airflow`.

## DAG 작성

`dags/` 디렉토리에 `.py` 파일을 추가하면 Airflow가 자동으로 감지한다.

```python
from airflow import DAG
from airflow.operators.python import PythonOperator
from datetime import datetime

def my_task():
    print("Hello Airflow!")

with DAG(
    dag_id="my_first_dag",
    start_date=datetime(2024, 1, 1),
    schedule_interval="@daily",
    catchup=False,
) as dag:
    task = PythonOperator(
        task_id="hello_task",
        python_callable=my_task,
    )
```

## 주의사항

- 메모리 4GB 이상, CPU 2코어 이상 권장. 부족하면 `airflow-init`에서 경고를 출력한다.
- `AIRFLOW__CORE__LOAD_EXAMPLES: 'true'`를 `'false'`로 바꾸면 샘플 DAG가 안 뜬다. 처음엔 켜두고 UI 구조 파악 후 끄는 게 좋다.
- 이 설정은 로컬 개발용이다. 운영 환경에서는 Fernet Key, DB 비밀번호, API 인증 설정을 별도로 강화해야 한다.
