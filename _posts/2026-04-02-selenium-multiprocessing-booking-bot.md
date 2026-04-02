---
layout: post
title: Selenium + multiprocessing 예약 봇 — Shadow DOM JS 접근과 프로세스 간 통신
subtitle: asyncio 없이 Process + Queue + Event로 병렬 워커 구현, shadowRoot를 execute_script로 직접 뚫기
author: HyeongJin
date: 2026-04-02 10:00:00 +0900
categories: Backend
tags: [Python, Selenium, automation, multiprocessing]
sidebar: []
published: true
---

이전에 Playwright + asyncio 조합으로 예약 봇을 만들었는데, 같은 로직을 Selenium + multiprocessing으로 다시 구현했다. Playwright가 없는 환경이거나 Selenium으로만 유지해야 하는 상황에서 쓸 수 있다.

구현하면서 Playwright와 달라지는 지점이 세 곳 있었다: Shadow DOM 접근법, 병렬 처리 방식, 봇 감지 우회.

## Shadow DOM: execute_script로 shadowRoot 직접 참조

Playwright는 `pierce/` selector로 Shadow DOM을 관통한다. Selenium에는 그런 게 없다. `find_element`는 Shadow DOM 경계를 넘지 못한다.

대신 `execute_script`로 `shadowRoot`를 직접 꺼낸 뒤 그 안에서 `find_elements`를 호출한다.

```python
booking_widget = driver.find_element(By.TAG_NAME, "booking-widget")
shadow_root = driver.execute_script('return arguments[0].shadowRoot', booking_widget)

all_p_tags = shadow_root.find_elements(By.CSS_SELECTOR, "p.jiku6p0._9rkf6m0")
```

`shadowRoot`는 일반 WebElement처럼 `find_elements`를 지원한다. CSS selector로 내부 요소를 찾으면 된다.

버튼 클릭도 마찬가지다. `click()`이 막히는 경우가 있어서 `execute_script`로 직접 호출한다.

```python
driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", button)
button.click()
```

스크롤 없이 클릭하면 `ElementClickInterceptedException`이 나는 경우가 있어서 `scrollIntoView` 후 클릭하는 패턴을 썼다.

## 봇 감지 우회

Selenium은 기본적으로 `navigator.webdriver`가 `true`로 잡힌다. 이를 숨기기 위해 두 가지를 적용했다.

```python
options.add_argument('--disable-blink-features=AutomationControlled')
options.add_experimental_option("excludeSwitches", ["enable-automation"])
options.add_experimental_option('useAutomationExtension', False)
```

그리고 드라이버 초기화 직후 `webdriver` 프로퍼티를 `undefined`로 덮어쓴다.

```python
driver.execute_script(
    "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
)
```

Chrome 옵션만으로는 일부 사이트에서 여전히 탐지된다. JS로 직접 override하는 게 더 확실하다.

## 2단계 가용성 체크

예약 가능 여부를 두 단계로 나눠서 체크한다.

**1차: 리스트 페이지**에서 슬롯 버튼의 `disabled` 속성 확인.

```python
def is_slot_available_in_list(self):
    booking_widget = self.driver.find_element(By.TAG_NAME, "booking-widget")
    shadow_root = self.driver.execute_script('return arguments[0].shadowRoot', booking_widget)

    all_p_tags = shadow_root.find_elements(By.CSS_SELECTOR, "p.jiku6p0._9rkf6m0")
    target_text = f"{self.theme} / {self.target_time}"

    for p_tag in all_p_tags:
        if p_tag.text == target_text:
            parent_div = p_tag.find_element(
                By.XPATH, './ancestor::div[contains(@class, "_9rkf6m1")]'
            )
            button = parent_div.find_element(By.CSS_SELECTOR, 'button._9rkf6m3')

            if button.get_attribute('disabled'):
                return False, None
            return True, button

    return False, None
```

**2차: 상세 페이지**에서 날짜 셀 클래스 확인. `full_day`면 마감, `_day_item`이면 예약 가능.

