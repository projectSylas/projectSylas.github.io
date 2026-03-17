---
layout: post
title: PHP + jQuery로 사내 인트라넷 리뉴얼하기
subtitle: 레거시 그룹웨어 전면 개편과 대관 서비스 신규 개발
author: HyeongJin
date: 2020-05-30 09:00:00 +0900
categories: Backend
tags: [PHP, JavaScript, MySQL, backend]
sidebar: []
published: true
---

첫 직장인 한경ITS에서 맡은 첫 번째 프로젝트가 사내 인트라넷 리뉴얼이었다. 기존 그룹웨어 시스템이 오래돼서 UI도 낡고 기능도 부족한 상태였다.

PHP, JavaScript(jQuery), MySQL 스택이었다. 백엔드 API부터 프론트엔드까지 풀스택으로 혼자 담당했다.

## 기존 시스템 문제

- 인터페이스가 2010년대 초반 수준으로 노후화
- 기능이 게시판, 결재 정도만 있고 부서별 필요 기능이 누락
- 코드 구조가 파일 단위로 로직이 흩어진 절차적 방식

## 리뉴얼 방향

UI는 전면 재설계. jQuery로 동적 인터랙션을 추가했다.

```php
// 기존: 파일 하나에 DB 쿼리와 HTML이 뒤섞인 구조
// 리뉴얼: 로직 분리
class NoticeController {
    public function list() {
        $notices = $this->noticeModel->getAll();
        include 'views/notice_list.php';
    }
    public function create() {
        if ($_SERVER['REQUEST_METHOD'] === 'POST') {
            $this->noticeModel->insert($_POST);
            header('Location: /notice');
        }
        include 'views/notice_form.php';
    }
}
```

DB 쿼리는 별도 Model 클래스로 분리했다. 뷰와 로직이 뒤섞이지 않도록.

## 대관 서비스 개발

인트라넷 리뉴얼과 함께 대관 서비스도 새로 만들었다. 회의실, 강당 등 공간 예약 관리 시스템이다.

핵심 기능:
- 달력 뷰로 예약 현황 확인
- 시간대별 중복 예약 방지
- 예약 승인/반려 워크플로우

```javascript
// 달력 예약 현황 조회
function loadReservations(year, month) {
    $.ajax({
        url: '/api/reservations',
        data: { year, month },
        success: function(data) {
            data.forEach(r => {
                markCalendarDate(r.date, r.status);
            });
        }
    });
}
```

중복 예약 방지는 DB 단에서 처리했다. 같은 공간, 같은 날짜, 시간이 겹치는 예약이 있으면 INSERT를 막는 방식.

```php
// 중복 체크
$overlap = $db->query(
    "SELECT id FROM reservations
     WHERE space_id = ? AND date = ?
     AND NOT (end_time <= ? OR start_time >= ?)
     AND status != 'rejected'",
    [$spaceId, $date, $startTime, $endTime]
);

if ($overlap->rowCount() > 0) {
    throw new Exception('이미 예약된 시간대입니다.');
}
```

첫 직장이라 코드 품질이나 구조가 부족했던 부분이 많았는데, 이때 절차지향 코드의 한계를 직접 느끼면서 MVC 패턴의 필요성을 알게 됐다.
