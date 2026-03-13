---
layout: post
title: 나무위키 미디어 데이터 크롤링 + KOBIS-TMDB 매칭 자동화
subtitle: ETL 클래스 설계와 신뢰도 기반 데이터 병합 전략
author: HyeongJin
categories: AI/LLM
tags: [Python, AI, backend]
sidebar: []
published: true
---

KOBIS와 TMDB만으로는 드라마 스태프 데이터가 부족했다. 특히 OTT 오리지널이나 최신 드라마는 KOBIS에 누락이 많다.

나무위키가 의외로 드라마/영화 스태프 정보가 잘 정리되어 있다. 비공식 데이터라 신뢰성 이슈가 있지만 KOBIS 공식 데이터와 교차 검증하면 쓸 수 있다.

## ETL 클래스 설계

처음엔 각 소스별로 함수를 만들다가, 코드가 너무 비슷한데 각각 다른 파일에 흩어지는 문제가 생겼다.

공통 인터페이스를 정의하는 베이스 클래스로 리팩터링.

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Iterator

@dataclass
class ETLRecord:
    source: str
    media_id: str | None
    title: str
    title_en: str | None
    year: int | None
    raw_data: dict

class MediaETLBase(ABC):
    source_name: str = ""

    @abstractmethod
    def extract(self) -> Iterator[dict]:
        """원본 데이터 수집"""
        ...

    @abstractmethod
    def transform(self, raw: dict) -> ETLRecord | None:
        """정제 및 변환. None 반환 시 해당 레코드 스킵"""
        ...

    def load(self, records: list[ETLRecord]) -> int:
        """DB upsert. 공통 로직이라 베이스에서 구현"""
        return bulk_upsert_media_staging(records)

    def run(self) -> int:
        total = 0
        batch = []
        for raw in self.extract():
            record = self.transform(raw)
            if record is None:
                continue
            batch.append(record)
            if len(batch) >= 500:
                total += self.load(batch)
                batch.clear()
        if batch:
            total += self.load(batch)
        return total
```

나무위키 ETL은 이걸 상속.

```python
class NamuwikiMediaETL(MediaETLBase):
    source_name = "namuwiki"

    def extract(self) -> Iterator[dict]:
        for title in get_media_titles_to_crawl():
            html = fetch_namuwiki_page(title)
            if html:
                yield {"title": title, "html": html}

    def transform(self, raw: dict) -> ETLRecord | None:
        soup = BeautifulSoup(raw["html"], "html.parser")
        info_box = soup.find("table", class_="wikitable")
        if not info_box:
            return None

        year = extract_year_from_infobox(info_box)
        title_en = extract_english_title(info_box)

        return ETLRecord(
            source="namuwiki",
            media_id=None,  # 나무위키엔 공식 ID 없음
            title=raw["title"],
            title_en=title_en,
            year=year,
            raw_data={"html_hash": hashlib.md5(raw["html"].encode()).hexdigest()},
        )
```

## KOBIS-TMDB 매칭

세 소스(KOBIS, TMDB, 나무위키)에서 같은 작품을 가리키는 레코드를 연결해야 한다.

정확한 매칭이 어렵다. 한국 제목, 영어 제목, 연도, 감독 등 여러 속성을 복합적으로 비교.

```python
from enum import Enum

class MatchConfidence(Enum):
    EXACT = "exact"       # 공식 ID 일치
    HIGH = "high"         # 제목 + 연도 일치
    MEDIUM = "medium"     # 제목만 일치 또는 유사도 높음
    LOW = "low"           # 유사도 낮음
    NONE = "none"         # 매칭 없음

def match_kobis_to_tmdb(kobis: dict, tmdb_candidates: list[dict]) -> tuple[dict | None, MatchConfidence]:
    # 1. TMDB ID가 있으면 확실한 매칭
    if kobis.get("tmdb_id"):
        for t in tmdb_candidates:
            if t["id"] == kobis["tmdb_id"]:
                return t, MatchConfidence.EXACT

    # 2. 제목 + 연도 매칭
    for t in tmdb_candidates:
        if (
            similar_title(kobis["title"], t.get("title", ""))
            and abs(int(kobis.get("year", 0)) - t.get("release_year", 0)) <= 1
        ):
            return t, MatchConfidence.HIGH

    # 3. 제목만 유사도 매칭
    best_match = None
    best_score = 0
    for t in tmdb_candidates:
        score = title_similarity(kobis["title"], t.get("title", ""))
        if score > best_score:
            best_score = score
            best_match = t

    if best_score > 0.85:
        return best_match, MatchConfidence.MEDIUM
    if best_score > 0.6:
        return best_match, MatchConfidence.LOW

    return None, MatchConfidence.NONE
```

`MEDIUM` 이상만 자동 병합하고, `LOW`는 수동 검토 큐에 넣었다.

## 영어 제목 추가

KOBIS 데이터엔 영문 제목이 없거나 부정확한 경우가 많았다. 나무위키에서 크롤링한 영문 제목을 보완용으로 씀.

```python
@task
def enrich_english_titles():
    """나무위키에서 영문 제목 보강"""
    mediae_without_en = Media.objects.filter(title_en__isnull=True)

    for media in mediae_without_en:
        namuwiki_record = NamuwikiMedia.objects.filter(
            title=media.title,
            year=media.year
        ).first()
        if namuwiki_record and namuwiki_record.title_en:
            media.title_en = namuwiki_record.title_en
            media.save(update_fields=['title_en'])
```

나무위키 크롤링 데이터라 100% 신뢰할 순 없지만, 없는 것보다 훨씬 낫다.
