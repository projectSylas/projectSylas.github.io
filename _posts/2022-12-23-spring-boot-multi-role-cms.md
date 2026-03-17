---
layout: post
title: Spring Boot 다중 역할 CMS 설계 — Admin/Inspector/Worker 인증 분리
subtitle: Spring Security 다중 Provider + JPA + MyBatis + AWS S3 파일 업로드
author: HyeongJin
date: 2022-12-23 10:00:00 +0900
categories: Backend
tags: [Java, Spring, backend, AWS]
sidebar: []
published: true
---

서울애널리티카에서 촬영 스태프 프로젝트 관리 CMS를 맡았다. 관리자(Admin), 감리(Inspector), 작업자(Worker) 세 가지 역할이 있고, 역할마다 접근 가능한 메뉴와 기능이 달랐다.

Spring Boot + Spring Security로 역할별 인증을 분리하고, JPA(Repository) + MyBatis(Mapper) 혼용 구조로 설계했다.

## 다중 역할 인증 구조

세 역할이 같은 로그인 폼을 쓰지만, 인증 처리는 각각 별도 Provider를 쓴다.

```
HTTP 요청
    ↓
CustomAuthenticationFilter (공통 필터)
    ↓
AuthenticationManager
    ├── AdminAuthenticationProvider  → AdminSecurityService
    ├── InspectorAuthenticationProvider → InspectorSecurityService
    └── WorkerAuthenticationProvider → WorkerSecurityService
```

```java
@Configuration
@EnableWebSecurity
@RequiredArgsConstructor
public class SecurityConfiguration extends WebSecurityConfigurerAdapter {

    private final AdminSecurityService adminSecurityService;
    private final InspectorSecurityService inspectorSecurityService;
    private final WorkerSecurityService workerSecurityService;

    @Override
    protected void configure(AuthenticationManagerBuilder auth) throws Exception {
        // 세 가지 Provider 등록
        auth.authenticationProvider(adminAuthProvider())
            .authenticationProvider(inspectorAuthProvider())
            .authenticationProvider(workerAuthProvider());
    }

    @Bean
    public CustomAuthenticationProvider adminAuthProvider() {
        CustomAuthenticationProvider provider = new CustomAuthenticationProvider();
        provider.setUserDetailsService(adminSecurityService);
        provider.setPasswordEncoder(new BCryptPasswordEncoder());
        return provider;
    }
    // Inspector, Worker도 동일 구조
}
```

AuthenticationProvider는 `UserDetailsService` 구현체를 주입받는다. 로그인 시 각 Provider가 순서대로 시도하고, 성공하면 해당 역할의 Principal이 SecurityContext에 저장된다.

## 역할 기반 URL 접근 제어

```java
@Override
protected void configure(HttpSecurity http) throws Exception {
    http
        .authorizeRequests()
            .antMatchers("/cms/admin/**").hasRole("ADMIN")
            .antMatchers("/cms/inspect/**").hasAnyRole("ADMIN", "INSPECTOR")
            .antMatchers("/cms/srook/**").hasAnyRole("ADMIN", "WORKER")
            .antMatchers("/cms/**").authenticated()
            .anyRequest().permitAll()
        .and()
        .formLogin()
            .loginPage("/login")
            .successHandler(new CustomLoginSuccessHandler())
            .failureHandler(new CustomLoginFailureHandler());
}
```

로그인 성공 핸들러에서 역할을 확인하고 역할별 첫 페이지로 리다이렉트한다.

## JPA + MyBatis 혼용

단순 CRUD는 JPA Repository로 처리하고, 복잡한 집계 쿼리나 동적 쿼리는 MyBatis Mapper로 처리했다.

```java
// JPA Repository — 단순 조회
public interface ProjectRepository extends JpaRepository<Project, Long> {
    List<Project> findByGroupAndStatus(Group group, String status);
}

// MyBatis Mapper — 복잡한 집계
@Mapper
public interface DashBoardMapper {
    int countProjectByStatus(DataMap param);
    List<DataMap> listProjectSummary(DataMap param);
}
```

