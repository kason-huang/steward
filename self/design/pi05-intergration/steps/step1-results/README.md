# π0.5 Step 1 验证报告

2026-11-10（北京时间），`rtx-4090-2`。验收依据为 Steward `hqk-dev` 的 `self/design/pi05-intergration/steps/step1.md`，文档提交 `1f57bd4399`。

## 验收结果

真实 `pi05_libero` 在原始 LIBERO 仿真中完成了字母汤罐抓取、搬运和入篮。目标资格回合在第 130 个策略控制步收到 LIBERO 的独立成功信号。录像显示罐子被抬起、移至篮口并降入篮内。最后一步夹爪命令为 `-0.99539`，按 PandaGripper 的实现发出打开指令，同一步 `done=true`、`check_success=true`、`reward=1.0`。录像随原始基准终止。

| 阶段 | 配置 | 结果 |
| --- | --- | --- |
| 官方试跑 | `libero_object` 全 10 个任务，每个任务用官方第一个初态跑 1 回合 | 10/10 |
| 目标资格回合 | 任务 0，初态 0，种子 7 | 1/1 |
| 正式固定批次 | 任务 0，初态 0–4，种子 7，独立重启策略服务 | **5/5，100%** |

正式批次的分母为 5。预先锁定的规则把失败、异常和超时计为未成功，并要求独立成功信号、环境终止、无人为干预。此次正式批次无失败、异常、超时或人为干预。试跑、资格回合及合成输入接口检查独立保存，不进入正式批次分母。以上比例适用于该任务的五个固定初态。

| 初态索引 | 策略控制步 | 推理次数 | 回合耗时 | 结果 |
| --- | ---: | ---: | ---: | --- |
| 0 | 142 | 29 | 13.97 s | 成功 |
| 1 | 145 | 29 | 5.70 s | 成功 |
| 2 | 179 | 36 | 6.66 s | 成功 |
| 3 | 139 | 28 | 5.53 s | 成功 |
| 4 | 151 | 31 | 5.85 s | 成功 |

批次首次 WebSocket 推理约 8.33 s，含 JAX 首次编译。各回合后续请求的中位延迟约 71 ms。回合耗时包含仿真、采集和录像。初态 0 在官方试跑、资格回合和正式批次均成功。

## 环境与版本

- Ubuntu 22.04.5 LTS，NVIDIA GeForce RTX 4090，驱动 `580.196.07`。JAX 返回 `CudaDevice(id=0)`，MuJoCo 使用 EGL。宿主快照见 [host.txt](evidence/host.txt)。
- OpenPI `215abfb217dbac7d5f1273282331b9b1866c0479`，LIBERO `f78abd68ee283de9f9be3c8f7e2a9ad60246e95c`，ALOHA `d1dc83afd89ded4379851257fe5d85632d31d5ec`。完整记录见 [revisions.txt](evidence/revisions.txt)。
- 采用 OpenPI 官方的非 Docker 双环境路径。策略端 Python 3.11.18、JAX 0.5.3、Orbax 0.11.13。仿真端 Python 3.8.20、MuJoCo 3.2.3、robosuite 1.4.1、NumPy 1.22.4、PyTorch 1.11.0+cu113。见 [策略依赖](runs/20261110T021656Z/server-freeze.txt)和[仿真依赖](runs/20261110T021656Z/client-freeze.txt)。
- 策略端锁文件只替换了 PyPI 注册表和 wheel 下载 URL，版本、锁定 Git 提交及哈希保持一致。仿真端 PyTorch 和 NumPy 使用本地暂存的同版本 wheel，SHA-256 校验通过。构建、依赖重试和校验记录均在 [evidence](evidence/)。
- 官方 GCS checkpoint `gs://openpi-assets/checkpoints/pi05_libero/`，共 16 个文件、12,439,085,481 字节，按 GCS generation 下载并以 CRC32C 校验，GCS 提供 MD5 的文件也做了 MD5 校验。保留 [GCS 清单](evidence/gcs-inventory.json)、[checkpoint SHA-256](evidence/checkpoint.sha256)和 [PaliGemma tokenizer SHA-256](evidence/tokenizer.sha256)。实际模型加载日志见 [batch-policy.log](runs/20261110T021656Z/batch-policy.log)。

## 任务、机器人与控制

套件 `libero_object`，`task_order_index=0`，任务 ID `0`，原始策略指令为 `pick up the alphabet soup and place it in the basket`。初态文件共有 50 个官方初态。本次固定使用 0–4。任务定义和初态集合的哈希见 [task-inventory.json](runs/20261110T021656Z/preflight/task-inventory.json)，每回合保存独立初态数组和哈希。

仿真加载 `LIBERO_Floor_Manipulation`、`OnTheGroundPanda` 和 `PandaGripper`，控制器为 `OSC_POSE`。关节顺序、初始关节位置、夹爪关节、控制站点和相机参数见 [simulator.json](runs/20261110T021656Z/target-qualification/simulator.json)。场景、字母汤罐、篮子、初态和本体来源见 [LIBERO 资产清单](evidence/libero-assets.sha256)及 [LIBERO 许可证](evidence/LIBERO-LICENSE)。Panda MJCF、夹爪、控制器及网格来自锁定的 robosuite 1.4.1，见 [robosuite 资产清单](evidence/robosuite-assets.sha256)和[包许可证](evidence/robosuite-LICENSE)。

