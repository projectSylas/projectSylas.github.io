---
layout: post
title: Django에서 NICE 본인인증 구현하기
subtitle: OAuth 토큰 → 암호화 토큰 → AES-CBC 암호화 → 콜백 복호화까지, 멀티워커 문제와 pg_trgm/GIN 인덱스 적용
author: HyeongJin
date: 2025-08-08 10:00:00 +0900
categories: Backend
tags: [Python, Django, Authentication, PostgreSQL]
sidebar: []
published: true
---

NICE 본인인증(CheckPlus)을 Django 백엔드에 붙이면서 문서만 봐서는 파악하기 어려운 부분들이 있었다. 토큰 캐싱, 멀티워커 환경의 콜백 처리, AES 키 파생 방식이 특히 그랬다.

## NICE 본인인증 흐름

NICE CheckPlus 표준창 서비스는 3단계로 진행된다.

```
1. OAuth 토큰 발급  →  2. 암호화 토큰 발급  →  3. enc_data 생성 + HTML Form 제출
                                                        ↓
                                              NICE 본인인증 화면 (팝업)
                                                        ↓
                                              콜백 URL로 enc_data 반환
                                                        ↓
                                              복호화 → 사용자 CI/DI 추출
```

각 단계마다 별도 API 호출이 필요하고, 토큰은 유효기간이 각각 다르다.

## 서비스 추상화

환경(실제 NICE API / 테스트 모드)에 따라 구현체를 교체할 수 있도록 추상 클래스로 분리했다.

```python
class IdentityVerificationService(ABC):

    @classmethod
    def instance(cls, test=False):
        if getattr(settings, 'NICE_API_APPROVED', False) and getattr(settings, 'NICE_API_CLIENT_ID', ''):
            return IdentityVerificationServiceWithNiceApi()
        elif test or settings.TEST:
            return IdentityVerificationServiceWithTest()
        else:
            return IdentityVerificationServiceWithTest()

    @abstractmethod
    def create_verification_request(self, real_name, mobile_no, request=None, context=None) -> dict:
        pass

    @abstractmethod
    def verify_verification_result(self, verification_id: str) -> dict:
        pass
```

`NICE_API_APPROVED` 설정이 없으면 자동으로 테스트 서비스로 폴백된다. 개발 환경에서 실제 NICE API를 호출하지 않아도 본인인증 플로우를 전부 테스트할 수 있다.

## OAuth 토큰 캐싱

NICE OAuth 토큰은 만료기간이 매우 길다(사실상 영구). 요청마다 새로 발급하면 불필요한 API 호출이 생긴다.

```python
def _get_access_token(self) -> Optional[str]:
    if self._is_oauth_token_valid():
        return self._oauth_token  # 캐시 히트

    # Basic Auth: Base64("client_id:client_secret")
    auth = f"{self.client_id}:{self.client_secret}"
    base64_auth = base64.b64encode(auth.encode('utf-8')).decode('utf-8')

    response = requests.post(
        f"{self.api_url}/digital/niceid/oauth/oauth/token",
        headers={'Authorization': f'Basic {base64_auth}'},
        data={'grant_type': 'client_credentials', 'scope': 'default'},
    )

    result = response.json()
    if result.get('dataHeader', {}).get('GW_RSLT_CD') == '1200':
        access_token = result['dataBody']['access_token']
        self._oauth_token = access_token
        self._oauth_token_expires = time.time() + (50 * 365 * 24 * 60 * 60)
        return access_token
```

응답 성공 여부는 HTTP 상태코드가 아니라 `dataHeader.GW_RSLT_CD == '1200'`으로 판단한다. NICE API는 HTTP 200이어도 내부 오류 코드로 실패를 알린다.

암호화 토큰은 유효기간이 1시간이라 별도로 캐싱한다.

```python
self._crypto_token_expires = time.time() + (60 * 60)
```

## AES 키 파생

NICE 문서에서 가장 헷갈리는 부분이 키 파생 방식이다.

```python
# value = req_dtim + req_no + token_val
result = f"{crypto_data['req_dtim']}{crypto_data['req_no']}{crypto_data['token_val']}"

# SHA256 해시 → Base64
result_hash = hashlib.sha256(result.encode()).digest()
result_val = base64.b64encode(result_hash).decode('utf-8')

# Base64 문자열에서 직접 슬라이싱
key      = result_val[:16]    # AES-128 키
iv       = result_val[-16:]   # 초기화 벡터
hmac_key = result_val[:32]    # HMAC-SHA256 키
```

SHA256 결과물을 바이너리가 아닌 Base64 인코딩한 문자열에서 슬라이싱한다. 바이너리 다이제스트에서 슬라이싱하면 복호화가 실패한다. NICE 샘플 코드와 완전히 동일한 방식으로 구현해야 한다.

