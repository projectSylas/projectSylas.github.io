---
layout: post
title: 디지털 청첩장 버그 수정 — BGM CDN 차단·카카오맵 중복 삽입·URL.createObjectURL 누수·API 입력 검증
subtitle: bensound CDN 차단 후 자체 호스팅 전환, script id 중복 삽입 방지, 갤러리 메모리 누수 cleanup, guestbook·RSVP 서버 검증 추가까지
author: HyeongJin
date: 2026-04-27 14:00:00 +0900
categories: 웹 개발
tags: [Next.js, Supabase, TypeScript, React, WebDev]
sidebar: []
published: true
---

초기 구현 이후 실제로 청첩장 링크를 공유해보니 BGM이 안 나오고, 카카오맵이 두 번 초기화되는 문제가 발견됐다. 보안 검토를 하면서 API 입력 검증도 빠져 있다는 걸 확인했다. 이 포스트는 배포 후 발견된 버그와 보안 취약점 수정 과정을 정리한다.

## BGM — bensound CDN 차단 → 자체 호스팅

초기 구현에서 BGM 파일은 bensound.com CDN URL을 그대로 사용했다.

```typescript
// 수정 전
export const BGM_PRESETS = [
  { id: 'piano', url: 'https://www.bensound.com/bensound-music/bensound-romantic.mp3' },
  { id: 'strings', url: 'https://www.bensound.com/bensound-music/bensound-love.mp3' },
  { id: 'acoustic', url: 'https://www.bensound.com/bensound-music/bensound-tenderness.mp3' },
  { id: 'classic', url: 'https://www.bensound.com/bensound-music/bensound-memories.mp3' },
]
```

Referer 기반 핫링크 차단으로 `audio.play()`가 실패했다. `<audio>` 태그의 `src`를 외부 CDN으로 걸면 해당 서비스가 Referer를 확인해서 차단할 수 있다 — bensound가 정확히 그런 정책을 쓰고 있었다.

해결은 MP3를 `public/bgm/`에 직접 넣는 것이다.

```
public/
└── bgm/
    ├── piano.mp3     (드보르작 로맨틱 소품 1번)
    ├── strings.mp3   (드보르작 로맨틱 소품 2번)
    ├── acoustic.mp3  (드보르작 로맨틱 소품 3번)
    └── classic.mp3   (드보르작 로맨틱 소품 4번)
```

```typescript
// 수정 후
export const BGM_PRESETS = [
  { id: 'piano', label: '피아노', desc: '드보르작 로맨틱 소품 1번', url: '/bgm/piano.mp3' },
  { id: 'strings', label: '현악', desc: '드보르작 로맨틱 소품 2번', url: '/bgm/strings.mp3' },
  { id: 'acoustic', label: '어쿠스틱', desc: '드보르작 로맨틱 소품 3번', url: '/bgm/acoustic.mp3' },
  { id: 'classic', label: '클래식', desc: '드보르작 로맨틱 소품 4번', url: '/bgm/classic.mp3' },
]
```

상대 경로 `/bgm/*.mp3`는 Next.js가 `public/` 디렉토리를 정적 파일로 서빙한다. CDN 의존성이 사라지고 재생이 안정적으로 된다.

## 카카오맵 — 스크립트 중복 삽입 버그

`KakaoMap` 컴포넌트가 언마운트 후 다시 마운트되면(React 18 Strict Mode, 라우팅) `<script>` 태그가 `<head>`에 두 번 들어가는 문제가 있었다.

```typescript
// 수정 전 — 중복 삽입 가능
const script = document.createElement('script')
script.src = `//dapi.kakao.com/v2/maps/sdk.js?appkey=${apiKey}&libraries=services&autoload=false`
script.onload = () => window.kakao.maps.load(initMap)
document.head.appendChild(script)
```

문제는 두 가지였다.

1. `window.kakao?.maps` 체크가 `maps.services` 로드까지 기다리지 않는다 — SDK가 부분 로드된 상태에서도 truthy가 될 수 있다
2. 이미 삽입된 스크립트가 있는데 또 삽입하면 SDK가 두 번 초기화되면서 지도가 렌더링되지 않거나 에러가 난다

수정 코드:

```typescript
// 이미 완전히 로드된 경우 — maps.services까지 확인
if (window.kakao?.maps?.services) {
  initMap()
  return
}

