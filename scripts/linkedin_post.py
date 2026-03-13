#!/usr/bin/env python3
"""
LinkedIn 자동 포스팅 스크립트
- 기술 뉴스 RSS / 내 블로그 최신 포스트 기반으로 LinkedIn 게시글 생성
- LinkedIn API (공식) 또는 브라우저 자동화(Playwright) 선택 가능

환경변수:
  OPENAI_API_KEY       : GPT API 키 (필수)
  LINKEDIN_ACCESS_TOKEN: LinkedIn API Access Token (공식 API 사용 시)

사용법:
  python scripts/linkedin_post.py                   # 자동 주제 선택
  python scripts/linkedin_post.py --topic "LangChain RAG 구현"
  python scripts/linkedin_post.py --dry-run         # 미리보기만
  python scripts/linkedin_post.py --mode api        # LinkedIn API 사용
  python scripts/linkedin_post.py --mode browser    # Playwright 브라우저 자동화

⚠️  LinkedIn API 주의사항:
  - LinkedIn API는 개인 계정 자동 포스팅에 엄격한 제한이 있음
  - Developer App 등록 필요: https://www.linkedin.com/developers/apps
  - Access Token 만료 주기: 60일 (주기적 갱신 필요)
  - 대안: Playwright 브라우저 자동화 (단, LinkedIn 이용약관 주의)
"""

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

# .env 자동 로드
_env_file = Path(__file__).parent / ".env"
if _env_file.exists():
    from dotenv import load_dotenv
    load_dotenv(_env_file)

import feedparser
import openai
import requests

BLOG_ROOT = Path(__file__).parent.parent
POSTS_DIR = BLOG_ROOT / "_posts"

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = "gpt-4o"
LINKEDIN_ACCESS_TOKEN = os.getenv("LINKEDIN_ACCESS_TOKEN")
LINKEDIN_PERSON_URN = os.getenv("LINKEDIN_PERSON_URN")  # "urn:li:person:XXXXXXXX"

RSS_FEEDS = [
    "https://hnrss.org/newest?q=LLM+OR+AI+OR+python&count=10",
    "https://dev.to/feed/tag/llm",
    "https://dev.to/feed/tag/ai",
    "https://dev.to/feed/tag/python",
]

BLOG_URL = "https://projectSylas.github.io"


# ── 최신 블로그 포스트 가져오기 ────────────────────────────────────────────────

def get_latest_blog_post() -> Optional[dict]:
    """_posts/에서 가장 최신 포스트 정보를 가져옵니다."""
    import re
    posts = sorted(POSTS_DIR.glob("*.md"), reverse=True)
    for p in posts:
        try:
            text = p.read_text(encoding="utf-8")
            title_m = re.search(r"^title:\s*(.+)$", text, re.MULTILINE)
            subtitle_m = re.search(r"^subtitle:\s*(.+)$", text, re.MULTILINE)
            categories_m = re.search(r"^categories:\s*(.+)$", text, re.MULTILINE)
            date_str = p.name[:10]
            slug = p.stem[11:]  # YYYY-MM-DD- 제거
            # Jekyll 기본 permalink: /:categories/:year/:month/:day/:title/
            category = categories_m.group(1).strip().lower() if categories_m else ""
            y, m, d = date_str.split("-")
            if category:
                url = f"{BLOG_URL}/{category}/{y}/{m}/{d}/{slug}/"
            else:
                url = f"{BLOG_URL}/{y}/{m}/{d}/{slug}/"
            if title_m:
                return {
                    "title": title_m.group(1).strip(),
                    "subtitle": subtitle_m.group(1).strip() if subtitle_m else "",
                    "category": categories_m.group(1).strip() if categories_m else "",
                    "url": url,
                    "date": date_str,
                }
        except Exception:
            pass
    return None


# ── RSS 기반 주제 수집 ─────────────────────────────────────────────────────────

def fetch_rss_topic() -> dict:
    import random
    items = []
    for url in RSS_FEEDS:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:5]:
                title = entry.get("title", "").strip()
                summary = entry.get("summary", "")[:400]
                link = entry.get("link", "")
                if title:
                    items.append({"title": title, "summary": summary, "link": link})
        except Exception:
            pass
    if not items:
        return {"title": "AI/LLM 개발 트렌드", "summary": "", "link": ""}
    return random.choice(items)


# ── GPT로 LinkedIn 게시글 생성 ─────────────────────────────────────────────────

