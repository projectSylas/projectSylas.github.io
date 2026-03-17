---
layout: post
title: Git API로 PHP 프로젝트 FTP/SFTP 자동 배포 구현하기
subtitle: CI/CD 없이 Git 버전 체크 → 자동 배포 파이프라인을 PHP로 직접 만든 경험
author: HyeongJin
date: 2022-03-10 10:00:00 +0900
categories: DevOps
tags: [PHP, Git, DevOps, backend]
sidebar: []
published: true
---

에스아이알소프트 CMS는 여러 고객사 서버에 올라가 있었다. 업데이트가 생기면 개발자가 직접 FTP로 파일을 하나씩 올려야 했다. 실수로 파일을 빠뜨리거나 이전 버전을 덮어씌우는 사고가 간간이 있었다.

CI/CD 도구 없이 Git API를 활용해서 자동 배포를 구현하기로 했다.

## 기본 아이디어

GitHub API로 최신 릴리스 태그를 읽어서, 현재 설치된 버전보다 높으면 자동으로 업데이트 파일을 내려받아 배포하는 방식이다.

```
고객사 서버에서 스케줄러 실행
    → GitHub API로 최신 릴리스 버전 확인
    → 현재 버전과 비교
    → 버전이 높으면 변경된 파일 목록 조회
    → FTP/SFTP로 파일 배포
```

## GitHub API로 릴리스 버전 확인

```php
function getLatestRelease($owner, $repo) {
    $url = "https://api.github.com/repos/{$owner}/{$repo}/releases/latest";
    $ch = curl_init($url);
    curl_setopt_array($ch, [
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_HTTPHEADER => [
            'User-Agent: CMS-Updater',
            'Accept: application/vnd.github+json',
        ]
    ]);
    $response = json_decode(curl_exec($ch), true);
    curl_close($ch);
    return $response['tag_name'];
}

$current = file_get_contents('/path/to/version.txt');
$latest = getLatestRelease('sirsoft', 'cms-core');

if (version_compare(trim($current), $latest, '<')) {
    // 업데이트 실행
}
```

## 변경된 파일만 가져오기

전체 파일을 매번 올리면 비효율적이다. GitHub compare API로 두 버전 사이에 바뀐 파일만 가져왔다.

```php
function getChangedFiles($owner, $repo, $from, $to) {
    $url = "https://api.github.com/repos/{$owner}/{$repo}/compare/{$from}...{$to}";
    // API 호출...
    $data = json_decode($response, true);
    return array_map(fn($f) => $f['filename'], $data['files']);
}
```

## FTP 배포

```php
function deployViaFtp($host, $user, $pass, $files) {
    $conn = ftp_connect($host);
    ftp_login($conn, $user, $pass);
    ftp_pasv($conn, true);

    foreach ($files as $file) {
        $localPath = download_from_github($file);
        $remotePath = '/public_html/' . $file;

        // 디렉토리 생성
        $dir = dirname($remotePath);
        ensure_ftp_dir($conn, $dir);

        ftp_put($conn, $remotePath, $localPath, FTP_BINARY);
    }
    ftp_close($conn);
}
```

SFTP가 필요한 서버는 `phpseclib`을 썼다. FTP와 SFTP 둘 다 지원하도록 인터페이스를 분리했다.

## 롤백 처리

배포 중 오류가 생기면 이전 버전으로 롤백해야 한다. 배포 전에 현재 파일을 백업 디렉토리에 저장해두고, 오류 발생 시 복원하도록 했다.

```php
// 배포 전 백업
function backupCurrentFiles($files) {
    $backupDir = '/backup/' . date('YmdHis');
    foreach ($files as $file) {
        copy('/public_html/' . $file, $backupDir . '/' . $file);
    }
    return $backupDir;
}
```

## 결과

기존에 30분~1시간 걸리던 수동 배포가 2~3분으로 줄었다. 파일 누락 사고도 없어졌다. GitHub Actions나 Jenkins 같은 도구 없이 순수 PHP로 만든 CI/CD 대용이었다.

이때 Git API를 직접 쓰면서 GitHub의 compare, releases, contents API가 생각보다 강력하다는 걸 알았다. 나중에 CI/CD 도구를 쓰게 됐을 때 내부 동작이 어떻게 되는지 이해가 빨랐다.
