---
layout: post
title: 배너에 DB 실시간 통계 붙이기 - 클라이언트에서 계산하던 걸 API로 옮긴 이야기
subtitle: 프론트 계산 로직을 서버로 이전하고 동적 게시판 카테고리도 같이
author: HyeongJin
date: 2025-07-09 09:30:00 +0900
categories: Backend
tags: [Django, backend]
sidebar: []
published: true
---

홈 화면 배너에 "등록 배우 X명", "등록 작품 Y편" 같은 수치를 보여주는 기능이 있었다.

초기에는 React Native에서 API로 유저 수, 작품 수를 따로 가져와서 계산했다. API가 늘어나면서 홈 화면 진입 시 요청이 5~6개가 됐다.

## 통계 집계 API

한 번의 요청으로 배너에 필요한 수치를 모두 반환하는 API를 만들었다.

```python
class DBStatsAPIView(APIView):
    def get(self, request):
        from django.db.models import Count

        stats = {
            'user_count': User.objects.filter(is_active=True).count(),
            'media_count': Media.objects.count(),
            'career_count': Career.objects.count(),
            'recent_joined': User.objects.filter(
                date_joined__gte=timezone.now() - timedelta(days=30)
            ).count(),
        }
        return Response(stats)
```

프론트에서 계산하던 `growth` 수치도 서버에서 처리하게 했다.

```python
@property
def growth(self) -> int:
    """이번 달 신규 등록 수"""
    start = timezone.now().replace(day=1, hour=0, minute=0, second=0)
    return Career.objects.filter(created_at__gte=start).count()
```

처음엔 `real_count == growth`일 때 어떻게 표시할지 예외처리가 없었다. 서비스 초기에 전체 count와 이달 count가 같은 경우가 생겼고 프론트에서 `0%` 또는 `NaN`이 찍혔다. 서버에서 `growth >= real_count`면 `real_count` 그대로 반환하도록 처리.

## 동적 게시판 카테고리

기존에 게시판 토픽이 enum으로 하드코딩되어 있었다.

```python
# 기존
class PostTopic(models.TextChoices):
    GENERAL = 'general', '일반'
    NOTICE = 'notice', '공지'
    QNA = 'qna', 'Q&A'
    # 새 카테고리 추가할 때마다 코드 수정 + 배포
```

마케팅 팀에서 "이벤트 게시판 추가해주세요", "잡담 게시판 빼주세요" 요청이 자주 왔다. 코드 수정 없이 관리자 페이지에서 바꿀 수 있게 DB로 옮겼다.

```python
class BoardCategory(models.Model):
    code = models.CharField(max_length=50, unique=True)
    name = models.CharField(max_length=100)
    is_active = models.BooleanField(default=True)
    order = models.IntegerField(default=0)
    is_pinned_supported = models.BooleanField(default=False)

    class Meta:
        ordering = ['order']
```

PostTopic enum을 쓰던 코드를 전부 `BoardCategory` FK로 교체하고, 기존 데이터 마이그레이션.

```python
def migrate_post_topics(apps, schema_editor):
    Post = apps.get_model('community', 'Post')
    BoardCategory = apps.get_model('community', 'BoardCategory')

    topic_map = {
        'general': BoardCategory.objects.get(code='general'),
        'notice': BoardCategory.objects.get(code='notice'),
        'qna': BoardCategory.objects.get(code='qna'),
    }
    for post in Post.objects.all():
        post.board_category = topic_map.get(post.topic)
        post.save(update_fields=['board_category'])
```

## 공지 우선 정렬

공지사항 게시글을 항상 상단에 표시해야 했다.

```python
from django.db.models import Case, When, IntegerField

queryset = Post.objects.annotate(
    pin_order=Case(
        When(is_pinned=True, then=0),
        default=1,
        output_field=IntegerField()
    )
).order_by('pin_order', '-created_at')
```

단순히 `is_pinned=True`인 것들을 앞에 정렬하는 방법. `CASE WHEN`을 annotate로 쓰는 게 익숙하지 않아서 처음엔 Python에서 정렬하려고 했다가 데이터가 많아지면 비효율적이라 DB로 넘겼다.

## 배너 자동 슬라이드

RN 쪽에서 배너 캐러셀 자동 슬라이드를 구현했는데, 화면을 나갔다 돌아올 때 타이머가 중복으로 생성되는 문제가 있었다.

```typescript
useFocusEffect(
  useCallback(() => {
    const timer = setInterval(() => {
      // 다음 배너로 이동
    }, 3000);

    return () => clearInterval(timer);  // 화면 벗어날 때 정리
  }, [])
);
```

`useEffect` 대신 `useFocusEffect`를 쓴 이유가 여기 있다. `useEffect`는 컴포넌트 마운트/언마운트 시에만 실행되는데, React Navigation의 탭 화면은 탭 전환 시 언마운트되지 않아서 타이머 정리가 안 됐다. `useFocusEffect`는 화면 포커스/블러 시 실행된다.
