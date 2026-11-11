# Step 7 接通 EH 工具、策略 Job 与停止

2026-11-11。状态为已实施，N1、N2、C1、F1 及嵌套无凭据负例已有通过证据；整套回归仍有基线失败，详见 [Step 7 实测报告](step7-results/README.md)。对应[实施计划第 7 步](../pi05-integration-plan.md#7-接通-eh-工具与停止)，遵循[接入设计的调用链与能力分层](../pi05-integration-design.md#31-llm-能力程序与-π05-动作策略)。本步让操作员从 Steward 聊天提交字母汤罐任务，经真实 ego profile、`ctx.ego` 和 Ego API 创建 Panda 策略 Job，查看进度并通过现有入口停止。

## 1. 已有证据与本步范围

Step 6 在 4090-2 上使用真实 `pi05_libero` 跑通了初态 0 的抓起、搬运、释放和撤爪，三个完整成功回合包含一次复现脚本实跑。独立 WSim 记录篮底承托与稳定窗口，实际动作复放逐物理步相同。Step 6 的物理验收在独立试验脚本中使用世界侧证据，接入 EH 后，Job 结束依据需要由机器人观测提供。[Step 6 报告](step6-results/full-task/README.md) · [逐回合索引](step6-results/full-task/attempts.json)

首轮固定 `libero-object-alphabet-soup-panda`、初态 0、字母汤罐入篮任务、同版 OpenPI 与权重。沿用冻结推理时钟、20 Hz 控制、每条 25 个物理子步、每五条动作重新观测推理、最多 280 条动作和 420 s 墙钟的已验证配置。停止观察至少 500 步、最多 2500 步，速度与连续停稳要求沿用 Step 3。配置由 ER 装配读取并记录版本。实时模式、正式成功率和完整感知负例覆盖分别继续按 Step 6 待验收项及 Step 8 实施。

本步交付固定任务的预测、授权、执行、查询、停止及真实 profile 调用证据。正常样例需要最小的机器人侧完成判断才能验收完整调用链，完整抓放识别覆盖率和负例批次在 Step 8 交付。机器人看不清结局时返回未确认，世界侧报告保留其实际物理结果。

## 2. 现有接口与接入缺口

| 接入前定位 | 接入前行为 | Step 7 工作 |
| --- | --- | --- |
| [Panda ER](../../../../steward/src/steward/ego/panda_runtime.py) | 发布同步观测、ES、抓放判断和策略输入预览 | 增加任务预测与策略 Job，接入共享的作业、授权与停止语义 |
| [本体登记](../../../../steward/src/steward/embodiments.py)与[本体说明](../../../../steward/embodiments/libero-panda.yaml) | Panda 为 `skills: false`，说明文件仍缺固定外部视角和实际 ER 装配描述 | 同步两路 RGB-D、本体状态和执行栈声明，接通后按实际能力发布技能 |
| [CLI](../../../../steward/src/steward/cli.py)与[控制台装配](../../../../steward/src/steward/world/server.py) | 按 Scenario 启停独立 Panda ER，观测型本体的聊天任务会被拒绝 | 校验配置和技能就绪状态，完成准入后连接真实机器人聊天与 Ego API |
| [共享 Ego Runtime](../../../../steward/src/steward/ego/runtime.py)、[技能 Job](../../../../steward/src/steward/ego/skills.py)与[Ego API](../../../../steward/src/steward/ego/server.py) | 有 receipt、控制 owner、Job、等待与停止接口，执行器仍有旧本体运动假设 | 复用必要的 Job 生命周期与接口，Panda 执行后端使用专用观测和 OSC 端口 |
| [Ego 类型](../../../../steward/dsh/packages/dsh-ego/src/index.ts)、[HTTP 客户端](../../../../steward/dsh/packages/dsh-ego-runtime-client/src/index.ts)与[工具](../../../../steward/dsh/packages/dsh-tool-ego/src/index.ts) | 技能枚举只有六种已有技能，`jobView` 只投影基础字段 | 同步任务技能、目标结论、停止结果、原因和证据引用的类型、传输与展示 |
| [安全守卫](../../../../steward/dsh/packages/dsh-robot-safety/src/index.ts) | 检查 receipt 可行性、期限、使用状态和风险，`/estop` 文案收到响应即显示已停止 | 保留 EH 与 ER 双重校验，让 `/estop` 根据撤销与遥测确认结果报告状态 |
| [反思](../../../../steward/dsh/packages/dsh-ego-reflection/src/index.ts)与[能力库](../../../../steward/dsh/packages/dsh-tool-ego-capabilities/src/index.ts) | 读取 Job 状态，能力计数依据 `run_code` 是否报错 | 处理未确认、停止未知和结构化失败，能力程序遇到这些结果结束依赖步骤 |

Panda 保留自己的传感器、FK 和控制适配，共享层承载任务与作业语义。实现时以现有模块的职责为准提取必要逻辑，避免为一个固定任务建立通用机器人插件平台。

## 3. 固定任务与授权

已登记任务级技能 `libero_pick_place`。首轮只支持已验证的字母汤罐入篮任务，参数使用机器人 ES 的 `target` 与 `on` 引用。任务配置从这组引用生成固定原始 prompt，校验对象类别、关联和可见性。缺失、歧义、旧 Episode 引用及其他目标在预测或准入时拒绝。技能名和新增结果字段在实施时与所有消费者一并固定。

EH 通过 `ego_state`、`ego_query` 和必要的 `ego_observe` 获取对象，再调用 `ego_predict`。返回的 receipt 绑定 Episode、机器人、目标引用、策略与本体配置版本、观测和 ES 依据、允许操作区、预期接触、执行预算及提交期限。模型端点、权重、动作尺度和控制周期由部署配置确定。`skill_run` 只消费 receipt，目标或配置变化需要重新预测。

| Ego API 路由 | 语义 |
| --- | --- |
| `GET /ego/summary`、`GET /ego/query`、`POST /ego/observe` | 为现有工具提供 Panda 可用技能和机器人观测摘要，保留对象引用的时效与不确定性 |
| `POST /ego/predict`、`GET /ego/receipt?id=…` | 生成并复核固定任务的可行性、实际风险检查覆盖和授权条件 |
| `GET /ego/control`、`POST /ego/control` | 请求体使用 `action: acquire / release` 与 `owner`，复用逻辑 owner 的独占技能使用约定 |
| `POST /skill/run` | 在同一控制临界区复核授权、冲突与配置，取得 WSim 执行器租约并创建一个 Job |
| `GET /skill/status?job=…`、`POST /skill/wait` | 返回同一份 Job 记录，等待超时仍可返回运行态 |
| `POST /skill/stop` | 立即使本次执行资格失效，并报告控制撤销与本体停稳结果 |

上表沿用共享 Ego API 的路由语义，Panda 当前只实现第 2 节所列观测入口。逻辑 owner、任务 receipt、世界时钟占用和 WSim 执行器租约分别校验。冻结仿真时仍需有单调墙钟提交期限，沿用 receipt 的仿真期限字段时明确两种时钟的检查方式。执行器租约绑定 Episode 与控制代次，客户端的 owner 名称只表示技能使用归属。

`skill_run` 的校验、receipt 消费、Job 发布与租约申请需要一致处理。并发提交同一 receipt 最多创建一个 Job，冲突请求不产生额外动作。取得部分资源后失败要撤销和释放，返回可核实的状态。启动响应丢失时，通过 receipt 绑定的 Job 查询已接受结果，重发不能创建第二次抓放。ER 重启和 Episode 切换后，旧 Job、receipt 和请求标识失效，旧查询不能读到另一轮同名 Job。

## 4. Job、动作检查与结束语义

将 Step 6 的取样、推理、前缀检查、按回执执行和重观测循环放到 ER 的策略执行后端。外部验证脚本保留为实验员客户端。每个 Job 最多一个在途推理请求，关联 Job、Episode、控制代次、请求编号、观测步和截止时间。等待推理时仍能处理停止、状态查询和独立租约续租，所有出口释放时钟占用与控制资源。

任务预测与每段动作检查都使用机器人 ES、标定和本体反馈。Step 6 已有的数值、工作区和观测门禁需要接到 receipt，并补足 Panda 自身运动学、关节限位、候选动作扫掠、可观测障碍和预期接触区的检查。receipt 公开实际覆盖的风险与证据，风险数值需要可解释。场景遮挡或当前 ES 无法支撑检查时拒绝执行。该项通过后再开放真实 EH 运动准入。[动态动作检查要求](../pi05-integration-design.md#7-动态动作如何经过安全检查)

拟为策略 Job 增加 `goalStatus`、`stopStatus`、`reasonCode`、`evidence` 和运行阶段，名称在实施时固定。保留现有 Job 的 `running / succeeded / failed / stopped` 四种状态。目标结论和停止结论在 HTTP、`ctx.ego`、工具投影、同步等待、异步查询、反思与会话记录中一致传递。

| 情况 | Job 与目标结果 | 对后续程序的约束 |
| --- | --- | --- |
| 机器人连续观测确认已释放并稳定置于篮内，停止与清理已确认 | `succeeded`、`achieved` | 允许能力程序判断是否推进依赖步骤 |
| 执行预算结束或结果不可见 | `failed`，按已有证据返回 `not_achieved` 或 `unconfirmed` | 结束依赖步骤，保留观测与原因 |
| 推理、动作检查、配置或观测失败 | `failed`，保留最后目标判断 | 撤销控制，报告原始故障和停止结果 |
| 操作员停止且停稳已确认 | `stopped`，保留停止前目标判断 | 结束当前任务 |
| 撤销或停稳未确认 | `failed`、停止为 `unknown` | 锁住后续执行准入，完成明确的状态复核后才能重新预测 |

正常样例的最小完成依据来自 Panda 两路观测、指间距和 ES 目标关联。需要在释放后连续新帧中确认汤罐位于篮内，且关联与可见性足够。手臂到达目标附近、动作序列结束或夹爪张开都只能作为局部证据。世界侧篮底接触与真值速度独立保存，供实验员复核物理成功。Ego API、EH 会话和能力程序的停止或重试条件均使用机器人侧证据。Step 8 再核验完成判断的完整覆盖率和误判。

候选结果失败、取消、未确认或停止未知时，能力程序抛出结构化失败并结束依赖工作。终态记录固定，后来取得的新观测可以支持一次新请求。能力库的 `verified` 计数、模型会话中的工具成功和世界侧物理成功分别记录，返回 `failed` 的工具调用不能因 JSON 正常返回而被能力程序当成任务完成。

## 5. 停止与时钟

停止按以下顺序实施和取证。

1. 在 ER 控制临界区标记取消、失效在途请求并清空剩余候选，向 WSim 撤销当前代次。
2. WSim 确认清除待应用动作并保持本体。ER 记录最后已应用与已完成动作，之后收到的旧响应一律失效。
3. 按仿真周期采集本体遥测，使用 Step 3 的阈值和连续窗口判定停稳，最多推进 2500 个物理步。网络与停止请求另有单调墙钟截止时间，暂停仿真期间也能超时。
4. 合并原始结束原因、控制撤销和物理停稳的独立结果。确认清理后发布终态，停止未知保留执行准入锁。ER 重启后依据执行器状态重新核实该锁，完成明确的恢复确认后才能开放新请求。

`body_stop`、`/estop`、控制台停止、Job 取消、推理故障、租约失效及 ER 退出走同一停止流程。收到停止请求与本体已停稳分别可见。修改 `/estop` 的提示和工具输出，超时或通信中断时准确报告未知状态。

`skill_run(wait=false)` 快速返回 Job。`skill_status` 在推理中和停止处理中可调用。`skill_run` 的同步等待截止时间与 Job 执行预算独立，等待结束仍为 `running` 时允许继续查询同一个 Job。取消工具等待是否同时停止 Job 必须在接口中明确，操作员取消路径应进入统一停止流程。420 s 执行预算需要与 PTC 的当前默认 300 s、最大 600 s 及会话等待限制协调，计划验收优先采用异步启动和有界查询。

世界时钟 hold 使用每个 Job 独立的名称及有效期，策略调用阻塞时由独立路径续租。完成、取消、异常、Episode 重置、场景切换与进程退出均释放占用。失去时钟续租时使当前结果失效并停止。世界侧执行器租约失联停止继续生效，ER 不能因 HTTP 失败自行宣称本体已停止。

## 6. 实施顺序与前置门禁

| 阶段 | 工作 | 进入下一阶段的证据 |
| --- | --- | --- |
| 7A 固定配置与接口 | 记录服务启动和预热流程，补齐本体说明，固定技能与结果字段，核对共享 Job 可复用部分 | 配置校验、接口清单、结果字段的所有消费者及测试位置明确 |
| 7B 接通 ER 和 Ego API | 实现任务 receipt、独占 Job、异步查询、状态传播、停止与清理，接入 Panda 前缀预测 | 可控策略替身经真实 HTTP 和工具守卫通过准入、并发、失败与停止测试，Panda 风险检查达到第 4 节要求 |
| 7C 接通真实 ego profile | 扩展技能枚举和说明、`jobView`、反思与 `/estop`，接入聊天和 `run_code` 能力程序 | 实际组合配置确认工具与权限，嵌套调用受到同样守卫，失败和未确认终止依赖步骤 |
| 7D 真实 GPU 实测 | 在独立初态 0 回合经聊天、真实 profile 和 ER 调用真实权重 | 完整任务、取消与推理故障均有 Job、动作回执、停止、双机位录像和独立物理记录 |

Step 6 的长期运行 18065 服务曾在相同原始输入上出现与基线明显不同的输出，新加载同版 18066 服务后完成抓放。7A 固定本次验证服务的配置、启动身份、健康检查与一次预热，使用 Step 6 的已验证流程复核完整任务；端口号只作为连接配置。服务差异继续作为独立问题追踪。更换服务或发生加载异常时重新验证，禁止将单次张量维度正确当成任务可用的依据。[Step 6 服务差异记录](step6-results/full-task/README.md)

真实 EH 开放需要第 4 节的任务级授权与动作预测检查。若当前 ES 或本体预测不足，保持该配置预测不可行并补齐缺口。可控替身用于验证调度和故障，真实 GPU 任务使用固定权重。所有注入方式、人工操作和配置变化单独记录。

## 7. 验收矩阵与完成条件

先以可控策略响应和真实 Panda HTTP 控制通道验证下表，再经真实 ego profile 验证工具调用与结果传播。每项同时检查返回值和 WSim 实际动作应用记录。

| 用例 | 必须观察到的结果 |
| --- | --- |
| 不支持任务、对象缺失、旧 ES 引用、配置不匹配 | 预测不可行或准入拒绝，WSim 无应用动作 |
| receipt 缺失、伪造、过期、已使用、旧 Episode 或 owner 冲突 | EH 守卫与 ER 各自拒绝，直接 HTTP 绕过 EH 仍被拒绝 |
| 同一 receipt 并发提交、冲突 Job、启动响应丢失后重发 | 最多一个可核实的 Job，无重复抓放 |
| 短等待超时与异步查询 | 返回运行态后可继续查询同一 Job，终态字段保持一致 |
| 无效动作、NaN、维度错误、越界或前缀预测拒绝 | 拒绝相关前缀，保留原因，执行停止与清理 |
| 缺帧、旧观测、Episode 重置、场景切换或时钟租约失效 | 旧结果与旧动作失效，释放相应资源 |
| 等待推理时取消、动作执行中取消、迟到响应 | 停止请求及时处理，撤销后无新旧动作应用，报告最后确认状态 |
| 策略断连、超时、ER 退出及机器人通信中断 | 保留原始故障，执行器侧失联停止生效，未确认时锁住后续准入 |
| 停止请求成功但停稳观察超时 | `/estop`、工具与 Job 均报告停止未知，后续 `skill_run` 被拒 |
| 真实 profile 的嵌套 `run_code` 无凭据调用 | 守卫生效，无法借能力程序绕过授权 |
| 能力程序收到失败、未确认或停止未知 | 不执行依赖动作，失败进入会话与能力运行记录 |
| 信息隔离与旧本体回归 | 机器人 profile 无 WCA、完整场景或通用计算机工具访问，新结果字段兼容旧本体调用 |

7D 在运行前固定四个独立回合，均从初态 0 复位，使用相同已验证配置。N1 从控制台聊天发起正常抓放，N2 通过真实 profile 获取并执行保存的任务能力，两者均经过嵌套 `run_code`、工具守卫和真实 π0.5。C1 在真实策略动作执行期间由操作员调用停止入口。F1 在执行期间注入一次策略服务超时或断连。额外重试另建目录与 Episode，保留原失败。

N1、N2 要求同一 Episode 中 Job 为 `succeeded`、机器人目标结论为 `achieved`、停止已确认，世界侧独立确认撤爪后篮底稳定承托。机器人未确认而世界侧成功时保留两侧结果，本项功能验收待解决。C1、F1 要求结束原因正确、撤销后无迟到动作、控制和时钟资源清理可核实，停止未知的故障注入另验后续准入被锁住。四回合用于验证调用链和故障处置，正式成功率按 Step 8 固定批次测量。

## 8. 命令、产物与验收状态

2026-11-11 已在 4090-2 的 `hqk-dev` 工作区实施。真实聊天、安装后的 `ego` profile、嵌套 `run_code`、Ego API、Panda ER 和真实 `pi05_libero` 已贯通。全部 17 次尝试（含失败和 API 调试）均列在 [批次索引](step7-results/batch-results.json)，四个目标用例的最终证据为 N1-06、N2-03、C1-03、F1-02。此处的功能通过不代表未经重试的成功率。

| 项目 | 验收状态与证据 |
| --- | --- |
| ER 策略 Job、receipt、owner、异步查询与停止 | 通过真实 HTTP 回归，停止未知会锁住准入 |
| Panda FK、关节范围、扫掠与可观测区域检查 | 已接入；深度 MSAA 边缘失真已修复并保留原始拒绝样本。检查采用标定几何和局部扫掠近似，覆盖范围见 receipt 与主报告 |
| 最小机器人完成判断 | 正常回合由机器人两路观测确认，世界侧独立确认篮底承托；全量识别负例留至 Step 8 |
| N1 聊天发起并保存能力 | N1-06 通过：150 条完成动作，succeeded / achieved / confirmed，保存能力 v2 |
| N2 同版能力复用 | N2-03 通过：获取并执行同一 v2，150 条完成动作，独立物理成功 |
| C1 动作中停止 | C1-03 通过：第 26 条动作的 10/25 子步处撤销；stopped / unconfirmed / confirmed；撤销后无迟到动作 |
| F1 策略断连 | F1-02 通过：50 条完成动作后断连，推理墙钟超时；failed / unconfirmed / confirmed |
| 嵌套 run_code 无凭据调用 | G1-01 通过：真实 profile 拒绝，无 Job、无动作 |
| 进程退出、Episode 重置与异常清理 | 已做独立回归；暂停时停止有界、续租原始原因、主/辅助线程启动失败和未知停止恢复均有回归记录 |
| 旧本体与 DSH 全套回归 | 有保留：旧本体 look_around 转向失败在本步前的源码也可复现；6 个已有快照缺少 llm-replay 依赖，不能标为全部通过 |
| 报告、命令、日志、逐回合动作和双机位录像 | 已归档至 step7-results；原始 RGB-D/PNG 留在 var/step7 并提供软链接 |

固定配置和一次原始输入预热已封装到以下脚本；每个新目录独立初态 0，使用新加载的专用模型服务 18067。N2 应在 N1 成功保存能力后运行。脚本读取现有隔离 profile；HarnessChat 为每个新回合重新加载 `steward/.env`，报告不保存 key。

```bash
ssh rtx-4090-2
cd /home/huawei/hqk/workspace/steward/steward
export PATH="$PWD/var/dsh-toolchain/bin:$PWD/var/dsh-toolchain/node/bin:$PATH"
STEWARD_DSH_VAR="$PWD/var/step7/dsh" bash scripts/setup-dsh-profiles.sh
bash scripts/run_libero_step7.sh N1 my-N1-01
bash scripts/run_libero_step7.sh N2 my-N2-01
bash scripts/run_libero_step7.sh C1 my-C1-01
bash scripts/run_libero_step7.sh F1 my-F1-01
```

日常控制台启动需要显式策略配置；未加载配置时 Panda 保持观测模式。

```bash
cd /home/huawei/hqk/workspace/steward/steward
MUJOCO_GL=egl OPENBLAS_NUM_THREADS=1 .venv/bin/python -m steward.cli \
  --root var/step4/wsim-root up --scenario libero-object-alphabet-soup-panda \
  --panda-policy-config var/step7/policy.toml --freeze-idle --speed max --no-dispatch-task \
  --wca-port 0 --robot-port 0 --api-port 0 \
  --robot-harness "$PWD/var/step7/dsh/robot-1" --world-harness "$PWD/var/step7/dsh/world"
```

[主报告](step7-results/README.md)包含完整启动条件、测试结果、失败原因和原始目录。[HTML 报告](step7-results/index.html)可以直接查看分类结果和双机位录像。录像为仿真时间，冻结推理等待不计入播放时长。所有回合保留 receipt、Job、候选动作、应用/完成回执、停止遥测、原始 EH 会话和独立世界记录。能力库的程序层计数与实际物理回合分开说明。
