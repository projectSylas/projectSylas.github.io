---
layout: post
title: TypeScript + GraphQL로 B2B 경비 정산 서비스 백엔드 설계하기
subtitle: graphql-yoga + TypeORM + MySQL + AWS EB — 실시간 정산 API와 모바일 앱 연동
author: HyeongJin
date: 2021-05-27 10:00:00 +0900
categories: Backend
tags: [TypeScript, GraphQL, Node.js, backend, AWS]
sidebar: []
published: true
---

B2B 경비 정산 서비스의 백엔드와 모바일 앱을 풀스택으로 개발했다. 기업의 임직원 경비 신청 → 승인 → 정산 흐름을 처리하는 서비스다.

백엔드는 Node.js + TypeScript, API 레이어는 GraphQL(`graphql-yoga`), 데이터는 TypeORM + MySQL로 구성했다. 모바일 앱은 React Native + Expo + Apollo Client.

## 왜 GraphQL인가

REST API로 설계했다면 경비 신청 상세, 프로젝트 정보, 승인자 목록을 각각 다른 엔드포인트로 호출해야 했다. GraphQL로 클라이언트가 필요한 데이터를 한 쿼리로 가져오게 설계했다.

```typescript
// 경비 신청 스키마 예시
type Expense {
  id: ID!
  title: String!
  amount: Int!
  status: ExpenseStatus!
  project: Project
  applicant: User!
  approver: User
  evidence: [Evidence!]
  createdAt: String!
}

enum ExpenseStatus {
  PENDING
  APPROVED
  REJECTED
  SETTLED
}

type Query {
  expense(id: ID!): Expense
  myExpenses(status: ExpenseStatus): [Expense!]!
}

type Mutation {
  createExpense(input: CreateExpenseInput!): Expense!
  approveExpense(id: ID!): Expense!
  rejectExpense(id: ID!, reason: String!): Expense!
}
```

클라이언트는 필요한 필드만 요청한다. 앱 화면마다 필요한 데이터가 달라도 별도 엔드포인트 없이 쿼리만 바꾸면 된다.

## TypeORM 엔티티 설계

도메인은 `Project → Expense → Evidence` 계층이다. 프로젝트에 예산이 배정되고, 경비는 프로젝트에 묶이고, 영수증(Evidence)이 경비에 첨부된다.

```typescript
@Entity()
export class Expense {
  @PrimaryGeneratedColumn()
  id: number;

  @Column()
  title: string;

  @Column({ type: 'int' })
  amount: number;

  @Column({
    type: 'enum',
    enum: ExpenseStatus,
    default: ExpenseStatus.PENDING,
  })
  status: ExpenseStatus;

  @ManyToOne(() => Project, project => project.expenses)
  project: Project;

  @ManyToOne(() => User)
  applicant: User;

  @ManyToOne(() => User, { nullable: true })
  approver: User;

  @OneToMany(() => Evidence, evidence => evidence.expense, { cascade: true })
  evidence: Evidence[];

  @CreateDateColumn()
  createdAt: Date;
}
```

홈택스 카드 내역 자동 연동도 있었다. `CardLog`와 `BankAccountLog` 엔티티를 별도로 두고, 카드사 API를 통해 주기적으로 긁어와서 경비 신청과 매핑하는 구조.

## JWT 인증 + Passport.js

Passport.js 로컬 전략(아이디/비밀번호)으로 로그인 후 JWT를 발급했다. GraphQL context에서 JWT를 파싱해 사용자 정보를 주입했다.

```typescript
// GraphQL context 설정
const server = createServer({
  schema,
  context: async ({ req }) => {
    const token = req.headers.authorization?.replace('Bearer ', '');
    let user = null;
    if (token) {
      try {
        const payload = jwt.verify(token, process.env.JWT_SECRET!);
        user = await userRepository.findOne({ id: (payload as any).userId });
      } catch {}
    }
    return { user };
  },
});
```

Resolver에서 `context.user`로 인증 여부를 확인하고, 권한이 없으면 에러를 던진다.

## AWS Elastic Beanstalk + Docker 배포

Docker 이미지를 빌드해서 AWS EB(Elastic Beanstalk)에 배포했다. CircleCI로 main 브랜치 push 시 자동 빌드 → 배포 파이프라인을 구성했다.

```yaml
# .circleci/config.yml
version: 2.1
jobs:
  build-and-deploy:
    docker:
      - image: cimg/node:18.0
    steps:
      - checkout
      - run: npm ci
      - run: npm run build
      - run: docker build -t conplus-api .
      - run: eb deploy
```

파일 첨부(영수증 이미지)는 AWS S3에 직접 업로드하고, DB에는 S3 URL만 저장했다.

## 모바일 앱 — React Native + Apollo Client

앱 쪽은 React Native + Expo로 구성했다. 상태 관리는 Redux + Redux-Saga, API 통신은 Apollo Client(GraphQL).

```javascript
// 경비 목록 쿼리
const MY_EXPENSES = gql`
  query MyExpenses($status: ExpenseStatus) {
    myExpenses(status: $status) {
      id
      title
      amount
      status
      project {
        name
      }
      createdAt
    }
  }
`;

function ExpenseListScreen() {
  const { data, loading } = useQuery(MY_EXPENSES, {
    variables: { status: 'PENDING' },
  });

  return (
    <FlatList
      data={data?.myExpenses}
      renderItem={({ item }) => <ExpenseCard expense={item} />}
    />
  );
}
```

Apollo Client의 캐시 덕분에 같은 쿼리를 중복 요청하지 않았다. 승인/거절 Mutation 후 `refetchQueries`로 목록을 갱신했다.

Firebase Cloud Messaging으로 경비 승인/거절 시 앱 푸시 알림을 보냈다. 신청자가 바로 결과를 알 수 있게 했다.

## 경비 정산 서비스에서 배운 것

GraphQL은 스키마가 API 문서 역할을 겸한다. 프론트와 백이 스키마를 먼저 합의하고 병렬로 개발하는 방식이 REST보다 협업에 더 유리했다.

TypeORM의 관계 매핑(`@OneToMany`, `@ManyToOne`)을 쓰면 JOIN 쿼리를 직접 작성하지 않아도 돼서 개발 속도는 빠르지만, N+1 쿼리 문제가 생기기 쉽다. DataLoader로 배치 처리를 적용해서 쿼리 수를 줄였다.
