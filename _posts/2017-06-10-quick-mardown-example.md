---
layout: post
title: Quick markdown example
subtitle: This is a quick markdown example
categories: markdown
tags: [example]
published: true
---

문단은 빈 줄로 구분됩니다.

두 번째 단락 *Italic*, **bold**, 'monospace' 항목별 목록
다음과 같이 보입니다:

* 이거
* 저놈
* 다른 한 사람

별표를 고려하지 않음 --- 실제 텍스트
콘텐츠는 4-columns부터 시작합니다.

> 블록 견적은
>그렇게 쓴.
>
>여러 단락에 걸쳐 있을 수 있고,
>원하신다면.

전자 대시에는 3개의 대시를 사용하고 범위에는 2개의 대시를 사용합니다(예: "모든 것이 다입니다
12-14장에서) 점 세 개가 타원으로 바뀝니다.
유니코드가 지원됩니다. ☺



Anh2 헤더
------------

번호가 매겨진 목록은 다음과 같습니다:

1. 제1항목
2. 제2호
3. 세번째 항목

실제 텍스트가 어떻게 4개의 열(4자)에서 시작되는지 다시 기록합니다
왼쪽부터). 여기 코드 샘플이 있습니다:

# 다시 한 번 말씀드리겠습니다...
i in 1 … 10 { do-something(i)}에 대하여

아마 짐작하시겠지만, 4칸을 압인했습니다. 그런데, 대신에
블록을 들여쓰면 구분된 블록을 사용할 수 있습니다:

~~~
푸바 () {를 정의합니다
"맛나라에 오신 것을 환영합니다!"라고 인쇄합니다;
}
~~~

복사 및 붙여넣기가 더 쉬워집니다. 선택적으로 표시할 수 있습니다
Pandoc에서 구문을 강조하기 위한 구분 블록:

~~ python
수입시간
# 빨리, 10까지 카운트!
i 범위(10)인 경우:
# (그러나 *너무 빠르지는 않습니다)
time.sleep(0.5)
인쇄(i)
~~~



### Anh3 헤더 ###

이제 중첩 목록:

1. 먼저, 다음 재료를 얻습니다:

* 당근
* 샐러리
* 렌틸콩

2. 물을 좀 끓이세요.

3. 모든 것을 냄비에 버리고 따라갑니다
이 알고리즘:

나무 숟가락을 찾다
냄비를 따다
저어요
뚜껑 냄비
나무 숟가락을 냄비 손잡이에 조심스럽게 균형을 잡습니다
10분 기다리다
첫걸음으로 나아가다(또는 끝나면 버너를 차단합니다)

나무 숟가락을 부딪치지 마십시오. 그렇지 않으면 떨어질 것입니다.

텍스트가 항상 4-스페이스 인텐트에 줄을 서는 방법을 다시 확인하십시오(포함)
위 항목 3)을 계속하는 마지막 줄.

다음은 [a 웹사이트](http://foo.bar ), [local](로컬)에 대한 링크입니다
doc](local-doc.html) 및 현재의 [섹션 방향]으로 이동합니다
doc](#an-h2-header). 여기 각주[^1]가 있습니다.

[^1] : 일부 각주 텍스트.

테이블은 다음과 같이 보일 수 있습니다:

이름 크기 재료 색상
------------- ----- ------------ ------------
올비즈니스9 가죽브라운
약 10개의 대마 캔버스 내추럴
신데렐라 11잔 투명

표: 신발 사이즈, 재질, 색상.

(위는 표의 캡션입니다.) Pandoc도 지원합니다
다중 줄 표:

-------- -----------------------
키워드 텍스트
-------- -----------------------
붉은 노을, 사과
그 밖의 빨강 또는 빨강
형편.

초록잎, 풀, 개구리
그리고 다른 것들은
호락호락한 존재.
-------- -----------------------

수평적인 규칙이 뒤따릅니다.

***

다음은 정의 목록입니다:

사과
: 사과 소스 만들기에 좋습니다.

오렌지
: 시트러스!

토마토
: 토마토에는 'e'가 없습니다.

역시 텍스트는 들여쓰기 4칸입니다.

Paragraphs are separated by a blank line.

2nd paragraph. *Italic*, **bold**, and `monospace`. Itemized lists
look like:

  * this one
  * that one
  * the other one

Note that --- not considering the asterisk --- the actual text
content starts at 4-columns in.

> Block quotes are
> written like so.
>
> They can span multiple paragraphs,
> if you like.

Use 3 dashes for an em-dash. Use 2 dashes for ranges (ex., "it's all
in chapters 12--14"). Three dots ... will be converted to an ellipsis.
Unicode is supported. ☺



An h2 header
------------

Here's a numbered list:

 1. first item
 2. second item
 3. third item

Note again how the actual text starts at 4 columns in (4 characters
from the left side). Here's a code sample:

    # Let me re-iterate ...
    for i in 1 .. 10 { do-something(i) }

As you probably guessed, indented 4 spaces. By the way, instead of
indenting the block, you can use delimited blocks, if you like:

~~~
define foobar() {
    print "Welcome to flavor country!";
}
~~~

(which makes copying & pasting easier). You can optionally mark the
delimited block for Pandoc to syntax highlight it:

~~~python
import time
# Quick, count to ten!
for i in range(10):
    # (but not *too* quick)
    time.sleep(0.5)
    print(i)
~~~



### An h3 header ###

Now a nested list:

 1. First, get these ingredients:

      * carrots
      * celery
      * lentils

 2. Boil some water.

 3. Dump everything in the pot and follow
    this algorithm:

        find wooden spoon
        uncover pot
        stir
        cover pot
        balance wooden spoon precariously on pot handle
        wait 10 minutes
        goto first step (or shut off burner when done)

    Do not bump wooden spoon or it will fall.

Notice again how text always lines up on 4-space indents (including
that last line which continues item 3 above).

Here's a link to [a website](http://foo.bar), to a [local
doc](local-doc.html), and to a [section heading in the current
doc](#an-h2-header). Here's a footnote [^1].

[^1]: Some footnote text.

Tables can look like this:

Name           Size  Material      Color
------------- -----  ------------  ------------
All Business      9  leather       brown
Roundabout       10  hemp canvas   natural
Cinderella       11  glass         transparent

Table: Shoes sizes, materials, and colors.

(The above is the caption for the table.) Pandoc also supports
multi-line tables:

--------  -----------------------
Keyword   Text
--------  -----------------------
red       Sunsets, apples, and
          other red or reddish
          things.

green     Leaves, grass, frogs
          and other things it's
          not easy being.
--------  -----------------------

A horizontal rule follows.

***

Here's a definition list:

apples
  : Good for making applesauce.

oranges
  : Citrus!

tomatoes
  : There's no "e" in tomatoe.

Again, text is indented 4 spaces. (Put a blank line between each
term and  its definition to spread things out more.)

Here's a "line block" (note how whitespace is honored):

| Line one
|   Line too
| Line tree

and images can be specified like so:

![example image](https://user-images.githubusercontent.com/9413601/123900693-1d9ebd00-d99c-11eb-8e9e-cf7879187606.png "An exemplary image")

Inline math equation: $\omega = d\phi / dt$. Display
math should get its own line like so:

$$I = \int \rho R^{2} dV$$

And note that you can backslash-escape any punctuation characters
which you wish to be displayed literally, ex.: \`foo\`, \*bar\*, etc.
