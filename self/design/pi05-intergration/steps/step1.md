# Step 1：用 OpenPI 跑通原始 LIBERO 任务

操作方案日期为 2026-11-09，实测日期为 2026-11-10（北京时间）。**状态：原始 LIBERO 基线验收通过。** 本步对应[实施计划第 1 步](../pi05-integration-plan.md#1-跑通原始-libero-任务)。

在 `rtx-4090-2` 上使用 OpenPI 的非 Docker 双 Python 环境和 EGL，运行官方 `pi05_libero`。官方 `libero_object` 试跑 10/10 成功。目标任务为字母汤罐入篮，任务 ID 0、排序 0。资格回合在第 130 个策略控制步收到 LIBERO 成功信号。固定正式批次使用种子 7、初态 0–4，**5/5 成功（100%）**，无人工干预。

- [实测报告及录像索引](step1-results/README.md)
- [复现手册](step1-results/REPRODUCE.md)
- [字母汤罐入篮录像](step1-results/runs/20261110T021656Z/target-qualification/episode.mp4)
- [正式批次结果](step1-results/runs/20261110T021656Z-batch/summary.json)

原始日志、逐次推理输入和动作轨迹保存在远端 `/home/huawei/hqk/workspace/pi05-step1`。验收运行 ID 为 `20261110T021656Z`。

在已配置的 `4090-2` 上，登录后执行：

```bash
ssh rtx-4090-2
cd /home/huawei/hqk/workspace/pi05-step1
bash run-validation.sh
```

## 1. 固定技术路线

采用 OpenPI 官方实现、官方 `pi05_libero` 权重及其配套 LIBERO 子模块，官方 Docker Compose 是备选运行方式。4090-2 实测使用同机的非 Docker 双 Python 环境。Step 1 的策略服务在后续接入中继续作为候选，ER 调用方式见[接入设计](../pi05-integration-design.md)。

### 1.1 模型、机器人与任务选型

以下选型依据 OpenPI 与 LIBERO 当前公开源码核对，运行验证由 Step 1 实施者完成。固定 OpenPI commit 后，使用其 `third_party/libero` 子模块版本，并核对表中配置与实际加载结果。

| 项目 | 采用的配置 | 来源 |
| --- | --- | --- |
| 策略模型 | Physical Intelligence π0.5，LIBERO 微调 checkpoint，官方结果表标为 `π0.5 @ 30k (finetuned)` | [官方结果与权重](https://github.com/Physical-Intelligence/openpi/blob/main/examples/libero/README.md#results) |
| 推理实现与格式 | OpenPI，配置 `pi05_libero`，GCS 原始 JAX checkpoint，包含 `params/` 和 `assets/` | [模型配置](https://github.com/Physical-Intelligence/openpi/blob/main/src/openpi/training/config.py) |
| 机器人型号 | Franka Emika Panda，7 个机械臂关节，单臂 | [Panda 模型定义](https://github.com/ARISE-Initiative/robosuite/blob/v1.4.1/robosuite/models/robots/manipulators/panda_robot.py) |
| LIBERO 本体实现 | `OnTheGroundPanda`，沿用 Panda MJCF，`default_mount=None`，使用其配套初始关节姿态 | [本体定义](https://github.com/Lifelong-Robot-Learning/LIBERO/blob/master/libero/libero/envs/robots/on_the_ground_panda.py) |
| 夹爪 | `PandaGripper`，双指平行夹爪，1 维开合命令驱动两个手指关节 | [夹爪实现](https://github.com/ARISE-Initiative/robosuite/blob/v1.4.1/robosuite/models/grippers/panda_gripper.py) |
| 控制器与动作 | `OSC_POSE`，6 维末端位姿增量和 1 维夹爪命令，环境控制频率 20 Hz | [环境默认配置](https://github.com/Lifelong-Robot-Learning/LIBERO/blob/master/libero/libero/envs/env_wrapper.py)、[控制器配置](https://github.com/ARISE-Initiative/robosuite/blob/v1.4.1/robosuite/controllers/config/osc_pose.json) |
| 相机与推理节奏 | `agentview` 和 `robot0_eye_in_hand`，渲染 256×256，输入 224×224，预测 10 步、执行前 5 步后重新观测 | [官方运行器](https://github.com/Physical-Intelligence/openpi/blob/main/examples/libero/main.py)、[模型配置](https://github.com/Physical-Intelligence/openpi/blob/main/src/openpi/training/config.py) |
| 首期任务 | `libero_object/pick_up_the_alphabet_soup_and_place_it_in_the_basket.bddl`，目标物 `alphabet_soup_1`，容器 `basket_1` | [任务定义](https://github.com/Lifelong-Robot-Learning/LIBERO/blob/master/libero/libero/bddl_files/libero_object/pick_up_the_alphabet_soup_and_place_it_in_the_basket.bddl) |
| 场景实现 | `LIBERO_Floor_Manipulation`，`floor` 场景，`scenes/libero_floor_base_style.xml`，由任务实现将 `Panda` 装配为 `OnTheGroundPanda` | [场景与本体装配](https://github.com/Lifelong-Robot-Learning/LIBERO/blob/master/libero/libero/envs/problems/libero_floor_manipulation.py) |
| 初态与成功判据 | 配套 `.pruned_init` 初态集合，任务目标为字母汤罐位于篮子包含区域内 | [初态文件](https://github.com/Lifelong-Robot-Learning/LIBERO/blob/master/libero/libero/init_files/libero_object/pick_up_the_alphabet_soup_and_place_it_in_the_basket.pruned_init)、[任务目标](https://github.com/Lifelong-Robot-Learning/LIBERO/blob/master/libero/libero/bddl_files/libero_object/pick_up_the_alphabet_soup_and_place_it_in_the_basket.bddl) |

按任务名称查找数字 ID，再从该任务的 `task.language` 读取原始策略指令。任务排序和编号以固定版本的 benchmark 为准，记录 `task_order_index` 与初态索引。上述场景的地面布局、本体基座、物体及干扰物在实施计划第 2、3 步一同复刻，策略输入和动作规格见[核对策略输入、动作与控制](#5-核对策略输入动作与控制)。

本步只验证原始仿真与策略，不修改 Steward 的机器人、WSim、EH 工具或 ER 控制路径。先取得一次完整任务成功证据，再按实施计划第 1 步，在运行前冻结初态集合、Episode 数 N（N > 1）、种子、预算及计数规则，完成原始环境固定批次评测并报告成功次数、总次数和成功率。Steward 内的成功率验收在第 8 步完成，全量基准与跨任务能力留待后续。

### 1.2 权重、机器人资产与下载入口

原始基线使用 OpenPI 的 `pi05_libero` 配置与 `gs://openpi-assets/checkpoints/pi05_libero/` 权重目录。模型参数和 `assets/physical-intelligence/libero/norm_stats.json` 一起保留，下载时维持目录结构。官方成绩及对应权重见 [LIBERO Results](https://github.com/Physical-Intelligence/openpi/blob/main/examples/libero/README.md#results)。

| 下载内容 | 地址或安装后的相对路径 | 获取方式 |
| --- | --- | --- |
| π0.5-LIBERO 完整权重 | `gs://openpi-assets/checkpoints/pi05_libero/` | 第 3 节策略服务首次启动时下载，原始目录约 12.44 GB |
| OpenPI 代码与 LIBERO 子模块 | [OpenPI 仓库](https://github.com/Physical-Intelligence/openpi)、[LIBERO 仓库](https://github.com/Lifelong-Robot-Learning/LIBERO) | 第 2 节递归克隆命令获取代码、任务、初态和 LIBERO 场景资产 |
| Panda 机械臂 MJCF 与配套网格 | robosuite 的 `robosuite/models/assets/robots/panda/`，入口为 `robot.xml` | 随官方 LIBERO 环境依赖安装 robosuite，完整资产见 [Panda 资产目录](https://github.com/ARISE-Initiative/robosuite/tree/v1.4.1/robosuite/models/assets/robots/panda) |
| Panda 夹爪 MJCF 与配套资产 | robosuite 的 `robosuite/models/assets/grippers/panda_gripper.xml` 及其引用资源 | 随 robosuite 一起安装，见[夹爪资产目录](https://github.com/ARISE-Initiative/robosuite/tree/v1.4.1/robosuite/models/assets/grippers) |
| LIBERO 场景、物体、网格和纹理 | `third_party/libero/libero/libero/assets/` | 随子模块获取，见 [LIBERO 资产目录](https://github.com/Lifelong-Robot-Learning/LIBERO/tree/master/libero/libero/assets) |
| 任务和初态 | `third_party/libero/libero/libero/bddl_files/libero_object/` 与 `init_files/libero_object/` 下的同名 `.bddl` 和 `.pruned_init` | 随子模块获取，使用第 1.1 节的任务与初态文件 |

当前 OpenPI 示例依赖锁定 `robosuite==1.4.1`、`mujoco==3.2.3`，安装以所固定 OpenPI commit 的依赖文件为准。迁入 Steward 时保留 MJCF 引用的网格、纹理、材质、夹爪和场景资源相对路径，并记录来源版本及许可证。安装依据见[官方依赖文件](https://github.com/Physical-Intelligence/openpi/blob/main/examples/libero/requirements.txt)。

| 入口 | 用途 |
| --- | --- |
| [OpenPI 环境要求与安装](https://github.com/Physical-Intelligence/openpi#requirements) | 准备 Linux NVIDIA GPU 环境，官方测试系统为 Ubuntu 22.04，推理显存要求大于 8 GB |
| [官方 LIBERO 执行说明](https://github.com/Physical-Intelligence/openpi/blob/main/examples/libero/README.md) | Docker 与非 Docker 两种运行方式 |
| [Docker Compose 配置](https://github.com/Physical-Intelligence/openpi/blob/main/examples/libero/compose.yml) | 核对 GPU、缓存挂载、服务和渲染配置 |
| [LIBERO 客户端](https://github.com/Physical-Intelligence/openpi/blob/main/examples/libero/main.py) | 核对任务套件、回合数、初态、观测、动作与录像输出 |
| [远程推理说明](https://github.com/Physical-Intelligence/openpi/blob/main/docs/remote_inference.md) | 后续从 ER 通过 WebSocket 调用策略服务 |

Hugging Face 可下载 [LeRobot 基础版本](https://huggingface.co/lerobot/pi05_libero_base)和[额外微调的评测版本](https://huggingface.co/lerobot/pi05_libero_finetuned_v044)。两者使用 LeRobot 格式，替换本计划中的 OpenPI 权重前需验证转换、预处理和归一化配置。额外微调与评测口径见 [LeRobot π0.5 说明](https://huggingface.co/docs/lerobot/pi05#performance-results)。

## 2. 准备 GPU 机器与运行目录

在 GPU 机器安装 NVIDIA 驱动。若采用 Docker 路线，安装 Docker、Compose 插件和 NVIDIA Container Toolkit，并验证容器可以访问 GPU。具体安装按 [NVIDIA Container Toolkit 官方说明](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)执行。记录 GPU 型号、显存、驱动、操作系统、Docker 和 Compose 版本；为模型下载、构建缓存、录像和轨迹预留磁盘空间。

OpenPI 的通用推理要求列为显存大于 8 GB，这不是包含仿真渲染在内的总资源保证。实际显存占用、首次加载与推理耗时以本机记录为准，见 [OpenPI 环境要求](https://github.com/Physical-Intelligence/openpi#requirements)。

在 GPU 机器克隆独立的 OpenPI 工作目录：

```bash
git clone --recurse-submodules https://github.com/Physical-Intelligence/openpi.git
cd openpi
git submodule update --init --recursive
git rev-parse HEAD
git submodule status --recursive
```

将首次选定的 commit 写入基线记录，后续复跑检出该 commit 并执行 `git submodule update --init --recursive`。不要在两次尝试之间自动更新主分支或独立升级 LIBERO、MuJoCo、robosuite、JAX 等依赖。

Steward 仓库内新增的基线入口、配置和说明集中放在 `steward/` 下，具体文件名在实现时确定。大型权重、构建缓存和原始实验输出存放在 GPU 机器的数据目录，报告保存其位置与校验信息，不将凭据或权重提交进仓库。

### 2.1 下载访问验证

2026-11-09 在当前 Mac 上通过 GCS HTTPS 接口匿名列举到 16 个文件，总计 12,439,085,481 字节，约 12.44 GB。完整下载 `norm_stats.json` 返回 HTTP 200，JSON 解析成功。抽样下载一个大型参数文件的前 1 MiB 返回 HTTP 206，用时约 1.65 秒。随后在 4090-2 下载并校验了全部 16 个权重文件，并完成 GPU 加载和评测，见[实测报告](step1-results/README.md)。

下面的命令可在目标 GPU 机器复查元数据文件访问，成功时保存 JSON 并显示 HTTP 200。完整模型的下载由第 3 节的策略服务启动过程触发，需为权重、镜像及实验输出预留磁盘空间。

```bash
curl -fLsS --connect-timeout 15 --max-time 30 \
  'https://storage.googleapis.com/openpi-assets/checkpoints/pi05_libero/assets/physical-intelligence/libero/norm_stats.json' \
  -o /tmp/pi05-libero-norm-stats.json \
  -w 'HTTP %{http_code}, %{size_download} bytes\n'
```

## 3. 用官方示例试跑

以下是官方 Docker 试跑参考。在 OpenPI 仓库根目录执行时，显式指定权重与 `libero_object`。4090-2 实测命令见[复现手册](step1-results/REPRODUCE.md)。首次先运行每个任务一个回合，确认模型加载、通信、渲染和动作执行；这轮试跑也属于需要保留的尝试。

```bash
export MUJOCO_GL=egl

export SERVER_ARGS="policy:checkpoint --policy.config pi05_libero --policy.dir gs://openpi-assets/checkpoints/pi05_libero"

export CLIENT_ARGS="--args.task-suite-name libero_object --args.num-trials-per-task 1 --args.seed 7"

docker compose -f examples/libero/compose.yml up --build
```

启动前保存展开后的 Compose 配置和完整环境参数，运行时持久保存服务端、客户端和构建日志。等待模型实际完成加载并接受推理请求，不能以容器启动作为策略可用的依据。首次下载的模型、归一化资产、配置和镜像标识都要进入基线记录。

官方 Compose 同时启动仿真与策略服务。默认模型缓存为 `~/.cache/openpi`，可通过 `OPENPI_DATA_HOME` 指定缓存目录。运行方式、挂载和 GPU 配置以固定版本的 [compose.yml](https://github.com/Physical-Intelligence/openpi/blob/main/examples/libero/compose.yml)为准。无桌面的服务器先使用 EGL；若 EGL 初始化失败，先核对容器 GPU 访问和驱动。官方 GLX 备选路径需要可用的 X 服务，不能仅修改环境变量就假定能在无显示服务的机器上运行。

试跑可观察到两类结果：客户端推进 Episode 并输出成功统计；输出目录产生机器人执行录像。具体日志、录像路径和实际成功状态都由实施者记录。若发生异常或超时，保留日志和最后状态，不将其计作任务成功。

### 3.1 独立策略服务

后续将策略服务独立部署到 GPU 机器时，在完成 OpenPI 安装的仓库根目录使用以下入口，再按[远程推理说明](https://github.com/Physical-Intelligence/openpi/blob/main/docs/remote_inference.md)配置客户端地址、观测和动作执行。

```bash
uv run scripts/serve_policy.py policy:checkpoint \
  --policy.config pi05_libero \
  --policy.dir gs://openpi-assets/checkpoints/pi05_libero/
```

非 Docker 路径的两套 Python 环境安装步骤见[官方说明](https://github.com/Physical-Intelligence/openpi/blob/main/examples/libero/README.md#without-docker-not-recommended)。

## 4. 固定一个任务与初态

官方当前示例遍历整个任务套件，未提供单任务 ID 参数；它按 Episode 索引读取官方初态。任务编号取决于固定版本及任务排序，不能直接假定某个编号对应目标物品。参考 [LIBERO 运行器](https://github.com/Physical-Intelligence/openpi/blob/main/examples/libero/main.py)。

在配套 LIBERO Python 环境查询任务清单，使用 `benchmark.get_benchmark_dict()["libero_object"]()` 创建套件，再通过 `get_task(task_id)` 读取任务名称、`language`、`problem_folder` 和 `bddl_file`。按名称定位第 1.1 节选定的字母汤罐任务，记录 `task_order_index`、数字 ID 与初态索引，并从 `task.language` 读取原始指令。

实现 Steward 自有基线入口时，复用官方观测与执行过程，只补充任务选择和证据记录：

1. 显式接收任务 ID、任务排序配置、初态索引、seed、尝试编号和输出目录；验证任务与初态索引存在。
2. 从 `get_task_init_states(task_id)` 读取选定初态，保存初态数组及其校验值，按官方过程 reset、设置初态并等待物体稳定。
3. 每次尝试使用独立目录；文件名包含任务、初态和尝试编号，失败及异常也写入结果。
4. 保存策略实际输入、推理返回的动作段、实际执行的动作前缀、仿真步编号、终止原因和独立的基准成功信号。
5. 为两路策略图像保存可核对的样例，为完整 Episode 保存录像；录像生成失败时记录原因并补齐验收证据。

这些参数由本次[评测入口](step1-results/evaluate.py)提供。实际启动命令和配置见[复现手册](step1-results/REPRODUCE.md)。

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

以下验收项均已通过，证据见[实测报告](step1-results/README.md)。

- [x] 真实 `pi05_libero` 已加载，模型和配套环境版本可核对。
- [x] 选定任务、原始指令、初态和完整启动方式已经固定。
- [x] 至少一次完整抓取、搬运、放入篮子的任务成功，基准成功信号成立且录像可见完整过程。
- [x] 原始环境固定批次已经完成，按预先冻结的初态、次数、预算与计数规则报告成功次数、总次数和成功率；保留全部失败与人工干预记录。
- [x] 全部尝试、失败、异常、超时与人工干预记录保留，录像未被覆盖。
- [x] 相机、状态、动作、控制周期、执行前缀和机器人资产规格足以开展后续适配。
- [x] 实测报告区分原始基线成功与 Steward 接入验收；后者仍按[实施计划](../pi05-integration-plan.md)推进。

若始终失败，先用已保存的两路图像、原始动作与仿真日志定位模型加载、预处理、归一化、控制器或物理执行问题。保留每次修改与对应结果；不能用其他模型的成功记录替代 π0.5 基线，也不能在只有容器启动或单次推理成功时宣布本步完成。
