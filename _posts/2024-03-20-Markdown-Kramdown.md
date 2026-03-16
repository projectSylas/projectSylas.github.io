---
layout: post
title: Markdown & Kramdown 문법
subtitle: 기술블로그를 개발하며 Markdown 문법의 공부가 필요하여, 해당 내용들을 정리한 문서입니다.
author: HyeongJin
categories: Markdown
tag: [Markdown, Kramdown]
sidebar: []
published: true
---

> ## Markdown 문법

## 폰트 크기, 굵기, 기울기

## H1
### H2
#### H3
##### H4
###### H5


{% highlight markdown%}
## H1
### H2
#### H3
##### H4
###### H5
{% endhighlight %}

*기울여서*

**굵게**

***강조하고 기울여서***

**혼용하여 _기울여서_ 사용할 수 있습니다**

{% highlight markdown%}
*기울여서*
**굵게**
***강조하고 기울여서***
**혼용하여 _기울여서_ 사용할 수 있습니다**
{% endhighlight %}


## 단락 나누기


-

--

---

{% highlight markdown%}
-
--
---
{% endhighlight %}

>BlockQuote
>>Second line
>>>Third line
>>>>Fourth line
>>>>>Fifth line

{% highlight markdown%}
>BlockQuote
>>Second line
>>>Third line
>>>>Fourth line
>>>>>Fifth line
{% endhighlight %}

## 리스트

### Ordered list

1. Item 1
2. A second item
3. Number 3
4. Ⅳ

* 참고: 네 번째 항목에는 유니코드 문자를 사용합니다.

~~~
1. Item 1
2. A second item
3. Number 3
4. Ⅳ

* 참고: 네 번째 항목에는 유니코드 문자를 사용합니다.
~~~

## 테이블

### Rowspan and Colspan
`^^` 셀은 위의 셀과 병합되어야 함을 나타냅니다.
이 기능은 [pmcloghrylaing](https://github.com/pmccloghrylaing) 에서 제공합니다.

| Stage | Direct Products | ATP Yields |
| ----: | --------------: | ---------: |
|Glycolysis | 2 ATP                   ||
|^^         | 2 NADH      | 3--5 ATP   |
|Pyruvaye oxidation | 2 NADH | 5 ATP   |
|Citric acid cycle  | 2 ATP           ||
|^^                 | 6 NADH | 15 ATP  |
|^^                 | 2 FADH | 3 ATP   |
| 30--32 ATP                         |||

```
| Stage | Direct Products | ATP Yields |
| ----: | --------------: | ---------: |
|Glycolysis | 2 ATP                   ||
|^^         | 2 NADH      | 3--5 ATP   |
|Pyruvaye oxidation | 2 NADH | 5 ATP   |
|Citric acid cycle  | 2 ATP           ||
|^^                 | 6 NADH | 15 ATP  |
|^^                 | 2 FADH | 3 ATP   |
| 30--32 ATP                         |||
```



### Multiline
다음 행으로 셀 내용을 연결하기 위해 끝에 있는 백슬래시입니다.
이 기능은 [Lucas-C](https://github.com/Lucas-C) 에서 제공합니다

|:     Easy Multiline     :|||
|:------ |:------ |:-------- |
| Apple  | Banana |  Orange  \
| Apple  | Banana |  Orange  \
| Apple  | Banana |  Orange
| Apple  | Banana |  Orange  \
| Apple  | Banana |  Orange  |
| Apple  | Banana |  Orange  |

```
|:     Easy Multiline     :|||
|:------ |:------ |:-------- |
| Apple  | Banana |  Orange  \
| Apple  | Banana |  Orange  \
| Apple  | Banana |  Orange
| Apple  | Banana |  Orange  \
| Apple  | Banana |  Orange  |
| Apple  | Banana |  Orange  |
```


### Headerless
테이블 헤더를 제거할 수 있습니다.

|--|--|--|--|--|--|--|--|
|♜ |  |♝ |♛ |♚ |♝ |♞ |♜ |
|  |♟ |♟ |♟ |  |♟ |♟ |♟ |
|♟ |  |♞ |  |  |  |  |  |
|  |♗ |  |  |♟ |  |  |  |
|  |  |  |  |♙ |  |  |  |
|  |  |  |  |  |♘ |  |  |
|♙ |♙ |♙ |♙ |  |♙ |♙ |♙ |
|♖ |♘ |♗ |♕ |♔ |  |  |♖ |


```markdown
|:     Fruits \|\| Food           :|||
|:-------- |:-------- |:------------ |
| Apple    |: Apple  :|    Apple     \
| Banana   |  Banana  |    Banana    \
| Orange   |  Orange  |    Orange    |
|:   Rowspan is 5   :||:  How's it? :|
|^^   A. Peach       ||^^ 1. Fine    |
|^^   B. Orange      ||^^ 2. Bad  $I = \int \rho R^{2} dV$     |
|^^   C. Banana      ||   It's OK! ![example image][my-image]  |
```

> ## Kramdown 문법

## 이미지


![Crepe](https://velog.velcdn.com/images/bluewind8791/post/ae5626e4-25ac-4948-b0bd-384a2da4f0e2/image.png)

```
![Crepe](https://velog.velcdn.com/images/bluewind8791/post/ae5626e4-25ac-4948-b0bd-384a2da4f0e2/image.png)
```

Kramdown에서는 이미지 사이즈 조절기능이 있습니다.

![mdlogo1](https://velog.velcdn.com/images/bluewind8791/post/ae5626e4-25ac-4948-b0bd-384a2da4f0e2/image.png){:height="30px" width="30px"}

```
![mdlogo1](https://velog.velcdn.com/images/bluewind8791/post/ae5626e4-25ac-4948-b0bd-384a2da4f0e2/image.png){:height="30px" width="30px"}
```

중앙 배치

![Crepe](https://velog.velcdn.com/images/bluewind8791/post/ae5626e4-25ac-4948-b0bd-384a2da4f0e2/image.png){: .center-block height="300px" width="300px" :}

```
![Crepe](https://velog.velcdn.com/images/bluewind8791/post/ae5626e4-25ac-4948-b0bd-384a2da4f0e2/image.png){: .center-block :}
```

## 인라인 코드블럭

중간중간에 코드블럭을 삽입할 수 있습니다.
코드블럭 안에 `백틱`을 넣어보세요
kramdown에는 랭귀지 코드블럭도 넣을 수 있습니다. System.out.println("java");{:.language-java}

```
중간중간에 코드블럭을 삽입할 수 있습니다.
코드블럭 안에 `백틱`을 넣어보세요
kramdown에는 랭귀지 코드블럭도 넣을 수 있습니다. System.out.println("java");{:.language-java}
```
## 약어

약어에 대한 설명을 할 수 있는 기능이 있습니다.

*[약어]: 단어 설명
```
약어에 대한 설명을 할 수 있는 기능이 있습니다.

*[약어]: 단어 설명
```

## 각주

각주[^1]를 사용할 수 있습니다.

[^1]: 각주에 대한 설명 내용 부분 (문서 최하단)


```
각주[^1]를 사용할 수 있습니다.

[^1]: 각주에 대한 설명 내용 부분 (문서 최하단)
```