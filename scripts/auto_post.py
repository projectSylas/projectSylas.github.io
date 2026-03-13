#!/usr/bin/env python3
"""
Jekyll 블로그 자동 포스트 생성기
- 기술 뉴스 RSS에서 주제를 수집
- OpenAI GPT로 한국어 기술 포스트 생성
- _posts/ 디렉토리에 저장 후 git push

사용법:
  python scripts/auto_post.py                   # 자동으로 주제 선택
  python scripts/auto_post.py --topic "LLM RAG"  # 수동 주제 지정
  python scripts/auto_post.py --dry-run          # 파일 저장/push 없이 미리보기
"""

import argparse
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Set

# .env 자동 로드
_env_file = Path(__file__).parent / ".env"
if _env_file.exists():
    from dotenv import load_dotenv
    load_dotenv(_env_file)

import feedparser
import openai
import random

# ── 설정 ──────────────────────────────────────────────────────────────────────

BLOG_ROOT = Path(__file__).parent.parent
POSTS_DIR = BLOG_ROOT / "_posts"

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = "gpt-4o"

AUTHOR = "HyeongJin"

# Jekyll 카테고리 → 태그 매핑
CATEGORY_TAGS = {
    "AI/LLM":    ["AI", "LLM", "Python"],
    "Python":    ["Python", "backend"],
    "DevOps":    ["Docker", "CI/CD", "DevOps"],
    "Backend":   ["Django", "Python", "backend"],
    "React":     ["React", "frontend", "JavaScript"],
    "Database":  ["PostgreSQL", "database"],
    "Career":    ["career", "engineering"],
}

# 기술 뉴스 RSS 피드 목록
RSS_FEEDS = [
    "https://hnrss.org/newest?q=LLM+OR+AI+OR+python&count=20",         # HackerNews
    "https://feeds.feedburner.com/bdtechtalks",                          # BD Tech Talks
    "https://dev.to/feed/tag/python",                                    # dev.to Python
    "https://dev.to/feed/tag/llm",                                       # dev.to LLM
    "https://dev.to/feed/tag/ai",                                        # dev.to AI
    "https://realpython.com/atom.xml",                                   # Real Python
]


# ── RSS에서 주제 수집 ──────────────────────────────────────────────────────────

def fetch_rss_topics(n: int = 10) -> List[dict]:
    """RSS에서 최신 기술 기사 제목/링크를 수집합니다."""
    items = []
    for url in RSS_FEEDS:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:5]:
                title = entry.get("title", "").strip()
                link = entry.get("link", "")
                summary = entry.get("summary", "")[:300]
                if title:
                    items.append({"title": title, "link": link, "summary": summary})
        except Exception as e:
            print(f"[RSS] {url} 파싱 실패: {e}", file=sys.stderr)

    # 중복 제거 + 셔플
    seen = set()
    unique = []
    for item in items:
        if item["title"] not in seen:
            seen.add(item["title"])
            unique.append(item)

    random.shuffle(unique)
    return unique[:n]


# ── GPT로 포스트 생성 ──────────────────────────────────────────────────────────

SYSTEM_PROMPT = """당신은 현업 백엔드/AI 개발자입니다. 직접 겪은 것처럼 자연스럽게 기술 블로그 포스트를 한국어로 작성하세요.

참고할 실제 국내 기술 블로그 작성 스타일:
- 카카오, 우아한형제들, 당근마켓 기술 블로그처럼 담백하고 실용적
- 불필요한 서론 없이 바로 본론 시작 ("오늘은 X에 대해 알아보겠습니다" 같은 표현 금지)
- 이모지 사용 금지
- "~해보겠습니다", "~살펴볼게요" 같은 형식적 표현 최소화
- 실제 코드와 에러 메시지, 삽질 과정을 포함해서 신뢰감 형성
- 문장은 짧고 단호하게. 불필요한 수식어 제거
- 결론부터 말하는 역피라미드 구조 선호
- 제목은 "X 삽질기", "Y 때문에 고생한 이야기", "Z 도입 후기" 같은 현실적인 형태

금지 표현:
- "안녕하세요", "이번 포스팅에서는", "마치며", "이렇게 해서"
- 의미 없는 감탄사나 칭찬 문구
- 과도한 배경 설명

구조:
- ## 제목 없이 바로 상황 설명 또는 문제 제시로 시작
- 코드 블록 언어 명시 필수 (```python, ```bash 등)
- 마지막은 짧은 회고나 다음 할 것으로 마무리 (요약 섹션 불필요)
- 길이: 600~900 단어

출력 형식 (JSON):
{
  "title": "포스트 제목",
  "subtitle": "한 줄 부제목",
  "category": "AI/LLM | Python | DevOps | Backend | React | Database | Career 중 하나",
  "tags": ["태그1", "태그2", "태그3"],
  "content": "마크다운 본문 (front matter 제외)"
}
"""

def generate_post(topic: str, context: str = "") -> dict:
    """GPT로 기술 블로그 포스트를 생성합니다."""
    client = openai.OpenAI(api_key=OPENAI_API_KEY)

    user_msg = f"주제: {topic}"
    if context:
        user_msg += f"\n\n참고 컨텍스트:\n{context}"

    response = client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        response_format={"type": "json_object"},
        temperature=0.8,
    )

    import json
    return json.loads(response.choices[0].message.content)


# ── Jekyll 포스트 파일 생성 ────────────────────────────────────────────────────

