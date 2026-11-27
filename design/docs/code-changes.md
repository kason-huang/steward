# 首阶段代码改动

以 `b0af02e596` 为基线，先在 Steward 原有仿真里测清 S2 的中途响应，再把同一条机器人侧接口接到 PiPER 构型。沿用 `Ego Harness → Ego API → Ego Runtime → ESim 与技能执行器 → S0 本体控制`。World Harness 继续通过 WCA 布景和施加扰动，Observer 根据世界真值独立评分。

## 实现状态与仿真证据

- Steward 原版厨房回归已在 MuJoCo 通过。修复了带抽屉餐边柜生成时的缩进问题。TypeScript Ego 工具通过 HTTP 完成了杯子搬运，并由世界侧检查结果。
- S0 增加独立运动状态、停止与恢复接口。Ego Runtime 把运动授权绑定到 Job，每个运动包使用该授权。仿真覆盖了运动中停止、暂停导致停稳未知、保留夹爪状态、保护优先于任务恢复，以及恢复后拒绝迟到指令。[S0 仿真测试](../../steward/tests/test_motion_hold.py)
- Ego Runtime 的监控线程采样本体反馈和作业目标。超时、观察到目标移位或搬运中两次收到夹持丢失时，先请求 S0 保持，再发布事件。仿真已验证超时唤醒、目标移位和掉件停稳。S2 可用 `ego_motion_status` 查询停稳并从相机或编码器重新取证。[监控器](../../steward/src/steward/ego/monitor.py) · [保护唤醒测试](../../steward/tests/test_chat.py#L310) · [移位与掉件回归](../../steward/tests/test_piper_workstation.py)
- 控制台支持修订当前任务。模型等待时重用同一会话；技能仍在运行时取消冲突 Job，确认停稳后再继续。已实测真实模型接到更正后在原会话中改为只查看夹爪。脚本驱动的抓放回归另验证已抓稳的杯子被保留、旧预测回执被拒绝，以及最终落点距更新目标约 4 mm。该回归使用仿真真值评分。[会话回归](../../steward/tests/test_chat.py) · [最新目标落点回归](../../steward/tests/test_e2e.py)
- Start 响应丢失时按原预测回执查询 Job。TypeScript 客户端经测试只派发一次，查询仍无法确认时请求本地保持。[派发响应丢失测试](../../steward/dsh/test/integration.test.mjs)
- PiPER 工作台使用 PhyAgentOS `robot-control-sim` 0.1.3 的官方 MJCF。原生六轴、夹爪传动和腕部相机由 Steward 的 MuJoCo 世界推进。Ego Runtime 用工作台 RGB-D 识别并跟踪物体。相机断流时继续更新关节反馈，保留物体最后观测时间。集成回归还覆盖本地停稳、World 移动物体后的重新定位、抓放及搬运途中改目的地。[部署与复现](deployment.md) · [PiPER 测试](../../steward/tests/test_piper_workstation.py)
- 放置验收分别记录 Job、物理效果和当前意图。机器人从新观测核验，World 按真值独立评分。原版厨房的一次真实模型搬运耗尽 135 秒。下表记录 PiPER 的指定场景结果。[放置验收](../../steward/src/steward/ego/verification.py) · [世界评分](../../steward/src/steward/world/goals.py)

| PiPER 仿真回合 | 机器人侧 | World 独立评分 |
| --- | --- | --- |
| 自然语言指令把黑盒放到左盘中央 | Ego Harness 调用抓取、放置和验收，确认最新意图满足 | 黑盒已释放并由左盘支撑，距目标中心 4.3 mm |
| 左盘放置仍在运行时改为右盘 | 原会话继续。左盘 Job 停止、保留夹持，右盘 Job 执行并通过机器人核验 | 黑盒已释放并由右盘支撑，距目标中心 3.9 mm。左盘评分未通过 |
| 接近时移动黑盒 80 mm | 监控器估计移位 79.4 mm，请求 S0 保持。原会话只读确认停稳，报告抓取未完成 | 记录扰动与停止事件 |
| 模型用 `wait=false` 发起抓取 | 模型先结束回复。后台 Job 正常返回后，系统唤醒同一会话复查 | 记录 Job 的终态和唤醒事件 |
| 新任务继承默认托盘偏好，黑盒已被移位 | 重新定位黑盒，旧轨迹由有效深度帧清除。机器人按已保存的操作员偏好放到右盘 | 放置成功，距目标中心约 4.0 mm |
| 遮挡黑盒后只观察 | 模型报告无法确认当前黑盒位置，引用带时间的旧估计。未启动运动 Job。遮挡后的其他物体关系仍有误判 | 确认黑盒仍在原位，由屏遮挡 |

前两个放置回合都用 `deepseek-flash`、种子 0 和 1 倍仿真速度，单轮等待预算分别为 180 秒和 160 秒。改口由测试脚本在左盘作业开始后注入，属于操作员介入。机器人使用工作台 RGB-D 和匿名实例掩码。上表回合采用初版的理想附着。

PiPER 后续改用原生双指碰撞几何、联动关节和夹爪伺服，通过 MuJoCo 接触与摩擦夹取。模型从自然语言指令完成了左盘到右盘的搬运。世界端观测到双指保持接触时黑盒抬高约 39 mm，释放后距右盘中心 8.8 mm，机器人与 World 独立验收均通过。回归验证了空夹不会报告夹持，降低指面与物体的摩擦会导致掉件并触发停稳保护。[夹持反馈](../../steward/src/steward/world/sim.py#L870) · [PiPER 回归](../../steward/tests/test_piper_workstation.py)

脚本回归另外覆盖相机断流时保留物体最后观测时间、继续更新关节状态，以及遮挡导致观测过期后拒绝生成抓取计划。仿真验收通过 77 条 Python 回归和 43 条定向 DSH 测试。另单独复跑了 5 条 S0 与运动交接回归，均通过。

## 目标移位监控配对试验

同一组种子 0–4 各跑四种条件。每轮把黑盒搬到左盘。扰动组在机械臂接近途中将黑盒移动约 90 mm。唯一开关是目标移位检测，四组都保留 S0 的停止和授权检查。恢复由同一个脚本重新观察并最多重试一次，需要恢复授权时等 S0 报告停稳后授予。各组 S2 调用、额外人工介入和未知结果均为 0。扰动由试验脚本注入。

| 条件 | 首次抓取成功 | 重试后任务成功 | 最终任务成功（Ego 与 World 一致） | 任务耗时中位数（仿真秒） |
| --- | --- | --- | --- | --- |
| 正常，关闭目标移位检测 | 5/5 | 无需重试 | 5/5 | 12.8 |
| 正常，开启目标移位检测 | 5/5 | 无需重试 | 5/5 | 12.8 |
| 移位，关闭目标移位检测 | 0/5，继续尝试后失败 | 5/5 | 5/5 | 16.2 |
| 移位，开启目标移位检测 | 0/5，由 S0 保持中断 | 5/5 | 5/5 | 13.1 |

开启检测时，5 次扰动全部触发保持，正常组没有触发移位事件。从扰动到下达保持请求用时 0.4–0.5 仿真秒，从请求到采样读到停稳约 0.3 仿真秒。这个对比验证了工作台上的提前停止和耗时收益。各组的最终成功率均为 5/5。[配对试验脚本](../../steward/scripts/piper_sweep.py)

配置好 PiPER 资产后，在 `steward/` 运行以下命令。

```bash
python scripts/piper_sweep.py --seeds 0 1 2 3 4 --out var/validation/piper-sweep.jsonl
```

## 实施路径与代码位置

| 顺序 | 改动 | 代码落点 | 仿真验收 |
| --- | --- | --- | --- |
| 1 | **把基线实验跑成可比较的回合。** 通过 Ego Harness 运行自然语言任务。分别记录用户改口或扰动时刻、模型响应、技能状态、停稳证据和最终落点。保留已有脚本搬运作为基础动作基线。 | 复用 World 侧物体编辑、Ego 会话日志和多种子回归的组织方式。[世界服务](../../steward/src/steward/world/server.py#L587) · [会话入口](../../steward/src/steward/world/chat.py#L358) · [种子回归](../../steward/scripts/seed_sweep.py) | 原版跑正常搬运、抓取前移动目标、运行中改目的地。记录现有技能和反思已能处理的情形。 |
| 2 | **接通中途改口和事件唤醒。** 控制台在忙碌时默认拒绝普通新消息，`assign()` 则先停止并重置会话。增加显式的“修订当前任务”入口，保留当前任务和已确认的进展。监控器从 Ego-State、作业和用户事件取证，把值得复查的事件交给 Ego Harness。首轮从改口及作业终态或超时开始，再根据试验补物体移动等检测。 | 扩展控制台入口和 Ego 会话接收侧，Runtime 发布机器人可用的事件。复用既有工具结果和反思插件。[会话的发送与取消](../../steward/src/steward/world/chat.py#L358) · [任务重新派发](../../steward/src/steward/world/chat.py#L434) · [反思插件](../../steward/dsh/packages/dsh-ego-reflection/src/index.ts) | 模型仍在等待、技能仍在运行时各改口一次。旧回复无法派发动作，已完成且仍有效的抓取可以保留。 |
| 3 | **S0 提供本体控制和停稳确认。** 基线已有位置目标、执行器限位、夹爪控制及本体反馈。增加后端本地的停止与保持、运动授权校验和停稳确认。保护请求可在模型阻塞时直接到达 S0。 | 从机器人侧 `/command`、本体反馈与 `WorldSim` 控制入口开始。在 PiPER 中由适配器转发给本地控制端，运动目标只有经过控制端检查才能生效。[机器人接口](../../steward/src/steward/world/server.py#L662) · [执行器控制](../../steward/src/steward/world/sim.py#L669) · [本体反馈](../../steward/src/steward/world/sim.py#L1078) | 在模型阻塞、机械臂仍运动时请求停止。检查旧目标失效、本体反馈确认停稳，以及反馈中断或仿真暂停时是否正确报告未知。 |
| 4 | **Ego Runtime 与 S1 管理取消和控制权交接。** 在任务修订或保护触发时取消冲突 Job，串行处理旧授权撤销和新作业派发。分别记录停止请求、Job 终态和 S0 停稳反馈。按原作业 ID 核对迟到或丢失的结果。 | 扩展已有 owner 检查、回执消费和技能取消。现有停止逻辑设置技能停止标志，并请求保持底盘位姿。[控制权](../../steward/src/steward/ego/runtime.py#L1941) · [派发](../../steward/src/steward/ego/runtime.py#L2127) · [停止与 Job](../../steward/src/steward/ego/skills.py#L315) | 旧调用在停止后到达时无法启动动作。只有在旧目标失效、收到 S0 停稳确认且允许恢复后，才派发新 Job。 |
| 5 | **补齐任务验收及回放记录。** 关联用户意图、计划、预测回执、Job、证据帧和验收结论。已有技能后置检查继续提供证据。任务收尾单独核验用户当前目标。 | 复用 Job 的 `postcondition` 和既有 Ego-State 字段，记录受理、终止、物理效果和意图满足四项结论。World 侧按最终意图独立评分。[Job 结构](../../steward/src/steward/ego/skills.py#L160) · [Observer](../../steward/src/steward/observer/observer.py) | 区分命令正常但夹空、物体放进错误托盘、遮挡导致证据不足。三个回合的任务验收和世界评分均可复盘。 |
| 6 | **接 PiPER 构型、程序技能和世界侧接口。** 先检查 PiPER 仿真资产及接口。注册固定台面构型，适配机器人模型、相机、关节与夹爪、坐标变换、动作和仿真时钟。为固定机械臂实现基于本体约束的抓放预测与执行。将 World 侧重置、扰动和真值读取接到同一物理实例。 | 从现有构型注册、`RobotLink` 和 CLI 后端选择接入。当前 `RobotLink` 也承担仿真时钟协作、标定和遥测，接入范围由首版技能实际调用决定。现有 `cobot-magic` 适配提供参考，须另核对桌面 PiPER 的本体接口。若运行 PhyAgentOS 的后端，WCA 与 Observer 需要对应的世界侧桥接。[构型注册](../../steward/src/steward/embodiments.py) · [构型适配](../../steward/src/steward/ego/bodies.py) · [RobotLink](../../steward/src/steward/ego/runtime.py#L171) · [PiPER 部署](deployment.md) | 先走“World 移动目标 → Ego 相机重新定位 → Observer 核对真值”，再验收抓放、取消和停稳。 |

## S0 和 S1 的实现分界

- **S1 产生运动目标。** 程序技能根据 ESim 的计划和当前观测，给出本体目标，并维护 Job 进度。后续 VLA/WAM 也可通过同一边界提交兼容的动作目标。
- **S0 执行并反馈。** Steward 仿真的调用链是 `SkillExecutor → RobotSkillAdapter → RobotLink → /command → WorldSim.command()`。S0 检查运动授权，应用传动比和执行器目标限位，提供编码器和夹持状态。PiPER 的运动规划先把末端位姿换成六轴关节目标，S0 接收关节角（弧度）和夹爪开合指令。[技能适配器](architecture.md#s1-技能适配器) · [机器人端口](../../steward/src/steward/world/server.py#L670) · [执行器控制](../../steward/src/steward/world/sim.py#L685)
- **S0 保持并确认停稳。** `POST /motion/hold` 作废旧授权，并保持当前位置和夹爪目标。`GET /motion` 返回 `stopping`、`held` 或 `unknown` 等状态。仿真每个物理步采样速度，在阈值内持续 0.1 仿真秒才报告 `held`，暂停时返回 `unknown`。`POST /motion/resume` 核对本次保持授权及停稳反馈，再发放新授权。真机接入需要实现同一组接口并用设备反馈标定停稳条件。[停稳逻辑](../../steward/src/steward/world/sim.py#L728) · [独立于 Ego Runtime 的 S0 测试](../../steward/tests/test_motion_hold.py#L27)

基线在普通抓取前已经会尝试重新看目标，视位移微调计划或要求重规划。升级时复用这项检查，把它扩到同一任务内的冲突判断，并为遮挡或过期证据规定何时必须暂停补看。[执行前看目标](../../steward/src/steward/ego/runtime.py#L1844)

**PiPER 接入选择。** 本阶段将 PhyAgentOS 原生 MJCF 安装在 Steward 的仿真实例中，复用 WCA、Observer、时钟与 S0 停稳协议。Ego Runtime 注册固定工作台构型。S2 沿用相同工具，通过预测回执调用 `pick` 和 `place`。[部署设计](deployment.md) · [PiPER 规划](../../steward/src/steward/ego/piper_motion.py)
