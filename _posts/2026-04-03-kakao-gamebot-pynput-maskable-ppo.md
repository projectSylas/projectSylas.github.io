---
layout: post
title: 카카오 게임봇 매크로 — pynput F-키 시스템과 MaskablePPO Action Masking
subtitle: 클립보드 실시간 파싱으로 상태 읽기, 유효하지 않은 액션을 마스킹한 PPO로 강화/판매 결정
author: HyeongJin
date: 2026-04-03 10:00:00 +0900
categories: AI/LLM
tags: [Python, ReinforcementLearning, pynput, StableBaselines3, automation]
sidebar: []
published: true
---

카카오톡 게임봇 "검키우기"는 검을 강화하거나 판매하는 미니게임이다. 강화에 실패하면 레벨이 유지되거나 파괴되고, 잔고가 없으면 강화를 못 한다. 이 제약을 모델에 반영하면서 매크로로 실행하는 것까지 구현했다.

이전 포스트에서 Gymnasium 환경 설계와 PPO 학습 흐름을 다뤘다. 이 글은 그 다음 두 가지에 집중한다.

- 표준 PPO 대신 **MaskablePPO**를 쓴 이유와 Action Masking 구현
- pynput 기반 **F-키 매크로 시스템**과 카카오톡 클립보드 실시간 파싱

## Action Masking: MaskablePPO

표준 PPO는 어떤 액션이든 선택할 수 있다고 가정한다. 하지만 실제 게임에는 제약이 있다.

- 잔고가 강화 비용보다 적으면 강화 불가
- 검 레벨이 최소 판매 레벨 미만이면 판매 불가
- 레벨 20이면 더 이상 강화 불가

이 상태에서 표준 PPO가 "강화"를 선택하면 환경이 에러를 내거나 음의 보상을 줘야 한다. 음의 보상으로 처리하면 학습이 느려진다. Action Masking은 처음부터 불가능한 액션을 선택지에서 제거한다.

`sb3_contrib`의 `MaskablePPO`를 썼다.

```python
from sb3_contrib import MaskablePPO
from sb3_contrib.common.wrappers import ActionMasker

def make_env(rank, seed=0):
    def _init():
        env = SwordEnv()
        env = ActionMasker(env, lambda env: env.action_masks())
        env = Monitor(env, LOG_DIR)
        env.reset(seed=seed + rank)
        return env
    set_random_seed(seed)
    return _init

env = DummyVecEnv([make_env(i) for i in range(N_ENVS)])
env = VecNormalize(env, norm_obs=True, norm_reward=True, clip_obs=10.)

model = MaskablePPO("MlpPolicy", env, verbose=1, learning_rate=LEARNING_RATE,
                    n_steps=N_STEPS, batch_size=BATCH_SIZE, gamma=GAMMA)
```

`ActionMasker` 래퍼가 매 스텝마다 `action_masks()`를 호출해서 마스크 배열을 정책에 전달한다.

환경의 `action_masks` 구현:

```python
def action_masks(self):
    masks = [True, True]
    level = self.state[1]
    cost = level_cost[level]

    if self.state[0] < cost:
        masks[0] = False  # 잔고 부족 → 강화 불가
    if level < self.minimum_sell_level:
        masks[1] = False  # 레벨 미달 → 판매 불가
    if level >= 20:
        masks[0] = False  # 최대 레벨 → 강화 불가

    return np.array(masks, dtype=bool)
```

추론 시에도 마스크를 직접 만들어서 넘긴다.

```python
def predict(self, fund: int, level: int, fail_count=0):
    cost = level_cost.get(level, 0)
    raw_obs = np.array([fund, level, cost, fail_count], dtype=np.int32)
    action_masks = self._get_mask(fund, level)

    if not any(action_masks):
        return -1  # 행동 불가

    norm_obs = self.vec_norm.normalize_obs(np.array([raw_obs]))
    action, _ = self.model.predict(norm_obs, action_masks=action_masks, deterministic=True)
    return int(action[0])
```

`VecNormalize`로 관측값을 정규화한 것과 동일한 stats를 추론에서도 써야 한다. `vec_norm.training = False`로 설정하지 않으면 추론 중에도 running stats가 업데이트돼서 결과가 달라진다.

