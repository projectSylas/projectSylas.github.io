---
layout: post
title: 모바일 디지털 청첩장 — Next.js 14 + Supabase로 3분 만에 링크 발급
subtitle: slug 충돌 방지, bcrypt PIN 수정 보호, IntersectionObserver 스크롤 페이드인, BGM 자동재생 우회까지
author: HyeongJin
date: 2026-04-27 10:00:00 +0900
categories: 웹 개발
tags: [Next.js, Supabase, TypeScript, React, WebDev]
sidebar: []
published: true
---

외부 서비스 없이 링크 하나로 공유하는 모바일 디지털 청첩장을 만들었다. 6가지 테마, 갤러리, BGM, 카카오맵, RSVP, 방명록, 계좌번호 복사, 카카오톡 공유까지 담았다. 스택은 Next.js 14 App Router + Supabase + Capacitor(iOS/Android)다.

## 생성 플로우 — 6단계 위저드

청첩장 하나를 만들기까지 6개 Step 컴포넌트를 순서대로 통과한다.

```
Step1Theme   → 테마 선택 (6가지 미리보기)
Step2Info    → 신랑·신부·부모님·예식 일시·장소 입력
Step3Photo   → 커버 + 갤러리 사진 업로드 (최대 10장)
Step4Greeting → 인사말 프리셋 + 플레이스홀더 자동 치환
Step5Preview  → 실제 청첩장과 동일한 미리보기
Step6Complete → slug URL + 관리 링크 발급
```

`Step5Preview`에서는 `InvitationView`를 `preview=true`로 렌더한다. RSVP/방명록은 숨기고 나머지는 완성본과 동일하게 보여준다.

## slug 생성 — 한글 이름 처리

청첩장 URL은 `/{groom}-{bride}-{YYYYMMDD}` 형태다.

```typescript
function sanitizeName(name: string): string {
  const ascii = name.toLowerCase().replace(/[^a-z0-9]/g, '')
  return ascii || ''
}

export function buildSlugBase(groomName: string, brideName: string, ceremonyDate: string): string {
  const g = sanitizeName(groomName)
  const b = sanitizeName(brideName)
  const d = formatDate(ceremonyDate)  // '2026-09-20' → '20260920'

  // 둘 다 한글이면 날짜 + 랜덤 4자리
  if (!g && !b) return `wedding-${d}-${randomSuffix()}`

  // 하나라도 영문이 있으면 없는 쪽은 'x'로 대체
  return `${g || 'x'}-${b || 'x'}-${d}`
}
```

이름에 영문이 있으면 `jason-minjung-20260920` 같은 읽기 쉬운 slug가 나온다. 한글 이름만 있으면 `wedding-20260920-a3f9`처럼 날짜 + 랜덤 suffix를 붙인다.

같은 날 같은 이름이 중복될 수 있어 충돌 방지 로직도 넣었다.

```typescript
export async function generateUniqueSlug(
  base: string,
  checkExists: (slug: string) => Promise<boolean>,
): Promise<string> {
  if (!(await checkExists(base))) return base

  for (let i = 2; i <= 999; i++) {
    const candidate = `${base}-${i}`
    if (!(await checkExists(candidate))) return candidate
  }
  return `${base}-${Date.now()}`
}
```

`checkExists`는 Supabase에서 해당 slug가 이미 있는지 조회하는 콜백이다. `jason-minjung-20260920`이 이미 있으면 `-2`, `-3`을 붙여 시도한다.

## PIN — bcrypt 해시로 수정 보호

청첩장을 만든 사람만 수정할 수 있어야 한다. 별도 로그인 없이 4자리 PIN으로 해결했다. PIN은 평문을 저장하지 않고 bcrypt로 해시해서 DB에 저장한다.

```typescript
// POST /api/invitations — 생성 시
const pin_hash = await bcrypt.hash(body.pin, 10)
await supabase.from('invitations').insert({ ...rest, slug, pin_hash })
```

수정 요청 시 PIN 검증을 먼저 한다.

```typescript
// PATCH /api/invitations/[id]/edit — 수정 시
const valid = await bcrypt.compare(pin, inv.pin_hash)
if (!valid) return NextResponse.json({ error: 'PIN이 올바르지 않습니다.' }, { status: 401 })

// 수정 불가 필드 제거 후 update
const { theme_id, slug, pin_hash, id, created_at, ...updateFields } = fields
await supabase.from('invitations').update(updateFields).eq('id', id)
```

`theme_id`, `slug`, `pin_hash`는 생성 후 변경 불가다. 구조분해로 제거하고 나머지만 update한다.

## 오프닝 오버레이 — CSS transition width 애니메이션

청첩장 링크를 처음 열면 오프닝 화면이 나온다. 이름과 날짜가 페이드인된 뒤 "청첩장 열기" 버튼을 탭하면 메인 콘텐츠로 전환된다. 상단·하단 장식선이 펼쳐지는 효과는 width transition으로 구현했다.

```tsx
// width를 0 → 48px로 transition
<div
  style={{
    width: textIn ? '48px' : '0',
    height: '1px',
    backgroundColor: accentColor,
    transition: 'width 1s ease 0.2s',  // 0.2초 딜레이 후 1초 동안
  }}
/>
```

텍스트 진입 애니메이션은 `opacity + translateY`로 처리한다.

```tsx
const [textIn, setTextIn] = useState(false)
useEffect(() => {
  const t = setTimeout(() => setTextIn(true), 300)  // 300ms 후 진입
  return () => clearTimeout(t)
}, [])
```

"청첩장 열기" 탭 시 `setFadeOut(true)` → 600ms 후 오버레이 언마운트 + `onOpen()` 호출이다.

## 스크롤 페이드인 — IntersectionObserver

