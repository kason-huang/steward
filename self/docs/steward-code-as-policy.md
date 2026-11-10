# Steward 的代码化策略与 LLM 能力积累

整理于 2026-11-09，基于本地实现与机器人能力文件。本文记录当前机制及其可验证范围。

## phase0 的能力来源

phase0 将感知、状态估计、预测和控制分配给世界侧与机器人侧的 Python 模块。

| 环节 | 实现方 | 当前机制 |
|---|---|---|
| 检测 | 世界侧 WSim | 从仿真场景获取类别和有向包围盒，检查视野与遮挡，加入测量噪声、漏检和类别混淆 |
| 状态估计 | 机器人侧 EgoEstimator | 融合检测与里程计，估计物体位置、朝向、尺寸及不确定性，维护 Ego-State |
| 预测 | 机器人侧 ESim | 在 Ego-State 上规划路径与操作动作，检查碰撞、可达性和风险，生成带具体计划的预测回执 |
| 技能执行 | 机器人侧 SkillExecutor | 执行回执中的计划，结合自身状态反馈，发送底盘、关节与夹爪命令 |
| 物理执行 | 世界侧 WSim 与 MuJoCo | 接收控制目标并推进物理，生成后续传感器读数，抓取包含捕获范围内附着物体的理想化机制 |

“带噪声的位姿和尺寸”表示检测值在真实值上加入随机测量误差。位姿包含位置与朝向，尺寸是包围盒的长、宽、高。默认位置各轴的噪声标准差为 `(0.01 + 0.02 × 距离米数) × 240 / 图像高度像素数` 米，朝向噪声标准差为 `0.05` 弧度，尺寸各轴的相对噪声标准差为 `3%`。估计器通过融合多次观测逐步改善估计。

相关实现见 [Phase 配置](../../steward/src/steward/phases.py)、[仿真与检测](../../steward/src/steward/world/sim.py)、[估计器](../../steward/src/steward/ego/estimator.py)、[ESim](../../steward/src/steward/ego/esim.py) 和 [技能执行器](../../steward/src/steward/ego/skills.py)。

## ctx.ego 的作用

`ctx.ego` 是 Cordis 插件上下文中的 `EgoService`，代表某台机器人的 Ego 能力。Harness 插件通过它读取感知与状态、申请动作预测、执行回执和查询任务。`dsh-ego-runtime-client` 将方法调用转换成发给 Python Ego Runtime 的 HTTP 请求，默认地址为 `http://127.0.0.1:7420`。

模型调用的机器人工具经由这层服务访问 Runtime。例如，`ego_predict` 请求预测，`skill_run` 执行回执对应的计划。模型负责选择目标、组合操作与处理反馈，Python 模块负责状态估计、几何规划和控制周期。

相关实现见 [服务定义](../../steward/dsh/packages/dsh-ego/src/index.ts)、[HTTP 提供方](../../steward/dsh/packages/dsh-ego-runtime-client/src/index.ts) 和 [机器人工具](../../steward/dsh/packages/dsh-tool-ego/src/index.ts)。

## LLM 编写并持久保存的能力

`steward/var/dsh/robot-1/steward-capabilities/` 是机器人自己的持久能力库，每个 JSON 文件保存一个命名程序，包含 `code`、参数说明、前置条件、预期结果、版本、状态和运行统计。库按机器人 Harness home 存放，可跨会话与场景复用。

Ego Harness 的提示要求模型先查已有能力，缺少合适能力时用 `run_code` 编写程序，组合感知、图案比对、预测与执行工具。任务做成后，模型可以调用 `capability_save`，将通用操作保存为 `skill`，将组合多个技能的任务流程保存为 `recipe`。物体 ID、位置和尺寸等任务特定数据通过参数传入。

后续任务通过 `capability_list` 查目录，通过 `capability_get` 获取代码，再在 `run_code` 内执行。模型可修改程序并重新保存，代码变化时版本递增，状态重置为 `candidate`，当前版本的运行统计重新开始。默认在两个不同 Harness run 中成功后标记为 `verified`，连续三次失败后标记为 `deprecated`。这里的成功依据是取用该能力的 `run_code` 程序无错误结束，物理目标是否达成还依赖程序自身的检查。

相关实现见 [能力库与工具](../../steward/dsh/packages/dsh-tool-ego-capabilities/src/index.ts) 和 [Ego Harness 提示与能力摘要](../../steward/src/steward/world/chat.py)。

## 本次观察到的能力文件

| 文件 | 保存的行为 |
|---|---|
| `flip_flat_objects_face_up.json` | 读取物体状态，逐个预测并执行抓取，再通过翻面放置将物体放回原处附近 |
| `assemble_jigsaw_by_picture.json` | 比对拼图片与参考图，计算网格位置和朝向，逐块抓取并摆放，必要时借助中转位置调整朝向 |

两份文件在本次读取时均为 `version: 1`、`status: candidate`、`uses: 0`，成功和失败计数均为零。这些字段支持“当前版本已保存，尚无能力库记录的复用运行”这一判断。保存前是否试运行、是否修改过临时代码，以及文件的实际生成调用，需要结合会话日志确认。

本次读取了能力文件及其保存、加载和计数实现，尚未逐条核对会话中的生成与试运行记录。

## 对 Code as Policy 的理解

Steward 的策略代码有两个来源。开发者编写基础规划和控制技能，其中包含停靠、抓取方向、动作顺序和失败恢复等行为决策。LLM 在这些基础工具之上编写带循环、条件、计算和工具调用的程序，并将其保存到能力库，供后续任务复用和修订。

因此，“LLM 编排预设技能”只覆盖了其中一部分。能力库进一步让模型生成的程序承担任务策略，并成为可持久积累的行为实现。这是本次讨论中“有 Code as Policy 的味道”的具体含义，所指范围是代码承载策略、由模型生成组合程序并通过执行反馈修订。

当前实现提供了生成、执行、保存、复用和更新能力的机制。某个能力是否经历多轮迭代、是否跨场景可靠，需要由对应版本的运行记录和任务结果支持。
