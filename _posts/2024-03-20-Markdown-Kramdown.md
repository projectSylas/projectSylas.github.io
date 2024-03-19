---
layout: post
title: Markdown & Kramdown 문법
subtitle: 
author: HyeongJin
categories: Markdown
tag: [Markdown, Kramdown]
sidebar: []
published: true
---

기술블로그를 개발하며 Markdown 문법의 공부가 필요하여, 해당 내용들을 정리한 문서입니다.

# Markdown 문법

# H1
## H2
### H3
#### H4
##### H5
###### H6

{% highlight markdown%}
# H1
## H2
### H3
#### H4
##### H5
###### H6
{% endhighlight %}


별 하나는 *기울여서*
별 두개는 **굵게**
별 세개는 ***강조하고 기울여서***
이렇게 **혼용하여 _기울여서_ 사용할 수 있다**


## 이미지

![Crepe](https://s3-media3.fl.yelpcdn.com/bphoto/cQ1Yoa75m2yUFFbY2xwuqw/348s.jpg)

It can also be centered!

![Crepe](https://s3-media3.fl.yelpcdn.com/bphoto/cQ1Yoa75m2yUFFbY2xwuqw/348s.jpg){: .center-block :}

Here's a code chunk:
~~~
\         backslash
.         period
*         asterisk
_         underscore
+         plus
-         minus
=         equal sign
`         back tick
()[]{}<>  left and right parens/brackets/braces/angle brackets
#         hash
!         bang
<<        left guillemet
>>        right guillemet
:         colon
|         pipe
"         double quote
'         single quote
$         dollar sign
~~~
~~~
var foo = function(x) {
  return(x + 5);
}
foo(3)
~~~

And here is the same code with syntax highlighting:

```javascript
var foo = function(x) {
  return(x + 5);
}
foo(3)
```

And here is the same code yet again but with line numbers:

{% highlight javascript linenos %}
var foo = function(x) {
  return(x + 5);
}
foo(3)
{% endhighlight %}

## Boxes
You can add notification, warning and error boxes like this:

### Notification

{: .box-note}
**Note:** This is a notification box.

### Warning

{: .box-warning}
**Warning:** This is a warning box.

### Error

{: .box-error}
**Error:** This is an error box.
