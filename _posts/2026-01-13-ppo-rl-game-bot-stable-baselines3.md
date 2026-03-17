---
layout: post
title: PPO 강화학습으로 게임 봇 만들기 - Stable Baselines3 실전 사용기
subtitle: 카카오 미니게임 자동화를 위한 커스텀 Gym 환경 설계와 PPO 에이전트 학습
author: HyeongJin
date: 2026-01-13 17:00:00 +0900
categories: AI/LLM
tags: [Python, AI, MachineLearning, ReinforcementLearning]
sidebar: []
published: true
---

강화학습(RL)이 게임 자동화에 어떻게 쓰이는지 직접 실험해보고 싶었다. 카카오 게임봇에서 실행되는 카드 강화 미니게임을 타겟으로 잡았다. 규칙 기반 매크로는 이미 만들어뒀고, RL 에이전트가 더 잘할 수 있는지 비교하고 싶었다.

## 구조 설계

```
게임 화면 파싱 → Gym 환경 → PPO 에이전트 학습 → 매크로 실행
```

화면에서 게임 상태를 읽어서 Gym 환경에 넣고, PPO 에이전트가 액션을 선택하면 매크로로 실행하는 구조다.

## 데이터 수집

카카오톡 채팅 로그를 export해서 게임 상태를 파싱했다. 채팅 메시지에 강화 결과(성공/실패, 현재 레벨 등)가 텍스트로 나온다.

```python
import pandas as pd

def parse_game_log(csv_path: str) -> pd.DataFrame:
    df = pd.read_csv(csv_path)
    # 속보 메시지 필터링
    df = df[~df["message"].str.contains("속보", na=False)]

    records = []
    for _, row in df.iterrows():
        state = extract_state(row["message"])
        if state:
            records.append(state)
    return pd.DataFrame(records)
```

`속보` 메시지는 게임 결과가 아닌 공지라 파싱 전에 제거해야 했다. 이걸 놓쳐서 파싱 로직이 계속 틀렸다가 뒤늦게 발견했다.

## Gym 환경 설계

```python
import gymnasium as gym
import numpy as np

class SwordEnv(gym.Env):
    def __init__(self):
        # 관측 공간: [현재 레벨, 누적 실패 수, 강화 성공률]
        self.observation_space = gym.spaces.Box(
            low=np.array([0, 0, 0.0]),
            high=np.array([100, 50, 1.0]),
            dtype=np.float32
        )
        # 액션 공간: 0=강화, 1=판매
        self.action_space = gym.spaces.Discrete(2)

    def reset(self, seed=None):
        self.level = 1
        self.fail_count = 0
        self.success_rate = 0.5
        return self._get_obs(), {}

    def step(self, action):
        if action == 0:  # 강화 시도
            success = np.random.random() < self._enhance_prob()
            if success:
                self.level += 1
                reward = self.level * 0.1
                self.fail_count = 0
            else:
                self.fail_count += 1
                reward = -0.5
                # 일정 레벨 이상에서 실패하면 파괴 가능
                if self.level > 10 and self.fail_count > 3:
                    reward = -5.0
                    return self._get_obs(), reward, True, False, {}
        else:  # 판매
            reward = self.level * 0.5
            return self._get_obs(), reward, True, False, {}

        return self._get_obs(), reward, False, False, {}

    def _enhance_prob(self):
        # 레벨이 높을수록 성공률 감소
        return max(0.1, 0.9 - self.level * 0.05)
```

보상 설계가 핵심이다. 높은 레벨에서 계속 강화를 시도하다가 파괴되는 것보다, 적당한 시점에 판매하는 게 기대 보상이 높도록 설계했다.

## PPO 학습

```python
from stable_baselines3 import PPO
from stable_baselines3.common.env_util import make_vec_env

env = make_vec_env(SwordEnv, n_envs=4)

model = PPO(
    "MlpPolicy",
    env,
    learning_rate=3e-4,
    n_steps=2048,
    batch_size=64,
    n_epochs=10,
    verbose=1
)

model.learn(total_timesteps=500_000)
model.save("sword_ppo")
```

`n_envs=4`로 병렬 환경 4개를 동시에 돌렸다. 단일 환경보다 샘플 효율이 높아진다.

## Windows 호환 문제

개발은 Mac에서 했는데 실제 게임이 실행되는 환경은 Windows다. pyautogui의 hotkey 처리 방식이 OS마다 다르다.

```python
import platform

def press_enhance():
    if platform.system() == "Windows":
        import pyautogui
        # Windows: Ctrl 키 조합
        pyautogui.hotkey("ctrl", "1")
    else:
        import pyautogui
        # macOS: Cmd 키 조합
        pyautogui.hotkey("command", "1")
```

좌표 찾기 도구도 별도로 만들었다. 게임 화면에서 채팅창 입력/출력 좌표를 config에 저장해두고 쓰는 방식.

## 규칙 기반 vs RL 비교

규칙 기반 전략은 단순하다. "레벨 N 이상에서 연속 실패 M회면 판매"처럼 하드코딩된 조건으로 동작한다.

PPO 에이전트는 학습 후 규칙 기반보다 조금 더 높은 기대 판매값을 달성했다. 하지만 학습된 환경(시뮬레이션)과 실제 게임의 확률 분포가 다를 수 있어서 실 환경에서는 규칙 기반과 큰 차이가 없었다.

강화학습이 빛나는 건 상태 공간이 복잡하고 최적 전략이 직관적으로 보이지 않는 경우다. 이 게임처럼 상태가 단순하면 규칙 기반이 충분하다.
