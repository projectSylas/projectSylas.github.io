---
layout: post
title: Java Spring에서 외부업체 REST API Web Service 코어 설계하기
subtitle: DB 다이렉트 방식을 REST API로 전환하면서 토큰 관리, 로깅, 보안 코어를 구축한 경험
author: HyeongJin
date: 2023-07-20 10:00:00 +0900
categories: Backend
tags: [Java, Spring, backend, API]
sidebar: []
published: true
---

서울애널리티카에서 TG삼보컴퓨터와 외부 파트너사(Amway, Epson, Ilyang Logis 등) 간 데이터 연동을 담당했다. 기존 방식은 파트너사가 TG삼보의 DB에 직접 접근하는 방식이었다.

보안상 문제가 있고 인터페이스 관리가 어려워서 REST API로 전환하는 프로젝트를 맡았다.

## 기존 방식의 문제

- 파트너사 담당자가 DB 접근 계정을 알고 있음
- 어떤 쿼리를 실행하는지 모니터링 불가
- 파트너사별 접근 권한 제어 불가능
- DB 스키마가 바뀌면 파트너사 코드도 전부 바꿔야 함

## API 코어 설계

모든 파트너사 요청이 공통 인터셉터를 거치도록 했다.

```java
@Component
public class ApiAuthInterceptor implements HandlerInterceptor {

    @Override
    public boolean preHandle(HttpServletRequest request,
                             HttpServletResponse response,
                             Object handler) throws Exception {
        String apiKey = request.getHeader("X-Api-Key");
        String clientId = request.getHeader("X-Client-Id");

        if (!tokenService.validate(clientId, apiKey)) {
            response.setStatus(HttpServletResponse.SC_UNAUTHORIZED);
            return false;
        }

        // 요청 로깅
        auditLogService.record(clientId, request.getRequestURI(),
                               request.getMethod());
        return true;
    }
}
```

## 토큰 관리

파트너사별로 API 키를 발급하고 DB에서 관리했다.

```java
@Service
public class TokenService {

    @Autowired
    private PartnerRepository partnerRepo;

    public boolean validate(String clientId, String apiKey) {
        return partnerRepo.findByClientIdAndApiKeyAndActive(
            clientId, apiKey, true
        ).isPresent();
    }

    public String issueToken(String clientId) {
        String apiKey = UUID.randomUUID().toString().replace("-", "");
        // DB에 저장
        partnerRepo.updateApiKey(clientId, apiKey);
        return apiKey;
    }
}
```

## Logging / Audit

어느 파트너사가 언제 어떤 API를 얼마나 호출했는지 전부 기록했다.

```java
@Entity
@Table(name = "api_audit_log")
public class AuditLog {
    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    private String clientId;
    private String endpoint;
    private String method;
    private int statusCode;
    private long responseTime;

    @CreationTimestamp
    private LocalDateTime createdAt;
}
```

응답 시간도 기록해서 특정 파트너사가 API를 너무 자주 호출하는지, 응답이 느려지는지 모니터링할 수 있게 했다.

## 파트너사별 엔드포인트

파트너사마다 필요한 데이터가 달라서 엔드포인트를 분리했다.

```java
@RestController
@RequestMapping("/api/v1/partner")
public class PartnerApiController {

    @GetMapping("/inventory")
    public ResponseEntity<InventoryResponse> getInventory(
            @RequestHeader("X-Client-Id") String clientId,
            @RequestParam String productCode) {
        // 파트너사가 조회할 수 있는 재고 정보만 반환
        return ResponseEntity.ok(inventoryService.getForPartner(
            clientId, productCode
        ));
    }

    @PostMapping("/order")
    public ResponseEntity<OrderResponse> createOrder(
            @RequestHeader("X-Client-Id") String clientId,
            @RequestBody @Valid OrderRequest request) {
        return ResponseEntity.ok(orderService.createFromPartner(
            clientId, request
        ));
    }
}
```

## 결과

DB 다이렉트 방식을 REST API로 전환하고 나서 파트너사별 접근 제어가 명확해졌다. 어떤 데이터를 언제 얼마나 가져가는지 추적이 됐다. DB 스키마가 바뀌어도 API 응답 형태를 유지하면 파트너사 코드는 안 바꿔도 됐다.

Java Spring 프레임워크를 처음 실무에서 제대로 쓴 프로젝트였다. `@Component`, `@Service`, `@Repository` 계층 분리와 인터셉터 패턴을 몸으로 익혔다.
