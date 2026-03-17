---
layout: post
title: PHP 웹 서버 온프레미스 → GCP Docker 이전
subtitle: 도메인 이전부터 Docker 이미지 구성까지, 서버 마이그레이션 전 과정
author: HyeongJin
date: 2022-08-10 10:00:00 +0900
categories: DevOps
tags: [PHP, Docker, GCP, DevOps, Linux]
sidebar: []
published: true
---

서울애널리티카에서 인하대학교 PHP 웹 서비스의 서버 이전을 맡았다. 기존 온프레미스 서버에서 회사 GCP 서버로 옮기고, Docker 기반으로 재구성하는 작업이었다.

## 기존 환경

- 온프레미스 Linux 서버에서 Apache + PHP 5.x 직접 구동
- 파일이 서버에 직접 배포된 방식 (FTP 접근)
- DB는 동일 서버에 MySQL

이전 목표는 GCP VM에 Docker 기반으로 올려서 이전 가능성과 관리 편의성을 높이는 것이었다.

## Docker 구성

PHP 5.x 레거시라 기존 버전을 유지해야 했다. Docker 덕분에 호스트 환경과 분리할 수 있었다.

```dockerfile
# PHP 5.6 + Apache
FROM php:5.6-apache

# 필요한 PHP 확장 설치
RUN docker-php-ext-install mysqli pdo pdo_mysql

# Apache mod_rewrite 활성화
RUN a2enmod rewrite

# 소스 복사
COPY ./source /var/www/html

# Apache 설정
COPY ./apache/000-default.conf /etc/apache2/sites-available/000-default.conf
```

```yaml
# docker-compose.yml
version: '3'
services:
  web:
    build: .
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./source:/var/www/html
    depends_on:
      - db

  db:
    image: mysql:5.7
    environment:
      MYSQL_ROOT_PASSWORD: ${DB_ROOT_PASS}
      MYSQL_DATABASE: ${DB_NAME}
    volumes:
      - mysql_data:/var/lib/mysql

volumes:
  mysql_data:
```

## DB 마이그레이션

온프레미스에서 DB 덤프 후 GCP로 이전했다.

```bash
# 구 서버에서 덤프
mysqldump -u root -p --single-transaction database_name > dump.sql

# GCP로 전송
scp dump.sql user@gcp-vm:/tmp/

# GCP에서 Docker 컨테이너로 복원
docker exec -i [컨테이너명] mysql -u root -p database_name < /tmp/dump.sql
```

한글 인코딩 문제가 또 있었다. 기존 DB가 `euckr`이었는데, 덤프 파일에 인코딩을 명시하고 이전 후 변환하는 작업이 필요했다.

## 도메인 이전

기존 도메인의 DNS A 레코드를 GCP VM의 외부 IP로 변경했다. GCP에서 고정 IP(Static IP)를 할당받아야 도메인이 바뀌지 않는다.

```bash
# GCP에서 고정 IP 할당 (gcloud CLI)
gcloud compute addresses create web-static-ip --region asia-northeast3

# 인스턴스에 연결
gcloud compute instances add-access-config [VM명] \
    --access-config-name "External NAT" \
    --address [할당된 IP]
```

DNS 전파에 최대 48시간이 걸리는 점이 항상 긴장되는 부분이다. TTL을 미리 낮춰두고 이전하면 더 빠르게 반영된다.

## SSL 설정

Let's Encrypt로 HTTPS를 붙였다.

```bash
# Certbot으로 인증서 발급
apt-get install certbot python3-certbot-apache
certbot --apache -d example.com
```

Docker 안에서 certbot을 실행하는 것보다 reverse proxy 방식이 더 깔끔하다는 걸 이후에 알았다. Nginx를 앞단에 두고 Let's Encrypt를 Nginx에서 처리하는 구성이 일반적이다.

## 이전 후

온프레미스와 Docker 환경이 달라서 PHP 확장 모듈 중 몇 가지가 빠져있었다. 로컬에서 테스트할 때 잡히지 않았던 것들이 실제 환경에서 나왔다. 이후로 개발 환경을 Docker로 먼저 맞추는 습관이 생겼다.
