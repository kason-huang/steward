# Steward 与 robot harness 的关系

本文讨论的是仓库中的 `steward/` 具身智能系统，依据 [Steward README](../../steward/README.md) 与 [设计文档](../../steward.md)，区分现有系统定位与未来抽象方向。

Steward 已经在探索一种 robot harness；它同时包含机器人侧的执行系统，以及用于开发、观察和评估它的仿真系统。

## 系统关系

```text
DeepSeek Harness：通用 agent 基础设施
└─ Steward：具身智能系统
   ├─ 机器人侧
   │  ├─ Ego Harness：理解任务、规划、编排技能、处理异常
   │  └─ Ego Runtime：感知、自我态、动作预测、技能执行、安全监督
   └─ 仿真与评估侧
      ├─ World Harness：构建场景、引入扰动、编排实验
      └─ 世界仿真、Observer、控制台
```

DeepSeek Harness 提供插件组合、模型接入、工具调用、会话日志等通用 agent 能力。Steward 通过插件、bundle、profile、patch 与 SDK 构建具身智能系统，设计上要求对上游零侵入。

Ego Harness 负责模型驱动的任务理解、长时规划、技能编排与异常处理；Ego Runtime 承担非 LLM 的感知、状态估计、自我仿真、技能执行与安全监督。LLM 不进入实时控制回路。

## robot harness 的两种口径

- **狭义 robot harness** 对应 Ego Harness：让模型能够调用机器人的能力，持续完成任务。
- **广义 robot harness** 应包含 Ego Harness 与 Ego Runtime 的协作：把模型决策接入“感知 → 状态估计 → 预测 → 行动 → 验证”的完整闭环。Steward 的机器人侧正在实现这个方向。

这里的广义 robot harness 是对可复用架构的判断，并非当前代码中已经独立发布的组件。

## Steward 的关键价值

Steward 把“机器人认为世界是什么”与“世界实际是什么”分开了。机器人只依据自身传感器维护的 Ego-State（ES）行动；仿真与观察平面掌握 World State，也就是仿真中的 Ground Truth（GT），Observer 对比 ES 与 GT，评估机器人的认知与行为。

World Harness 可以改变环境、引入扰动，检验机器人能否发现变化、修正认知并恢复任务。这让系统能够评估自主行为，而不只是演示工具调用。

仿真与评估侧服务于机器人系统的开发和验证。设计目标是同一套 Ego 系统通过替换传感器与执行器接入迁移到实体机器人；这属于架构目标，不代表当前已经完成实体部署验证。

## 定位与演进判断

Steward 是 robot harness 的具体实现与验证系统；未来可复用的 robot harness，可以从其 Ego 系统中逐步沉淀出来。

现阶段先用不同任务、感知方案和机器人检验这些接口，再决定哪些部分值得抽成通用框架，会更稳妥。优先检验任务与技能的表达、感知和状态估计的接入、预测与执行的一致性、执行中断与恢复，以及完成结果的验证。
