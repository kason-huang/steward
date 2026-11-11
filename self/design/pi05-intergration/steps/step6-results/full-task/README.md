# Step 6 初态 0 完整策略抓放

2026-11-11，4090-2，`hqk-dev`。真实 `pi05_libero` 已在独立 Panda WSim 完成抓起字母汤罐、搬运、松爪和撤爪。最终回合使用 31 次推理、155 条动作。撤销执行器租约后，汤罐仍稳定由篮底承托，机器人满足停稳窗口。[最终回合](full-20261111T022014Z/summary.json) · [动作与物理审计](full-20261111T022014Z/audit.json)

## 模型与闭环配置

模型由固定版 OpenPI `215abfb217dbac7d5f1273282331b9b1866c0479` 的 `pi05_libero` 配置加载，权重目录为 `/home/huawei/hqk/workspace/pi05-step1/checkpoints/pi05_libero`。所有权重文件对照 Step 1 的 SHA-256 清单通过校验。专用策略服务监听 `127.0.0.1:18066`。复测从新加载的服务开始，使用归档的原版初态 0 传感器输入做一次预热，再运行 WSim。[权重校验](checkpoint-validation.log) · [策略启动日志](full-20261111T022014Z/policy-server.log) · [预热输出](full-20261111T022014Z/preflight.json)

每段采集同一物理步的固定外部相机、腕部相机和编码器。策略接收两路 224×224 RGB、八项本体状态和 `pick up the alphabet soup and place it in the basket`。每次返回十条七维动作，执行前五条后丢弃其余五条，重新观测和推理。控制周期为 0.05 s，每条动作推进 25 个 0.002 s 子步并核对应用回执。推理期间冻结仿真时钟。最多 280 条动作、420 s 墙钟，观测有效期 20 s。任务专用租约允许五个控制周期，悬空试验仍为三个。实际配置和每次输入、原始候选、限幅后的应用动作均已保存。[运行配置](full-20261111T022014Z/configuration.json) · [动作日志](full-20261111T022014Z/actions.jsonl)

WSim 在动作入队及执行时复核 Episode、控制代次、观测年龄和物理步。任务动作按固定版执行器约定限幅至 ±1，段级累计名义平移不超过 0.25 m。每个动作的 OSC 目标根据应用时的末端位置计算，工作区为 x、y 各 −0.32 至 0.32 m，z 为 0.025 至 0.43 m。停止后至少观测 500 步，最多观测 2500 步，沿用 Step 3 的连续停稳阈值。世界侧物体位姿和接触只用于独立记录及终止验收，不输入策略。

## 独立物理结果

| 证据 | 最终回合 |
| --- | --- |
| 双指夹持并离地 | 已确认 |
| 抓持期间最高提升 | 约 0.233 m |
| 搬运 | 双指接触维持时，罐体发生水平位移 |
| 松爪 | 双指间距增加至少 4 mm，两个指垫接触力均低于 0.05 N |
| 撤爪后的篮底稳定窗口 | 第 3751–3875 步，共 125 步，即 0.25 s |
| 最小篮底承托力 | 0.253602683 N |
| 稳定窗口内最小末端距离 | 0.135927 m |
| 稳定窗口内最大罐体线速度 | 2.91×10⁻¹⁰ m/s |
| 稳定窗口内最大罐体角速度 | 1.63×10⁻⁸ rad/s |
| 撤销租约后的再次确认 | 第 4251–4375 步仍满足全部承托条件 |
| 停止状态 | `stopped`，无待执行动作及迟到动作应用 |

每个稳定窗口内，罐体均在篮内，所有有正接触力的外部支撑均来自篮底。线速度阈值为 0.005 m/s，角速度阈值为 0.05 rad/s，末端距离阈值为 0.08 m。逐步轨迹包含接触几何、法向力、物体速度和末端位置，审计脚本重新检查了每个窗口中的全部样本。[逐步物理轨迹](full-20261111T022014Z/wsim-physics.jsonl.gz)

在独立复位的 WSim 复放最终回合的 155 条实际动作，全部 4375 个物理步的罐体、篮子、末端、速度及手指位置与原记录逐步相同。首次成功回合的 160 条动作和 4500 步也通过同样复放。[最终回合复放](replay-20261111T022014Z/replay.json) · [首次成功复放](replay-20261111T021123Z/replay.json)

## 全部尝试与问题记录

| 运行目录 | 动作 / 推理 | 抓放物理结果 | 停稳结果 | 本次完整检查 |
| --- | --- | --- | --- | --- |
| `full-20261110T120449Z` | 87 / 30 | 未抓取，候选前缀触发工作区限制 | 已停稳 | 未通过 |
| `full-20261111T020909Z` | 88 / 18 | 未抓取，动作请求返回 HTTP 400 | 已停稳 | 未通过 |
| `full-20261111T021123Z` | 160 / 32 | 撤爪后稳定落在篮底 | 已停稳 | 通过 |
| `full-20261111T021839Z` | 155 / 31 | 撤爪后稳定落在篮底 | 一秒观察结束时为 `unknown` | 未通过 |
| `full-20261111T022014Z` | 155 / 31 | 撤爪后稳定落在篮底 | 已停稳 | 通过 |
| `reproduction-check/rollout` | 160 / 32 | 撤爪后稳定落在篮底 | 已停稳 | 通过 |

