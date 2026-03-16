---
layout: post
title: React Native 메모리 누수 잡기 - FlatList, React Query, 타이머
subtitle: JS 프레임 드랍과 메모리 누수를 유발한 세 가지 패턴
author: HyeongJin
date: 2025-07-18 10:00:00 +0900
categories: React
tags: [React, frontend, JavaScript]
sidebar: []
published: true
---

앱 사용 중 스크롤이 점점 버벅거린다는 피드백이 있었다. 오래 쓸수록 심해졌다.

메모리 누수 세 군데에서 찾았다.

## 1. BannerSection 타이머 누적

배너 자동 슬라이드 타이머가 화면 전환 시 제거되지 않았다. 탭을 왔다갔다 할수록 타이머가 쌓였다.

```typescript
// 문제 코드
useEffect(() => {
  const timer = setInterval(slideNext, 3000);
  // 반환값 없음 - cleanup 없음
}, []);
```

React Navigation에서 탭 화면은 탭 전환해도 unmount되지 않는다. `useEffect`의 cleanup이 호출 안 된다.

```typescript
// 수정 - useFocusEffect 사용
useFocusEffect(
  useCallback(() => {
    const timer = setInterval(slideNext, 3000);
    return () => clearInterval(timer);
  }, [currentIndex])
);
```

탭 포커스를 잃을 때 타이머를 정리하고, 돌아올 때 새로 시작.

## 2. React Query 캐시 설정

React Query가 기본적으로 inactive query를 5분 보관한다. 컴포넌트가 언마운트되도 데이터는 캐시에 남는다. 미디어 상세 페이지 이미지 데이터가 계속 쌓였다.

```typescript
// 문제: 기본 캐시 설정으로 미디어 이미지 데이터가 누적
const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 1000 * 60 * 5,  // 5분
      gcTime: 1000 * 60 * 10,    // 10분 캐시 유지
    }
  }
});
```

상세 페이지처럼 데이터가 큰 화면은 캐시를 짧게.

```typescript
// MediaDetail 쿼리 - 짧은 gcTime
const { data } = useQuery({
  queryKey: ['media', mediaId],
  queryFn: () => fetchMediaDetail(mediaId),
  gcTime: 1000 * 60,   // 1분으로 단축
  staleTime: 1000 * 30,
});
```

네비게이터 레벨에서 `unmountOnBlur` 옵션도 추가했다. MediaDetail 스크린은 화면을 벗어나면 언마운트하도록.

```typescript
<Stack.Screen
  name="MediaDetail"
  component={MediaDetailScreen}
  options={{ unmountOnBlur: true }}
/>
```

## 3. FlatList 중첩 구조와 가상화

사용자 목록 화면에 FlatList 안에 또 FlatList가 있었다. React Native에서 중첩 스크롤 뷰는 가상화가 제대로 안 된다.

```typescript
// 문제: 중첩 FlatList
<FlatList
  data={sections}
  renderItem={({ item }) => (
    <FlatList  // 중첩 - 가상화 안 됨
      data={item.users}
      renderItem={renderUser}
    />
  )}
/>
```

`SectionList`로 교체했다.

```typescript
<SectionList
  sections={sectionData}
  renderItem={({ item }) => <UserCard user={item} />}
  renderSectionHeader={({ section }) => <SectionHeader title={section.title} />}
  keyExtractor={(item) => item.id}
  getItemLayout={(_, index) => ({
    length: USER_CARD_HEIGHT,
    offset: USER_CARD_HEIGHT * index,
    index,
  })}
/>
```

`getItemLayout`을 구현하면 스크롤 위치 계산이 정확해지고 가상화 성능이 좋아진다.

## 4. setTimeout cleanup

여러 컴포넌트에서 `setTimeout`을 쓰면서 cleanup을 안 한 케이스들.

```typescript
// 문제 패턴
const handlePress = () => {
  setTimeout(() => {
    setLoading(false);
  }, 1000);
};

// 수정
const timerRef = useRef<ReturnType<typeof setTimeout>>();

const handlePress = () => {
  timerRef.current = setTimeout(() => {
    setLoading(false);
  }, 1000);
};

useEffect(() => {
  return () => {
    if (timerRef.current) clearTimeout(timerRef.current);
  };
}, []);
```

채팅 화면, 알림 화면, UI 컴포넌트 여러 곳에서 이 패턴이 있었다. 일괄 수정.

## 결과

수정 후 Flipper 메모리 프로파일러로 측정했을 때 30분 사용 기준 메모리가 안정적으로 유지됐다. 이전에는 30분 후 200MB+로 치솟았는데 수정 후 100MB 내외로 안정.
