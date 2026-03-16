---
layout: post
title: React Native BottomSheet 크로스플랫폼 이슈 정리
subtitle: iOS/Android 동작 차이와 snap points 설계
author: HyeongJin
date: 2025-09-18 09:30:00 +0900
categories: React
tags: [React, frontend, JavaScript]
sidebar: []
published: true
---

앱 여러 화면에서 BottomSheet를 썼는데, iOS에서는 자연스러운 게 Android에서 어색하거나 그 반대인 경우가 계속 생겼다. 전체 리팩터링을 하면서 정리했다.

라이브러리는 `@gorhom/bottom-sheet`를 썼다.

## snap points 문제

기존 코드에서 snap points를 퍼센트로 고정해놨다.

```typescript
// 기존 - 단순 고정값
const snapPoints = ['25%', '50%', '90%'];
```

문제는 콘텐츠 높이에 따라 25%가 너무 낮거나 너무 높은 케이스가 생겼다. 댓글이 1개인데 50% 높이가 되거나, 댓글이 50개인데 90%가 부족하거나.

동적으로 콘텐츠 높이에 맞추되, 최소/최대 경계를 두는 방식으로 변경했다.

```typescript
const [contentHeight, setContentHeight] = useState(300);

const snapPoints = useMemo(() => {
  const minHeight = '10%';
  const contentPercent = Math.min(
    Math.max((contentHeight / SCREEN_HEIGHT) * 100, 30),
    93
  );
  return [minHeight, `${contentPercent}%`];
}, [contentHeight]);

// 콘텐츠 측정
<BottomSheetScrollView
  onContentSizeChange={(_, height) => setContentHeight(height)}
>
```

`Math.min(Math.max(...), 93)` 범위를 30~93%로 제한. 93%로 상단에 약간의 여유를 남겨야 status bar가 가려지지 않는다.

더 세밀한 제어가 필요한 화면에서는 1% 단위 snap points를 썼다.

```typescript
// 10%부터 93%까지 1% 간격
const detailedSnapPoints = useMemo(
  () => Array.from({ length: 84 }, (_, i) => `${10 + i}%`),
  []
);
```

snap points가 촘촘할수록 드래그 중 자연스럽게 멈추는 느낌이 든다.

## iOS vs Android 동작 차이

**백드롭 처리:**

```typescript
<BottomSheet
  backdropComponent={(props) => (
    <BottomSheetBackdrop
      {...props}
      disappearsOnIndex={0}
      appearsOnIndex={1}
      // iOS: opacity 애니메이션 부드럽게 동작
      // Android: 간헐적으로 백드롭이 시트보다 늦게 사라지는 문제
    />
  )}
>
```

Android에서 백드롭 사라지는 타이밍 이슈는 `opacity` 대신 `pressBehavior='none'`으로 설정하고 직접 닫기 핸들러를 연결해서 해결했다.

**키보드 동작:**

iOS에서는 `keyboardBehavior='interactive'`가 자연스럽게 동작했는데 Android에서 BottomSheet가 키보드와 겹치거나 키보드 위에 붕 뜨는 현상이 있었다.

```typescript
<BottomSheet
  keyboardBehavior={Platform.OS === 'ios' ? 'interactive' : 'fillParent'}
  keyboardBlurBehavior="restore"
  android_keyboardInputMode="adjustResize"
>
```

Android에서는 `fillParent`가 안정적이었다. 시트가 키보드 위에 쌓이는 대신 키보드를 포함한 전체 영역을 채우는 방식.

## 홈 화면 재설계

BottomSheet 리팩터링과 함께 홈 화면 전체 구조도 손봤다. 기존에는 탭마다 별도 컴포넌트였는데, 공통 데이터를 각각 fetch하면서 중복 API 호출이 생겼다.

```typescript
// 홈 화면 데이터를 상위에서 한 번만 fetch
const HomeScreen = () => {
  const { data: feedData } = useQuery(['feeds'], fetchFeeds);
  const { data: stats } = useQuery(['stats'], fetchDBStats);
  const { data: banners } = useQuery(['banners'], fetchBanners);

  return (
    <View>
      <BannerSection stats={stats} banners={banners} />
      <FeedList data={feedData} />
    </View>
  );
};
```

React Query의 캐시 덕분에 같은 key로 여러 컴포넌트에서 useQuery를 호출해도 실제 fetch는 한 번만 일어난다. 컴포넌트 구조를 어떻게 설계하든 데이터 페칭은 중복되지 않는다.

## Android 15 대응

Android 15에서 앱이 16KB 메모리 페이지 크기를 지원해야 한다. 기존 빌드가 8KB 기준이라 Play Store에서 경고.

```groovy
// android/app/build.gradle
android {
    ...
    packagingOptions {
        jniLibs {
            useLegacyPackaging = false
        }
    }
}
```

네이티브 라이브러리들이 16KB 페이지 정렬을 지원하는 버전으로 업데이트가 필요한 경우도 있었다. `@gorhom/bottom-sheet`는 별도 수정 없이 됐는데, 일부 카메라/미디어 라이브러리는 버전 업그레이드가 필요했다.
