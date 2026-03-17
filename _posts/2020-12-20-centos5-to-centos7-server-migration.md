---
layout: post
title: CentOS 5 → CentOS 7 서버 이전 삽질기
subtitle: EOL 서버 교체와 기존 프로젝트 마이그레이션에서 겪은 것들
author: HyeongJin
date: 2020-12-20 09:00:00 +0900
categories: DevOps
tags: [Linux, DevOps, Apache, backend]
sidebar: []
published: true
---

한경ITS에서 마지막으로 맡은 작업이 서버 이전이었다. 운영 중인 서버가 CentOS 5였다. CentOS 5는 2017년에 이미 EOL이었는데 계속 쓰고 있었다. 보안 업데이트가 전혀 안 되는 상태였다.

신규 CentOS 7 서버를 구축하고 기존 PHP/Apache 서비스를 전부 이전하는 작업이었다.

## CentOS 5와 7의 차이

처음에 별거 아니라고 생각했는데 생각보다 차이가 컸다.

**init vs systemd**
CentOS 5는 SysVinit 기반이라 서비스 관리가 `service` 명령어였다. CentOS 7은 systemd라서 `systemctl`을 써야 한다.

```bash
# CentOS 5
service httpd start
chkconfig httpd on

# CentOS 7
systemctl start httpd
systemctl enable httpd
```

**PHP 버전**
CentOS 5에서 yum으로 설치하면 PHP 5.1이었다. CentOS 7 기본 yum도 PHP 5.4였다. 기존 코드가 PHP 5.x 문법이라 큰 이슈는 없었지만 `mysql_*` 함수들이 deprecated 처리됐다.

```bash
# PHP 버전 확인
php -v
```

`mysql_connect()` 같은 구 함수들을 `mysqli_*`로 바꿔야 했다.

## Apache 설정 이전

`httpd.conf`와 VirtualHost 설정을 그대로 복사했는데 문제가 있었다. CentOS 7의 Apache 디렉터리 구조가 달라서 `Include` 경로를 수정해야 했다.

```apache
# CentOS 5: /etc/httpd/conf/httpd.conf 에 다 있던 설정
# CentOS 7: /etc/httpd/conf.d/ 디렉토리로 분리

# VirtualHost 예시
<VirtualHost *:80>
    ServerName example.internal
    DocumentRoot /var/www/html/project
    <Directory /var/www/html/project>
        Options -Indexes +FollowSymLinks
        AllowOverride All
        Require all granted  # CentOS 7: Order allow,deny 방식 대신
    </Directory>
</VirtualHost>
```

`Order allow,deny` 방식이 CentOS 7 Apache에서는 `Require all granted`로 바뀐 부분에서 403 에러가 났다.

## DB 이전

MySQL 데이터는 `mysqldump`로 백업 후 복원했다.

```bash
# 구 서버에서 덤프
mysqldump -u root -p --all-databases > backup_all.sql

# 신 서버에서 복원
mysql -u root -p < backup_all.sql
```

문자셋 문제가 있었다. 구 서버 DB가 `euckr`이었는데 신 서버 MySQL 기본이 `utf8`이라 한글이 깨지는 케이스가 생겼다. DB 덤프 파일에서 `euckr`을 명시해서 가져오고, 이전 후 utf8로 변환했다.

## 결과

이전 후 운영 서비스에서 바로 403 에러가 났던 게 기억에 남는다. Apache 권한 설정 문법 차이 때문이었다. 이때 처음으로 서버 작업이 운영에 직접 영향을 주는 긴장감을 경험했다.
