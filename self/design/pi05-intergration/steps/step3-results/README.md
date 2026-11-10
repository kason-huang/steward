# Step 3 实测报告

在 `rtx-4090-2` 上通过 Steward 控制台使用的 WCA 接口加载五个 Panda Scenario，并经各自的机器人 HTTP 端口重放固定动作。模型、控制响应和控制权生命周期均通过下面的专项验收。

## 模型与初态

- 原始 LIBERO 使用 MuJoCo 3.2.3、robosuite 1.4.1。初态 0–4 各跑两次，每次 10 个预热周期和 55 个固定动作周期。相同初态的两份解压轨迹逐字段相等。每周期记录一次目标更新及 25 个子步执行器输出。[动作协议](libero-baseline/protocol.json) · [固定版本与编译参数](libero-baseline/model.json)
- WSim 使用 MuJoCo 3.17.0。五个 Scenario 各重置两次，16 个命名关节的初始位置和速度均与各自的原始预热结束快照完全相同。核对了有效惯量、关节轴与限位、九个执行器的类型与传动、碰撞掩码、工具站点及外部和腕部相机。WSim 复位后的时钟从零起算。
- 独立探针从未预热初态重放时，首周期即出现目标位置偏差。整个序列的末端位置最大差为 7.21 mm，臂力矩最大差为 22.21 N·m。以原始预热结束快照重放后，误差降至下表量级，因此 WSim 采用该快照作为可控复位起点。原始状态仍保存在轨迹与初态表中。[未预热探针](osc-probe/summary.json) · [预热后探针](osc-probe-warm/summary.json)
- Panda 由 Scenario 注册。现有监督器管理本体切换，Panda profile 在此阶段使用机器人端口和腕部 RGB。Ego Runtime、旧技能和通用 RGB-D 感知接口由后续阶段处理。[控制台截图](console.png)

## 控制结果

五个初态各跑 55 个动作，合计 275 个控制周期、6,875 个物理子步。以固定协议的周期边界比较。下表为所有周期和关节中的最大绝对差，姿态使用相对旋转角。

| 项目 | 最大差 | 验收阈值 |
| --- | ---: | ---: |
| 末端位置 | 2.35×10⁻⁸ m | 1×10⁻⁵ m |
| 末端姿态 | 2.33×10⁻⁷ rad | 1×10⁻⁴ rad |
| OSC 目标位置 | 2.35×10⁻⁸ m | 1×10⁻⁵ m |
| OSC 目标姿态 | 2.00×10⁻⁵ rad | 1×10⁻⁴ rad |
| 臂关节位置 | 2.23×10⁻⁷ rad | 1×10⁻⁵ rad |
| 臂关节速度 | 9.11×10⁻⁷ rad/s | 1×10⁻⁴ rad/s |
| 臂力矩 | 2.63×10⁻⁵ N·m | 1×10⁻³ N·m |
| 双指执行器目标 | 0 | 1×10⁻¹⁰ |

- 六轴的 12 个正负脉冲在全部五个初态方向正确。以脉冲开始至随后的保持周期结束测量净运动，旋转取世界轴投影。
- 初态 0 的双指开口从 78.41 mm 缩到 35.30 mm，反向打开后到 74.76 mm。零指令保持执行器目标，手指仍可继续向目标运动。逐周期双指位置与原始轨迹差小于 1 μm。
- 测试经真实物理手指执行。Panda 不注册旧理想附着抓点，旧 `/command` 入口拒绝 Panda 请求。旧厨房和 Cobot 场景分别通过正常端口执行底座命令并重置。Step 2 无机器人场景五个初态也重跑通过。WorldSim、Host 和 Cobot 的 20 项针对性测试全部通过。

## 控制权