| 项目 | 实际规格 |
| --- | --- |
| 外部相机 | `agentview` 挂在 world，垂直视场 45° |
| 腕部相机 | `robot0_eye_in_hand` 挂在 `robot0_right_hand`，垂直视场 75° |
| 图像处理 | 两路均以 256×256 渲染，旋转 180°，保比例缩放及填充为 224×224 RGB uint8 |
| 策略状态 | 8 维，世界系末端站点位置 xyz、末端本体世界朝向的轴角、两指关节位置 |
| 策略动作 | 每次输出 `10×7`。每步依次为 xyz 位移、轴角旋转和夹爪命令。执行前 5 步后重新推理，终止时允许执行更短前缀 |
| 位姿控制 | OSC 将前 6 维按 `[-1, 1]` 裁剪。三个平移分量各缩放至 ±0.05 m，并与当前世界系末端位置相加。三个轴角分量各缩放至 ±0.5 rad，其旋转矩阵左乘当前世界系末端旋转 |
| 夹爪 | 单维符号命令驱动两个手指。负值打开，正值闭合 |
| 时钟 | 物理步长 0.002 s，控制周期 0.05 s。先用官方空动作等待 10 个控制步，策略预算 280 步，每回合墙钟上限 600 s |

成功谓词来自 LIBERO 的目标状态 `In(alphabet_soup_1, basket_1_contain_region)`。脚本在每步后另外调用 `check_success()` 记录独立信号。发送给策略的字段只有两路图像、8 维状态和原始任务指令。

## 复跑与证据

逐条命令见 [复现手册](REPRODUCE.md)。

在远端 `/home/huawei/hqk/workspace/pi05-step1` 执行以下命令。启动脚本会选择已验证的两个 Python 环境，独立运行官方试跑、资格回合和正式批次。策略服务只监听 `127.0.0.1:18005`，批次完成后退出。复跑将生成新的时间戳目录。

```bash
cd /home/huawei/hqk/workspace/pi05-step1
bash run-validation.sh
```

本次运行 ID 为 `20261110T021656Z`，实际官方试跑参数如下。

```bash
bash server.sh
bash client.sh examples/libero/main.py --args.host 127.0.0.1 --args.port 18005 \
  --args.task-suite-name libero_object --args.num-trials-per-task 1 --args.seed 7 \
  --args.video-out-path "$PWD/runs/<run-id>/official-smoke"
```

- [冻结批次配置](evidence/frozen-batch.json)和[冻结时间](evidence/frozen-batch.timestamp)记录于试跑前。
- [官方试跑日志](runs/20261110T021656Z/official-smoke.log)及[十段录像](runs/20261110T021656Z/official-smoke/)覆盖每个试跑任务。
- [目标资格回合结果](runs/20261110T021656Z/target-qualification/result.json)、[录像](runs/20261110T021656Z/target-qualification/episode.mp4)、[抽帧](runs/20261110T021656Z/target-contact-sheet.png)及[逐步轨迹](runs/20261110T021656Z/target-qualification/trajectory.jsonl)可核对抓取、移动和入篮。
- [正式批次结果](runs/20261110T021656Z-batch/summary.json)及[审计输出](evidence/batch-audit.log)覆盖全部五次尝试。每个回合目录包含两路输入样例、逐次推理的真实图像与状态、返回动作块、实际执行动作、仿真时间、延迟、终止原因、初态和独立录像。
- [评测入口](evaluate.py)复用固定 OpenPI 版本的环境构造、四元数转换和图像工具，并补充任务选择和证据记录。[原始运行器](evidence/official-libero-main.py)保存在旁供比对。[批次审计](audit-attempts.py)检查输入规格、动作前缀、录像帧数、初态校验值和成功率。

## 后续 Steward 接入

本次验收对应原始 OpenPI/LIBERO 基线。Step 2–6 需要复刻地面场景和资产，核对 Panda 基座与关节顺序，将 Steward 相机和本体测量转换成相同的输入规格，并实现等价的世界系 OSC 增量和夹爪符号。Steward 侧闭环和任务成功率按原计划另行验收。

## 录像索引

完整原始记录保存在 `/home/huawei/hqk/workspace/pi05-step1`。本目录收录验收报告、复现入口、批次结果和全部 16 段录像。

- [目标资格回合](runs/20261110T021656Z/target-qualification/episode.mp4)
- [官方试跑 10 段录像](runs/20261110T021656Z/official-smoke/)
- [正式批次初态 0](runs/20261110T021656Z-batch/attempt-00-init-000/episode.mp4)
- [正式批次初态 1](runs/20261110T021656Z-batch/attempt-01-init-001/episode.mp4)
- [正式批次初态 2](runs/20261110T021656Z-batch/attempt-02-init-002/episode.mp4)
- [正式批次初态 3](runs/20261110T021656Z-batch/attempt-03-init-003/episode.mp4)
- [正式批次初态 4](runs/20261110T021656Z-batch/attempt-04-init-004/episode.mp4)
