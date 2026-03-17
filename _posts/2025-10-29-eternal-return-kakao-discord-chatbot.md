---
layout: post
title: 이터널리턴 카카오톡 + 디스코드 챗봇 만들기
subtitle: Flask 웹훅 + discord.py로 게임 유저 조회 / 패치노트 자동 알림 봇 구축
author: HyeongJin
date: 2025-10-29 10:00:00 +0900
categories: Backend
tags: [Python, Flask, backend]
sidebar: []
published: true
---

이터널리턴(Eternal Return) 게임 커뮤니티용 챗봇을 만들었다. 카카오톡 채널 봇과 디스코드 봇 두 가지를 동시에 지원한다.

주요 기능:
- 닉네임으로 유저 전적 / 통계 / 랭킹 조회
- 공식 홈페이지 패치노트 자동 스크래핑 → 디스코드 채널로 전송
- 카트 명령어, 기타 유틸리티

## 구조

카카오톡 봇은 Flask 웹훅 서버로 처리하고, 디스코드 봇은 `discord.py`로 별도 프로세스로 실행한다.

```
.
├── app.py           # Flask 웹훅 서버 (카카오톡)
├── discord_bot.py   # discord.py 봇
├── eternal_api.py   # 이터널리턴 공식 API 클라이언트
├── scraper.py       # 패치노트 스크래핑 (BeautifulSoup)
└── Procfile         # Heroku 배포
```

카카오톡 채널 봇은 카카오에서 webhook 방식으로 메시지를 받아서 처리하고 JSON 응답을 돌려준다.

## Flask 웹훅 — 카카오톡 봇

카카오 채널 봇은 사용자가 메시지를 보내면 카카오 서버가 내 서버로 POST 요청을 날린다. 응답은 카카오 응답 JSON 포맷에 맞게 만들어야 한다.

```python
from flask import Flask, request, jsonify

app = Flask(__name__)

@app.route('/kakao', methods=['POST'])
def kakao_webhook():
    body = request.json
    utterance = body['userRequest']['utterance']  # 사용자 입력
    user_id = body['userRequest']['user']['id']

    response_text = handle_command(utterance)

    return jsonify({
        "version": "2.0",
        "template": {
            "outputs": [{
                "simpleText": {
                    "text": response_text
                }
            }]
        }
    })

def handle_command(utterance: str) -> str:
    if utterance.startswith('전적 '):
        nickname = utterance.replace('전적 ', '').strip()
        return get_user_stats(nickname)
    elif utterance.startswith('랭킹 '):
        return get_ranking(utterance.replace('랭킹 ', '').strip())
    else:
        return "명령어: 전적 [닉네임] / 랭킹 [닉네임]"
```

카카오 응답 포맷이 까다롭다. `simpleText`, `basicCard`, `listCard` 등 타입마다 JSON 구조가 다르고, 이미지 비율 같은 세부 제약도 있다.

## 이터널리턴 공식 API

이터널리턴은 공식 REST API를 제공한다. 닉네임으로 유저 번호 조회 → 유저 번호로 통계/랭킹 조회하는 2-step 흐름이다.

```python
import requests

BASE_URL = "https://open-api.bser.io"

def get_user_num(nickname: str) -> int:
    resp = requests.get(
        f"{BASE_URL}/v1/user/nickname",
        params={"query": nickname},
        headers={"x-api-key": API_KEY}
    )
    return resp.json()['user']['userNum']

def get_user_stats(nickname: str) -> str:
    try:
        user_num = get_user_num(nickname)
        resp = requests.get(
            f"{BASE_URL}/v1/user/stats/{user_num}/0",  # 시즌 0 = 전체
            headers={"x-api-key": API_KEY}
        )
        stats = resp.json()['userStats'][0]
        return (
            f"[{nickname}]\n"
            f"게임수: {stats['totalGames']}\n"
            f"승리: {stats['wins']}\n"
            f"승률: {stats['wins']/stats['totalGames']*100:.1f}%\n"
            f"평균 킬: {stats['averageKills']:.1f}"
        )
    except Exception:
        return f"'{nickname}' 유저를 찾을 수 없습니다."
```

## discord.py — 패치노트 자동 알림

디스코드 봇은 `discord.py`로 구현했다. 일정 주기로 공식 홈페이지에서 패치노트 최신 글을 감지하고, 새 글이 있으면 지정 채널에 Embed로 알림을 보낸다.

```python
import discord
from discord.ext import commands, tasks
from scraper import get_latest_patch_note

intents = discord.Intents.default()
bot = commands.Bot(command_prefix='!', intents=intents)

last_patch_title = None

@tasks.loop(minutes=30)
async def check_patch_note():
    global last_patch_title
    patch = get_latest_patch_note()
    if patch and patch['title'] != last_patch_title:
        last_patch_title = patch['title']
        channel = bot.get_channel(PATCH_CHANNEL_ID)
        embed = discord.Embed(
            title=patch['title'],
            url=patch['url'],
            description=patch['summary'],
            color=0x00b0f4,
        )
        await channel.send(embed=embed)

@bot.event
async def on_ready():
    check_patch_note.start()
```

패치노트는 BeautifulSoup으로 스크래핑했다. 공식 홈페이지 공지사항 목록에서 제목과 링크를 가져오고, 마지막으로 전송한 제목과 비교해서 새 글이면 Embed를 보내는 방식이다.

## Heroku 배포

Flask 서버와 디스코드 봇이 동시에 실행돼야 해서 Procfile에 두 프로세스를 분리했다.

```
web: python app.py
worker: python discord_bot.py
```

Heroku에서 `web` dyno는 Flask 서버, `worker` dyno는 디스코드 봇을 별도로 실행한다.

## 만들면서 겪은 것

카카오톡 봇 응답 JSON 형식이 생각보다 엄격하다. `template.outputs` 배열 크기 제한, 버튼 개수 제한, 이미지 URL 요구사항 등 꼼꼼히 맞춰야 심사를 통과한다.

패치노트 스크래핑은 공식 홈페이지 DOM이 바뀌면 셀렉터가 깨진다. 실제로 운영하면서 한 번 구조가 바뀌어서 스크래핑이 멈춘 적이 있었다. 최신 패치노트 자동 감지 로직도 이때 보완했다.

이터널리턴 공식 API는 rate limit이 있다. 여러 명이 동시에 조회하면 429가 나오는 상황이 생겨서, 요청 간 딜레이를 추가해서 대응했다.