LINKEDIN_SYSTEM_PROMPT = """현업 개발자가 LinkedIn에 직접 쓴 것처럼 자연스러운 한국어 기술 게시글을 작성하세요.

다음 예시처럼 써라. 짧고 직접적이며 실제 사람이 쓴 느낌:

[좋은 예시 1]
Prefect로 LLM 파이프라인 짜다가 task 재시도 로직 때문에 반나절 날렸다.
문제는 단순했는데 — upstream task가 실패해도 downstream이 cached 결과로 실행되고 있었던 것.
result_storage 끄고 나서야 해결됐다.
문서에 있긴 한데 눈에 잘 안 띄는 설정이라 기록해둠.

#Prefect #Python #LLMOps

[좋은 예시 2]
LangChain으로 멀티에이전트 시스템 만들면서 느낀 것.
에이전트 간 컨텍스트 공유를 어떻게 설계하느냐가 퀄리티를 거의 결정한다.
처음엔 단순히 메시지 히스토리 넘기면 되겠지 했는데, 에이전트 수가 늘수록 노이즈가 쌓이는 게 눈에 보임.
지금은 각 에이전트가 자기 도메인 결과만 structured output으로 뱉고, 오케스트레이터가 조합하는 방식으로 바꿨다.

#LangChain #MultiAgent #AI

규칙:
- 이모지 절대 금지
- 인사말, 서론, "~에 대해 알아보겠습니다" 같은 표현 금지
- "~라고 생각합니다", "~할 수 있습니다" 같은 조심스러운 문어체 최소화
- 짧은 문단. 단호한 문장
- 해시태그 3~5개, 영문, 마지막 줄에만
- 블로그 링크 있으면 마지막 줄에 URL만 달랑 넣기
- 전체 100~180자 내외 (한국어 기준). 길게 쓰지 말 것

출력 형식 (JSON):
{
  "text": "LinkedIn 게시글 전체 텍스트 (해시태그 포함)"
}
"""

def generate_linkedin_post(topic: str, blog_post: Optional[dict] = None, context: str = "") -> str:
    client = openai.OpenAI(api_key=OPENAI_API_KEY)

    user_msg = f"주제: {topic}"
    if context:
        user_msg += f"\n\n컨텍스트: {context}"
    if blog_post:
        user_msg += f"\n\n내 블로그 포스트 링크: {blog_post['url']}\n제목: {blog_post['title']}"

    response = client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=[
            {"role": "system", "content": LINKEDIN_SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        response_format={"type": "json_object"},
        temperature=0.85,
    )

    data = json.loads(response.choices[0].message.content)
    return data["text"]


# ── LinkedIn API 포스팅 ────────────────────────────────────────────────────────

def post_to_linkedin_api(text: str) -> bool:
    """LinkedIn API v2를 통해 게시글을 포스팅합니다."""
    if not LINKEDIN_ACCESS_TOKEN:
        print("[오류] LINKEDIN_ACCESS_TOKEN 환경변수 없음", file=sys.stderr)
        return False
    if not LINKEDIN_PERSON_URN:
        print("[오류] LINKEDIN_PERSON_URN 환경변수 없음", file=sys.stderr)
        return False

    url = "https://api.linkedin.com/v2/ugcPosts"
    headers = {
        "Authorization": f"Bearer {LINKEDIN_ACCESS_TOKEN}",
        "Content-Type": "application/json",
        "X-Restli-Protocol-Version": "2.0.0",
    }
    payload = {
        "author": LINKEDIN_PERSON_URN,
        "lifecycleState": "PUBLISHED",
        "specificContent": {
            "com.linkedin.ugc.ShareContent": {
                "shareCommentary": {"text": text},
                "shareMediaCategory": "NONE",
            }
        },
        "visibility": {
            "com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"
        },
    }

    resp = requests.post(url, headers=headers, json=payload, timeout=30)
    if resp.status_code in (200, 201):
        print(f"[LinkedIn API] 포스팅 성공: {resp.headers.get('x-linkedin-id', '')}")
        return True
    else:
        print(f"[LinkedIn API] 실패 {resp.status_code}: {resp.text}", file=sys.stderr)
        return False


STATE_PATH = Path(__file__).parent / "linkedin_state.json"


# ── Playwright 브라우저 자동화 ────────────────────────────────────────────────

def post_to_linkedin_browser(text: str) -> bool:
    """Playwright로 LinkedIn 브라우저 자동화 포스팅 (저장된 세션 사용)."""
    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
    except ImportError:
        print("[오류] playwright 미설치. 'pip install playwright && playwright install chromium'", file=sys.stderr)
        return False

    if not STATE_PATH.exists():
        print("[오류] 세션 파일 없음. 먼저 로그인하세요:", file=sys.stderr)
        print("       python scripts/linkedin_login.py", file=sys.stderr)
        return False

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            storage_state=str(STATE_PATH),
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
        )
        page = context.new_page()
        try:
            # ── 피드로 이동 ───────────────────────────────────────
            print("[브라우저] LinkedIn 피드 접속 중...")
            page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded", timeout=20000)
            page.wait_for_timeout(2000)

            # 세션 만료 감지
            if "authwall" in page.url:
                print("[경고] 세션 만료. 다시 로그인하세요: python scripts/linkedin_login.py", file=sys.stderr)
                return False

            # 계정 선택 화면 감지 → 저장된 계정 클릭
            try:
                account_btn = page.wait_for_selector(
                    "button.member-profile__details, [aria-label*='Login as'], [aria-label*='로그인']",
                    timeout=3000
                )
                if account_btn:
                    print("[브라우저] 계정 선택 화면 감지 → 계정 클릭")
                    account_btn.click()
                    page.wait_for_timeout(3000)
                    # 비밀번호 입력창이 뜨면 자동 입력
                    pw_input = page.query_selector("#password")
                    if pw_input:
                        li_password = os.getenv("LINKEDIN_PASSWORD", "")
                        if not li_password:
                            print("[경고] LINKEDIN_PASSWORD 없음. 세션 재저장 필요", file=sys.stderr)
                            return False
                        pw_input.fill(li_password)
                        page.click("[type=submit]")
                        page.wait_for_timeout(3000)
            except PWTimeout:
                pass  # 계정 선택 화면 아님 → 이미 피드

            print(f"[브라우저] 현재 URL: {page.url}")

            # ── 글쓰기 모달 열기 ────────────────────────────────
            start_btn = page.wait_for_selector("[aria-label='Start a post']", timeout=8000)
            start_btn.click()
            page.wait_for_timeout(2000)

            # ── 텍스트 입력 ──────────────────────────────────────
            editor = page.wait_for_selector("div.ql-editor", timeout=8000)
            editor.click()
            page.keyboard.type(text, delay=15)
            page.wait_for_timeout(1000)

            # ── 게시 버튼 클릭 (한국어: 업데이트 / 영어: Post) ───
            post_btn_selectors = [
                "button:has-text('업데이트')",
                "button:has-text('Post')",
                "button:has-text('게시')",
            ]
            posted = False
            for sel in post_btn_selectors:
                try:
                    page.click(sel, timeout=4000)
                    posted = True
                    break
                except PWTimeout:
                    continue

            if not posted:
                print("[오류] 게시 버튼을 찾지 못했습니다.", file=sys.stderr)
                page.screenshot(path="scripts/logs/linkedin_error.png")
                return False

            page.wait_for_timeout(3000)
            print("[브라우저] LinkedIn 포스팅 완료")
            return True

        except Exception as e:
            print(f"[Playwright] 실패: {e}", file=sys.stderr)
            import traceback
            traceback.print_exc()
            try:
                Path("scripts/logs").mkdir(exist_ok=True)
                page.screenshot(path="scripts/logs/linkedin_error.png")
                print("[디버그] 스크린샷 저장: scripts/logs/linkedin_error.png")
            except Exception:
                pass
            return False
        finally:
            context.close()
            browser.close()


