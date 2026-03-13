#!/usr/bin/env python3
"""
LinkedIn 최초 1회 로그인 → 쿠키 저장
이후 linkedin_post.py는 이 쿠키를 재사용합니다.

사용법:
  python scripts/linkedin_login.py
"""

import json
from pathlib import Path

STATE_PATH = Path(__file__).parent / "linkedin_state.json"


def main():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("[오류] playwright 미설치: pip install playwright && playwright install chromium")
        return

    print("브라우저 창이 열립니다. LinkedIn에 직접 로그인하세요.")
    print("피드 화면이 뜨면 자동으로 세션이 저장됩니다.\n")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(viewport={"width": 1280, "height": 800})
        page = context.new_page()
        page.goto("https://www.linkedin.com/login")

        print("로그인 대기 중... (최대 2분)")
        page.wait_for_url("https://www.linkedin.com/feed/**", timeout=120000)
        print("[완료] 로그인 성공!")

        # storageState로 쿠키 + localStorage 전체 저장
        context.storage_state(path=str(STATE_PATH))
        print(f"[저장] 세션 저장 완료: {STATE_PATH}")

        browser.close()

    print("\n이제 linkedin_post.py를 실행하면 자동 로그인됩니다.")


if __name__ == "__main__":
    main()