JPA만 쓰면 복잡한 JOIN + 집계가 JPQL로 지저분해진다. MyBatis를 같이 쓰면 SQL을 직접 작성할 수 있어서 대시보드 통계 쿼리가 훨씬 깔끔해졌다.

## 프로젝트 → 작업 → 작업자 계층 구조

```java
@Entity
public class Project {
    @Id @GeneratedValue
    private Long projectSeq;

    private String projectName;
    private String status;

    @ManyToOne
    @JoinColumn(name = "group_id")
    private Group group;

    @OneToMany(mappedBy = "project", cascade = CascadeType.ALL)
    private List<Work> works = new ArrayList<>();
}

@Entity
public class Work {
    @Id @GeneratedValue
    private Long workSeq;

    @ManyToOne
    private Project project;

    @OneToMany(mappedBy = "work")
    private List<WorkWorker> workWorkers = new ArrayList<>();

    @OneToMany(mappedBy = "work", cascade = CascadeType.ALL)
    private List<WorkImage> workImages = new ArrayList<>();
}
```

WorkWorker는 Work-Worker 간 다대다 관계 테이블이다.

## AWS S3 파일 업로드

작업 현장 이미지를 AWS S3에 업로드한다. 파일을 DB에 직접 저장하지 않고 S3 URL만 저장하는 구조다.

```java
@Component
public class S3Util {

    @Value("${cloud.aws.s3.bucket}")
    private String bucket;

    @Value("${cloud.aws.region}")
    private String region;

    private AmazonS3 s3Client;

    @PostConstruct
    public void init() {
        // credentials는 환경변수(AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY)에서 읽음
        s3Client = AmazonS3ClientBuilder.standard()
                .withRegion(region)
                .build();
    }

    public String upload(String filePath, String fileId, MultipartFile file) throws IOException {
        String ext = file.getOriginalFilename()
                .substring(file.getOriginalFilename().lastIndexOf("."));
        String key = (filePath + "/" + fileId + ext).replace(File.separatorChar, '/');

        s3Client.putObject(new PutObjectRequest(bucket, key,
                file.getInputStream(), buildMetadata(file)));

        return s3Client.getUrl(bucket, key).toString();
    }
}
```

AWS 자격증명은 코드에 하드코딩하지 않고 환경변수나 IAM Role로 관리해야 한다.

## Docker 배포

```dockerfile
FROM openjdk:17-jdk-slim
WORKDIR /app
COPY build/libs/*.jar app.jar
ENTRYPOINT ["java", "-jar", "app.jar"]
```

```yaml
# docker-compose.yml
services:
  app:
    build: .
    ports:
      - "8080:8080"
    environment:
      - SPRING_DATASOURCE_URL=${DB_URL}
      - SPRING_DATASOURCE_USERNAME=${DB_USER}
      - SPRING_DATASOURCE_PASSWORD=${DB_PASS}
      - AWS_ACCESS_KEY_ID=${AWS_KEY}
      - AWS_SECRET_ACCESS_KEY=${AWS_SECRET}
  db:
    image: mysql:8.0
    environment:
      MYSQL_ROOT_PASSWORD: ${DB_ROOT_PASS}
      MYSQL_DATABASE: ${DB_NAME}
```

## 배운 것

Spring Security 다중 Provider 구조는 처음에 동작 원리가 헷갈렸다. AuthenticationManager가 Provider 목록을 순서대로 시도하면서 `UsernameNotFoundException`이 나면 다음 Provider로 넘기는 방식이다. Provider마다 다른 `UserDetailsService`를 주입해서 역할별로 다른 DB 테이블에서 사용자를 조회하게 했다.

JPA + MyBatis 혼용은 처음에 어색했지만, 실무에서는 복잡한 쿼리를 JPA로 억지로 표현하는 것보다 MyBatis로 SQL 직접 작성하는 게 유지보수에 유리했다.
