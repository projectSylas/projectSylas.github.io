---
layout: post
title: Selenium + EasyOCR로 LCK 티켓팅 매크로 만든 후기
subtitle: 인터파크 티켓 좌석 선택 자동화와 CAPTCHA 우회
author: HyeongJin
categories: Python
tags: [Python, backend]
sidebar: []
published: true
---

LCK 플레이오프 티켓을 매번 놓쳤다. 예매 오픈 시간에 맞춰 대기해도 순식간에 매진된다.

자동화 도구를 만들기로 했다.

## 목표

- 인터파크 티켓 자동 로그인
- 원하는 구역 좌석 자동 선택
- 결제 단계까지 자동화

Selenium + Chrome WebDriver 기반.

## 기본 구조

```python
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import time

def driverLogin(id, pw):
    chrome_options = Options()
    chrome_options.add_experimental_option("detach", True)
    driver = webdriver.Chrome(options=chrome_options)
    driver.set_window_size(1000, 1800)

    driver.get('https://ticket.interpark.com/Gate/TPLogin.asp')
    driver.implicitly_wait(2)

    # iframe 안에 로그인 폼이 있음
    driver.switch_to.frame(
        driver.find_element(By.XPATH, "//div[@class='leftLoginBox']/iframe[@title='login']")
    )
    driver.find_element(By.ID, 'userId').send_keys(id)
    driver.find_element(By.ID, 'userPwd').send_keys(pw)
    driver.find_element(By.ID, 'userPwd').send_keys(Keys.ENTER)
    return driver
```

로그인 자체는 어렵지 않았다. 문제는 그 다음부터였다.

## 좌석 선택 - 인터파크 Flash/Canvas

인터파크 좌석 선택 화면이 Flash 기반이었다. Selenium으로 DOM 조작이 불가능.

HTML5 Canvas로 렌더링되는 새 버전도 문제가 비슷했다. Canvas는 픽셀 기반이라 좌석 위치를 좌표로 클릭해야 한다.

```python
import easyocr
import cv2
import numpy as np

reader = easyocr.Reader(['ko', 'en'])

def find_seat_by_ocr(driver, target_section):
    """스크린샷 찍고 OCR로 구역명 위치 찾기"""
    screenshot = driver.get_screenshot_as_png()
    img_array = np.frombuffer(screenshot, dtype=np.uint8)
    img = cv2.imdecode(img_array, cv2.IMREAD_COLOR)

    results = reader.readtext(img)
    for (bbox, text, prob) in results:
        if target_section in text and prob > 0.7:
            # bbox: [[x1,y1], [x2,y1], [x2,y2], [x1,y2]]
            center_x = int((bbox[0][0] + bbox[2][0]) / 2)
            center_y = int((bbox[0][1] + bbox[2][1]) / 2)
            return center_x, center_y
    return None
```

EasyOCR로 스크린샷에서 구역명 텍스트를 인식하고 좌표를 추출해서 클릭.

```python
from selenium.webdriver.common.action_chains import ActionChains

coords = find_seat_by_ocr(driver, "2층 A구역")
if coords:
    canvas = driver.find_element(By.ID, 'seatCanvas')
    canvas_rect = canvas.rect
    action = ActionChains(driver)
    action.move_to_element_with_offset(
        canvas,
        coords[0] - canvas_rect['x'],
        coords[1] - canvas_rect['y']
    ).click().perform()
```

## 생년월일 입력

결제 단계에서 생년월일 입력 창이 뜨는데 이것도 자동화.

```python
def inputBirthDate(driver, birth_date):
    wait = WebDriverWait(driver, 10)
    birth_input = wait.until(
        EC.presence_of_element_located((By.ID, 'birthDate'))
    )
    birth_input.clear()
    birth_input.send_keys(birth_date)  # YYYYMMDD
```

## Flask 웹 UI

터미널에서 실행하기 불편해서 Flask로 간단한 컨트롤 패널을 만들었다.

```python
@app.route('/run_lck', methods=['POST'])
def run_lck():
    script_path = os.path.join(os.path.dirname(__file__) + '/src/', 'lck.py')
    subprocess.run(['python', script_path])
    return '<script>alert("실행 완료"); window.close();</script>'
```

Docker로 묶어서 배포. 노트북 어디서든 웹 브라우저로 접속해서 실행 가능하게.

## 결과와 한계

실제로 티켓을 잡았냐고? 반은 성공했다.

OCR 인식률이 약 85% 수준이었다. 구역명 폰트나 배경색에 따라 인식을 못 하는 케이스가 있었다. 좌석 선택 이후 결제까지 완전 자동화는 되는데, 간헐적으로 인터파크가 레이아웃을 변경하면 셀렉터가 깨졌다.

Playwright로 다시 만든다면 auto-wait 기능 때문에 더 안정적이었을 것 같다. Selenium에서 `implicitly_wait`와 `WebDriverWait` 혼용하는 게 지저분했다.
