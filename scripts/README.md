# 블로그 & LinkedIn 자동화

## 파일 구조

```
scripts/
  auto_post.py       ← 블로그 자동 포스팅 (핵심)
  linkedin_post.py   ← LinkedIn 자동 포스팅
  setup_cron.sh      ← macOS launchd 설정 헬퍼
  requirements.txt   ← Python 의존성
  logs/              ← 실행 로그 (자동 생성)
.github/workflows/
  auto-post.yml      ← GitHub Actions (매일 오전 9시 KST)
```

---

## 1. 설치

```bash
cd /Users/hjkim/Dev/Hjkim/Blog
pip install -r scripts/requirements.txt
export OPENAI_API_KEY="sk-..."
```

---

## 2. 블로그 자동 포스팅

### 수동 실행
```bash
# RSS에서 주제 자동 선택 + 저장 + git push
python scripts/auto_post.py

# 주제 직접 지정
python scripts/auto_post.py --topic "Prefect로 멀티에이전트 파이프라인 만들기"

# 미리보기 (저장/push 없음)
python scripts/auto_post.py --dry-run

# 저장만, push 안 함
python scripts/auto_post.py --no-push
```

### GitHub Actions 자동화 (권장)
1. GitHub 저장소 → Settings → Secrets → `OPENAI_API_KEY` 추가
2. `.github/workflows/auto-post.yml` 이미 포함됨
3. 매일 오전 9시 KST(00:00 UTC)에 자동 실행
4. Actions 탭 → "Auto Blog Post" → "Run workflow"로 수동 실행 가능

### macOS launchd 자동화 (로컬 실행 선호 시)
```bash
bash scripts/setup_cron.sh
# 생성된 plist에 OPENAI_API_KEY 직접 입력 후
launchctl load ~/Library/LaunchAgents/io.github.projectsylas.blog-autopost.plist
```

---

## 3. LinkedIn 자동 포스팅

### 방식 A: LinkedIn 공식 API (권장)

#### 초기 설정 (1회)
1. https://www.linkedin.com/developers/apps 에서 앱 생성
2. Products → "Share on LinkedIn" + "Sign In with LinkedIn" 추가 신청
3. OAuth 2.0 → Access Token 발급 (유효기간 60일)
4. 내 Person URN 확인:
   ```bash
   curl -H "Authorization: Bearer ACCESS_TOKEN" \
     "https://api.linkedin.com/v2/me?projection=(id)"
   # 반환값 id로 "urn:li:person:XXXXX" 형태로 조합
   ```

#### 실행
```bash
export LINKEDIN_ACCESS_TOKEN="AQX..."
export LINKEDIN_PERSON_URN="urn:li:person:XXXXXXXX"

python scripts/linkedin_post.py             # 자동 주제
python scripts/linkedin_post.py --topic "LangChain LCEL 실전 사용법"
python scripts/linkedin_post.py --dry-run   # 미리보기
```

### 방식 B: Playwright 브라우저 자동화

⚠️ LinkedIn 이용약관 위반 가능성 있음. 개인 연구 목적으로만 사용.

```bash
pip install playwright && playwright install chromium

export LINKEDIN_EMAIL="your@email.com"
export LINKEDIN_PASSWORD="yourpassword"

python scripts/linkedin_post.py --mode browser
```

---

## 4. .env 파일로 편리하게 관리

```bash
# scripts/.env (gitignore에 추가 필수!)
OPENAI_API_KEY=sk-...
LINKEDIN_ACCESS_TOKEN=AQX...
LINKEDIN_PERSON_URN=urn:li:person:XXXXXXXX
```

.env 로드:
```bash
set -a && source scripts/.env && set +a
python scripts/auto_post.py
```

---

## 5. 포스트 생성 품질 향상 팁

`auto_post.py`의 `SYSTEM_PROMPT`를 수정해 내 전문성 영역에 맞게 조정:
- AI/LLM 파이프라인 경험 추가
- 실제 트러블슈팅 사례 유도
- 선호하는 글 스타일 지정
