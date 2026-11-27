---
kind: upgrade-guide
description: Steward 客户端在停止后必须重新授权运动。
---

# 停止后恢复 Steward 运动

[English](guide.md) | 中文

## 变更

过去，`POST /skill/stop` 会取消正在运行的技能。现在，该请求还会让机器人保持停稳，并使之前的运动授权失效。保持期间，Ego API 拒绝启动新技能。

## 迁移

1. 停止后读取 `GET /ego/motion`，直到返回 `state: "held"`。确认机器人状态和保持原因后，再授权运动。
2. 将返回的 `epoch` 发送给 `POST /ego/motion/resume`。如果有客户端持有独占控制权，同时传入当前的 `owner`。
3. 确认响应返回 `state: "ready"`。执行下一项技能前重新预测。直接向机器人端口发送指令的客户端必须在之后的 `POST /command` 请求中传入当前的 `motionEpoch`。
