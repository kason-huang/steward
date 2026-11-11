# Step 7 实测报告

2026-11-11，4090-2，`hqk-dev`。真实 `ego` profile、嵌套 `run_code`、Ego API、Panda ER 策略 Job 与真实 `pi05_libero` 已贯通，四个目标用例均有通过证据。额外的真实 profile 无凭据负例也已通过。所有失败和重试保留，整套回归存在下文列明的保留项。

[打开 HTML 报告与录像](index.html) · [全部 17 次尝试](batch-results.json) · [源码与审查说明](review.md) · [校验清单](SHA256SUMS)

## 1. 正式用例结果

场景固定为 `libero-object-alphabet-soup-panda`，LIBERO 初态 0。策略 prompt 为 `pick up the alphabet soup and place it in the basket`。每回合独立重建 WSim、新加载专用模型服务、原始输入预热一次，20 Hz 控制、每条 25 个物理步、每五条重新观测推理，推理期间冻结仿真时钟。

| 用例与目录 | 入口 | Job / 目标 / 停止 | 完成动作 | 独立物理核验 |
| --- | --- | --- | ---: | --- |
| [N1-06](attempts/N1-06/result.json) | 控制台聊天，run_code 预测和执行，保存能力 v2 | succeeded / achieved / confirmed | 150 | 抓起、搬运、释放、撤爪，篮底稳定承托 125 步 |
| [N2-03](attempts/N2-03/result.json) | 真实 profile 获取并执行相同能力 v2，重新获取对象引用 | succeeded / achieved / confirmed | 150 | 同样通过，篮底稳定承托 125 步 |
| [C1-03](attempts/C1-03/result.json) | 真实策略运动中调用控制台停止 | stopped / unconfirmed / confirmed | 25，另有一条被中断 | 撤销后无新动作，旧控制代次请求被拒，owner 与 hold 清空 |
| [F1-02](attempts/F1-02/result.json) | 50 条动作后终止本次专用策略服务 | failed / unconfirmed / confirmed | 50 | 原因 inference_timeout，撤销、停稳、资源释放通过 |
| [G1-01](attempts/G1-01/result.json) | 真实 profile 内嵌套无效 receipt 的 skill_run | 守卫拒绝，无 Job | 0 | lastApplied 为 null |

N1 的稳定承托窗口为物理步 4151–4275，最小篮底力 0.25360 N，最小撤爪距离 0.13711 m；N2 为 4176–4300，最小篮底力 0.25360 N，最小撤爪距离 0.14812 m。每步物理时间为 2 ms，125 步即 0.25 s。两回合中，机器人先用自身两路观测、夹爪状态和连续证据确认结果；世界侧接触、速度和篮内真值仅由验证脚本独立记录，不作为 ER 的成功输入。

C1 在序号 25 的动作执行到第 10/25 子步时停止：动作从 625 计划运行至 650，实际在 635 撤销。注入时末端线速度约 0.1286 m/s，确为运动中停止。测试用独立时钟 hold 固定注入点，撤销后立即释放该 hold，再继续停稳观察。该受控注入中观测到代次撤销约 2.3 ms、停止请求返回约 2.46 s；这不是实时系统的延迟保证。最终控制代次为 2，lastApplied 保持序号 25，lastCompleted 保持序号 24，后续无迟到动作。

F1 主动关闭专用 18067 服务，客户端在 15 s 的单调墙钟期限内未返回，Job 保留 `inference_timeout` 后停止。模型服务断连、目标未确认和停稳已确认是三个独立事实。C1/F1 不计为抓放成功。

这些是经过调试和额外重试后的功能验证，不能读成“所有尝试 4/4 成功”，也不是 LIBERO 全量评测或正式成功率。Step 8 再做固定批次与完成识别覆盖率。

## 2. 录像和逐回合材料

| 回合 | 双机位同步录像 | 分机位 |
| --- | --- | --- |
| N1 | [dual.mp4](attempts/N1-06/videos/dual.mp4) | [外部](attempts/N1-06/videos/external.mp4) / [腕部](attempts/N1-06/videos/wrist.mp4) |
| N2 | [dual.mp4](attempts/N2-03/videos/dual.mp4) | [外部](attempts/N2-03/videos/external.mp4) / [腕部](attempts/N2-03/videos/wrist.mp4) |
| C1 | [dual.mp4](attempts/C1-03/videos/dual.mp4) | [外部](attempts/C1-03/videos/external.mp4) / [腕部](attempts/C1-03/videos/wrist.mp4) |
| F1 | [dual.mp4](attempts/F1-02/videos/dual.mp4) | [外部](attempts/F1-02/videos/external.mp4) / [腕部](attempts/F1-02/videos/wrist.mp4) |

视频左侧为外部相机，右侧为腕部相机，原始 256×256 图像，名义 20 fps。录像展示仿真时间，冻结推理等待不占播放时间；逐帧时间以对应 Job 的 `observations.jsonl` 为准。C1 部分动作被中断，注入点附近的采样间隔可略有不同。关键帧图在每个 `videos/contact-sheet.jpg`，编码信息在 `videos/timing.json`。

