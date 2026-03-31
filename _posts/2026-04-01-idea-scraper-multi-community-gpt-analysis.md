---
layout: post
title: Reddit + 국내 커뮤니티 멀티소스 스크래퍼 + GPT-4o로 사이드 프로젝트 아이디어 발굴하기
subtitle: 에펨코리아·디시인사이드·클리앙·Reddit에서 "이런 앱 없나" 글을 긁어 GPT-4o가 MVP 후보까지 분류
author: HyeongJin
date: 2026-04-01 10:00:00 +0900
categories: AI/LLM
tags: [Python, OpenAI, Selenium, BeautifulSoup, Crawling, Reddit]
sidebar: []
published: true
---

사이드 프로젝트 아이디어를 찾으려면 커뮤니티를 직접 뒤지는 게 제일 확실하다. "이런 앱 있으면 좋겠다", "왜 아직도 없냐" 같은 글들이 실제 수요를 가장 날 것 그대로 보여준다. 근데 에펨코리아, 디시인사이드, 클리앙, Reddit을 일일이 검색하다 보면 금방 질린다.

그래서 멀티소스 스크래퍼를 만들고 GPT-4o로 수요 분류와 MVP 후보 추출까지 자동화했다.

## 구조

```
idea-scraper/
├── main.py
├── config.py
├── scrapers/
│   ├── browser.py    # Selenium 공통 드라이버
│   ├── reddit.py     # requests + JSON API
│   ├── fmkorea.py    # Selenium + BS4
│   ├── dcinside.py   # Selenium + BS4
│   └── clien.py      # Selenium + BS4
└── analyzer/
    └── classifier.py # 중복 제거 + GPT-4o 분석
```

각 스크래퍼가 동일한 dict 형태(`source`, `title`, `body`, `url`, `score`, `keyword`)를 반환하게 맞춰서 `main.py`에서 단순 합산한다.

## 키워드 설계

수요 발굴용 키워드는 한국어/영어 두 세트다.

```python
KEYWORDS_KO = [
    "이런 서비스 있으면", "이런 앱 있으면", "만들어주세요",
    "왜 아직도 없", "누가 만들어줬으면", "서비스가 없네",
    "앱이 없네", "자동화 해주는 서비스", "이거 해주는 앱",
    "이런거 없나요", "이런거 없을까", "개발해주세요",
]

KEYWORDS_EN = [
    "why isn't there an app", "someone should build",
    "would pay for an app", "why doesn't this exist",
    "i need an app that", "there should be a service",
    "can't believe there's no app",
]
```

한국 커뮤니티 세 곳은 한국어 키워드로, Reddit은 영어 키워드로 검색한다. 키워드 자체가 "불만 토로"와 "수요 표현" 패턴이기 때문에 노이즈보다 신호가 훨씬 많다.

## Reddit: requests로 JSON API 직접 호출

Reddit은 별도 인증 없이 `.json` 엔드포인트를 쓸 수 있다. `praw`를 쓰지 않고 `requests`로 직접 때린다.

```python
def scrape():
    results = []
    search_terms = KEYWORDS_EN[:5]

    for keyword in search_terms:
        url = f"https://www.reddit.com/search.json?q={requests.utils.quote(keyword)}&sort=new&limit=50"
        res = requests.get(url, headers=HEADERS, timeout=10)
        posts = res.json()["data"]["children"]

        for post in posts:
            p = post["data"]
            results.append({
                "source": "Reddit",
                "title": p.get("title", ""),
                "body": p.get("selftext", "")[:300],
                "url": f"https://reddit.com{p.get('permalink', '')}",
                "score": p.get("score", 0),
                "keyword": keyword,
            })

        time.sleep(1.5)

    return results
```

`sort=new`로 최신순 정렬, `limit=50`으로 한 번에 50개. 키워드당 1.5초 sleep을 줘서 rate limit을 피했다. `body`는 300자로 잘라서 토큰 낭비를 줄인다.

## 국내 커뮤니티: Selenium + BeautifulSoup

에펨코리아, 디시인사이드, 클리앙은 JS 렌더링이 필요하거나 봇 감지가 있어서 Selenium을 썼다.

공통 드라이버는 `browser.py`에서 싱글턴처럼 관리한다.

```python
def get_driver(headless=True):
    options = Options()
    if headless:
        options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    options.add_argument("user-agent=Mozilla/5.0 ...")

    driver = webdriver.Chrome(
        service=Service(ChromeDriverManager().install()),
        options=options
    )
    driver.execute_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    )
    return driver
```

