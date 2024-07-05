---
layout: post
title: Git-Hub 사용법
subtitle: git
author: HyeongJin
categories: GIT
tag: [git, github.io]
sidebar: []
published: true
---

# GitHub 사용법

GitHub는 소스 코드 관리를 위한 웹 기반의 호스팅 서비스로, Git을 사용하는 프로젝트를 지원합니다. 이 포스트에서는 GitHub의 기본적인 사용법을 단계별로 설명하겠습니다.

## 목차
1. [GitHub 계정 만들기](#github-계정-만들기)
2. [저장소 만들기](#저장소-만들기)
3. [로컬 저장소와 연결하기](#로컬-저장소와-연결하기)
4. [커밋하고 푸시하기](#커밋하고-푸시하기)
5. [풀 리퀘스트 보내기](#풀-리퀘스트-보내기)
6. [기타 유용한 명령어](#기타-유용한-명령어)

## GitHub 계정 만들기

1. [GitHub](https://github.com) 웹사이트로 이동합니다.
2. `Sign up` 버튼을 클릭합니다.
3. 필요한 정보를 입력하고 계정을 만듭니다.

## 저장소 만들기

1. 로그인 후, 오른쪽 상단의 `+` 버튼을 클릭하고 `New repository`를 선택합니다.
2. 저장소 이름과 설명을 입력하고, 공개 여부를 설정한 후 `Create repository` 버튼을 클릭합니다.

## 로컬 저장소와 연결하기

1. 로컬에서 터미널을 열고, 프로젝트 폴더로 이동합니다.
2. 다음 명령어를 입력하여 Git을 초기화합니다.

    ```bash
    git init
    ```

3. GitHub에서 생성한 저장소의 URL을 복사합니다.
4. 다음 명령어를 입력하여 원격 저장소를 추가합니다.

    ```bash
    git remote add origin <저장소 URL>
    ```

## 커밋하고 푸시하기

1. 변경된 파일을 스테이징합니다.

    ```bash
    git add .
    ```

2. 커밋 메시지를 작성하여 커밋합니다.

    ```bash
    git commit -m "Initial commit"
    ```

3. 변경 사항을 원격 저장소에 푸시합니다.

    ```bash
    git push origin master
    ```

## 풀 리퀘스트 보내기

1. 브랜치를 생성하고 변경 사항을 커밋합니다.

    ```bash
    git checkout -b feature-branch
    git add .
    git commit -m "Add new feature"
    ```

2. 변경 사항을 원격 저장소에 푸시합니다.

    ```bash
    git push origin feature-branch
    ```

3. GitHub 웹사이트에서 `Pull requests` 탭으로 이동하여 `New pull request` 버튼을 클릭합니다.
4. 변경 사항을 확인하고 `Create pull request` 버튼을 클릭합니다.

## 기타 유용한 명령어

- **브랜치 목록 보기**

    ```bash
    git branch
    ```

- **브랜치 전환하기**

    ```bash
    git checkout <브랜치 이름>
    ```

- **병합하기**

    ```bash
    git merge <브랜치 이름>
    ```

- **충돌 해결하기**

    충돌이 발생한 파일을 편집하고, 다음 명령어를 사용하여 충돌을 해결합니다.

    ```bash
    git add .
    git commit -m "Resolve merge conflict"
    ```

이 포스트를 통해 GitHub의 기본적인 사용법을 익히셨길 바랍니다. 더 자세한 정보는 [GitHub 공식 문서](https://docs.github.com)를 참고하세요.