뷰어 각 섹션은 스크롤 진입 시 아래에서 위로 올라오며 나타난다. 외부 라이브러리 없이 `IntersectionObserver`로 구현했다.

```tsx
function FadeSection({ children, delay = 0 }) {
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const el = ref.current
    if (!el) return
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) {
          el.style.opacity = '1'
          el.style.transform = 'translateY(0)'
          observer.disconnect()  // 한 번만 발동
        }
      },
      { threshold: 0.1 },  // 10% 보이면 트리거
    )
    observer.observe(el)
    return () => observer.disconnect()
  }, [])

  return (
    <div
      ref={ref}
      style={{
        opacity: 0,
        transform: 'translateY(40px)',
        transition: `opacity 0.7s ease ${delay}ms, transform 0.7s ease ${delay}ms`,
      }}
    >
      {children}
    </div>
  )
}
```

`observer.disconnect()`를 진입 직후 호출해서 한 번 나타난 섹션이 스크롤을 올릴 때 다시 사라지지 않게 했다.

## BGM 자동재생 우회

브라우저는 사용자 인터랙션 없이 `audio.play()`를 차단한다. 오프닝 오버레이의 "청첩장 열기" 탭이 인터랙션이므로 그 시점에 재생을 시도한다.

```tsx
const audioRef = useRef<HTMLAudioElement | null>(null)

function handleOpen() {
  setOpened(true)
  if (audioRef.current) {
    audioRef.current.play().catch(() => {})  // 실패해도 무시
  }
}
```

`BgmPlayer` 컴포넌트는 `canplaythrough` 이벤트 전까지 버튼을 반투명(opacity 0.4)으로 표시한다. 로드 전 탭하면 아무 일도 일어나지 않는 혼란을 방지한다.

```tsx
audio.addEventListener('canplaythrough', () => setLoaded(true))
// 버튼: opacity: loaded ? 1 : 0.4
```

## 달력 위젯 — 라이브러리 없이 직접 구현

예식일 달력은 react-datepicker 같은 라이브러리 없이 직접 계산했다.

```tsx
const firstDay = new Date(year, month, 1).getDay()  // 1일의 요일
const daysInMonth = new Date(year, month + 1, 0).getDate()  // 해당 월 총 일수

const cells = [
  ...Array(firstDay).fill(null),         // 앞쪽 빈 칸
  ...Array.from({ length: daysInMonth }, (_, i) => i + 1),
]
// 7의 배수가 될 때까지 null 추가
while (cells.length % 7 !== 0) cells.push(null)
```

CSS Grid `gridTemplateColumns: 'repeat(7, 1fr)'`로 7열 달력을 만들고, 예식일에만 `backgroundColor: 'var(--theme-accent)'`로 하이라이트한다.

## OG 메타데이터 — 카카오/SNS 미리보기

`generateMetadata`로 slug마다 동적 OG 태그를 생성한다.

```typescript
export async function generateMetadata({ params }): Promise<Metadata> {
  const invitation = await getInvitation(slug)

  return {
    title: `${invitation.groom_name} ♡ ${invitation.bride_name}의 청첩장`,
    description: `${dateStr}, ${invitation.venue_name}에서 결혼합니다.`,
    openGraph: {
      images: invitation.cover_image_url
        ? [{ url: invitation.cover_image_url, width: 1200, height: 630 }]
        : [{ url: '/images/og-default.jpg' }],
    },
  }
}
```

커버 사진이 있으면 그 사진이 카카오톡 미리보기 이미지로 나온다.

## 카카오톡 공유 — SDK 동적 로드

Kakao JS SDK는 사용 시점에 동적으로 로드한다.

```tsx
useEffect(() => {
  const jsKey = process.env.NEXT_PUBLIC_KAKAO_JS_KEY
  if (!jsKey || (window as any).Kakao) return  // 이미 로드됐으면 skip
  const script = document.createElement('script')
  script.src = 'https://developers.kakao.com/sdk/js/kakao.min.js'
  script.onload = () => {
    const K = (window as any).Kakao
    if (K && !K.isInitialized()) K.init(jsKey)
  }
  document.head.appendChild(script)
}, [])
```

카카오 SDK가 없거나 초기화 실패 시 `handleKakaoShare`는 링크 복사로 fallback한다.

## 지도 앱 3종 딥링크

카카오맵, 네이버지도, Tmap을 URL scheme으로 연결한다.

```tsx
const buttons = [
  { label: '카카오맵', url: `https://map.kakao.com/?q=${enc(venueAddress)}` },
  { label: '네이버지도', url: `https://map.naver.com/search?query=${enc(venueName + ' ' + venueAddress)}` },
  { label: 'Tmap', url: `https://tmap.life/share?name=${enc(venueName)}&address=${enc(venueAddress)}` },
]
```

모바일에서 해당 앱이 설치돼 있으면 앱으로 연결되고, 없으면 웹으로 연결된다.

## 구조 요약

```
Next.js 14 App Router
├── /              랜딩 + 청첩장 목록
├── /create        6단계 생성 위저드
├── /i/[slug]      청첩장 뷰어 (SSR + 동적 OG)
├── /edit/[slug]   PIN 인증 후 수정
├── /manage/[slug] 관리 (방명록·RSVP 현황)
└── /api/...       Supabase CRUD + 파일 업로드

Supabase
├── invitations    청첩장 데이터 (pin_hash 포함)
├── invitation_photos  갤러리 사진
├── rsvp           참석 여부
└── guestbook      방명록

Storage
├── covers/        커버 사진
└── photos/        갤러리 사진
```

Capacitor로 빌드하면 iOS/Android 앱으로도 배포할 수 있다. 웹뷰 기반이라 Next.js 코드 변경 없이 앱 스토어에 올릴 수 있다.
