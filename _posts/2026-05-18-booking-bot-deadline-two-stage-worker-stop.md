---
layout: post
title: 예약 봇 마감 감지 — 2단계 체크와 워커 독립 종료 패턴
subtitle: 리스트 disabled → 상세 full_day CSS → Alert, 성공한 워커만 종료하고 나머지는 계속 돌리기
author: HyeongJin
date: 2026-05-18 10:00:00 +0900
categories: Backend
tags: [Python, Selenium, automation, threading]
sidebar: []
published: true
---

방탈출 예약 봇을 만들면서 마감 감지가 생각보다 까다로웠다. 마감 여부를 확인할 수 있는 지점이 세 군데인데, 각 지점에서 다르게 표현된다. 그리고 병렬 워커 중 하나가 예약에 성공했을 때 나머지를 어떻게 처리할지도 고민이 필요했다.

이전에 작성한 [Playwright 예약 봇]({% post_url 2025-11-07-playwright-parallel-booking-automation %})과 [Selenium + multiprocessing 버전]({% post_url 2026-04-02-selenium-multiprocessing-booking-bot %})에서 다루지 않은 마감 감지 로직과 워커 종료 패턴만 정리한다.

## 마감을 잡는 세 지점

예약 사이트에서 마감 여부는 세 단계에서 다른 방식으로 표현된다.

### 1) 리스트 페이지 — disabled 버튼

Shadow DOM 안의 예약 버튼에 `disabled` 속성이 붙으면 마감이다.

```python
widget = driver.find_element(By.TAG_NAME, "booking-widget")
shadow = driver.execute_script('return arguments[0].shadowRoot', widget)

ps = shadow.find_elements(By.CSS_SELECTOR, "p.jiku6p0._9rkf6m0")
for p in ps:
    if p.text == f"{theme} / {target_time}":
        parent = p.find_element(By.XPATH, './ancestor::div[contains(@class, "_9rkf6m1")]')
        btn = parent.find_element(By.CSS_SELECTOR, 'button._9rkf6m3')

        if btn.get_attribute('disabled'):
            # 리스트 단계에서 마감 확인
            continue
```

리스트에서 disabled가 아니면 상세 페이지로 진입한다. 하지만 리스트와 상세 사이에 시간차가 있어서 상세에서도 다시 확인해야 한다.

### 2) 상세 페이지 — full_day CSS 클래스

상세 페이지 캘린더의 `td` 태그에 `full_day` 클래스가 붙으면 해당 날짜 전체가 마감이다.

```python
# "2025-11-13" → "2025-11-13" (월/일 앞 0 제거)
d_parts = target_date.split('-')
search_date = f"{d_parts[0]}-{d_parts[1].lstrip('0')}-{d_parts[2].lstrip('0')}"

cell = driver.find_element(By.XPATH, f"//td[@data-date='{search_date}']")
cls = cell.get_attribute('class')

if 'full_day' in cls:
    continue  # 딜레이 없이 즉시 새로고침
```

날짜 형식 변환에 주의해야 한다. `data-date` 속성은 `"2025-11-3"` 형식(월/일 앞 0 없음)인데, Python datetime에서 나온 `"2025-11-03"` 그대로 넣으면 요소를 못 찾는다. `lstrip('0')`으로 맞춰준다.

`full_day`가 없으면 예약 가능 날짜다. 날짜를 클릭하고 예약하기 버튼을 누른다.

### 3) 비회원 예약 진입 후 — Alert

날짜가 열려 있어도 특정 시간대가 마감인 경우 비회원 예약 버튼 클릭 직후 Alert가 뜬다.

```python
guest_btn = wait.until(EC.presence_of_element_located(
    (By.XPATH, "//a[contains(@class, '_guest_payment')]")
))
driver.execute_script("arguments[0].click();", guest_btn)
time.sleep(1.2)  # Alert 대기

try:
    alert = driver.switch_to.alert
    alert.accept()
    continue  # Alert 있으면 시간대 마감 → 새로고침
except:
    pass  # Alert 없으면 예약 가능 → 결제 진행
```

Alert가 없으면 결제 페이지로 넘어간 것이므로 결제 폼을 채운다.

## 워커 독립 종료 패턴

여러 시간대를 동시에 시도할 때, 한 워커가 성공하면 나머지를 어떻게 처리할지가 문제다.

`stop_event.set()`으로 전체를 끄면 간단하지만 문제가 있다. 예를 들어 두 시간대를 동시에 시도할 때 하나가 성공해도 다른 시간대는 계속 시도해서 두 개를 잡고 싶을 수 있다.

대신 **성공한 워커만 스스로 종료**하고 나머지는 계속 돌리는 구조로 만들었다.

```python
class ParallelBookingWorker:
    def __init__(self, ...):
        self.keep_browser = False  # 성공 시 브라우저 유지 플래그

    def run(self):
        while not self.stop_event.is_set():
            # ... 예약 시도 ...

            if 예약_성공:
                self.success_queue.put({...})
                self.keep_browser = True
                break  # 이 워커만 루프 탈출

        finally:
            if driver and not self.keep_browser:
                driver.quit()  # 실패한 워커는 브라우저 종료
            # 성공한 워커는 브라우저 유지 (결과 확인 가능)
```

성공한 워커는 `keep_browser = True`로 브라우저를 남겨두고 루프에서만 빠져나온다. `stop_event`는 건드리지 않으므로 다른 워커는 계속 동작한다.

메인 스레드는 `success_queue`를 감시하며 성공 결과를 출력한다.

```python
def run_parallel_booking(target_date, bookings, ...):
    success_queue = Queue()
    stop_event = Event()
    threads = []

    for idx, (theme, time_slot, name, phone) in enumerate(bookings):
        worker = ParallelBookingWorker(
            worker_id=idx,
            theme=theme,
            target_date=target_date,
            target_time=time_slot,
            ...
            success_queue=success_queue,
            stop_event=stop_event,
        )
        t = Thread(target=worker.run)
        t.daemon = True
        threads.append(t)
        t.start()
        time.sleep(0.5)  # 브라우저 순차 초기화

    while True:
        while not success_queue.empty():
            result = success_queue.get()
            print(f"✅ 예약 성공: {result['theme']} / {result['time']} ({result['attempts']}회 시도)")

        if all(not t.is_alive() for t in threads):
            break

        time.sleep(0.3)
```

## 최초 진입 시 새로고침 스킵

리스트에서 상세 페이지로 처음 진입한 직후에는 페이지가 이미 로드되어 있다. 바로 `driver.get(detail_url)`로 새로고침하면 불필요한 왕복이 생긴다.

`first_entry` 플래그로 최초 진입 시 새로고침을 건너뛴다.

```python
first_entry = True

while not self.stop_event.is_set():
    if not detail_url:
        # 리스트에서 상세 진입
        ...
        first_entry = True

    if not first_entry:
        driver.get(detail_url)
        time.sleep(1.0)
    else:
        time.sleep(1.0)  # 페이지 안정화만
        first_entry = False

    # 마감 체크 및 예약 진행
    ...
```
