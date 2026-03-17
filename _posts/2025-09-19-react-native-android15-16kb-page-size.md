---
layout: post
title: Android 15 16KB 페이지 크기 지원 - React Native 앱 대응기
subtitle: Google이 요구하는 16KB 페이지 크기 정렬 작업과 React Native에서의 대응 방법
author: HyeongJin
date: 2025-09-19 10:00:00 +0900
categories: React
tags: [ReactNative, Android, mobile]
sidebar: []
published: true
---

Google이 Android 15부터 일부 기기에서 16KB 페이지 크기를 지원하기 시작했고, 2025년 이후로는 Play Store 신규 앱이 16KB 정렬을 지원해야 한다는 요구사항을 공지했다.

클레딧(Cleddit) React Native 앱에서 이 대응 작업을 했다.

## 뭐가 문제인가

기존 Android는 4KB 페이지 크기를 기준으로 동작했다. 16KB 페이지 크기 기기에서는 네이티브 라이브러리(`.so` 파일)가 16KB 경계에 정렬되어 있어야 한다. 그렇지 않으면 앱이 실행되지 않는다.

직접 작성한 C/C++ 코드가 없어도, 의존하는 네이티브 라이브러리 중 하나라도 정렬이 안 돼 있으면 문제가 된다.

## 확인 방법

```bash
# APK에서 .so 파일 정렬 상태 확인
python3 check_elf_alignment.py app-release.apk
```

Google에서 제공하는 `check_elf_alignment.py` 스크립트로 APK 내 모든 `.so` 파일의 정렬 상태를 확인할 수 있다.

## React Native에서의 대응

React Native 앱은 주로 세 가지 소스의 네이티브 라이브러리가 들어간다:
1. React Native 코어 라이브러리
2. 서드파티 라이브러리의 네이티브 모듈
3. 직접 작성한 네이티브 코드

React Native 0.74+ 버전은 대부분 16KB 정렬이 이미 대응돼 있다. 문제는 서드파티 네이티브 모듈들이다.

`android/app/build.gradle`에 정렬 설정을 추가해야 한다.

```groovy
android {
    ...
    defaultConfig {
        ...
        // 16KB 페이지 크기 지원
        externalNativeBuild {
            cmake {
                arguments "-DANDROID_SUPPORT_FLEXIBLE_PAGE_SIZES=ON"
            }
        }
    }
}
```

## 버전 코드 관리

이 작업과 함께 버전 코드도 올렸다.

```groovy
// android/app/build.gradle
android {
    defaultConfig {
        versionCode 3063  // 이전: 3062
        versionName "3.0.63"
    }
}
```

React Native에서 버전 코드는 `android/app/build.gradle`과 iOS는 `ios/[AppName]/Info.plist` 두 곳을 같이 올려야 한다.

```xml
<!-- ios/Cleddit/Info.plist -->
<key>CFBundleVersion</key>
<string>3063</string>
```

## Play Store 제출 시 확인 사항

Play Console에서 내부 테스트 트랙으로 올리면 앱 번들 분석 결과에 16KB 지원 여부가 표시된다. `aab` 파일 기준으로 검사하기 때문에 APK보다 `aab`로 빌드하는 게 정확하다.

```bash
# Release AAB 빌드
cd android && ./gradlew bundleRelease
```

서드파티 라이브러리 중 16KB 미대응 라이브러리가 있으면 해당 라이브러리를 업데이트하거나, 해당 라이브러리를 16KB 지원 버전으로 교체해야 한다. 없으면 해당 기능을 순수 JavaScript로 대체하는 방법도 있다.

이 업데이트 이후 Android 15 기기에서 앱 설치가 정상적으로 됐다. Google Play 정책 대응은 미리미리 하는 게 맞는데, 이번에도 데드라인이 가까워져서 급하게 했다.
