---
layout: post
title: DRF 피드 API N+1 쿼리 전부 잡기
subtitle: prefetch_related와 select_related로 쿼리 43개를 4개로
author: HyeongJin
date: 2025-06-25 10:30:00 +0900
categories: Backend
tags: [Django, PostgreSQL, backend]
sidebar: []
published: true
---

배너 섹션과 피드를 합친 홈 화면 API 응답이 느렸다. 측정해보니 쿼리가 40개 넘게 나가고 있었다.

## 문제 파악

```python
# TimedViewSetMixin으로 측정
[PERF] GET /api/v2/feeds/ | 580ms total | 47 queries | 420ms SQL
```

47개 쿼리. 피드 20개 목록 요청에.

각 쿼리 로그를 보면 패턴이 보였다.

```
SELECT * FROM feeds_feed WHERE id = X  (반복 20회)
SELECT * FROM users_user WHERE id = X  (반복 20회)
SELECT * FROM careers_career WHERE user_id = X  (반복 20회 이상)
```

피드 목록을 가져온 뒤, 각 피드의 작성자를 따로, 각 작성자의 대표 경력을 또 따로 쿼리. 전형적인 N+1.

## FeedSerializer 분석

```python
class FeedSerializer(serializers.ModelSerializer):
    author = UserBriefSerializer(source='user')  # 매 호출마다 user 쿼리
    user_careers = serializers.SerializerMethodField()

    def get_user_careers(self, obj):
        # user마다 추가 쿼리
        return obj.user.careers.filter(is_representative=True)[:3]
```

`SerializerMethodField`에서 `obj.user.careers`에 접근할 때 prefetch가 없으면 매번 SQL이 나간다.

## 수정

QuerySet에 필요한 관계를 모두 선언.

```python
class FeedViewSet(viewsets.ModelViewSet):
    def get_queryset(self):
        return Feed.objects.select_related(
            'user',
            'user__profile',
        ).prefetch_related(
            Prefetch(
                'user__careers',
                queryset=Career.objects.filter(
                    is_representative=True
                ).select_related('role', 'media').order_by('-created_at')[:3],
                to_attr='representative_careers'
            ),
            'likes',
            'comments__user',
        )
```

`Prefetch` 객체를 쓰면 prefetch되는 QuerySet에 조건과 ordering을 줄 수 있다. `to_attr`로 결과를 별도 속성에 저장하면 시리얼라이저에서 접근이 깔끔해진다.

```python
def get_user_careers(self, obj):
    # prefetch된 데이터를 그대로 사용 — 쿼리 없음
    return obj.user.representative_careers
```

## 코멘트 N+1

피드 목록에 최근 댓글 미리보기도 있었는데 여기도 N+1이 있었다.

```python
# 문제
def get_recent_comments(self, obj):
    return obj.comments.all()[:3]  # 각 피드마다 쿼리

# 수정
Prefetch(
    'comments',
    queryset=Comment.objects.select_related('user').order_by('-created_at')[:3],
    to_attr='recent_comments'
)

def get_recent_comments(self, obj):
    return [CommentSerializer(c).data for c in obj.recent_comments]
```

한 가지 주의할 점: `Prefetch`에서 slicing(`[:3]`)을 쓰면 각 feed당 상위 3개를 가져오는 게 아니라 전체 결과에서 3개만 가져온다. 정확히 하려면 별도 subquery나 애플리케이션 레벨에서 잘라야 한다.

## 좋아요 상태 최적화

로그인 유저가 해당 피드를 좋아요 했는지 여부도 매번 쿼리가 나갔다.

```python
# 기존 - 피드마다 쿼리
def get_is_liked(self, obj):
    request = self.context.get('request')
    if not request or not request.user.is_authenticated:
        return False
    return obj.likes.filter(user=request.user).exists()
```

```python
# 수정 - 한 번에 가져와서 set으로 비교
class FeedViewSet(viewsets.ModelViewSet):
    def list(self, request, *args, **kwargs):
        queryset = self.get_queryset()
        liked_ids = set(
            FeedLike.objects.filter(
                user=request.user,
                feed__in=queryset
            ).values_list('feed_id', flat=True)
        ) if request.user.is_authenticated else set()

        serializer = self.get_serializer(queryset, many=True, context={
            **self.get_serializer_context(),
            'liked_ids': liked_ids
        })
```

## 결과

47개 → 4개. 420ms → 22ms SQL. 응답 580ms → 45ms.

N+1은 코드에서 바로 보이지 않는다. 실제 실행된 SQL을 찍어보는 습관이 없으면 놓치기 쉽다.
