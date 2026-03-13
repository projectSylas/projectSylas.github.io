---
layout: post
title: PostgreSQL DISTINCT ON과 ORDER BY 함께 쓸 때 빠지는 함정
subtitle: 경력 페이지네이션에서 만난 쿼리 오류와 해결 과정
author: HyeongJin
categories: Backend
tags: [PostgreSQL, Django, backend]
sidebar: []
published: true
---

경력 목록 API에서 특정 조건으로 조회 시 500 에러가 발생했다. 에러 메시지는 이랬다.

```
ProgrammingError: SELECT DISTINCT ON expressions must match initial ORDER BY expressions
```

`DISTINCT ON`을 쓰면서 `ORDER BY`가 맞지 않아서 생긴 에러. 처음 보면 뭔 소린지 몰랐다.

## 왜 DISTINCT ON을 썼나

사용자의 경력에서 같은 작품에 여러 직무로 참여한 경우, 작품별로 하나씩만 보여줘야 했다.

```python
Career.objects.order_by('media_id', '-created_at').distinct('media_id')
```

Django ORM에서 `distinct('field')`는 PostgreSQL `DISTINCT ON`으로 변환된다.

```sql
SELECT DISTINCT ON (careers_career.media_id)
    careers_career.*
FROM careers_career
ORDER BY careers_career.media_id, careers_career.created_at DESC
```

여기까지는 잘 된다.

## 문제: 추가 정렬이 붙으면서

필터링 후 페이지네이션을 위한 추가 `order_by`가 붙으면서 문제가 생겼다.

```python
queryset = Career.objects.filter(
    user=user
).order_by('media_id', '-created_at').distinct('media_id')

# 페이지네이션에서 ordering 추가
queryset = queryset.order_by('-year', 'media_title')  # 이 순간 오류
```

```sql
SELECT DISTINCT ON (careers_career.media_id)
    careers_career.*
FROM careers_career
ORDER BY careers_career.year DESC, careers_career.media_title ASC
-- 오류: DISTINCT ON (media_id) 인데 ORDER BY가 media_id로 시작 안 함
```

PostgreSQL의 `DISTINCT ON` 규칙: **ORDER BY의 첫 번째 컬럼이 DISTINCT ON에 명시된 컬럼과 같아야 한다.**

## 해결

`DISTINCT ON` 컬럼을 ORDER BY 첫 번째에 두고, 원하는 정렬을 그 뒤에 붙인다.

```python
Career.objects.filter(
    user=user
).order_by(
    'media_id',      # DISTINCT ON 컬럼 — 반드시 첫 번째
    '-year',         # 원하는 정렬
    'media_title',
    '-created_at',   # 같은 media_id 중 어떤 레코드를 선택할지
)
.distinct('media_id')
```

생성된 SQL:

```sql
SELECT DISTINCT ON (careers_career.media_id)
    careers_career.*
FROM careers_career
WHERE careers_career.user_id = '...'
ORDER BY
    careers_career.media_id,   -- DISTINCT ON과 일치
    careers_career.year DESC,
    careers_career.media_title ASC,
    careers_career.created_at DESC
```

이제 DISTINCT ON이 `media_id` 기준으로 중복 제거하면서, 같은 `media_id` 중에서는 `created_at DESC` 기준으로 최신 레코드를 선택한다.

## 추가 정렬 컬럼 empty string 처리

같은 API에서 `start_at`, `end_at` 필드에 empty string이 들어오는 문제도 있었다.

```python
# 기존 시리얼라이저 - empty string이 DateField에 들어가서 ValidationError
class CareerSerializer(serializers.ModelSerializer):
    start_at = serializers.DateField(allow_null=True)
```

```python
# 수정 - empty string을 None으로 변환
class CareerSerializer(serializers.ModelSerializer):
    start_at = serializers.DateField(allow_null=True, allow_empty=True)

    def validate_start_at(self, value):
        if value == '':
            return None
        return value
```

`DateField`에는 `allow_blank` 대신 `allow_null`을 쓰고, validate에서 empty string을 None으로 변환.

## DISTINCT ON 쓸 때 주의사항 정리

1. ORDER BY 첫 번째 컬럼이 DISTINCT ON 컬럼과 같아야 한다
2. 같은 DISTINCT ON 컬럼 내에서 어떤 행을 선택할지 ORDER BY 순서가 결정한다
3. 결과 정렬 순서는 DISTINCT ON 컬럼 기준이다 — 다른 순서로 정렬하려면 서브쿼리가 필요하다

```sql
-- DISTINCT ON 결과를 다른 순서로 정렬하고 싶을 때
SELECT *
FROM (
    SELECT DISTINCT ON (media_id) *
    FROM careers_career
    ORDER BY media_id, created_at DESC
) subq
ORDER BY year DESC, media_title ASC;
```

Django ORM에서 이걸 표현하려면 `RawSQL` 또는 서브쿼리 구조로 써야 한다.
