---
layout: post
title: Django ViewSet에 쿼리 실행 시간 측정 붙이기
subtitle: 성능 병목을 찾기 전에 먼저 측정부터
author: HyeongJin
categories: Backend
tags: [Django, PostgreSQL, backend]
sidebar: []
published: true
---

API 응답이 느리다는 제보를 받았다. 얼마나 느린지 수치가 없으면 어디서 시작해야 할지 모른다.

QuerySet 실행 시간을 로그로 남기는 미들웨어를 붙이는 방법도 있지만, 특정 ViewSet 메서드 단위로 시간을 찍고 싶었다. 미들웨어는 요청 전체 시간이라 쿼리가 많은 뷰에서 어떤 쿼리가 병목인지 파악하기 어렵다.

## 접근 방법

`connection.queries`를 활용했다. Django `DEBUG=True` 환경에서는 실행된 모든 SQL과 시간이 기록된다.

```python
from django.db import connection, reset_queries
import time

class TimedViewSetMixin:
    """ViewSet에 쿼리 실행 시간 측정 기능을 추가하는 Mixin"""

    def dispatch(self, request, *args, **kwargs):
        reset_queries()
        start = time.perf_counter()

        response = super().dispatch(request, *args, **kwargs)

        elapsed = (time.perf_counter() - start) * 1000  # ms
        query_count = len(connection.queries)
        total_sql_time = sum(
            float(q['time']) * 1000 for q in connection.queries
        )

        logger.info(
            "[PERF] %s %s | %.1fms total | %d queries | %.1fms SQL",
            request.method, request.path,
            elapsed, query_count, total_sql_time
        )
        return response
```

`CareersViewSet`과 `MediaeViewSet`에 붙였다.

```python
class CareersViewSet(TimedViewSetMixin, viewsets.ModelViewSet):
    ...
```

## 실제로 나온 숫자

커리어 목록 API: 쿼리 43개, SQL 시간 380ms, 총 응답 430ms.

43개 쿼리면 N+1이 터지고 있다는 뜻이다. 페이지당 20개 결과에 각각 2~3개 쿼리가 추가로 발생하는 패턴.

로그에 각 쿼리 SQL도 같이 찍어봤다.

```python
for i, q in enumerate(connection.queries):
    logger.debug("[SQL %d] %.1fms | %s", i+1, float(q['time'])*1000, q['sql'][:200])
```

```
[SQL 1]  2.1ms | SELECT "careers_career"."id", ... FROM "careers_career" LIMIT 20
[SQL 2]  0.8ms | SELECT "users_user"."id", ... WHERE "users_user"."id" = '...'
[SQL 3]  0.9ms | SELECT "users_user"."id", ... WHERE "users_user"."id" = '...'
...  (18줄 더)
[SQL 21] 1.1ms | SELECT "mediae_media"."id", ... WHERE "mediae_media"."id" = '...'
...  (22줄 더)
```

`user`와 `media`가 각 career 레코드마다 별도 쿼리. 전형적인 N+1.

## 수정

```python
queryset = Career.objects.select_related(
    'user', 'media', 'role', 'role__parent'
).prefetch_related(
    'user__profile'
)
```

수정 후: 쿼리 4개, SQL 시간 18ms, 총 응답 25ms.

43 → 4개. 380ms → 18ms.

## RN 쪽도 같이

백엔드 수치만 보면 반쪽짜리다. React Native axios 인터셉터에도 응답 시간을 찍었다.

```typescript
axiosInstance.interceptors.response.use(
  (response) => {
    const duration = Date.now() - response.config.metadata?.startTime;
    console.log(`[API] ${response.config.method?.toUpperCase()} ${response.config.url} | ${duration}ms`);
    return response;
  }
);
```

백엔드 25ms인데 RN에서는 280ms로 찍혔다. 네트워크 레이턴시 + 직렬화 오버헤드. 이건 별도 최적화 대상.

측정 없이 최적화하면 어디가 병목인지 모른 채 코드만 복잡해진다. 먼저 숫자부터.