// 스크립트 중복 삽입 방지 — id로 체크
const existingScript = document.getElementById('kakao-map-sdk')
if (existingScript) {
  // 로드 중인 경우 — 100ms마다 체크
  const waitForLoad = setInterval(() => {
    if (window.kakao?.maps?.services) {
      clearInterval(waitForLoad)
      initMap()
    }
  }, 100)
  return () => clearInterval(waitForLoad)
}

const script = document.createElement('script')
script.id = 'kakao-map-sdk'  // id 부여
script.src = `https://dapi.kakao.com/v2/maps/sdk.js?appkey=${apiKey}&libraries=services&autoload=false`
script.onload = () => window.kakao.maps.load(initMap)
script.onerror = () => setError(true)  // 로드 실패 처리 추가
document.head.appendChild(script)
```

세 가지를 바꿨다.

- `window.kakao?.maps` → `window.kakao?.maps?.services` : `services` 라이브러리까지 로드됐을 때만 true
- `script.id = 'kakao-map-sdk'` : `getElementById`로 중복 삽입 방지
- `script.onerror` : 네트워크 오류나 앱키 오류 시 에러 화면으로 fallback
- `//dapi.kakao.com` → `https://dapi.kakao.com` : 프로토콜 명시

`waitForLoad` interval은 cleanup 함수(`return () => clearInterval(waitForLoad)`)로 언마운트 시 정리한다.

## PIN 필수화 — 선택에서 필수로

초기 구현에서 PIN은 "선택 사항"이었다.

```typescript
// 수정 전 — PIN 없어도 생성 가능
if (pin && (pin.length < 4 || !/^\d+$/.test(pin))) {
  setPinError('PIN은 숫자 4자리로 입력해 주세요.')
  return
}
```

`pin`이 빈 문자열이면 `if (pin && ...)` 조건 자체가 통과돼 PIN 없이 생성된다. 문제는 이렇게 생성된 청첩장은 수정 자체가 불가능하다는 것이다. 이후 날짜나 장소 오타를 수정하려면 PIN이 반드시 있어야 한다.

프론트엔드와 API 양쪽에서 모두 강제 검증으로 바꿨다.

### 프론트엔드 (Step5Preview)

```typescript
function handleSubmit() {
  if (!pin) {
    setPinError('PIN은 필수입니다. 숫자 4자리를 입력해 주세요.')
    return
  }
  if (pin.length < 4 || !/^\d+$/.test(pin)) {
    setPinError('PIN은 숫자 4자리로 입력해 주세요.')
    return
  }
  onSubmit()
}
```

### API (POST /api/invitations)

```typescript
export async function POST(request: Request) {
  const body = await request.json()

  if (!body.pin || !/^\d{4}$/.test(body.pin)) {
    return NextResponse.json(
      { error: 'PIN은 숫자 4자리가 필수입니다.' },
      { status: 400 }
    )
  }
  // ...
}
```

프론트에서 한 번 막고, API에서 한 번 더 막는다. 프론트 검증을 우회해서 직접 API를 호출해도 PIN 없는 청첩장은 생성되지 않는다.

UI 레이블도 바꿨다.

```tsx
// 수정 전: "수정용 PIN (선택)"
// 수정 후: "수정용 PIN *"
<label>수정용 PIN <span style={{ color: '#e57373' }}>*</span> — 나중에 청첩장 내용을 수정할 때 사용합니다</label>

// 하단 안내 문구도 변경
// 수정 전: "PIN 없이도 생성 가능하지만 수정이 불가합니다"
// 수정 후: "PIN은 청첩장 수정 시 사용됩니다. 분실 시 수정이 불가합니다."
```

## API 입력 검증 — 방명록·RSVP

초기 구현에서는 필수 필드 존재 여부만 확인하고 내용의 길이나 타입을 검증하지 않았다. 악의적인 요청으로 무제한으로 긴 문자열을 DB에 넣거나, RSVP의 `attendance` 필드에 임의 값을 넣는 게 가능했다.

### 방명록 (guestbook)