```python
self.vec_norm = VecNormalize.load(stats_path, self.dummy_env)
self.vec_norm.training = False
self.vec_norm.norm_reward = False
```

## 카카오톡 클립보드 실시간 파싱

게임 상태를 읽는 방법은 두 가지였다. 채팅 로그 CSV를 주기적으로 파싱하거나, 실행 중에 클립보드로 복사해서 읽거나. 로그 파일은 카카오톡이 점유하고 있어서 직접 읽기가 안 된다. 클립보드 방식을 썼다.

```python
def _copy_message():
    _click_mouse(*CHAT_OUTPUT_COORD)
    time.sleep(0.2)

    modifier_key = keyboard.Key.ctrl if platform.system() == 'Windows' else keyboard.Key.cmd
    controller.press(modifier_key)
    controller.press('a')
    time.sleep(0.1)
    controller.release('a')
    controller.press('c')
    time.sleep(0.1)
    controller.release('c')
    controller.release(modifier_key)
    time.sleep(0.1)

    _click_mouse(*CHAT_INPUT_COORD)
    return pyperclip.paste()
```

채팅 출력창 좌표를 클릭 → 전체 선택 → 복사 → 입력창으로 포커스 복귀. `CHAT_OUTPUT_COORD`와 `CHAT_INPUT_COORD`는 config에서 화면 해상도에 맞게 설정한다.

복사한 텍스트에서 레벨과 골드를 파싱한다.

```python
def _parse_message(message):
    global fail_count

    # 속보 메시지 제거 (게임 결과 아닌 공지)
    if "🚨[속보]🚨" in message:
        lines = message.split('\n')
        message = '\n'.join(line for line in lines if "🚨[속보]🚨" not in line)

    message = message.split('@')[-1]  # 마지막 @ 이후만 사용

    enhance_pattern = re.findall(r'강화 (\w+)', message)
    result = enhance_pattern[0] if enhance_pattern else None
    is_destroyed = (result == '파괴' or "파괴" in message)

    if result == '유지':
        fail_count += 1
    else:
        fail_count = 0

    level_pattern = re.findall(r'\+(\d+)', message)
    level = int(level_pattern[-1]) if level_pattern \
        else 0 if is_destroyed else None

    gold_pattern = re.findall(r'(?:남은|현재\s*보유|보유)\s*골드:\s*([\d,]+)\s*G', message)
    fund = int(gold_pattern[0].replace(',', '')) if gold_pattern else None

    return fund, level
```

`@` 기준으로 마지막 블록만 쓰는 이유는 전체 선택 시 이전 메시지들이 함께 딸려오기 때문이다. 카카오봇 메시지는 `@봇이름` 형식으로 시작한다.

`fail_count`는 글로벌 상태로 관리한다. "유지" 결과가 연속으로 나오면 카운트가 쌓이고, 다른 결과가 나오면 초기화된다. 이 값을 관측에 포함해서 연속 실패 시 판매 결정에 반영한다.

## pynput F-키 매크로 + 워커 스레드

키 입력을 감청하는 리스너와 실제 게임 동작을 수행하는 루프를 분리했다.

```python
running_mode = None  # 'ai' | 'heuristic' | 'rare_acquire' | 'rare_enforce' | None

def worker_loop():
    while True:
        if running_mode == 'ai':
            act_inference('ai')
            time.sleep(ACTION_DELAY)
        elif running_mode == 'heuristic':
            act_inference('heuristic')
            time.sleep(ACTION_DELAY)
        elif running_mode == 'rare_acquire':
            act_rare_acquire()
            time.sleep(ACTION_DELAY)
        elif running_mode == 'rare_enforce':
            act_rare_enforce()
            time.sleep(ACTION_DELAY)
        else:
            time.sleep(0.1)

t = threading.Thread(target=worker_loop, daemon=True)
t.start()
```

`running_mode` 글로벌 변수로 현재 동작 모드를 관리한다. F-키 핸들러가 이 값을 바꾸면 워커 스레드가 다음 반복에서 분기된다.

