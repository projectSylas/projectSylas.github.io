---
layout: post
title: Firestore로 실시간 채팅 구현 - React Native에서 쓸 때 주의할 것들
subtitle: 실시간 메시지 동기화와 키보드 레이아웃 이슈
author: HyeongJin
categories: React
tags: [React, frontend, JavaScript]
sidebar: []
published: true
---

클레딧 앱에 DM 기능을 붙였다. 기술 선택은 Firestore. Django 서버에 채팅 테이블을 만드는 것보다 실시간 동기화가 기본으로 제공되기 때문.

## Firestore 데이터 구조

```
chats/
  {chatRoomId}/
    messages/
      {messageId}/
        text: string
        sender_id: string
        created_at: Timestamp
        is_read: boolean
    participants: [userId1, userId2]
    last_message: string
    last_message_at: Timestamp
```

채팅방 목록은 `chats` 컬렉션에, 메시지는 `chats/{id}/messages` 서브컬렉션에.

## 실시간 구독

```typescript
const useMessages = (chatRoomId: string) => {
  const [messages, setMessages] = useState<Message[]>([]);

  useEffect(() => {
    const messagesRef = collection(db, 'chats', chatRoomId, 'messages');
    const q = query(messagesRef, orderBy('created_at', 'asc'));

    const unsubscribe = onSnapshot(q, (snapshot) => {
      const msgs = snapshot.docs.map(doc => ({
        id: doc.id,
        ...doc.data(),
      })) as Message[];
      setMessages(msgs);
    });

    return () => unsubscribe();  // cleanup
  }, [chatRoomId]);

  return messages;
};
```

`onSnapshot`이 실시간 리스너다. 컴포넌트 언마운트 시 `unsubscribe()` 호출하지 않으면 메모리 누수.

## 메시지 전송

```typescript
const sendMessage = async (text: string) => {
  const chatRef = doc(db, 'chats', chatRoomId);
  const messagesRef = collection(db, 'chats', chatRoomId, 'messages');

  const batch = writeBatch(db);

  // 메시지 추가
  const newMsgRef = doc(messagesRef);
  batch.set(newMsgRef, {
    text,
    sender_id: currentUserId,
    created_at: serverTimestamp(),
    is_read: false,
  });

  // 채팅방 last_message 업데이트
  batch.update(chatRef, {
    last_message: text,
    last_message_at: serverTimestamp(),
  });

  await batch.commit();
};
```

`writeBatch`로 메시지 추가와 채팅방 메타 업데이트를 원자적으로 처리. 메시지만 저장되고 last_message 업데이트 실패하는 케이스를 막기 위해.

## Android 키보드 이슈

iOS에서는 잘 됐는데 Android에서 키보드가 올라올 때 채팅 입력창이 키보드 뒤에 가려졌다.

```typescript
// Android 키보드 동작 설정
<KeyboardAvoidingView
  behavior={Platform.OS === 'ios' ? 'padding' : 'height'}
  style={{ flex: 1 }}
  keyboardVerticalOffset={Platform.OS === 'ios' ? 90 : 0}
>
```

Android에서 `behavior='padding'`은 잘 안 됐다. `height`로 바꾸니 해결됐는데, 이건 기기마다 동작이 달라서 테스트 기기를 여러 개로 확인해야 했다.

AndroidManifest.xml 설정도 맞춰야 한다.

```xml
<activity
  android:windowSoftInputMode="adjustResize">
```

`adjustPan`은 화면 전체가 위로 올라가는 방식이라 채팅 UI에서 어색했다. `adjustResize`가 맞다.

## 스로틀링

채팅 입력 중 키 입력마다 Firestore 쓰기가 나가면 비용 문제가 생긴다. 일반 텍스트 메시지는 전송 버튼 누를 때만 쓰면 되는데, "입력 중..." 상태 표시를 위한 타이핑 인디케이터는 쓰로틀이 필요했다.

```typescript
const throttledUpdateTyping = useCallback(
  throttle(async (isTyping: boolean) => {
    await updateDoc(doc(db, 'chats', chatRoomId), {
      [`typing.${currentUserId}`]: isTyping
    });
  }, 2000),  // 2초마다 최대 1회
  [chatRoomId]
);
```

## 자동 스크롤

새 메시지 도착 시 FlatList를 아래로 자동 스크롤.

```typescript
const flatListRef = useRef<FlatList>(null);

useEffect(() => {
  if (messages.length > 0) {
    flatListRef.current?.scrollToEnd({ animated: true });
  }
}, [messages.length]);
```

`messages` 자체가 아니라 `messages.length`를 dependency로 쓴 건 의도적이다. 메시지 내용이 바뀌어도(읽음 처리 등) 스크롤이 트리거되지 않게.

Firestore 실시간 DB는 소규모 채팅에서는 편하지만 트래픽이 늘면 비용이 빠르게 올라간다. 사용량 모니터링은 필수.