```typescript
// 수정 전
await supabase.from('guestbook').insert({ invitation_id, name: name.trim(), message: message.trim() })

// 수정 후
if (typeof name !== 'string' || name.trim().length > 50) {
  return NextResponse.json({ error: '이름은 50자 이하여야 합니다.' }, { status: 400 })
}
if (typeof message !== 'string' || message.trim().length > 500) {
  return NextResponse.json({ error: '메시지는 500자 이하여야 합니다.' }, { status: 400 })
}

// insert 시에도 slice로 한 번 더 truncate
await supabase.from('guestbook').insert({
  invitation_id,
  name: name.trim().slice(0, 50),
  message: message.trim().slice(0, 500),
})
```

`typeof` 체크로 타입을 확인하고, 길이 초과 시 400을 반환한다. insert 직전에도 `.slice(0, 50)` / `.slice(0, 500)`으로 한 번 더 자른다 — 검증과 truncate를 이중으로 걸어서 DB에는 절대 초과 길이가 들어가지 않는다.

### RSVP

```typescript
// attendance enum 검증
if (!['yes', 'no', 'maybe'].includes(attendance)) {
  return NextResponse.json({ error: '참석 여부 값이 올바르지 않습니다.' }, { status: 400 })
}

// headcount 범위 clamp
const safeHeadcount = Math.min(Math.max(1, Number(headcount) || 1), 20)

// name 길이 검증
if (typeof name !== 'string' || name.trim().length === 0 || name.trim().length > 50) {
  return NextResponse.json({ error: '이름은 1~50자여야 합니다.' }, { status: 400 })
}
```

`attendance`는 `['yes', 'no', 'maybe']` 외 값을 차단한다. `headcount`는 `Math.min(Math.max(1, ...), 20)`으로 1~20 범위를 강제한다. `Number()` 변환 실패 시 `|| 1` 폴백으로 최소 1을 보장한다.

## 갤러리 URL.createObjectURL 메모리 누수

사진 미리보기에 `URL.createObjectURL(file)`을 쓸 때 cleanup을 빠뜨리면 메모리 누수가 생긴다. 파일을 교체하거나 삭제할 때마다 이전 Object URL이 GC되지 않고 남는다.

```typescript
// 수정 전 — cleanup 없음
const [coverPreviewUrl, setCoverPreviewUrl] = useState<string | null>(null)
// ... file 변경 시 setCoverPreviewUrl(URL.createObjectURL(file))
```

```typescript
// 수정 후 — useEffect cleanup으로 revoke
useEffect(() => {
  if (!file) { setCoverPreviewUrl(null); return }
  const url = URL.createObjectURL(file)
  setCoverPreviewUrl(url)
  return () => URL.revokeObjectURL(url)  // 다음 파일로 바뀌면 이전 URL 해제
}, [file])

useEffect(() => {
  const urls = galleryFiles.map(f => URL.createObjectURL(f))
  setGalleryUrls(urls)
  return () => urls.forEach(u => URL.revokeObjectURL(u))  // 갤러리 전체 해제
}, [galleryFiles])
```

`useEffect`의 cleanup 함수(return)는 의존성 변경 시와 언마운트 시 둘 다 실행된다. 파일을 교체할 때마다 이전 Object URL이 즉시 해제된다. 갤러리는 `galleryFiles` 배열이 바뀔 때마다 이전 배열의 URL들을 일괄 해제한다.

## 요약

| 이슈 | 원인 | 수정 |
|------|------|------|
| BGM 무음 | bensound CDN 핫링크 차단 | `public/bgm/`에 MP3 자체 호스팅 |
| 카카오맵 이중 초기화 | script 중복 삽입, `maps.services` 미확인 | `script.id` 체크 + `waitForLoad` interval |
| PIN 없는 청첩장 생성 | `if (pin && ...)` 조건 — 빈 값 통과 | `if (!pin)` 먼저 체크, API 서버에서도 검증 |
| 방명록·RSVP 무제한 입력 | 서버 검증 없음 | 길이 상한·타입 체크·enum 검증 추가 |
| 갤러리 메모리 누수 | `URL.createObjectURL` cleanup 미처리 | `useEffect` return에서 `URL.revokeObjectURL` |

초기 구현이 동작하는 것과 프로덕션에서 안전하게 동작하는 것 사이의 간극이다. 외부 CDN에 직접 링크를 걸면 그 서비스의 정책에 종속된다. 서버 API는 프론트엔드 검증과 독립적으로 입력을 검증해야 한다. Object URL은 `useEffect` cleanup으로 반드시 해제해야 한다.
