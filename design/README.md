# Embodied Agent Runtime · 仿真系统设计

本项目派生自 Steward 的 `b0af02e596`。实验员通过 **World Harness** 布置和改变仿真世界。机器人侧由 **S2（Ego Harness）** 理解任务和选择技能，**S1** 生成运动目标，**S0** 在本体侧执行目标、返回反馈并确认停稳。每台机器人都有自己的 **Ego Runtime**，根据传感器更新 **Ego-State**，用 **ESim** 预测技能后果并管理作业。第一阶段先验收原版，再把同一流程接到 PhyAgentOS 的 PiPER 构型。[基线运行入口](../steward/README.zh.md) · [双 Harness 设计](../steward.md)

| 想了解什么 | 设计入口 |
| --- | --- |
| 现有代码改哪里、按什么顺序验证 | [首阶段代码改动](docs/code-changes.md) |
| 论文逐项对齐：图 3–5、Harness 组件、评估和能力等级 | [论文映射与验收口径](docs/paper-alignment.md) |
| 世界侧、机器人侧和论文 S2/S1/S0 怎样对应 | [逻辑架构](docs/architecture.md) · [架构图](docs/architecture.svg) |
| S2 怎样调用 S1，S1 怎样适配本体，S0 怎样执行动作和确认停稳 | [S1 技能适配器](docs/architecture.md#s1-技能适配器) · [正常时序](docs/sequence-normal.svg) · [中断时序](docs/sequence-interrupt.svg) · [S0 接口](docs/code-changes.md#s0-和-s1-的实现分界) |
| Profile 怎样加载工具，Console 怎样显示 S2 能调用的 S1 和 S0 | [工具加载与可见性](docs/architecture.md#s2-工具加载与-console-可见性) |
| 哪些服务已经有，PiPER 接在哪，怎样保证同一回合只有一个世界 | [部署设计](docs/deployment.md) · [部署图](docs/deployment.svg) |
| 世界真值、机器人估计、预测回执、技能作业和验收结果有什么区别 | [领域模型](docs/domain-model.md) · [关系图](docs/domain-model.svg) · [术语](CONTEXT.md) |

## 已有实现与本项目增量

| 范围 | 基线代码 | 本项目要交付 |
| --- | --- | --- |
| 构建世界与评估 | World Harness 通过 WCA 操作世界，可移动、添加或移除符合条件的实体。Observer 单向接收机器人估计，与 World State 比对。 | 让布景、扰动、相机、动作和评分都指向同一个 PiPER 世界实例，按同一回合和仿真时间对齐。[World 工具](../steward/dsh/packages/dsh-tool-world/src/index.ts) · [Observer](../steward/src/steward/observer/observer.py) |
| 机器人观察与定位 | Ego Runtime 的感知循环融合相机和本体。估计器维护机器人分配的物体 ID、位置误差、存在概率与最近观测时刻。 | 适配 PiPER 相机、标定、坐标系和本体反馈，验证遮挡和断流。只有 RGB 时先验收可见性和左右判断；桌面平面定位有高度假设，离开桌面的物体需要独立定位证据。[感知](../steward/src/steward/ego/perception.py) · [估计器](../steward/src/steward/ego/estimator.py) |
| 从决策到运动 | Ego Harness 调用 Ego 工具。ESim 给候选动作生成预测回执；Harness 安全插件初检，Runtime 执行前复核，随后启动 Job。 | 适配 PiPER 的运动学、可达性和夹爪。派发前增加与此次动作有关的意图和目标时效检查，复查“回执生成后目标移动”的情形。[工具](../steward/dsh/packages/dsh-tool-ego/src/index.ts) · [Runtime](../steward/src/steward/ego/runtime.py#L2110) |
| S0 本体控制 | Steward 的机器人端口接收运动目标，MuJoCo 执行器驱动本体。 | 两种仿真构型均已验证本地停止与保持、确认停稳和拒绝失效的运动授权。PiPER 使用自身的六个关节执行器、夹爪和本体反馈。[S0 代码边界](docs/code-changes.md#s0-和-s1-的实现分界) |
| 运行中响应与恢复 | 现有系统支持查询作业、请求停止、首次失败和重复失败的不同深度反思，以及按高层步骤检查进展。 | 先测原版在长技能运行时能否及时发现变化。按缺口增加异步事件、旧决策失效、设备停稳确认及恢复授权。VLA/WAM 后续可作为执行器接入；首版用程序技能建立基线。[安全插件](../steward/dsh/packages/dsh-robot-safety/src/index.ts) · [反思插件](../steward/dsh/packages/dsh-ego-reflection/src/index.ts) |

## 第一阶段的仿真验收

原版厨房场景的脚本回归已验证杯子搬运及途中更换落点。PiPER 工作台已用真实模型验证正常抓放、中途改目的地、异常与后台作业唤醒、跨任务偏好和遮挡后报告无法确认。仿真 S0 可通过独立的机器人端口测试。下表给出验收范围。[仿真证据](docs/code-changes.md#实现状态与仿真证据) · [S0 接口测试](../steward/tests/test_motion_hold.py)

| 用例 | 怎样判定 |
| --- | --- |
| 同一个世界 | World 侧在抓取前移动黑盒。Ego 侧从新图像重新定位，Observer 用该回合真实位置评分。机器人侧输入不得含扰动脚本、真实坐标或世界实体 ID。 |
| 只观察与感知退化 | 分别测试可见、遮挡和相机断流。检查回答与证据是否一致；在关节仍有反馈时，物体的最后观测时间保持原值。 |
| 多步操作 | 从自然语言入口完成定位、抓取、搬运、放置与复查。对照 Job 终态、机器人结论和物体最终所在托盘，记录误报成功。 |
| 中途改意图和保护 | 分别在模型推理中、左盘动作尚未下发、动作已下发时改为右盘。验证保留有效进展、旧回复失效和停止后交接。另测保护条件直接触发停止的时延。 |
| 结果未知与跨任务状态 | 模拟回执丢失，验证按原作业查询。下一任务可复用有来源的用户偏好，对已移动物体重新定位。 |

配对试验在同一批种子上比较目标移位检测开关。两组在允许一次重试时都完成了任务。移位后打开检测的组更早停止第一次尝试，任务耗时中位数从 16.2 降到 13.1 仿真秒。[试验条件与复现](docs/code-changes.md#目标移位监控配对试验)
