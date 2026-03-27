---
layout: post
title: price-agent Railway + Vercel 배포와 effective_grade 구현
subtitle: FastAPI → Railway / Next.js → Vercel 분리 배포, LLM 상품 상태 등급 라벨(최상/상/중/하/위험) 추가
author: HyeongJin
date: 2026-03-21 10:00:00 +0900
categories: Backend
tags: [Python, FastAPI, NextJS, Deploy, OpenAI, LLM]
sidebar: []
published: true
---

price-agent 플랫폼을 로컬에서 클라우드로 올리면서 두 가지 작업을 같이 했다. 백엔드(FastAPI)는 Railway, 프론트엔드(Next.js)는 Vercel로 분리 배포하고, LLM이 중고 상품의 상태를 등급으로 분류하는 `effective_grade` 기능을 추가했다.

## 배포 구조

```
Vercel (Next.js)  ──── NEXT_PUBLIC_BACKEND_URL ────▶  Railway (FastAPI)
                                                              │
                                                         SQLite (WAL)
                                                         OpenAI API
```

프론트와 백엔드가 분리된 구조라 환경변수 연결이 핵심이다.

### Railway (FastAPI 백엔드)

Railway는 Nixpacks로 자동 빌드를 지원한다. 별도 Dockerfile 없이 `nixpacks.toml`과 `railway.json`만으로 배포된다.

```toml
# nixpacks.toml
[phases.setup]
nixPkgs = ["python312", "gcc"]

[phases.install]
cmds = ["pip install -r requirements.txt"]

[start]
cmd = "uvicorn app.main:app --host 0.0.0.0 --port $PORT"
```

```json
// railway.json
{
  "build": { "builder": "NIXPACKS" },
  "deploy": {
    "startCommand": "uvicorn app.main:app --host 0.0.0.0 --port $PORT",
    "restartPolicyType": "ON_FAILURE",
    "restartPolicyMaxRetries": 3
  }
}
```

`$PORT`는 Railway가 자동으로 주입해준다. 고정 포트를 쓰면 안 된다.

### Vercel (Next.js 프론트엔드)

```json
// vercel.json
{
  "framework": "nextjs",
  "buildCommand": "npm run build",
  "installCommand": "npm install",
  "env": {
    "NEXT_PUBLIC_BACKEND_URL": "@price-agent-backend-url"
  }
}
```

`@price-agent-backend-url`은 Vercel 대시보드에 등록된 환경변수 시크릿이다. Railway에서 발급받은 백엔드 도메인을 여기에 넣는다.

### API 프록시 설정

초기에 Next.js `next.config.ts`에 API 프록시를 잘못 설정해서 CORS 오류가 났다. 로컬에서는 `localhost:8000`으로 직접 연결했는데, 프로덕션에서는 Railway URL로 바뀌어야 했다.

```typescript
// next.config.ts
const nextConfig: NextConfig = {
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${process.env.NEXT_PUBLIC_BACKEND_URL}/api/:path*`,
      },
    ];
  },
};
```

`NEXT_PUBLIC_BACKEND_URL`을 rewrites destination에 직접 참조하는 방식으로 수정했다. 환경에 따라 자동으로 로컬/프로덕션 URL을 구분한다.

## effective_grade

중고 상품 검색 결과에 상태 등급 라벨을 추가했다. 여러 마켓플레이스에서 수집한 매물의 상태 표기가 제각각이기 때문이다. 야후옥션은 일본어로 상태를 적고, 번개장터는 "S급/A급/B급"을 쓰고, 네이버는 자유 텍스트다. LLM이 이걸 통일된 5단계 등급으로 변환한다.

### 등급 정의

```python
"effective_grade: overall condition label — EXACTLY one of:\n"
"  '최상' → near mint, no defects (S급 or 새상품급)\n"
"  '상'   → minor wear, fully functional (A급, light scratches ok)\n"
"  '중'   → visible wear but functional (B급, some scratches/marks)\n"
"  '하'   → heavy wear or minor issues (C급)\n"
"  '위험' → defective, broken, flood damage, parts-only\n"
```

단순 등급 외에 `risk_flags`도 같이 추출한다.

```python
"risk_flags: array of short Korean defect labels found in the title.\n"
"  '생활기스' '액정기스' '액정불량' '고장' '전원불가' '침수흔적' '배터리팽창'\n"
"  '박스없음' '충전기없음' '케이블없음' '수리이력' '무상AS불가' '부품용'\n"
"  '도색흠집' '외장손상' '버튼불량' '배터리노화'\n"
```

제목에 "생활기스 있음" 같은 표현이 있으면 `risk_flags`에 추가되고 UI에서 태그로 표시된다.

### LLM 배치 분석

매물 1개씩 LLM을 호출하면 너무 느리고 비싸다. 10개씩 묶어서 한 번에 분석한다.

```python
result = self._chat_json(
    model=self.match_model,
    system_prompt=system,
    user_prompt=json.dumps(payload, ensure_ascii=False),  # 10개 묶음
    max_tokens=1200,
    operation="analyze_condition_batch",
)
```

응답은 `analyses` 배열로 오고, `index`로 원본 매물과 매핑한다.

```python
valid_grades = {"최상", "상", "중", "하", "위험"}
for item in raw_analyses:
    grade = item.get("effective_grade")
    output[idx] = {
        "effective_grade": str(grade).strip() if grade and str(grade).strip() in valid_grades else None,
        "risk_flags": flags if isinstance(flags, list) else [],
        "condition_summary": str(summary).strip()[:60] if summary else None,
        "value_verdict": str(verdict).strip() if verdict else None,
    }
```

`valid_grades`로 화이트리스트 검증을 한다. LLM이 가끔 "상/중" 같은 중간 값이나 영어를 내놓는데, 정해진 5개 외에는 `None`으로 처리한다.

### value_verdict

등급과 함께 가성비 판단도 LLM이 내린다.

```
'가성비좋음' | '시세적정' | '시세대비고가' | '상태감안주의'
```

`effective_grade`가 `위험` 또는 `하`면 `상태감안주의`를 권장하도록 프롬프트에 명시했다. 싼 가격이어도 고장품이면 매수를 주의해야 하기 때문이다.

## 배포 후 수정한 버그들

**scope_confirmed gate 오작동**

프론트엔드에서 검색 시작 시 "이대로 검색" 확인 메시지를 자동으로 전송하지 않아서, 백엔드가 사용자 확인을 무한 대기하는 문제가 있었다. 프론트엔드 `startRun` 함수에서 확인 메시지를 자동 전송하도록 수정했다.

**fast 모드 타임아웃**

`LLMWebSearchAdapter`(야후옥션/Amazon JP/네이버 중고를 web_search_preview로 검색)가 사이트당 8~15초 걸리는데, fast 모드(45초 제한)에서 이걸 돌리면 다른 에이전트들이 전부 타임아웃에 걸렸다.

```python
class LLMWebSearchAdapter(SourceAdapter):
    deep_only = True  # fast 모드에서 제외
```

`deep_only = True` 플래그 하나로 해결했다. Orchestrator가 fast 모드 시 이 플래그를 보고 해당 어댑터를 스킵한다.

## 결과

Railway 무료 티어(실험적 배포)에서 SQLite WAL 모드와 결합해서 동시 요청 타임아웃 없이 안정적으로 동작하고 있다. `effective_grade` 태그가 검색 결과 카드에 표시되면서 상태 불량 매물을 빠르게 걸러낼 수 있게 됐다.
