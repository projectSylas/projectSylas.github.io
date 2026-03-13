---
layout: post
title: PHP 5.4 레거시 프로젝트 Docker로 이전한 경험
subtitle: 구버전 PHP 로컬 환경 구축 고통 없애기
author: HyeongJin
categories: DevOps
tags: [Docker, CI/CD, DevOps]
sidebar: []
published: true
---

클라이언트가 PHP 5.4로 만들어진 사이트를 유지보수해달라고 했다. 2023년에 PHP 5.4.

로컬에 PHP 5.4를 설치하려면 Homebrew에서 지원도 안 하고, 수동으로 소스 컴파일해야 한다. macOS 최신 버전에서는 의존성 충돌도 있다.

Docker로 해결했다.

## 환경 구성

`docker-compose.yml`에 nginx + PHP-FPM + MySQL을 묶었다.

```yaml
version: '3.8'

services:
  nginx:
    image: nginx:alpine
    ports:
      - "8080:80"
    volumes:
      - ./src:/var/www/html
      - ./nginx.conf:/etc/nginx/conf.d/default.conf
    depends_on:
      - php

  php:
    build: ./docker/php
    volumes:
      - ./src:/var/www/html

  db:
    image: mysql:5.7
    environment:
      MYSQL_ROOT_PASSWORD: ${DB_PASSWORD}
      MYSQL_DATABASE: ${DB_NAME}
    volumes:
      - mysql_data:/var/lib/mysql
      - ./init.sql:/docker-entrypoint-initdb.d/init.sql
```

PHP 5.4용 Dockerfile이 핵심이었다.

```dockerfile
FROM php:5.6-fpm

# php 5.4는 공식 이미지가 없어서 5.6으로 대체 (5.4 호환)
RUN docker-php-ext-install mysqli pdo pdo_mysql
RUN apt-get update && apt-get install -y libpng-dev && docker-php-ext-install gd

WORKDIR /var/www/html
```

엄밀히는 PHP 5.4 이미지가 Docker Hub에 없어서 5.6으로 올렸다. 클라이언트 코드가 5.4 문법이라 대부분 5.6에서 돌아갔는데, `mysql_*` 함수들이 5.6에서 deprecated 경고만 나오고 동작은 했다.

## mysql_connect 문제

가장 골치 아팠던 건 구버전 MySQL 함수들이었다.

```php
// 원본 코드
$conn = mysql_connect("localhost", "root", "password");
mysql_select_db("mydb", $conn);
$result = mysql_query("SELECT * FROM users", $conn);
```

PHP 7.0부터 `mysql_*` 함수가 완전히 제거됐다. 5.6에서는 아직 작동하지만 경고 출력이 지저분했다.

로컬 개발 환경에서만 쓸 거라 경고 수준을 낮추는 방식으로 처리했다.

```ini
; php.ini
error_reporting = E_ALL & ~E_DEPRECATED & ~E_NOTICE
```

운영 서버에 올릴 코드라면 `mysqli_*`로 전면 교체했겠지만, 클라이언트 요구사항이 "건드리지 말고 돌아가게만 해달라"였다.

## nginx 파일 업로드 용량

팝업 게시판에서 파일 업로드가 안 된다는 버그 리포트.

```
HTTP 413 Request Entity Too Large
```

nginx 기본 업로드 제한이 1MB인데 클라이언트 쪽에서 PDF 첨부를 하려고 했던 것.

```nginx
# nginx.conf
server {
    client_max_body_size 50M;
    ...
}
```

PHP `php.ini`에도 맞춰줘야 한다.

```ini
upload_max_filesize = 50M
post_max_size = 50M
```

nginx만 올리거나 php.ini만 올리면 안 되고 둘 다 맞춰야 해서 한 쪽만 고쳤다가 삽질했다.

## Kakao 지도 API 연동

지도 표시 기능에 kakao API 키가 하드코딩되어 있었다.

```javascript
// 기존
var container = document.getElementById('map');
var options = { center: new kakao.maps.LatLng(37.xxx, 127.xxx) };
```

클라이언트가 계정을 바꾸면서 API 키도 갱신이 필요했다. 하드코딩 대신 서버에서 스크립트 URL을 렌더링하는 방식으로 변경했다.

전체적으로 레거시 코드를 들여다보는 경험이었다. PHP 5.4 코드는 현대 MVC 패턴과 거리가 멀었다. `include`, `require`로 연결된 스파게티 구조. 수정할 때마다 어디서 무엇이 include되는지 파악하는 데 시간이 걸렸다.

Docker 덕분에 팀원 모두 동일한 환경에서 개발할 수 있었던 건 확실한 성과였다.