每个 `attempts/<回合>/` 保存结果、聊天和工具记录、请求、EH 会话、能力快照、配置与启动日志。`runtime/ego-panda/policy-jobs/<Job>/` 保存 receipt、Job、原始策略输入输出、预测覆盖、`submissions.jsonl`、`actions.jsonl`、连续判断和 `stop.json`。`submissions` 表示接收动作；`actions` 只列完整完成动作，两者在 C1 有意不同。`world/` 保存压缩逐物理步记录与控制事件；`cleanup.json` 单独记录 owner、hold、终态不变与迟到动作核验。

归档主目录：

```text
/home/huawei/hqk/workspace/steward/self/design/pi05-intergration/steps/step7-results/
```

原始运行目录和原始 PNG/RGB-D：

```text
/home/huawei/hqk/workspace/steward/steward/var/step7/<回合>/
  runtime/ego-panda/policy-jobs/<Job>/external/*.png
  runtime/ego-panda/policy-jobs/<Job>/wrist/*.png
  runtime/ego-panda/policy-jobs/<Job>/sensors/*.npz
```

`raw-evidence` 是指向上述 `var/step7` 的绝对软链接。为节省重复存储，归档复制日志、JSON、会话、世界轨迹和编码视频，原始 PNG/NPZ 留在原目录；每回合 `raw-source.json` 给出精确路径。早期 API 调试回合的采集字段少于最终正式回合，按原样保留。隔离 profile 与能力库原件在 `/home/huawei/hqk/workspace/steward/steward/var/step7/dsh/robot-1/`。

## 3. 原失败、调试与改动

全部尝试见 `batch-results.json`，包括以下重要失败：

- `api-real-1`：世界侧已物理完成，机器人未确认，280 条动作后预算退出。原先要求末端高度大于 0.20 m，会漏掉较低撤爪；现在用可见罐体中心的撤爪距离和连续稳定证据判断，保留“不够清楚即未确认”。
- `N1-01`：旧 DeepSeek key 返回 401，没有创建 Job；更新 key 后新进程重新读取 `.env`，未将 key 写入报告。
- `N1-02`、`N1-03`：深度边缘出现虚假障碍，检查拒绝并停稳。定位到 MSAA 度量深度失真：同一腕部像素射线距离约 0.201608 m，MSAA 深度为 0.116050 m，关闭 MSAA 后为 0.201608 m。现在保持策略 RGB 渲染不变，为深度使用独立无 MSAA 上下文；原始拒绝样本仍须被检查拒绝，没有扩大运动权限。
- `N1-05`：模型的候选目标 z=0.0244015 m，低于固定授权下界 0.025 m，50 条动作后拒绝、停止并释放资源。这是实际动作授权拒绝，未扩大范围。说明相同权重和流程也不保证每个样例成功。
- `C1-02`：较晚的注入点未等到下一条正在执行的动作，验证器超时，不能算 C1 通过。后续将 C1 注入移至第 26 条动作中，保留原失败。最终验证器会为自身异常保存结构化失败与堆栈。

审查中修复了暂停时无限等待、续租故障被误写为取消、启动失败后清理短路和控制台丢弃停止未知。所有停止相关时钟等待都有墙钟期限；主 Job 线程和辅助续租线程启动失败均进入清理。C1 日志中因终止客户端产生的 BrokenPipe 堆栈保留，与本体的停止遥测分别判读。

## 4. 验证覆盖与保留项

通过的检查覆盖：不支持任务、缺失/旧对象、未知或过期 receipt、owner 冲突、标定不符、直接 HTTP 拒绝、同 receipt 并发最多一个 Job、短等待后继续查询、推理中取消、NaN/维度错误/推理超时、续租故障原因、停止未知锁与显式恢复、暂停仿真下有界停止、两个线程启动失败的独立清理、Episode 重置后旧结果失效、真实 ER 进程退出，以及真实 profile 的嵌套无凭据调用。源测试在 `steward/tests/test_panda_jobs.py` 与 `test_chat.py`，日志保留在 `checks/`。

实际命令结果分别保留，不合并成虚构的全绿总数：

- Panda 主回归 17 项通过；新增进程退出用例修正场景目录引用后单项通过，完整原失败日志也保留。
- `final-regression-delta.log`：Panda 与本体装配共 23 项通过。之前 63 通过、1 个旧 FakeProcess 缺少新增 skills 字段；已同步测试替身并复核该模块通过。
- `stop-review-tests-1.log`：23 项通过，包含聊天停止结果兼容、未知停稳告警和停止故障路径。
- DSH 工具/反思/能力工具单测 35 项通过；最终 `pnpm run build` 通过。
- DSH 全套：74 通过、7 失败、2 跳过。6 项既有快照缺少安装包 `@deepseek-ai/dsh-llm-replay`。另 1 项旧本体 `look_around` 返回 `turn did not converge (yaw error 1.57 rad)`；使用 Step 7 前源码覆盖层复跑得到相同失败，记录在 `old-body-baseline.log`，当前结果在 `old-body-integration.log`。这项没有标为通过。

