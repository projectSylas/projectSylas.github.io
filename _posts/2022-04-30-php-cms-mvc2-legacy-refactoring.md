---
layout: post
title: PHP 레거시 CMS 절차지향 → MVC2 패턴으로 리팩토링
subtitle: 스파게티 코드를 구조화하면서 배운 것들
author: HyeongJin
date: 2022-04-30 10:00:00 +0900
categories: Backend
tags: [PHP, backend, refactoring]
sidebar: []
published: true
---

에스아이알소프트의 CMS 코어 코드는 2010년대 초에 만들어진 레거시였다. 단일 PHP 파일에 DB 쿼리, 비즈니스 로직, HTML이 전부 섞여 있는 구조였다.

```php
// 기존 구조 예시
<?php
$conn = mysql_connect(...);  // mysql_* 함수 사용 중
$result = mysql_query("SELECT * FROM posts WHERE id=" . $_GET['id']);
$row = mysql_fetch_array($result);
// 바로 HTML 출력...
?>
<html>
  <body>
    <h1><?= $row['title'] ?></h1>  <!-- XSS 위험 그대로 -->
    <?= $row['content'] ?>
  </body>
</html>
```

SQL 인젝션, XSS 취약점, 중복 코드가 산재해 있었다. 새 기능을 추가할 때마다 어디 손대야 할지 찾는 것 자체가 일이었다.

## 리팩토링 목표

1. MVC2 패턴으로 역할 분리
2. PDO로 DB 레이어 교체 (SQL 인젝션 방지)
3. 출력 이스케이핑 적용 (XSS 방지)
4. 공통 로직 클래스화

## 디렉터리 구조 정리

```
cms/
├── controller/     # 요청 처리, 뷰 호출
├── model/          # DB 접근 계층
├── view/           # HTML 템플릿
├── core/           # Router, DB 클래스
└── index.php       # Front Controller
```

`index.php`가 모든 요청을 받아서 Router가 적절한 Controller로 보내는 Front Controller 패턴.

## PDO로 DB 레이어 교체

```php
class Database {
    private static $instance = null;
    private $pdo;

    private function __construct() {
        $this->pdo = new PDO(
            "mysql:host=localhost;dbname=cms;charset=utf8",
            DB_USER, DB_PASS,
            [PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION]
        );
    }

    public static function getInstance() {
        if (!self::$instance) {
            self::$instance = new self();
        }
        return self::$instance;
    }

    public function query($sql, $params = []) {
        $stmt = $this->pdo->prepare($sql);
        $stmt->execute($params);
        return $stmt;
    }
}
```

기존 `mysql_query($sql)` 방식을 전부 `query($sql, $params)` 방식으로 교체했다. prepared statement로 SQL 인젝션을 차단했다.

## Controller / Model 분리

```php
// PostController.php
class PostController {
    private $model;

    public function __construct() {
        $this->model = new PostModel();
    }

    public function show($id) {
        $post = $this->model->findById((int)$id);
        if (!$post) {
            $this->render('404');
            return;
        }
        $this->render('post/show', ['post' => $post]);
    }
}

// PostModel.php
class PostModel {
    public function findById($id) {
        $db = Database::getInstance();
        return $db->query(
            "SELECT * FROM posts WHERE id = ?",
            [$id]
        )->fetch(PDO::FETCH_ASSOC);
    }
}
```

## 출력 이스케이핑

뷰에서 출력할 때 `htmlspecialchars`를 빠뜨리면 XSS가 터진다. 헬퍼 함수 하나로 통일했다.

```php
// view 헬퍼
function e($value) {
    return htmlspecialchars($value ?? '', ENT_QUOTES, 'UTF-8');
}
```

뷰에서는 `<?= $post['title'] ?>` 대신 `<?= e($post['title']) ?>`로.

## 기억에 남는 것

리팩토링하면서 기존 코드에 SQL 인젝션이 가능한 부분이 10군데 넘게 있었다. `$_GET['id']`를 그대로 쿼리에 넣은 부분들이었다.

코드 구조를 바꾸는 것 자체보다 기존 동작을 유지하면서 바꾸는 게 더 어려웠다. 리팩토링할 때 테스트 코드가 없으면 얼마나 위험한지 이때 처음 실감했다.