各行均保留完整候选输入输出、逐条动作、物理记录、双机位录像和摘要。最后一行由归档的 `reproduce-full-task.sh` 实际执行，动作和物理审计、独立复放全部通过。[复现运行日志](reproduction-check.log) · [复现回合摘要](reproduction-check/rollout/summary.json) · [复现回合录像](reproduction-check/rollout/external-wrist.mp4)第三、四、五行使用修正后的渲染输入和独立加载的 18066 服务。第四行保留了物理成功与停止未确认两个结果，之后将停止观察改为有预算的持续确认。以上是配置变化过程中的调试尝试，正式成功率批次尚待按 Step 8 固定配置后运行。[逐回合索引](attempts.json)

WSim 初始图像比原版明显偏暗，并显示原版运行时隐藏的三个夹爪标记。加载器现显式设置旧版纹理色彩空间，隐藏 `gripper0_ft_frame`、`gripper0_grip_site` 和 `gripper0_grip_site_cylinder` 的绘制。同初态下，两路策略输入相对原版的平均像素差从约 36.59 / 40.75 降至 0.08 以下。图像方向仍按 Step 5 的水平反转对齐。原始场景资产、接触和物理参数保持其固定值。MuJoCo 从 3.3.3 起读取 PNG 的颜色空间，官方给出了显式设置 `linear` 的迁移方法。[输入对照图](render-comparison.png) · [对照数值](render-comparison.json) · [MuJoCo 迁移说明](https://mujoco.readthedocs.io/en/3.17.0/changelog.html#version-3-3-3-june-10-2025)

持续运行的 18065 服务收到原版成功回合的原始输入时，也出现了与基线明显不同的动作。本次新加载同版、同权重的独立 18066 服务后完成抓放。该服务间差异的具体原因尚未定位，复现流程固定了新加载服务和一次预热。第二行的 HTTP 400 原始响应正文未被旧脚本保存，后续脚本已补存响应正文。

任务租约复验通过缺腕图、过期物理步、排队后重置、排队后墙钟过期、NaN、维度错误、越界动作、越界目标、迟到推理响应、执行中停止和推理子进程超时检查。推理子进程超时使用 1 ms 截止时间注入，未声称模拟了服务器内部故障。迟到响应通过超过观测有效期的延迟注入。超时或撤销后均未应用旧动作。另有相机、本体注册和录像的现有回归 13 项通过，Step 5 普通 OSC 和同步观测探针通过。[基础故障门禁](task-gates-3/fault-gates.json) · [输出与停止门禁](task-gates-3/output-and-stop-gates.json) · [回归日志](pytest-full-task.log) · [原有入口复验](legacy-osc-render-fixed/transport-fk.json)

本轮验证确定性仿真时钟下的初态 0 策略抓放。实时推理时钟、可观测障碍预测的完整覆盖、EH 工具入口和正式成功率仍按后续方案验收。

## 报告和录像位置

本报告在 `/home/huawei/hqk/workspace/steward/self/design/pi05-intergration/steps/step6-results/full-task/README.md`。主报告位于其上一级 `step6-results/README.md`。最终成功回合的原始运行目录为 `/home/huawei/hqk/workspace/steward/steward/var/step6/full-20261111T022014Z/`，本目录保存其完整副本和校验清单。

- [双机位录像](full-20261111T022014Z/external-wrist.mp4) 左侧固定外部相机，右侧腕部相机。
- [外部相机录像](full-20261111T022014Z/external.mp4) 和[腕部相机录像](full-20261111T022014Z/wrist.mp4) 可单独播放。
- [关键帧](full-20261111T022014Z/contact-sheet.png) 方便定位抓取、搬运和释放阶段。
- [首次成功的双机位录像](full-20261111T021123Z/external-wrist.mp4) 保留在对应运行目录。

录像按仿真控制周期以 20 fps 编码。固定推理时间未插入视频。最终回合含初帧、155 个动作末帧和停止观察帧，外部和腕部视角来自同一状态快照。

## 复现命令

先在 4090-2 的一个终端启动新策略服务。服务启动后会打印监听地址。策略服务启动日志可重定向保存到新的运行目录。

```bash
cd /home/huawei/hqk/workspace/pi05-step1
bash server.sh --port 18066
```

在另一个终端运行已归档的复现脚本。它执行一次原版初态输入预热、真实 WSim 策略闭环、动作与物理审计以及独立动作复放。每次使用新的输出目录。

```bash
cd /home/huawei/hqk/workspace/steward
bash self/design/pi05-intergration/steps/step6-results/full-task/reproduce-full-task.sh
```

单独重验门禁和已有入口时使用以下命令。门禁试验总共包含约 40 s 的有效期等待。

```bash
cd /home/huawei/hqk/workspace/steward/steward
out=$(mktemp -d "$PWD/var/step6/gates.XXXXXX")
MUJOCO_GL=egl OPENBLAS_NUM_THREADS=1 .venv/bin/python scripts/verify_libero_step6_faults.py \
  var/step4/wsim-root "$out/task-gates"
MUJOCO_GL=egl OPENBLAS_NUM_THREADS=1 .venv/bin/python scripts/probe_libero_step5.py \
  var/step4/wsim-root "$out/transport"
MUJOCO_GL=egl OPENBLAS_NUM_THREADS=1 .venv/bin/pytest -q \
  tests/test_model_camera_compat.py tests/test_embodiments.py tests/test_rgb_recording.py
```

从 `step6-results/` 运行 `sha256sum -c SHA256SUMS` 可核对报告和所有归档文件。