## AES-CBC 암호화 + HMAC 무결성

```python
def _encrypt_data(self, plain_data: str, key: str, iv: str) -> str:
    from Crypto.Cipher import AES
    block_size = 16
    pad = lambda s: s + (block_size - len(s) % block_size) * chr(block_size - len(s) % block_size)
    cipher = AES.new(key.encode("utf8"), AES.MODE_CBC, iv.encode("utf8"))
    return base64.b64encode(cipher.encrypt(pad(plain_data).encode('utf-8'))).decode('utf-8')
```

암호화 후 무결성 값(integrity_value)도 생성한다.

```python
h = hmac.new(key=hmac_key.encode(), msg=enc_data.encode('utf-8'), digestmod=hashlib.sha256).digest()
integrity_value = base64.b64encode(h).decode('utf-8')
```

NICE 서버가 콜백 데이터를 전달할 때 integrity_value로 무결성을 검증한다. 틀리면 복호화가 거부된다.

## 멀티워커 문제: DB에 키 저장

gunicorn 멀티워커 환경에서 문제가 있었다. 인증 요청을 처리한 워커(key, iv 보유)와 콜백을 받는 워커가 달라지면 복호화할 수 없다. 인스턴스 변수로 키를 들고 있으면 워커 간 공유가 안 된다.

```python
# 키를 DB에 저장
NiceToken.objects.filter(token_version_id=crypto_data['token_version_id']).delete()
NiceToken.objects.create(
    token_version_id=crypto_data['token_version_id'],
    token_val=crypto_data['token_val'],
    req_no=crypto_data['req_no'],
    key=key,
    iv=iv,
    hmac_key=hmac_key,
    expires_at=timezone.now() + timedelta(hours=24)
)
```

`NiceToken` 모델에 암호화 키와 IV를 저장하고, 콜백에서 `token_version_id`로 조회해서 복호화한다. 어떤 워커가 콜백을 받아도 DB에서 키를 꺼내 쓸 수 있다.

```python
class NiceToken(BaseModel):
    token_version_id = models.CharField(max_length=255, unique=True)
    token_val        = models.TextField()
    req_no           = models.CharField(max_length=255)
    key              = models.TextField()
    iv               = models.TextField()
    hmac_key         = models.TextField()
    expires_at       = models.DateTimeField()
    user_id          = models.IntegerField(null=True, blank=True)

    class Meta:
        db_table = 'nice_tokens'
```

`expires_at`을 24시간으로 설정해서 만료된 토큰은 주기적으로 정리할 수 있다.

## pg_trgm/GIN 인덱스 추가

같은 커밋에서 career 검색 성능 문제도 잡았다. 기존 B-tree 인덱스로는 부분 문자열 검색(`ILIKE '%검색어%'`)이 전체 스캔으로 처리됐다.

```python
# migrations/0026
migrations.AddIndex(
    model_name='career',
    index=GinIndex(
        fields=['user_name'],
        name='career_user_name_trgm_gin',
        opclasses=['gin_trgm_ops']
    ),
),
migrations.AddIndex(
    model_name='career',
    index=GinIndex(
        fields=['title'],
        name='career_title_trgm_gin',
        opclasses=['gin_trgm_ops']
    ),
),
```

`pg_trgm` 확장과 GIN 인덱스 조합이다. `TrigramExtension()`으로 확장을 먼저 활성화하고 인덱스를 생성한다.

```python
operations = [
    TrigramExtension(),  # CREATE EXTENSION IF NOT EXISTS pg_trgm
    migrations.AddIndex(...),
]
```

`gin_trgm_ops` opclass가 핵심이다. 이걸 지정해야 `ILIKE` 쿼리가 GIN 인덱스를 탄다. 일반 GIN 인덱스(`gin_ops`)로는 부분 문자열 검색에 효과가 없다.

B-tree 인덱스를 먼저 제거하고 GIN으로 교체했다. 두 인덱스를 같은 컬럼에 유지하면 쓰기 오버헤드만 늘어난다.

## CI/DI 중복 가입 방지

NICE 콜백에서 받은 CI(연계정보)와 DI(중복가입확인정보)로 이미 가입된 사용자인지 확인한다.

```python
# CI/DI 중복 체크 — 이미 가입된 경우 업데이트 없이 그대로 반환
existing = User.objects.filter(nice_id_ci=ci).first()
if existing:
    # update_or_create 대신 get만 수행 (개인정보 변경 방지)
    return existing
```

`update_or_create`를 쓰면 기존 사용자 정보가 덮어써질 수 있다. CI로 이미 가입된 사용자가 있으면 업데이트 없이 그대로 반환한다.
