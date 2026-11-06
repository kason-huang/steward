# Step 1：用 OpenPI 跑通原始 LIBERO 任务

日期：2026-11-06。状态：待 GPU 实测。本文是[实施计划第 1 步](../pi05-integration-plan.md#1-跑通原始-libero-任务)的操作方案，面向拥有 Linux NVIDIA GPU 机器的实施者。目标是在固定配置下，由真实 π0.5 完成“把指定物品放入篮子”的原始 LIBERO 任务，取得完整成功证据和固定批次成功率，并保存全部尝试记录。

本文命令参考 OpenPI 官方示例，尚未在目标 GPU 机器执行；运行验证由 Step 1 实施者完成。任务 ID、物品、初态和具体依赖版本在实测后填写，不能将本文视为已经通过的实验报告。

## 1. 固定技术路线

采用 OpenPI 官方实现、官方 `pi05_libero` 权重及其配套 LIBERO 子模块，优先使用官方 Docker Compose 同机运行策略服务与仿真客户端。Step 1 的策略服务在后续接入中继续作为候选，ER 调用方式见[接入设计](../pi05-integration-design.md)。

| 项目 | 选择 |
| --- | --- |
| 策略实现 | `Physical-Intelligence/openpi`，固定 commit |
| 策略配置 | `pi05_libero` |
| 权重地址 | `gs://openpi-assets/checkpoints/pi05_libero` |
| 仿真依赖 | 选定 OpenPI commit 所引用的 LIBERO 子模块及配套依赖 |
| 首选任务套件 | `libero_object` |
| 任务 | 从固定版本中查询“指定物品放入篮子”的原始任务，保留原始英文指令 |
| 运行资源 | Linux NVIDIA GPU，优先官方测试过的 Ubuntu 22.04 |
| 首选渲染 | EGL 离屏渲染 |

选择 OpenPI 是为了让原始基线、观测预处理及后续远程策略调用使用同一套实现。LeRobot 也支持 π0.5 和 LIBERO，但其公布的复现结果使用额外微调的权重；本步采用 OpenPI 以减少迁移时需要同时比较的差异。参考 [OpenPI LIBERO 示例](https://github.com/Physical-Intelligence/openpi/blob/main/examples/libero/README.md)、[远程推理说明](https://github.com/Physical-Intelligence/openpi/blob/main/docs/remote_inference.md)和 [LeRobot LIBERO 说明](https://huggingface.co/docs/lerobot/libero)。

本步只验证原始仿真与策略，不修改 Steward 的机器人、WSim、EH 工具或 ER 控制路径。先取得一次完整任务成功证据，再按实施计划第 1 步，在运行前冻结初态集合、Episode 数 N（N > 1）、种子、预算及计数规则，完成原始环境固定批次评测并报告成功次数、总次数和成功率。Steward 内的成功率验收在第 8 步完成，全量基准与跨任务能力留待后续。

## 2. 准备 GPU 机器与运行目录

在 GPU 机器安装 NVIDIA 驱动、Docker、Compose 插件和 NVIDIA Container Toolkit，并验证容器可以访问 GPU。具体安装按 [NVIDIA Container Toolkit 官方说明](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)执行。记录 GPU 型号、显存、驱动、操作系统、Docker 和 Compose 版本；为模型下载、构建缓存、录像和轨迹预留磁盘空间。

OpenPI 的通用推理要求列为显存大于 8 GB，这不是包含仿真渲染在内的总资源保证。实际显存占用、首次加载与推理耗时以本机记录为准，见 [OpenPI 环境要求](https://github.com/Physical-Intelligence/openpi#requirements)。

在 GPU 机器克隆独立的 OpenPI 工作目录：

```bash
git clone --recurse-submodules https://github.com/Physical-Intelligence/openpi.git
cd openpi
git rev-parse HEAD
git submodule status --recursive
```

将首次选定的 commit 写入基线记录，后续复跑检出该 commit 并执行 `git submodule update --init --recursive`。不要在两次尝试之间自动更新主分支或独立升级 LIBERO、MuJoCo、robosuite、JAX 等依赖。

Steward 仓库内新增的基线入口、配置和说明集中放在 `steward/` 下，具体文件名在实现时确定。大型权重、构建缓存和原始实验输出存放在 GPU 机器的数据目录，报告保存其位置与校验信息，不将凭据或权重提交进仓库。

## 3. 用官方示例试跑

以下命令在 OpenPI 仓库根目录执行，显式指定权重与 `libero_object`。首次先运行每个任务一个回合，确认模型加载、通信、渲染和动作执行；这轮试跑也属于需要保留的尝试。

```bash
export MUJOCO_GL=egl

export SERVER_ARGS="policy:checkpoint --policy.config pi05_libero --policy.dir gs://openpi-assets/checkpoints/pi05_libero"

export CLIENT_ARGS="--args.task-suite-name libero_object --args.num-trials-per-task 1 --args.seed 7"

docker compose -f examples/libero/compose.yml up --build
```

启动前保存展开后的 Compose 配置和完整环境参数，运行时持久保存服务端、客户端和构建日志。等待模型实际完成加载并接受推理请求，不能以容器启动作为策略可用的依据。首次下载的模型、归一化资产、配置和镜像标识都要进入基线记录。

官方 Compose 同时启动仿真与策略服务。运行方式、挂载和 GPU 配置以固定版本的 [compose.yml](https://github.com/Physical-Intelligence/openpi/blob/main/examples/libero/compose.yml)为准。无桌面的服务器先使用 EGL；若 EGL 初始化失败，先核对容器 GPU 访问和驱动。官方 GLX 备选路径需要可用的 X 服务，不能仅修改环境变量就假定能在无显示服务的机器上运行。

试跑可观察到两类结果：客户端推进 Episode 并输出成功统计；输出目录产生机器人执行录像。具体日志、录像路径和实际成功状态都由实施者记录。若发生异常或超时，保留日志和最后状态，不将其计作任务成功。

## 4. 固定一个任务与初态

官方当前示例遍历整个任务套件，未提供单任务 ID 参数；它按 Episode 索引读取官方初态。任务编号取决于固定版本及任务排序，不能直接假定某个编号对应目标物品。参考 [LIBERO 运行器](https://github.com/Physical-Intelligence/openpi/blob/main/examples/libero/main.py)。

在配套 LIBERO Python 环境查询任务清单，使用 `benchmark.get_benchmark_dict()["libero_object"]()` 创建套件，再通过 `get_task(task_id)` 读取任务名称、`language`、`problem_folder` 和 `bddl_file`。选择一条抓起指定物品并放入篮子的完整任务，保留原始指令，不改写成自定义任务。

实现 Steward 自有基线入口时，复用官方观测与执行过程，只补充任务选择和证据记录：

1. 显式接收任务 ID、任务排序配置、初态索引、seed、尝试编号和输出目录；验证任务与初态索引存在。
2. 从 `get_task_init_states(task_id)` 读取选定初态，保存初态数组及其校验值，按官方过程 reset、设置初态并等待物体稳定。
3. 每次尝试使用独立目录；文件名包含任务、初态和尝试编号，失败及异常也写入结果。
4. 保存策略实际输入、推理返回的动作段、实际执行的动作前缀、仿真步编号、终止原因和独立的基准成功信号。
5. 为两路策略图像保存可核对的样例，为完整 Episode 保存录像；录像生成失败时记录原因并补齐验收证据。

这些参数是待实现的基线入口要求，不是官方现有 CLI 参数。新增入口的实际命令、配置文件和改动差异必须在验收报告中给出。

官方当前录像命名包含任务指令及成功/失败后缀，但没有尝试编号；同任务同结果的多次尝试可能覆盖录像。补齐独立输出目录或唯一文件名后，再进行重复验收。不得只保留成功回合，也不得在失败后悄悄更换初态或人工挪动物体。

## 5. 核对策略输入、动作与控制

先保持固定版本的官方预处理、动作执行和等待过程，再采集后续 Steward 适配所需的规格。下面是已查阅示例中的参考值，实施者需逐项核对固定 commit，不把默认值当成已经测量的结果。

| 项目 | 官方示例参考及本步记录要求 |
| --- | --- |
| 外部相机 | `agentview_image`；记录资产中的相机安装与参数 |
| 腕部相机 | `robot0_eye_in_hand_image`；记录安装连杆与相机参数 |
| 图像处理 | 两路图像均旋转 180°，从 256 分辨率渲染结果保比例缩放并填充至 224×224，转为 uint8 |
| 本体状态 | 拼接末端位置、四元数转换的轴角和夹爪关节位置；示例策略输入为 8 维 |
| 语言目标 | 原始 `task.language` |
| 策略输出 | 核对实际返回维度、动作顺序、单位、参考系、限幅和夹爪符号 |
| 执行前缀 | 运行器默认 `replan_steps=5`，执行前缀后重新观测与推理 |
| 预测长度 | `pi05_libero` 配置为 `action_horizon=10`；与执行前缀分别记录 |
| 初始等待 | 示例为 10 个仿真控制步 |
| Object 步数预算 | 示例为 280 个任务控制步，另加初始等待 |
| 控制周期 | 从实际控制器与仿真配置读取；分别记录物理步长、控制周期和推理耗时 |

动作和状态规格参照固定版本的 [LIBERO 运行器](https://github.com/Physical-Intelligence/openpi/blob/main/examples/libero/main.py)、[策略输入输出适配](https://github.com/Physical-Intelligence/openpi/blob/main/src/openpi/policies/libero_policy.py)和[模型配置](https://github.com/Physical-Intelligence/openpi/blob/main/src/openpi/training/config.py)。不能将推理返回速度作为控制频率，也不能把整个预测段默认全部执行。

记录机器人、夹爪、控制器、关节顺序、初始姿态、资产来源和许可证。末端状态参考系与动作参考系要分别核对，图像旋转也应以策略实际输入为证据。

本步允许使用 LIBERO 隐藏成功信号做原始基准评估，但不能将它输入策略。后续 Steward 的机器人完成判断、关节测量与前向运动学要求由[接入设计](../pi05-integration-design.md)维护；原始示例直接读取末端状态，并不等于后续机器人侧适配已经完成。

## 6. 保存报告与验收

每次运行分配独立运行 ID。报告与证据目录按以下内容组织，文件名可在实现时固定：

| 记录 | 必须包含 |
| --- | --- |
| 环境与版本 | OS、GPU、显存、驱动、容器运行时、OpenPI commit、子模块 commit、依赖清单、镜像 ID 与构建日志 |
| 模型与配置 | checkpoint 地址、本地文件校验清单、归一化资产、配置标识、实际启动命令及环境参数 |
| 任务与初态 | 套件、排序配置、任务 ID、名称、原始指令、BDDL 文件校验值、seed、初态索引及保存文件 |
| 每次尝试 | 开始与结束时间、是否人工干预、成功/失败/异常/超时、控制步数、终止原因、推理耗时 |
| 轨迹与图像 | 输入观测、动作段、实际执行动作、仿真步编号、两路图像样例和独立录像 |
| 接入差距 | 本体、控制器、相机、状态与动作映射中影响 Step 2–6 的待处理项 |

在相同任务、初态和配置下复跑验证启动方式可重复使用，并保留所有结果；随机生成策略与 GPU 计算不保证每次获得完全相同轨迹。若改变模型、控制参数或初态，建立新的配置标识，分别报告结果。

全部满足以下条件后，才将实施计划第 1 步标为完成：

- [ ] 真实 `pi05_libero` 已加载，模型和配套环境版本可核对。
- [ ] 选定任务、原始指令、初态和完整启动方式已经固定。
- [ ] 至少一次完整抓取、搬运、放入篮子的任务成功，基准成功信号成立且录像可见完整过程。
- [ ] 原始环境固定批次已经完成，按预先冻结的初态、次数、预算与计数规则报告成功次数、总次数和成功率；保留全部失败与人工干预记录。
- [ ] 全部尝试、失败、异常、超时与人工干预记录保留，录像未被覆盖。
- [ ] 相机、状态、动作、控制周期、执行前缀和机器人资产规格足以开展后续适配。
- [ ] 实测报告区分原始基线成功与 Steward 接入验收；后者仍按[实施计划](../pi05-integration-plan.md)推进。

若始终失败，先用已保存的两路图像、原始动作与仿真日志定位模型加载、预处理、归一化、控制器或物理执行问题。保留每次修改与对应结果；不能用其他模型的成功记录替代 π0.5 基线，也不能在只有容器启动或单次推理成功时宣布本步完成。