风险检查为固定任务的近似检查：标定 FK、关节范围、分段 arm/tool 扫掠、观测障碍、目标/篮子预期接触区和操作区可见性。它不是经过全局动力学验证的安全证明；receipt 保存实际覆盖和风险来源。全量遮挡、复杂自碰撞、其他任务/初态与实时推理模式未由这四个样例验收。

能力库的 `verified` 与 `runs` 是既有 `run_code` 程序层记账，获取能力的辅助程序也可能计数，不能当作物理回合数。N1-06 保存的是 v2，N2-03 获取相同 v2；两份 `capability.json` 保留代码和各自计数，N2 快照为 3 次程序成功、1 次程序失败。本文只把该回合唯一的真实策略 Job 及独立世界证据作为物理成功。失败程序的依赖步骤、物理目标和工具程序计数分别记录。

本步的 Panda 功能主线与进入 Step 8 的真实调用链证据已具备；旧本体基线失败、缺失快照依赖和完整成功率/感知负例仍有保留，不应描述为“所有回归和所有场景完美完成”。

## 5. 复现命令

```bash
ssh rtx-4090-2
cd /home/huawei/hqk/workspace/steward/steward
export PATH="$PWD/var/dsh-toolchain/bin:$PWD/var/dsh-toolchain/node/bin:$PATH"
export MUJOCO_GL=egl OPENBLAS_NUM_THREADS=1

# 已有 venv、预备 WSim root、OpenPI client/server 和权重。
# DEEPSEEK_API_KEY 放在此目录的 .env；每个新聊天进程会重新读取。
STEWARD_DSH_VAR="$PWD/var/step7/dsh" bash scripts/setup-dsh-profiles.sh

# 每次使用未存在的新目录名；先 N1 成功保存能力，再运行 N2。
bash scripts/run_libero_step7.sh N1 reproduce-N1-01
bash scripts/run_libero_step7.sh N2 reproduce-N2-01
bash scripts/run_libero_step7.sh C1 reproduce-C1-01
bash scripts/run_libero_step7.sh F1 reproduce-F1-01

# 真实 profile 嵌套无凭据负例，无须启动策略模型。
.venv/bin/python -u scripts/verify_libero_step7.py var/step4/wsim-root var/step7/reproduce-G1-01 \
  --policy-config var/step7/policy.toml --mode chat --case G1 \
  --harness-home var/step7/dsh/robot-1
```

`run_libero_step7.sh` 每次启动新加载的专用 18067 服务，用 Step 6 已验证的原始 `000.npz` 预热一次，再调用验证器。脚本只替换其 PID 文件所指且经命令行核实的专用服务；退出后关闭该服务，既有展示服务不参与测试。模型配置为 `pi05_libero`，OpenPI 固定 `215abfb217dbac7d5f1273282331b9b1866c0479`，权重目录 `/home/huawei/hqk/workspace/pi05-step1/checkpoints/pi05_libero`。权重校验沿用并复制 Step 6 的记录。

运行配置在 `configuration/policy.toml`：每次请求 15 s、最多 280 条动作、420 s 总墙钟、前缀 5、receipt 提交墙钟 30 s、停止墙钟 20 s，停止至少观察 500、最多 2500 物理步。端点、权重与动作配置由部署决定，EH 仅提交 ES 引用和 receipt。

```bash
cd /home/huawei/hqk/workspace/steward/steward
MUJOCO_GL=egl OPENBLAS_NUM_THREADS=1 .venv/bin/python -m pytest \
  tests/test_panda_jobs.py tests/test_chat.py tests/test_cameras.py \
  tests/test_embodiments.py tests/test_alerts.py tests/test_reflection.py -q
cd dsh
pnpm run build
node --test test/tools.test.mjs test/reflection.test.mjs
```

重新生成归档与视频（默认选择本报告的正式目录；新批次需相应更新脚本中的 FINAL）：

```bash
cd /home/huawei/hqk/workspace/steward/steward
.venv/bin/python scripts/archive_libero_step7.py var/step7 \
  ../self/design/pi05-intergration/steps/step7-results \
  --ffmpeg /home/huawei/hqk/workspace/pi05-step1/server-mirror-venv/lib/python3.11/site-packages/imageio_ffmpeg/binaries/ffmpeg-linux-x86_64-v7.0.2
cd ../self/design/pi05-intergration/steps/step7-results
sha256sum -c SHA256SUMS
```

源码快照、源码散列、配置、原始服务启动日志与预热结果一并保存。`SHA256SUMS` 覆盖归档实文件；不遍历 `raw-evidence` 软链接。改写主报告或新增检查后应更新清单。