def slugify(text: str) -> str:
    """제목을 Jekyll URL 슬러그로 변환합니다."""
    text = text.lower()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    text = re.sub(r"-+", "-", text)
    return text.strip("-")[:60]


def build_front_matter(data: dict) -> str:
    category = data.get("category", "AI/LLM")
    tags = data.get("tags", CATEGORY_TAGS.get(category, []))
    tags_str = "[" + ", ".join(tags) + "]"

    return f"""---
layout: post
title: {data['title']}
subtitle: {data.get('subtitle', '')}
author: {AUTHOR}
categories: {category}
tags: {tags_str}
sidebar: []
published: true
---

"""


BLOG_URL = "https://projectSylas.github.io"


def post_url(data: dict, date_str: str) -> str:
    """Jekyll permalink 형식으로 URL 생성 (/:categories/:year/:month/:day/:title/)"""
    category = data.get("category", "").lower()
    slug = slugify(data["title"])
    y, m, d = date_str.split("-")
    if category:
        return f"{BLOG_URL}/{category}/{y}/{m}/{d}/{slug}/"
    return f"{BLOG_URL}/{y}/{m}/{d}/{slug}/"


def save_post(data: dict, dry_run: bool = False) -> Path:
    """Jekyll _posts/ 디렉토리에 포스트를 저장합니다."""
    today = datetime.now().strftime("%Y-%m-%d")
    slug = slugify(data["title"])
    filename = f"{today}-{slug}.md"
    filepath = POSTS_DIR / filename

    content = build_front_matter(data) + data["content"]

    if dry_run:
        print(f"\n{'='*60}")
        print(f"[DRY-RUN] 파일: {filepath}")
        print(f"{'='*60}")
        print(content[:800] + ("\n..." if len(content) > 800 else ""))
        return filepath

    filepath.write_text(content, encoding="utf-8")
    url = post_url(data, today)
    print(f"[저장] {filepath}")
    print(f"[URL]  {url}  (push 후 약 1~2분 뒤 반영)")
    return filepath


# ── Git push ──────────────────────────────────────────────────────────────────

def git_push(filepath: Path, title: str) -> None:
    """변경사항을 커밋하고 GitHub에 push합니다."""
    try:
        subprocess.run(
            ["git", "add", str(filepath)],
            cwd=BLOG_ROOT, check=True
        )
        subprocess.run(
            ["git", "commit", "-m", f"post: {title}"],
            cwd=BLOG_ROOT, check=True
        )
        # 현재 브랜치 이름 자동 감지
        branch = subprocess.check_output(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=BLOG_ROOT
        ).decode().strip()
        subprocess.run(
            ["git", "push", "origin", branch],
            cwd=BLOG_ROOT, check=True
        )
        print(f"[Push] 완료: {title}")
    except subprocess.CalledProcessError as e:
        print(f"[오류] git 작업 실패: {e}", file=sys.stderr)
        sys.exit(1)


# ── 중복 방지 ──────────────────────────────────────────────────────────────────

def get_recent_post_titles(days: int = 30) -> Set[str]:
    """최근 N일간 포스트 제목을 수집해 중복 방지에 사용합니다."""
    titles = set()
    cutoff = datetime.now().toordinal() - days
    for p in POSTS_DIR.glob("*.md"):
        try:
            date_str = p.name[:10]  # YYYY-MM-DD
            post_date = datetime.strptime(date_str, "%Y-%m-%d").toordinal()
            if post_date >= cutoff:
                # front matter에서 title 추출
                text = p.read_text(encoding="utf-8")
                m = re.search(r"^title:\s*(.+)$", text, re.MULTILINE)
                if m:
                    titles.add(m.group(1).strip().lower())
        except Exception:
            pass
    return titles


# ── 메인 ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Jekyll 기술 블로그 자동 포스팅")
    parser.add_argument("--topic", type=str, help="포스트 주제 직접 지정")
    parser.add_argument("--dry-run", action="store_true", help="저장/push 없이 미리보기")
    parser.add_argument("--no-push", action="store_true", help="파일 저장만, push 안 함")
    args = parser.parse_args()

    if not OPENAI_API_KEY:
        print("[오류] OPENAI_API_KEY 환경변수가 설정되지 않았습니다.", file=sys.stderr)
        sys.exit(1)

    # 주제 결정
    topic = args.topic
    context = ""
    if not topic:
        print("[RSS] 최신 기술 뉴스 수집 중...")
        rss_items = fetch_rss_topics(n=15)

        # 최근 포스트와 겹치지 않는 주제 선택
        recent_titles = get_recent_post_titles(days=30)
        candidates = [
            item for item in rss_items
            if item["title"].lower() not in recent_titles
        ]

        if not candidates:
            candidates = rss_items  # 후보가 없으면 전체에서 선택

        chosen = candidates[0]
        topic = chosen["title"]
        context = chosen.get("summary", "")
        print(f"[주제] {topic}")
        if chosen.get("link"):
            print(f"[출처] {chosen['link']}")
    else:
        print(f"[주제] {topic} (수동 지정)")

    # 포스트 생성
    print("[GPT] 포스트 생성 중...")
    data = generate_post(topic, context)
    print(f"[제목] {data['title']}")
    print(f"[카테고리] {data.get('category', '-')} | 태그: {data.get('tags', [])}")

    # 저장
    filepath = save_post(data, dry_run=args.dry_run)

    # Push
    if not args.dry_run and not args.no_push:
        git_push(filepath, data["title"])

    print("\n[완료]")


if __name__ == "__main__":
    main()
