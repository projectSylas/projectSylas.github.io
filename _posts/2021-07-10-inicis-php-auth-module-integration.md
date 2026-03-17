---
layout: post
title: PHP CMS에 inicis 간편인증 모듈 연동하기
subtitle: 결제 모듈 연동의 복잡함과 오픈소스 배포까지
author: HyeongJin
date: 2021-07-10 10:00:00 +0900
categories: Backend
tags: [PHP, backend, authentication]
sidebar: []
published: true
---

에스아이알소프트에서 운영하던 PHP CMS에 inicis 간편인증 모듈을 붙이는 작업을 맡았다. 간편인증은 공인인증서 없이 SMS나 생체인식으로 본인확인을 하는 방식이다.

## inicis 연동 구조

inicis는 클라이언트 → inicis 서버 → 콜백 방식으로 동작한다.

```
사용자 브라우저
    → inicis 인증 팝업 열기
    → inicis 서버에서 인증 처리
    → 인증 결과를 콜백 URL로 POST
    → 우리 서버에서 결과 검증
```

PHP로 inicis에서 제공하는 SDK를 연동해야 했다. SDK가 PHP 5.x 기준이라 현재 환경에서 deprecated 함수를 일부 교체해야 했다.

## 가장 까다로웠던 부분

**암호화 처리**

inicis는 결과값을 암호화해서 보낸다. 복호화 로직이 SDK에 있긴 한데, 환경에 따라 openssl 설정이 달라서 복호화가 안 되는 케이스가 있었다.

```php
// inicis 결과 복호화 (간략화)
function decryptResult($encData, $key) {
    $decrypted = openssl_decrypt(
        base64_decode($encData),
        'AES-128-ECB',
        $key,
        OPENSSL_RAW_DATA
    );
    return $decrypted;
}
```

서버마다 openssl 버전이 달라서 동일한 코드가 다르게 동작하는 케이스가 있었다. 이때 암호화 라이브러리 버전 의존성을 처음 실감했다.

**팝업 콜백 처리**

인증 결과가 팝업에서 부모 창으로 전달돼야 한다. `window.opener`로 부모 창에 결과를 넘기는 방식인데, 브라우저 보안 정책에 따라 `window.opener`가 null이 되는 케이스가 있었다.

```javascript
// 팝업에서 부모 창으로 결과 전달
if (window.opener && !window.opener.closed) {
    window.opener.postMessage({
        type: 'auth_result',
        data: authResult
    }, window.location.origin);
    window.close();
}
```

`postMessage`로 바꾸고 나서 크로스브라우저 문제가 해결됐다.

## CMS 연동 및 오픈소스 배포

연동이 완료된 모듈을 CMS 플러그인 형태로 패키징했다. 동일한 inicis 연동 작업을 다른 프로젝트에서도 써야 하는 상황이 생길 것 같아서 재사용 가능하도록 만들었다.

```php
class InicisAuthModule {
    private $merchantId;
    private $apiKey;

    public function __construct($merchantId, $apiKey) {
        $this->merchantId = $merchantId;
        $this->apiKey = $apiKey;
    }

    public function generateAuthUrl($callbackUrl) {
        // 인증 URL 생성 로직
    }

    public function verifyCallback($postData) {
        // 콜백 검증 로직
    }
}
```

결국 이걸 오픈소스로 배포했다. 당시 PHP CMS에서 inicis 연동 샘플 코드가 많지 않았는데, 배포 후 실제로 쓰는 사람들이 있어서 뿌듯했다.