# ── 메인 ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="LinkedIn 자동 포스팅")
    parser.add_argument("--topic", type=str, help="게시글 주제")
    parser.add_argument("--dry-run", action="store_true", help="게시글 생성만, 실제 포스팅 안 함")
    parser.add_argument("--mode", choices=["api", "browser"], default="browser",
                        help="포스팅 방식 (기본: browser)")
    args = parser.parse_args()

    if not OPENAI_API_KEY:
        print("[오류] OPENAI_API_KEY 없음", file=sys.stderr)
        sys.exit(1)

    # 최신 블로그 포스트 참조
    blog_post = get_latest_blog_post()
    if blog_post:
        print(f"[블로그] 최신 포스트: {blog_post['title']}")

    # 주제 결정
    topic = args.topic
    context = ""
    if not topic:
        rss = fetch_rss_topic()
        topic = rss["title"]
        context = rss.get("summary", "")
        print(f"[주제] RSS에서 선택: {topic}")
    else:
        print(f"[주제] 수동 지정: {topic}")

    # 게시글 생성
    print("[GPT] LinkedIn 게시글 생성 중...")
    post_text = generate_linkedin_post(topic, blog_post, context)

    print(f"\n{'='*60}")
    print("LinkedIn 게시글 미리보기:")
    print(f"{'='*60}")
    print(post_text)
    print(f"{'='*60}\n")
    print(f"글자 수: {len(post_text)}")

    if args.dry_run:
        print("[DRY-RUN] 실제 포스팅 생략")
        return

    # 포스팅
    if args.mode == "api":
        success = post_to_linkedin_api(post_text)
    else:
        success = post_to_linkedin_browser(post_text)

    if not success:
        sys.exit(1)


if __name__ == "__main__":
    main()