- 机器人端口用 `episodeId`、`controlGeneration` 和仅在获取租约时返回的 `leaseId` 校验。动作提交使用递增 `sequence`，独占一条待执行槽。状态分别记录接受、开始应用与完成。
- 第 10 子步执行显式停止，正在运行的动作被截断，已排队的第二条动作被清除。撤销时末端线速度约 0.071 m/s，状态为 `unknown`。继续推进至第 510 步，连续 160 个子步满足速度阈值，夹爪目标保持，状态变为 `stopped`。判定窗为 25 步，臂关节 0.02 rad/s、手指 0.005 m/s、末端线速度 0.01 m/s、末端角速度 0.05 rad/s。
- 实测时钟 hold 和暂停期间租约按墙钟到期，恢复后无旧动作应用。重置和切换都会拒绝旧凭据，保留了旧仿真实例的迟到请求也被拒绝。新租约成功接管。无效维度和非有限值被拒绝，越界有限值裁剪至 `[-1,1]`。注入 OSC 数值故障时撤销控制、暂停仿真并报告 `unknown`，重置后清除故障。

[WSim 验收数据及阈值](wsim/summary.json) · [生命周期记录](wsim/lifecycle.json) · [WSim 双机位录像](wsim/wsim-init0.mp4) · [原始 LIBERO 录像](libero-baseline/libero-init0.mp4)

原始逐周期、逐子步轨迹在 `libero-baseline/init-*-repeat-*.json.gz`，对应 WSim 数据在 `wsim/wsim-init-*.json.gz`。约 92 MB 的完整准备态 MJCF、资产、原始许可证和 SHA256 清单位于 4090-2 的 `/home/huawei/hqk/workspace/steward/steward/var/step3/baseline-final-2/`。

## 完整套件回归

完整 Steward Python 套件结果为 339 通过、3 失败。将本次代码与 Step 3 前的 `810593ce2c` 在同一环境中分别重跑：

| 测试 | 完整套件 | Step 3 前重跑 | Step 3 后重跑 |
| --- | --- | --- | --- |
| Cobot 双抽屉取物 | 失败 | 通过 | 通过 |
| Phase 1 RGB-D 探索位置误差 | 失败 | 失败 | 失败 |
| Phase 1 柜体上半部识别 | 失败 | 失败 | 失败 |

两项 Phase 1 失败在基线版本同样出现。Cobot 的完整套件失败在单测重跑中未复现。4090-2 原始日志位于 `steward/var/step3/pytest-full.log`、`pytest-drawer-{base,current}.log` 和 `pytest-phase-{base,current}.log`。

## 复现

从 4090-2 仓库根目录执行，输出目录须尚未存在。

```bash
cd /home/huawei/hqk/workspace/steward
out="$PWD/steward/var/step3"
bash ../pi05-step1/client.sh "$PWD/steward/scripts/record_libero_step3.py" "$out/baseline-repro"
(cd "$out/baseline-repro" && sha256sum -c SHA256SUMS)
ffmpeg_path=$(find "$PWD/../pi05-step1/client-mirror-venv" -name 'ffmpeg-linux*' -type f -print -quit)
cd steward
mkdir "$out/repro-root"
ln -s "$out/baseline-repro" "$out/repro-root/libero-step3"
.venv/bin/python scripts/register_libero_step2.py "$out/repro-root" --panda
MUJOCO_GL=egl OPENBLAS_NUM_THREADS=1 .venv/bin/python scripts/verify_libero_step3.py \
  "$out/repro-root" "$out/wsim-repro" --ffmpeg "$ffmpeg_path"
```

可用下列命令单独启动控制台，监听 4090-2 的 17430 和 17431 端口。

```bash
MUJOCO_GL=egl OPENBLAS_NUM_THREADS=1 .venv/bin/python -m steward.cli --root "$out/repro-root" up \
  --scenario libero-object-alphabet-soup-panda --wca-port 17430 --robot-port 17431 \
  --freeze-idle --no-dispatch-task
```

机器人端口先读取 `GET /control/status`，再以当前 Episode 和代次调用 `POST /control/acquire`，请求包含 `ttlS`。后续 `POST /control/action`、`/control/renew` 和 `/control/stop` 携带获得的三项凭据。动作提交包含 `sequence` 与七元素 `action`。使用 `/clock/wait` 的独立 hold 在第 25 个子步收集反馈，调用 `/control/status` 读取执行结果。完整调用顺序在 `steward/scripts/verify_libero_step3.py`。
