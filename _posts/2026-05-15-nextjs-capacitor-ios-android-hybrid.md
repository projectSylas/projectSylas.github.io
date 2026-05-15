---
layout: post
title: Next.js 앱을 Capacitor로 iOS/Android 네이티브 앱으로 래핑하기
subtitle: 정적 빌드 연동, 개발/프로덕션 서버 분기, SplashScreen 수동 제어까지
author: HyeongJin
date: 2026-05-15 10:00:00 +0900
categories: Mobile
tags: [Next.js, Capacitor, iOS, Android, TypeScript]
sidebar: []
published: true
---

Next.js로 만든 웹 청첩장을 iOS/Android 앱으로도 배포해야 했다. 별도 React Native 프로젝트를 만드는 대신 Capacitor를 써서 기존 Next.js 코드를 그대로 네이티브 웹뷰로 래핑했다.

구현 과정에서 세 가지 문제가 있었다. 정적 빌드 연동, 개발 환경에서 라이브 서버 연결, SplashScreen 타이밍 제어.

## 정적 빌드 연동

Capacitor는 `webDir`에 지정된 디렉토리를 네이티브 앱에 번들링한다. Next.js의 정적 빌드 출력 경로인 `out/`을 맞춰야 한다.

`next.config.ts`에 `output: 'export'`를 추가해야 `next build` 시 `out/` 디렉토리가 생성된다. 이게 빠지면 `webDir: 'out'`이 빈 디렉토리를 참조하게 된다.

```ts
// next.config.ts
const nextConfig: NextConfig = {
  output: 'export',
}
```

```ts
// capacitor.config.ts
const config: CapacitorConfig = {
  appId: 'com.wedding.invitation',
  appName: '청첩장',
  webDir: 'out',
  // ...
}
```

## 개발/프로덕션 서버 분기

로컬 개발 중에는 `next dev` 서버(localhost:3000)를 실시간으로 연결하는 게 편하다. 그런데 프로덕션 빌드에서는 로컬 서버가 없으므로 `webDir`의 정적 파일을 써야 한다.

`capacitor.config.ts`는 TypeScript 파일이라 `process.env.NODE_ENV`로 분기할 수 있다.

```ts
const isProduction = process.env.NODE_ENV === 'production'

const config: CapacitorConfig = {
  appId: 'com.wedding.invitation',
  appName: '청첩장',
  webDir: 'out',
  server: isProduction
    ? undefined                          // 프로덕션: 번들 정적 파일 사용
    : {
        url: 'http://localhost:3000',
        cleartext: true,                 // HTTP 허용 (로컬 개발용)
      },
}
```

`server`가 `undefined`이면 Capacitor가 `webDir`의 정적 파일을 서빙한다. 개발 중엔 `url`을 지정해 `next dev`와 바로 연동된다.

## SplashScreen 수동 제어

SplashScreen을 앱 진입 직후 바로 숨기면 네트워크 연결 전에 콘텐츠가 깜빡이며 보인다. `launchAutoHide: false`로 자동 숨김을 끄고, 네트워크 확인이 완료된 뒤 수동으로 숨긴다.

```ts
// capacitor.config.ts
plugins: {
  SplashScreen: {
    launchShowDuration: 2000,
    launchAutoHide: false,           // 수동으로 숨길 것
    backgroundColor: '#FAFAF8',
    androidSplashResourceName: 'splash',
    showSpinner: false,
  },
},
```

네트워크 상태를 확인하는 훅을 만들고, 확인이 끝나면 SplashScreen을 숨긴다.

```ts
// hooks/useNetworkGuard.ts
'use client'

import { useEffect, useState } from 'react'

type NetworkStatus = 'checking' | 'online' | 'offline'

export function useNetworkGuard(): NetworkStatus {
  const [status, setStatus] = useState<NetworkStatus>('checking')

  useEffect(() => {
    async function check() {
      if (typeof window === 'undefined') {
        setStatus('online')
        return
      }

      try {
        // Capacitor 환경: @capacitor/network 사용
        const { Network } = await import('@capacitor/network')
        const state = await Network.getStatus()
        setStatus(state.connected ? 'online' : 'offline')

        Network.addListener('networkStatusChange', (s) => {
          setStatus(s.connected ? 'online' : 'offline')
        })
      } catch {
        // 웹 브라우저 환경: navigator.onLine fallback
        setStatus(navigator.onLine ? 'online' : 'offline')

        const handleOnline = () => setStatus('online')
        const handleOffline = () => setStatus('offline')
        window.addEventListener('online', handleOnline)
        window.addEventListener('offline', handleOffline)
        return () => {
          window.removeEventListener('online', handleOnline)
          window.removeEventListener('offline', handleOffline)
        }
      }
    }

    check()
  }, [])

  return status
}
```

`@capacitor/network`를 dynamic import로 가져오는 이유는 웹 브라우저에서 실행될 때 import 자체가 실패하기 때문이다. `try/catch` 안에서 dynamic import하면 Capacitor 없는 환경에서도 에러 없이 fallback으로 넘어간다.

```tsx
// components/AppShell.tsx
'use client'

import { useEffect } from 'react'
import { useNetworkGuard } from '@/hooks/useNetworkGuard'
import NetworkErrorScreen from '@/components/NetworkErrorScreen'

export default function AppShell({ children }: { children: React.ReactNode }) {
  const status = useNetworkGuard()

  useEffect(() => {
    if (status === 'checking') return
    // 네트워크 확인 완료 후 SplashScreen 숨김
    import('@capacitor/splash-screen')
      .then(({ SplashScreen }) => SplashScreen.hide())
      .catch(() => {})   // 웹 브라우저 환경 — 무시
  }, [status])

  if (status === 'checking') {
    // 스플래시가 덮고 있는 동안 빈 화면 유지
    return <div style={{ position: 'fixed', inset: 0, backgroundColor: '#FAFAF8' }} />
  }

  if (status === 'offline') {
    return <NetworkErrorScreen onRetry={() => window.location.reload()} />
  }

  return <>{children}</>
}
```

`status === 'checking'` 동안은 SplashScreen이 화면을 덮고 있으므로 React에서 빈 div를 렌더하면 된다. 확인이 끝나는 순간 `SplashScreen.hide()`가 호출되고 실제 콘텐츠가 나타난다.

## 빌드 흐름

```bash
# 정적 빌드
next build          # out/ 생성

# iOS
npx cap sync ios
npx cap open ios    # Xcode에서 빌드

# Android
npx cap sync android
npx cap open android  # Android Studio에서 빌드
```

`cap sync`는 `webDir`의 정적 파일을 네이티브 프로젝트에 복사하고 플러그인 설정을 동기화한다.