`AutomationControlled` 비활성화 + `webdriver` 프로퍼티 오버라이드 조합으로 자동화 감지를 피했다. `ChromeDriverManager`를 쓰면 드라이버 버전 관리를 신경 쓸 필요가 없다.

사이트마다 DOM이 다르기 때문에 셀렉터는 각자 구현했다. 에펨코리아 예시:

```python
soup = BeautifulSoup(driver.page_source, "html.parser")
posts = soup.select("h3 a")

for post in posts:
    title = post.get_text(strip=True)
    href = post.get("href", "")
    results.append({
        "source": "에펨코리아",
        "title": title,
        "url": f"https://www.fmkorea.com{href}" if href.startswith("/") else href,
        ...
    })
```

클리앙은 삭제된 게시물이 "관리자 삭제된 게시물입니다."라는 텍스트로 남아 있어서 필터링했다.

```python
if not title or title == "관리자 삭제된 게시물입니다.":
    continue
```

## 중복 제거

같은 게시물이 여러 키워드에 걸려서 중복으로 들어온다. 제목 앞 50자를 키로 써서 set으로 걸러낸다.

```python
def deduplicate(results: list[dict]) -> list[dict]:
    seen = set()
    unique = []
    for r in results:
        key = r["title"].strip()[:50]
        if key not in seen:
            seen.add(key)
            unique.append(r)
    return unique
```

완벽한 방법은 아니지만 (같은 글을 다르게 표시하는 사이트도 있다) 실용적으로 충분하다.

## GPT-4o 분석

중복 제거한 게시글을 최대 500개 모아서 GPT-4o에 넘긴다. 토큰을 아끼기 위해 제목 + body 80자만 넘긴다.

```python
posts_text = "\n".join([
    f"[{r['source']}] {r['title']}" + (f" / {r['body'][:80]}" if r.get('body') else "")
    for r in results[:500]
])

response = client.chat.completions.create(
    model="gpt-4o",
    max_tokens=3000,
    messages=[{
        "role": "user",
        "content": f"""아래는 커뮤니티에서 수집한 게시글들입니다.

{posts_text}

다음을 분석해주세요:

1. **카테고리별 수요 분류** (생활편의, IT/앱, 금융, 게임, 건강, 기타)
   - 각 카테고리에서 반복되는 니즈 top 3

2. **가장 유망한 아이디어 5개**
   - 아이디어명 / 타겟 유저 / 혼자 개발 가능 여부 (O/X) / 예상 수익 모델

3. **즉시 만들 수 있는 MVP 아이디어 1개** (2~4주 내 완성 가능)

한국어로 답해주세요."""
    }]
)
```

프롬프트 포인트 세 가지:
- **카테고리 분류**로 어떤 분야에 수요가 몰리는지 파악
- **유망 아이디어 5개**에 "혼자 개발 가능 여부" 항목을 넣어서 솔로 개발자 관점으로 필터링
- **MVP 아이디어 1개**를 명시적으로 뽑게 해서 바로 실행 가능한 것으로 좁힘

## 전체 흐름

```
Reddit          → 최신 게시물 50개 × 5키워드
에펨코리아       → 베스트 게시물 × 12키워드
디시인사이드     → 검색 결과 × 12키워드
클리앙          → 검색 결과 × 12키워드
        ↓
    합산 후 중복 제거
        ↓
   GPT-4o 분석
        ↓
  output/analysis.txt
```

실행 한 번에 보통 200~400개 게시물이 수집되고, 중복 제거 후 100~200개가 GPT-4o로 넘어간다.

## 실행

```bash
pip install requests beautifulsoup4 praw openai selenium webdriver-manager python-dotenv
```

```bash
# .env에 OPENAI_API_KEY 설정 후
python main.py
```

```
==================================================
💡 아이디어 수요 스크래퍼
==================================================
🔍 Reddit 검색 중...
  ✓ 'why isn't there an app' → 50개
  ✓ 'someone should build' → 50개
  ...
🔍 에펨코리아 검색 중...
  ✓ '이런 앱 있으면' → 23개
  ...
✅ 총 347개 게시글 발견
🗑️  중복 제거: 347개 → 189개

🤖 AI 분석 중...
```

결과는 `output/analysis.txt`에 저장된다.

## 실제로 써보니

국내 커뮤니티에서 반복적으로 올라오는 수요 패턴이 있다. "이런 앱 없나요" 류의 글을 모아보면 비슷한 불편함이 여러 커뮤니티에서 독립적으로 반복된다는 게 보인다. GPT-4o가 그 패턴을 잘 잡아낸다.

Reddit은 영어권 수요라 직접 만들기엔 거리가 있지만, 이미 검증된 아이디어가 한국에는 없는지 역으로 확인하는 용도로 유용했다.
