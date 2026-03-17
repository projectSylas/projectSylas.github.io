---
layout: post
title: 카카오톡 챗봇으로 Notion 게임 리스트 관리하기
subtitle: Flask 스킬 서버 + Notion API — 명령어 한 줄로 게임 DB에 항목 추가
author: HyeongJin
date: 2026-01-12 10:00:00 +0900
categories: Backend
tags: [Python, Flask, backend]
sidebar: []
published: true
---

Notion에 게임 리스트를 관리하는데, 매번 앱을 열어서 수동으로 추가하는 게 번거로웠다. 카카오톡 채널 봇으로 명령어 하나로 Notion DB에 추가되도록 만들었다.

```
/추가 백투더던 PC 생존 2D 시뮬레이션
```

이렇게 입력하면 제목, 플랫폼, 태그가 파싱돼서 Notion 데이터베이스에 새 행이 추가된다.

## 구조

Flask로 카카오톡 스킬 서버를 구현하고, Render에 배포했다.

```
src/
  app.py           # Flask 진입점
  kakao_chatbot.py # 스킬 서버 + Notion API 연동
assets/
  game_emoge.svg   # 카드 썸네일용 아이콘
```

## 카카오톡 스킬 서버 — Flask

카카오 채널 봇은 사용자가 메시지를 보내면 내 서버로 POST 요청을 보낸다. 응답은 카카오 스킬 JSON 포맷을 맞춰야 한다.

```python
from flask import Flask, jsonify, request
from notion_client import Client

app = Flask(__name__, static_folder='../assets', static_url_path='/assets')

NOTION_API_KEY = os.getenv("NOTION_API_KEY", "")
NOTION_DATABASE_ID = os.getenv("NOTION_DATABASE_ID", "")

notion_client = Client(
    auth=NOTION_API_KEY,
    notion_version="2025-09-03"  # multiple data sources 지원
)

@app.route('/skill', methods=['POST'])
def skill():
    body = request.json
    utterance = body['userRequest']['utterance']

    if utterance.startswith('/추가'):
        result = handle_add_game(utterance)
    else:
        result = make_simple_text("명령어: /추가 [제목] [플랫폼] [태그...]")

    return jsonify(result)
```

Notion API 최신 버전(`2025-09-03`)을 명시하지 않으면 일부 property 타입이 지원되지 않는 경우가 있었다.

## 명령어 파싱

입력 형식: `/추가 [제목] [플랫폼] [태그1 태그2 ...]`

```python
def parse_add_command(utterance: str) -> dict | None:
    # "/추가 " 이후 파싱
    text = utterance.replace('/추가', '').strip()
    parts = text.split()

    if len(parts) < 2:
        return None

    # 플랫폼 목록 (고정)
    PLATFORMS = ['PC', 'PS5', 'PS4', 'Switch', 'Mobile', 'Xbox']

    title_parts = []
    platform = None
    tags = []

    for part in parts:
        if part.upper() in [p.upper() for p in PLATFORMS] and platform is None:
            # 첫 번째 플랫폼 키워드를 플랫폼으로
            platform = part
        elif platform is not None:
            # 플랫폼 이후는 모두 태그
            tags.append(part)
        else:
            title_parts.append(part)

    if not title_parts or platform is None:
        return None

    return {
        'title': ' '.join(title_parts),
        'platform': platform,
        'tags': tags,
    }
```

## Notion API 연동 — DB에 행 추가

```python
def add_game_to_notion(title: str, platform: str, tags: list[str]) -> bool:
    today = datetime.now().strftime('%Y-%m-%d')

    properties = {
        "이름": {
            "title": [{"text": {"content": title}}]
        },
        "플랫폼": {
            "select": {"name": platform}
        },
        "태그": {
            "multi_select": [{"name": tag} for tag in tags]
        },
        "추가일": {
            "date": {"start": today}
        },
    }

    notion_client.pages.create(
        parent={"database_id": NOTION_DATABASE_ID},
        icon={"type": "external", "external": {"url": GAME_ICON_URL}},
        properties=properties,
    )
    return True
```

Notion 페이지 아이콘도 자동으로 붙인다. 게임 이모지 SVG를 서버에 static으로 올려두고 URL로 참조했다.

## 카카오 응답 포맷

성공/실패 여부에 따라 다른 메시지를 반환한다.

```python
def make_simple_text(text: str) -> dict:
    return {
        "version": "2.0",
        "template": {
            "outputs": [{"simpleText": {"text": text}}]
        }
    }

def handle_add_game(utterance: str) -> dict:
    parsed = parse_add_command(utterance)
    if not parsed:
        return make_simple_text("형식: /추가 [제목] [플랫폼] [태그...]")

    success = add_game_to_notion(**parsed)
    if success:
        tags_str = ' '.join(f'#{t}' for t in parsed['tags'])
        msg = f"✅ 추가됨\n제목: {parsed['title']}\n플랫폼: {parsed['platform']}\n태그: {tags_str}"
    else:
        msg = "❌ 추가 실패. Notion API를 확인해주세요."

    return make_simple_text(msg)
```

## Render 배포

`Procfile`로 Render에 배포했다.

```
web: gunicorn -w 4 -b 0.0.0.0:$PORT "src.app:app"
```

환경변수는 Render 대시보드에서 설정한다. `.env.example`에 필요한 키 목록만 커밋해두고, 실제 값은 배포 환경에서만 설정한다.

```bash
NOTION_API_KEY=secret_...
NOTION_DATABASE_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
SERVER_URL=https://your-app.onrender.com
```

## 만들면서 느낀 것

Notion API의 property 타입이 생각보다 다양하고 strict하다. `select`와 `multi_select`는 구조가 다르고, `date`는 반드시 `{"start": "YYYY-MM-DD"}` 형식이어야 한다.

카카오 스킬 서버 응답도 포맷이 정해져 있어서 틀리면 봇이 아무 응답도 안 한다. 개발 중에는 로컬에서 `ngrok`으로 터널링해서 카카오 채널 설정에 임시 URL을 넣고 테스트했다.

Render 무료 플랜은 15분 비활성 후 슬립 모드로 전환된다. 첫 요청에 응답 시간이 길어지는 문제가 있어서, 카카오 스킬 서버 응답 타임아웃(5초)에 걸릴 수 있다. 주기적으로 health check 요청을 보내서 슬립을 방지했다.
