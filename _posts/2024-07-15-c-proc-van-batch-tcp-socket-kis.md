---
layout: post
title: C(Pro C)로 VAN 대용량 배치 처리와 TCP 소켓 프로세스 개발하기
subtitle: KIS정보통신 핀테크 배치 — 하루 1,000만 건 거래내역과 원장 동기화 TCP 소켓
author: HyeongJin
date: 2024-07-15 10:00:00 +0900
categories: Backend
tags: [C, Python, Oracle, Linux, backend]
sidebar: []
published: true
---

KIS정보통신은 VAN(Value Added Network) 사업자다. 카드 결제 승인 데이터가 가맹점 → VAN → 카드사 → 정산 흐름으로 이동하는데, 이 중간 처리를 담당한다.

여기서 하루 1,000만 건이 넘는 거래내역 배치 프로세스와, 실시간 승인서버와 정보계(Oracle) 간 원장 동기화 TCP 소켓 프로세스를 맡았다.

## 기술 스택이 C(Pro C)인 이유

금융권 레거시 시스템은 아직 C 기반이 많다. 특히 AIX(IBM Unix), Linux 서버에서 Oracle DB와 직접 통신하는 Pro C(Oracle Precompiler for C)를 쓴다.

Pro C는 C 코드 안에 SQL을 직접 작성하는 방식이다.

```c
/* Pro C 예시 — 거래내역 조회 */
EXEC SQL BEGIN DECLARE SECTION;
    char tran_date[9];
    char van_code[4];
    int  tran_count;
    char approval_no[13];
EXEC SQL END DECLARE SECTION;

EXEC SQL CONNECT :db_user IDENTIFIED BY :db_pass AT :db_name;

EXEC SQL SELECT COUNT(*), SUM(amount)
         INTO :tran_count, :total_amount
         FROM tran_history
         WHERE tran_date = :tran_date
         AND   van_code  = :van_code;
```

C 코드에 SQL이 섞여있는 게 낯설지만, 컴파일 시 Oracle precompiler가 SQL 부분을 OCI(Oracle Call Interface) 호출로 변환한다.

## 거래내역 배치 구조

하루 1,000만 건 처리는 단순히 루프를 돌리면 안 된다. 배치를 청크(Chunk) 단위로 나눠서 처리하고, 중간에 실패해도 재시작 가능하도록 체크포인트를 잡아야 한다.

```c
#define CHUNK_SIZE 10000

int process_daily_transactions(char *tran_date) {
    int offset = 0;
    int processed = 0;

    EXEC SQL DECLARE tran_cursor CURSOR FOR
        SELECT tran_id, van_code, amount, approval_no
        FROM   tran_history
        WHERE  tran_date = :tran_date
        ORDER  BY tran_id;

    EXEC SQL OPEN tran_cursor;

    while (1) {
        /* FETCH BULK로 청크 단위로 가져옴 */
        EXEC SQL FETCH tran_cursor
            BULK COLLECT INTO :tran_id_arr, :van_arr,
                               :amount_arr, :approval_arr
            LIMIT :CHUNK_SIZE;

        if (sqlca.sqlerrd[2] == 0) break; /* 더 이상 데이터 없음 */

        int fetched = sqlca.sqlerrd[2];
        for (int i = 0; i < fetched; i++) {
            process_single_transaction(i);
        }

        EXEC SQL COMMIT;  /* 청크마다 커밋 */
        processed += fetched;

        /* 진행 상황 로그 */
        printf("처리: %d건\n", processed);
    }

    EXEC SQL CLOSE tran_cursor;
    return processed;
}
```

`BULK COLLECT`로 청크 단위로 가져오고, 청크마다 COMMIT한다. 중간에 프로세스가 죽어도 COMMIT된 데이터는 보존된다.

## TCP 소켓 — 원장 동기화

실시간 승인서버(Stratus)와 정보계(Oracle) 간 원장 동기화는 TCP 소켓으로 통신한다.

```c
/* TCP 서버 소켓 초기화 */
int init_socket(int port) {
    int sockfd = socket(AF_INET, SOCK_STREAM, 0);

    struct sockaddr_in addr;
    memset(&addr, 0, sizeof(addr));
    addr.sin_family = AF_INET;
    addr.sin_addr.s_addr = INADDR_ANY;
    addr.sin_port = htons(port);

    /* SO_REUSEADDR: 서버 재시작 시 포트 재사용 */
    int opt = 1;
    setsockopt(sockfd, SOL_SOCKET, SO_REUSEADDR, &opt, sizeof(opt));

    bind(sockfd, (struct sockaddr*)&addr, sizeof(addr));
    listen(sockfd, SOMAXCONN);

    return sockfd;
}
```

승인서버에서 원장 변경 이벤트가 발생하면 소켓으로 정보계에 전송하고, 정보계에서 Oracle DB에 반영하는 방식이다.

## Python으로 보조 자동화

순수 C로 처리하기 어려운 부분(타VAN사 스크래핑, DBA 반복 작업)은 Python으로 처리했다.

```python
import cx_Oracle
import requests
from bs4 import BeautifulSoup

def scrape_other_van_transactions(van_code, date):
    """타VAN사 웹에서 거래내역 스크래핑"""
    session = requests.Session()
    # 로그인 및 데이터 수집...
    return transactions

def bulk_insert(conn, transactions):
    cursor = conn.cursor()
    cursor.executemany(
        "INSERT INTO external_van_tran VALUES (:1, :2, :3, :4)",
        transactions
    )
    conn.commit()
```

DBA 업무 자동화도 Python으로 했다. 통계 쿼리를 매일 돌려서 리포트를 뽑거나, 특정 조건의 데이터를 찾아서 정리하는 스크립트들이다.

## 금융권 배치 개발에서 배운 것

C 언어와 Pro C는 현대적인 언어들과 다른 점이 많다. 메모리 관리를 직접 해야 하고, 에러 처리도 `sqlca.sqlcode`를 매번 체크해야 한다.

하지만 가장 인상적이었던 건 시스템의 안정성에 대한 접근 방식이었다. "무조건 죽어도 데이터는 보존돼야 한다"는 원칙이 코드 곳곳에 녹아있었다. COMMIT 시점, 롤백 조건, 재시작 포인트가 모두 명확하게 설계돼 있었다. 이후 다른 시스템을 만들 때도 이 원칙은 기억에 남았다.
