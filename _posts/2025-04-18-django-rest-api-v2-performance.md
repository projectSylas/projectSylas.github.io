---
layout: post
title: Django REST API v2 전환과 쿼리 성능 최적화
subtitle: MediaMetaInfo 모델 도입과 select_related 전면 적용
author: HyeongJin
date: 2025-04-18 11:00:00 +0900
categories: Backend
tags: [Django, PostgreSQL, backend]
sidebar: []
published: true
---

클레딧 API가 v1에서 v2로 전환되는 시점에 성능 최적화를 같이 진행했다.

기존 `Media` 모델에 메타 정보가 너무 많이 쌓여 있었다. 시즌, 에피소드, 제작사 정보까지 한 테이블에. v2에서는 `MediaMetaInfo`를 별도 모델로 분리했다.

## 모델 구조 변경

```python
# v1 - 하나의 모델에 다 때려넣기
class Media(models.Model):
    title = models.CharField(max_length=200)
    year = models.IntegerField(null=True)
    genre = models.CharField(max_length=100, null=True)
    director = models.CharField(max_length=100, null=True)
    episode_count = models.IntegerField(null=True)
    # ... 30개 필드

# v2 - 확장 가능한 구조
class Media(models.Model):
    title = models.CharField(max_length=200)
    media_type = models.CharField(max_length=20)

class MediaMetaInfo(models.Model):
    media = models.OneToOneField(Media, on_delete=models.CASCADE, related_name='meta')
    year = models.IntegerField(null=True)
    genre = models.CharField(max_length=100, null=True)
    episode_count = models.IntegerField(null=True)
    release_date = models.DateField(null=True)
```

v2 API URL 구조도 정리.

```python
# urls.py
urlpatterns = [
    path('v1/mediae/', MediaeViewSetV1.as_view({'get': 'list'})),
    path('v2/mediae/', MediaeViewSetV2.as_view({'get': 'list'})),
    path('v2/mediae/<uuid:pk>/', MediaeViewSetV2.as_view({'get': 'retrieve'})),
]
```

## N+1 쿼리 문제

v2 작업하면서 기존 v1 코드에서 N+1을 발견했다.

```python
# 문제 코드
class MediaeViewSet(viewsets.ModelViewSet):
    def list(self, request):
        mediae = Media.objects.all()[:20]
        # 각 media 접근 시 추가 쿼리 발생
        data = [{'title': m.title, 'meta': m.meta.year} for m in mediae]
```

`m.meta`에 접근할 때마다 SQL이 나갔다. 20개 목록이면 21개 쿼리.

```python
# 수정
queryset = Media.objects.select_related('meta').prefetch_related(
    'careers__user',
    'careers__role',
)[:20]
```

`select_related`는 FK/OneToOne 관계, `prefetch_related`는 M2M이나 역참조. 헷갈리기 쉬운 부분.

`select_related`는 JOIN으로 한 번에 가져오고, `prefetch_related`는 별도 쿼리로 가져온 뒤 Python에서 연결한다. 결과는 비슷해 보여도 실행되는 SQL이 다르다.

## 피드 UUID 버그

v2 전환 과정에서 피드 카드에 UUID가 잘못 전달되는 버그가 있었다.

```python
# 버그: 시리얼라이저에서 uuid 대신 pk(int)가 넘어가던 코드
class FeedSerializer(serializers.ModelSerializer):
    media_id = serializers.IntegerField(source='media.pk')  # 잘못됨
    # media_uuid = serializers.UUIDField(source='media.uuid')  # 올바름
```

프론트에서 `media_id`로 상세 API를 호출하는데 UUID 기반 endpoint에 int를 넘겨서 404가 났다. 시리얼라이저 필드 이름과 source를 같이 잘못 써서 생긴 문제.

## Career 중복 방지

```python
class CareerTakeBulkSerializerV2(serializers.ListSerializer):
    def create(self, validated_data):
        careers = [Career(**item) for item in validated_data]
        try:
            return Career.objects.bulk_create(
                careers,
                ignore_conflicts=True  # (media, role) unique 조건 위반 시 무시
            )
        except IntegrityError:
            raise serializers.ValidationError("중복된 경력이 포함되어 있습니다.")
```

처음엔 `ignore_conflicts=True`로 조용히 넘어가게 했는데, 어느 케이스에서 `IntegrityError`가 여전히 발생했다. DB 레벨에서 unique 조건이 `(media, role, user)` 세 컬럼이었는데 `ignore_conflicts`가 정확히 이 세 컬럼의 조합을 인식 못 하는 경우가 있었다. 결국 벌크 처리 전 Python 단에서 dedup 먼저 하는 방식으로 바꿨다.

## 결과

v2 전환 후 목록 API: 쿼리 21개 → 3개, 응답 430ms → 40ms. 가장 큰 차이는 select_related 적용이었다.
