---
kind: upgrade-guide
description: "Steward S0 位姿指令接收底座坐标系下的末端位置和单位四元数。"
---

# Steward S0 末端位姿

[English](guide.md) | 中文

## 变更

XLeRobot 的 `body_set_pose` 接收以米为单位的 `position: [x, y, z]` 和单位四元数 `orientation: [w, x, y, z]`。旧版接收 `x`、`z` 和 `pitch`。固定底座 PiPER 接受相同的位姿请求。HTTP 指令将这两个字段放在 `pose` 内。响应使用相同格式返回实测 `pose`，并附上测量时间戳、夹爪状态和指令 ID。

## 迁移

1. 调用 `ego_capabilities` 查询已开放的 S0 工具、坐标约定和工作臂约束。HTTP 客户端可从 `GET /ego/control` 的 `capabilities` 字段读取说明。
2. 将旧 XLeRobot 指令转换为 `[x, fixedYM, z]` 和单位四元数 `[0, cos(pitch/2), 0, sin(pitch/2)]`。从机械臂描述读取 `fixedYM`。通过 `ego_gripper_pose.tcp_pose` 读取相同格式的当前实测末端位姿。
3. 确认响应状态为 `reached`，再使用实测 `pose`。启用了原生关节调试的 PiPER 仍可通过 `body_set_joints` 发送关节目标。
