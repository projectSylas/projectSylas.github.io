---
layout: post
title: GCP 인스턴스 IP 교체 + Cloudflare DNS 자동 동기화
subtitle: deleteAccessConfig → addAccessConfig로 외부 IP 순환, Cloudflare PATCH로 A레코드 갱신까지 한 번에
author: HyeongJin
date: 2026-05-14 10:00:00 +0900
categories: DevOps
tags: [Node.js, GCP, Cloudflare, DevOps, automation]
sidebar: []
published: true
---

GCP VM 인스턴스의 외부 IP를 교체하고 Cloudflare DNS A레코드를 자동으로 갱신하는 스크립트가 필요했다. STANDARD tier 인스턴스는 재시작하거나 accessConfig를 교체하면 IP가 바뀐다. 그 순간 DNS와 실제 IP가 틀어지는데, 이걸 수동으로 맞추는 건 번거롭다.

GCP Compute API로 IP를 교체하고, 새 IP가 완전히 할당되면 Cloudflare API로 A레코드를 업데이트하는 흐름으로 구현했다.

## 동작 순서

1. `deleteAccessConfig` — 기존 External NAT 제거 (IP 반납)
2. 10초 대기 (GCP 내부 반영 시간)
3. `addAccessConfig` — 새 External NAT 할당 (새 IP 발급)
4. 새 IP가 응답에 나타날 때까지 polling
5. Cloudflare PATCH로 A레코드 새 IP로 갱신

## 환경 설정

```
GCP_CREDENTIAL=credential.json   # GCP 서비스 계정 키 파일
GCP_PROJECT=my-project
GCP_ZONE=asia-northeast3-a
GCP_INSTANCE=<인스턴스 ID>
CLOUDFLARE_EMAIL=user@example.com
CLOUDFLARE_KEY=<Global API Key>
CLOUDFLARE_TOKEN=<API Token>
CF_ZONE_ID=<Cloudflare Zone ID>
CF_RECORD_ID=<DNS Record ID>
```

## 구현

```javascript
require('dotenv').config()
const { InstancesClient } = require('@google-cloud/compute').v1
const request = require('request')

const credentials = require('./' + process.env.GCP_CREDENTIAL)
const { GCP_PROJECT: project, GCP_ZONE: zone, GCP_INSTANCE: instance } = process.env

const client = new InstancesClient({ credentials })

const accessConfig = 'External NAT'
const networkInterface = 'nic0'
const accessConfigResource = {
  kind: 'compute#accessConfig',
  type: 'ONE_TO_ONE_NAT',
  name: 'External NAT',
  networkTier: 'STANDARD',
}

const delay = ms => new Promise(resolve => setTimeout(resolve, ms))

const main = async () => {
  // 1) 기존 IP 반납
  await client.deleteAccessConfig({ accessConfig, instance, networkInterface, project, zone })
  console.log('accessConfig 삭제 완료')

  await delay(10000)

  // 2) 새 IP 할당
  await client.addAccessConfig({ accessConfigResource, instance, networkInterface, project, zone })
  console.log('accessConfig 추가 완료')

  // 3) 새 IP 확정될 때까지 polling
  let res
  do {
    await delay(1000)
    res = await client.get({ instance, project, zone })
  } while (!res?.[0].networkInterfaces?.[0].accessConfigs?.[0].natIP)

  const newIP = res[0].networkInterfaces[0].accessConfigs[0].natIP
  console.log('새 IP:', newIP)

  // 4) Cloudflare A레코드 업데이트
  const { CLOUDFLARE_EMAIL, CLOUDFLARE_KEY, CLOUDFLARE_TOKEN, CF_ZONE_ID, CF_RECORD_ID } = process.env
  request(
    {
      method: 'PATCH',
      url: `https://api.cloudflare.com/client/v4/zones/${CF_ZONE_ID}/dns_records/${CF_RECORD_ID}`,
      headers: {
        'X-Auth-Email': CLOUDFLARE_EMAIL,
        'X-Auth-Key': CLOUDFLARE_KEY,
        Authorization: `Bearer ${CLOUDFLARE_TOKEN}`,
      },
      json: true,
      body: { content: newIP },
    },
    (err, _, body) => {
      if (err) console.error(err)
      else console.log('DNS 갱신 완료:', body.result?.content)
    }
  )
}

main()
```

## 포인트별 설명

### deleteAccessConfig → addAccessConfig

GCP에서 외부 IP를 교체하는 방법은 두 가지다. Ephemeral IP를 쓰는 경우 accessConfig를 한 번 제거하고 다시 추가하면 새 IP가 발급된다. Static IP를 할당한 상태라면 IP가 바뀌지 않으므로 이 방법은 Ephemeral IP 전용이다.

두 API 호출 사이에 10초 delay를 넣은 이유는 GCP 내부에서 이전 accessConfig가 완전히 해제되기 전에 새 것을 추가하면 409 충돌이 날 수 있기 때문이다.

### IP polling

`addAccessConfig`가 완료됐다고 해서 즉시 natIP가 응답에 들어오지는 않는다. `client.get`을 반복 호출해 natIP 필드가 채워질 때까지 기다린다.

```javascript
do {
  await delay(1000)
  res = await client.get({ instance, project, zone })
} while (!res?.[0].networkInterfaces?.[0].accessConfigs?.[0].natIP)
```

optional chaining(`?.`)으로 중간 필드가 undefined여도 에러 없이 falsy로 평가되도록 한다.

### Cloudflare PATCH

Cloudflare DNS API는 레코드 전체를 교체하는 PUT과 일부 필드만 바꾸는 PATCH를 지원한다. content(IP 주소)만 바꾸면 되므로 PATCH를 쓴다.

Zone ID와 Record ID는 Cloudflare 대시보드 DNS 탭에서 확인할 수 있다.

## 실행

```bash
node index.js
```

10~15초 안에 GCP IP 교체 + DNS 갱신이 완료된다.