```python
def on_press(key):
    global running_mode
    try:
        if key in pressed_keys:
            return
        pressed_keys.add(key)

        if key == keyboard.Key.f1:
            act_enhance()           # 강화 1회
        elif key == keyboard.Key.f2:
            act_sell()              # 판매 1회
        elif key == keyboard.Key.f3:
            running_mode = 'ai'
        elif key == keyboard.Key.f4:
            running_mode = 'heuristic'
        elif key == keyboard.Key.f5:
            running_mode = None
            return False            # 리스너 종료
        elif key == keyboard.Key.f6:
            running_mode = 'rare_acquire'
        elif key == keyboard.Key.f7:
            running_mode = 'rare_enforce'
    except AttributeError:
        pass
```

`pressed_keys` set으로 키 반복 입력을 막는다. 키를 누르고 있으면 OS가 연속으로 이벤트를 보내는데, 이미 눌린 키면 무시한다.

강화/판매 명령은 카카오톡 입력창에 `/강` 또는 `/판`을 입력하고 엔터를 두 번 치는 방식이다.

```python
def act_enhance():
    controller.press('/')
    time.sleep(0.2)
    controller.press('강')
    time.sleep(0.2)
    controller.press(keyboard.Key.enter)
    time.sleep(0.2)
    controller.press(keyboard.Key.enter)
```

## 희귀무기 상태 전환

일반 모드와 별도로 희귀무기 전용 모드 두 개를 만들었다.

- `rare_acquire`: 판매를 반복하며 희귀무기 획득 대기
- `rare_enforce`: 희귀무기를 목표 레벨까지 강화

판매 후 채팅 메시지에서 획득 아이템 이름을 파싱한다.

```python
def _parse_item_name(message):
    lines = [line.strip() for line in message.split('\n') if line.strip()]
    for idx in reversed(range(max(0, len(lines)-10), len(lines))):
        line = lines[idx]
        if ("획득:" in line or "새로운 검 획득:" in line) and "[+0]" in line:
            match = re.search(r"\[\+0\]\s*(.+)", line)
            if match:
                item_name = re.split(r"[\(\[]", match.group(1).strip())[0].strip()
                return item_name
    return None
```

마지막 10줄을 역순으로 탐색해서 `[+0]` 다음 아이템 이름을 꺼낸다. 괄호 이후는 잘라낸다.

희귀 여부는 이름 매칭으로 판단한다.

```python
RARE_WEAPONS = ["광선검", "핫도그", "칫솔", "주전자", "채찍", "꽃다발", "소시지", "새해 검"]

def _is_rare_weapon(item_name):
    is_rare = any(rare_item in item_name for rare_item in RARE_WEAPONS)
    is_normal_weapon = "검" in item_name or "몽둥이" in item_name
    return is_rare or not is_normal_weapon
```

RARE 목록에 포함되거나, 일반 무기(검/몽둥이)가 아닌 것도 희귀로 간주한다.

희귀무기를 발견하면 `running_mode`를 바꿔서 강화 모드로 전환한다.

```python
if _is_rare_weapon(item_name):
    current_weapon_name = item_name
    running_mode = 'rare_enforce'
    act_enhance()
    return
```

강화 중 파괴되면 다시 `rare_acquire`로 복귀.

```python
if is_destroyed:
    running_mode = 'rare_acquire'
    act_sell()
    return
```

## 실행 구조 요약

```
F3/F4/F6/F7 입력
    ↓
running_mode 변경
    ↓
worker_loop (별도 스레드)
    ├── act_inference(mode)
    │   ├── _copy_message()      ← 클립보드 복사
    │   ├── _parse_message()     ← 레벨/골드 파싱
    │   └── ai.predict() or ai.heuristic()
    │       └── act_enhance() or act_sell()
    └── act_rare_acquire / act_rare_enforce
        └── _parse_item_name() → 상태 전환
```

리스너와 워커를 분리한 덕분에 키 입력과 게임 동작이 서로 블로킹하지 않는다. F5로 `running_mode = None`을 세트하면 워커는 sleep 루프로 떨어지고, 리스너는 `return False`로 종료된다.
