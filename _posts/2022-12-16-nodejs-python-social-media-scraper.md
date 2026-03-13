---
layout: post
title: Node.js + Python으로 멀티플랫폼 소셜 미디어 크롤러 만든 경험
subtitle: 인스타그램, 네이버, 트위터, 커뮤니티까지 한 서버에서
author: HyeongJin
categories: Backend
tags: [Python, backend, DevOps]
sidebar: []
published: true
---

서울애널리티카에서 소셜 미디어 데이터 수집 파이프라인을 처음부터 구축하는 일을 맡았다.

요구사항은 단순했다. 인스타그램, 네이버 뉴스/카페, 트위터, 뽐뿌, 보배드림에서 키워드 기반으로 게시물을 수집하고 대시보드에 보여줄 것. 문제는 각 플랫폼마다 수집 방식이 완전히 달랐다.

## 구조 결정

Node.js를 메인 서버로 쓰고, 처리가 복잡한 Instagram 부분은 Python으로 분리했다.

```
src/
  models/
    sns/          # Instagram, Twitter
    community/    # 뽐뿌, 보배드림
    portal/       # 네이버 뉴스, 카페
  routes/         # REST API
  setups/         # WebSocket 설정
```

클라이언트가 키워드를 입력하면 WebSocket으로 실시간 수집 진행률을 받고, 완료되면 REST로 결과를 가져가는 구조.

## 플랫폼별 수집 방식

**네이버 뉴스**: 공식 검색 API가 있어서 가장 깔끔했다. rate limit만 조심하면 됐다.

**보배드림/뽐뿌**: Puppeteer(Node.js) 기반 headless 크롤링. DOM 셀렉터가 자주 바뀌어서 유지보수가 번거로웠다. Playwright로 마이그레이션 검토했는데 결국 시간이 안 났다.

```javascript
// 보배드림 크롤링 예시
async function scrapBobaedream(keyword) {
  const browser = await puppeteer.launch({ headless: true });
  const page = await browser.newPage();
  await page.goto(`https://www.bobaedream.co.kr/search?keyword=${keyword}`);

  const posts = await page.evaluate(() => {
    return Array.from(document.querySelectorAll('.bbs-list li')).map(el => ({
      title: el.querySelector('.title')?.textContent?.trim(),
      date: el.querySelector('.date')?.textContent?.trim(),
      link: el.querySelector('a')?.href,
    }));
  });
  await browser.close();
  return posts;
}
```

**Instagram**: Instaloader (Python 라이브러리)를 선택했다. 공식 API가 아닌 비공식이라 세션 관리가 핵심이었다.

```python
import instaloader
from pathlib import Path

L = instaloader.Instaloader()
SESSION_FILE = Path("session/instagram_session")

def load_session():
    if SESSION_FILE.exists():
        L.load_session_from_file("계정명", str(SESSION_FILE))
    else:
        L.login("계정명", "비밀번호")
        L.save_session_to_file(str(SESSION_FILE))
```

로그인 세션을 파일로 저장해 재사용하지 않으면 매 요청마다 로그인이 필요해서 봇 탐지에 걸렸다.

## 동시성 문제

여러 플랫폼을 동시에 크롤링하면서 Puppeteer 인스턴스가 겹치는 문제가 생겼다. 브라우저 여러 개를 동시에 열면 메모리가 빠르게 차올랐다.

해결책은 단순했다. 브라우저 인스턴스를 싱글턴으로 만들고 페이지만 새로 열고 닫는 방식.

```javascript
let _browser = null;

async function getBrowser() {
  if (!_browser) {
    _browser = await puppeteer.launch({ headless: true });
  }
  return _browser;
}
```

다만 크롤링 중 브라우저가 crash나면 _browser가 null로 초기화 안 되는 문제가 있어서 에러 핸들러에 `_browser = null` 추가.

## Docker 배포

개발 환경에서 잘 되던 게 Docker에서 안 됐다. Puppeteer가 Chrome 실행에 필요한 라이브러리가 없어서.

```dockerfile
FROM node:18-slim

# Chromium 의존성
RUN apt-get update && apt-get install -y \
    chromium \
    libnss3 \
    libatk-bridge2.0-0 \
    libdrm2 \
    libxkbcommon0 \
    libgbm1 \
    --no-install-recommends

ENV PUPPETEER_SKIP_CHROMIUM_DOWNLOAD=true
ENV PUPPETEER_EXECUTABLE_PATH=/usr/bin/chromium
```

이 설정 찾는 데 두 시간 날렸다. Puppeteer Docker 이슈는 검색하면 널려 있는데 그 당시엔 왜 그렇게 헤맸는지 모르겠다.

## 결과

네이버, 트위터, 뽐뿌, 보배드림은 잘 됐고 Instagram은 세션 만료 주기가 불규칙해서 간헐적으로 실패했다. 실제 서비스에서 Instagram 수집은 신뢰성이 낮았다. 비공식 API의 한계.