```python
def is_date_available_in_detail(self):
    date_parts = self.target_date.split('-')
    search_date = f"{date_parts[0]}-{date_parts[1].lstrip('0')}-{date_parts[2].lstrip('0')}"

    date_cell = self.driver.find_element(
        By.XPATH,
        f"//td[contains(@class, 'booking_day')][@data-date='{search_date}']"
    )
    classes = date_cell.get_attribute('class')

    if 'full_day' in classes:
        return False, None
    if '_day_item' in classes:
        return True, date_cell

    return False, None
```

날짜 포맷 주의: `data-date` 속성이 `2025-11-5` 형태로 제로패딩 없이 들어간다. `lstrip('0')`으로 맞춰줘야 한다.

1차에서 마감이면 상세 페이지 진입 자체를 안 한다. 이미 상세 페이지에 들어간 워커는 리스트 페이지로 돌아가지 않고 상세 페이지에서 새로고침하며 2차 체크만 반복한다.

## multiprocessing으로 병렬 워커

Playwright는 `asyncio.gather`로 코루틴을 동시에 실행한다. Selenium은 비동기가 없으므로 `multiprocessing.Process`로 워커를 별도 프로세스로 띄운다.

각 워커는 시간대 하나를 담당하고, 하나라도 성공하면 `stop_event`로 나머지를 중단시킨다.

```python
from multiprocessing import Event, Process, Queue

success_queue = Queue()
stop_event = Event()

processes = []
for idx, (time_slot, name, phone) in enumerate(BOOKINGS):
    p = Process(
        target=worker_process,
        args=(idx, THEME, TARGET_DATE, time_slot,
              name, phone, PEOPLE_COUNT, TEST_MODE,
              success_queue, stop_event)
    )
    processes.append(p)

for p in processes:
    p.start()
    time.sleep(0.5)  # 브라우저 초기화 시차
```

메인 프로세스는 `success_queue`를 폴링한다.

```python
while True:
    if not success_queue.empty():
        result = success_queue.get()
        stop_event.set()  # 다른 워커 모두 중단
        break

    all_dead = all(not p.is_alive() for p in processes)
    if all_dead:
        break  # 전원 실패

    time.sleep(1)
```

워커 내부에서는 `stop_event.is_set()`을 루프 조건으로 체크한다.

```python
def run(self, success_queue, stop_event):
    self.init_driver()

    while not stop_event.is_set():
        # ... 체크 및 예약 시도

        if self.fast_reserve():
            success_queue.put({...})
            break
```

성공한 워커가 `success_queue`에 결과를 넣으면 메인 프로세스가 `stop_event`를 세트, 나머지 워커가 루프를 탈출한다.

## 정보 입력을 JS로 배치 처리

폼 입력도 빠르게 처리하기 위해 가능한 건 JS로 묶었다.

```python
# 라디오 버튼 선택 — JS로 직접 클릭
self.driver.execute_script(f"""
    document.querySelector('input[name="pay_type"][value="cash"]').click();
""")

# 인원 선택
self.driver.execute_script(f"""
    document.querySelector(
        'input[name="shop_form[f20220203e2dca73e394a6]"][value="{self.people_count}"]'
    ).click();
""")

# 동의 체크박스 일괄 처리
self.driver.execute_script("""
    var agreeCheckboxes = document.querySelectorAll(
        'input[type="checkbox"][data-group="payment"]'
    );
    agreeCheckboxes.forEach(cb => cb.click());
""")
```

`send_keys`를 반복 호출하는 것보다 JS 배치로 묶는 게 훨씬 빠르다. 특히 체크박스가 여러 개일 때 차이가 크다.

## Playwright vs Selenium 선택 기준

| | Playwright | Selenium |
|---|---|---|
| Shadow DOM | `pierce/` selector | `execute_script` → `shadowRoot` |
| 병렬 처리 | `asyncio.gather` | `multiprocessing.Process` |
| 봇 감지 우회 | 기본 설정으로 대부분 처리 | 옵션 + JS override 필요 |
| 속도 | 빠름 | 조금 느림 (프로세스 생성 오버헤드) |
| 설치 | `playwright install` 별도 필요 | ChromeDriver만 있으면 됨 |

Playwright를 쓸 수 있으면 Playwright가 낫다. Selenium을 써야 하는 상황이면 위 패턴으로 충분히 동일한 동작을 구현할 수 있다.
