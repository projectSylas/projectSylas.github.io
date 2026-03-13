---
layout: post
title: NICE 본인인증 React Native WebView 연동 삽질기
subtitle: Django 백엔드 암호화 처리와 iOS/Android 딥링크 차이
author: HyeongJin
categories: React
tags: [React, frontend, Django]
sidebar: []
published: true
---

클레딧 앱에 본인인증을 붙였다. NICE에서 제공하는 패스(PASS) 인증 방식.

공식 문서가 있긴 한데, React Native WebView에서 쓸 때의 레퍼런스가 거의 없어서 처음부터 삽질이었다.

## 인증 흐름

1. 앱 → Django 서버: 인증 토큰 요청
2. Django → NICE API: 암호화 토큰 발급
3. Django → 앱: 인증 URL + 암호화된 파라미터 반환
4. 앱: WebView로 NICE 인증 페이지 오픈
5. 사용자: PASS 앱 인증 완료
6. NICE → Django 콜백: 인증 결과 (암호화됨)
7. Django: 복호화 후 앱에 결과 전달

## Django 암호화 처리

NICE는 AES-128 CBC + Base64 방식을 쓴다.

```python
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad
import base64
import hashlib

class NiceAuthService:
    def __init__(self):
        self.client_id = settings.NICE_CLIENT_ID
        self.client_secret = settings.NICE_CLIENT_SECRET

    def get_crypto_token(self) -> dict:
        """NICE 암호화 토큰 발급"""
        auth_response = requests.post(
            "https://svc.niceapi.co.kr:22001/digital/niceid/oauth/oauth/token",
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Authorization": f"Basic {self._get_basic_auth()}",
            },
            data={"grant_type": "client_credentials", "scope": "default"}
        )
        return auth_response.json()

    def encrypt_request_data(self, data: str, key: str, iv: str) -> str:
        cipher = AES.new(key.encode(), AES.MODE_CBC, iv.encode())
        encrypted = cipher.encrypt(pad(data.encode(), AES.block_size))
        return base64.b64encode(encrypted).decode()

    def decrypt_response_data(self, encrypted: str, key: str, iv: str) -> str:
        cipher = AES.new(key.encode(), AES.MODE_CBC, iv.encode())
        decrypted = unpad(cipher.decrypt(base64.b64decode(encrypted)), AES.block_size)
        return decrypted.decode()
```

## 콜백 URL 환경별 분기

개발/스테이징/프로덕션마다 콜백 URL이 달라야 한다. 처음에 하드코딩했다가 스테이징에서 프로덕션 콜백이 호출되는 문제가 생겼다.

```python
def get_return_url(self) -> str:
    env = settings.ENVIRONMENT  # 'local', 'staging', 'production'
    return {
        'local': 'http://localhost:8000/api/nice/callback/',
        'staging': 'https://staging.cleddit.com/api/nice/callback/',
        'production': 'https://api.cleddit.com/api/nice/callback/',
    }.get(env, settings.NICE_CALLBACK_URL)
```

## React Native WebView 딥링크

NICE PASS 인증이 완료되면 PASS 앱에서 다시 우리 앱으로 돌아와야 한다. 딥링크 처리.

iOS와 Android 차이가 있었다.

```typescript
// iOS: 커스텀 스킴 설정 (info.plist)
// CFBundleURLSchemes: ["cleddit"]
// 딥링크: cleddit://nice-callback

// Android: AndroidManifest.xml
// <intent-filter>
//   <action android:name="android.intent.action.VIEW" />
//   <data android:scheme="cleddit" android:host="nice-callback" />
// </intent-filter>
```

WebView에서 딥링크 URL을 인터셉트해야 한다.

```typescript
<WebView
  source={{ uri: niceAuthUrl }}
  onShouldStartLoadWithRequest={(request) => {
    if (request.url.startsWith('cleddit://')) {
      // 딥링크 처리
      Linking.openURL(request.url);
      return false;  // WebView에서 로드 차단
    }
    return true;
  }}
/>
```

iOS에서는 `onShouldStartLoadWithRequest`가 잘 됐는데, Android에서는 `intent://` 스킴으로 시작하는 URL도 있어서 처리가 필요했다.

```typescript
if (request.url.startsWith('intent://')) {
  // Android intent URL → Linking으로 처리
  const fallbackUrl = request.url.match(/S.browser_fallback_url=([^;]+)/)?.[1];
  if (fallbackUrl) Linking.openURL(decodeURIComponent(fallbackUrl));
  return false;
}
```

## CI/DI 중복 가드

같은 ci(연계정보)로 중복 인증 요청이 들어오는 경우를 막아야 했다.

```python
@transaction.atomic
def verify_callback(self, request_data: dict) -> User:
    ci = request_data.get('ci')
    di = request_data.get('di')

    # 이미 인증된 ci가 있는지 확인
    existing = User.objects.filter(nice_ci=ci).first()
    if existing:
        if existing != request.user:
            raise ValidationError("이미 다른 계정에 등록된 본인인증 정보입니다.")
        return existing  # 동일 유저 재인증은 허용

    request.user.nice_ci = ci
    request.user.nice_di = di
    request.user.is_verified = True
    request.user.save(update_fields=['nice_ci', 'nice_di', 'is_verified'])
    return request.user
```

## 개인정보 로그 제거

디버깅 중에 이름, 생년월일 같은 PII(개인식별정보)가 로그에 찍히는 코드가 있었다.

```python
# 제거 전
logger.debug("NICE 응답: %s", decrypt_response_data(encrypted, key, iv))

# 제거 후
logger.info("NICE 인증 완료: user_id=%s", user.id)
```

인증 관련 코드에서 PII 로깅은 보안 감사에서 걸린다. 아예 암호화된 상태로만 로깅하거나, 처리 완료 후 user_id만 기록하는 방식으로.
