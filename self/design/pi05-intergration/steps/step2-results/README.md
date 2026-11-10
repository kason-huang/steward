# Step 2 实测报告

2026-11-10（北京时间），`rtx-4090-2`。固定任务、版本、范围见 [Step 2 操作方案](../step2.md)。本次采用**现有 WSim + 固定 Scenario 的准备态 MJCF**，继续使用原有 Episode、仿真时钟、WCA 与 Steward 控制台。

## 篮内外最小兼容性试验

从固定初态 0 的预热后篮子位姿布置两组测试。汤罐均从目标 site 中心上方 0.25 m 静止释放，篮外组另沿世界 X 轴偏移 0.30 m。每组推进 1,250 个物理步，共 2.5 s，检查最后 125 步的承托和稳定性。试验由 WSim 的重置、步进和 EGL 渲染执行。

| 场景 | 首次进入篮子定向区域 | 首次支撑接触 | 末态 LIBERO 包含谓词 | 录像 |
| --- | ---: | --- | --- | --- |
| 篮内 | 0.194 s | 0.236 s，汤罐与篮底 `basket_1_g1`，未接触地面 | 真 | [篮内释放](minimal/inside.mp4) |
| 篮外 | 未进入 | 0.244 s，汤罐与地面，未接触篮底 | 假 | [篮外释放](minimal/outside.mp4) |

最后 0.25 s 内，篮内外的最大线速度分别约为 2.1×10⁻¹⁰ m/s 和 5.8×10⁻¹¹ m/s。篮底承托通过正向接触力验证。世界侧同时记录原版 LIBERO 的包含谓词和定向区域诊断，二者与接触证据分别保存。参见[试验配置](minimal/protocol.json)、[结果](minimal/result.json)、[篮内逐步记录](minimal/inside.jsonl.gz)、[篮外逐步记录](minimal/outside.jsonl.gz)和[录像校验值](minimal/videos.sha256)。

## 完整场景和重置

| 核对项 | 实测 |
| --- | --- |
| 场景 | 地面、篮子、汤罐与五个干扰物均可通过控制台查看。WCA 报告 8 个实体、0 台机器人。 |
| 原始资产 | 通过名称核对 85 个 geom 的类型、尺寸、局部变换、碰撞掩码、摩擦及接触参数，并核对仿真选项。使用原始视觉 mesh、独立碰撞体与贴图。 |
| 质量与惯量 | 显式保留原始有效质量、质心和主惯量。篮子与罐子的质量差均为 0。 |
| 初态 0–4 | 5/5 通过。每个索引在控制台接口加载后连续两次重置，位姿一致，仿真时钟归零。直接 WSim 核对所有自由关节的位姿和速度，重复的 0.5 s 推进结果一致。 |
| 预热对照 | 相对原始 LIBERO，最大位置偏差约 0.83 µm、最大姿态偏差约 0.00023°、最大末态接触穿透约 0.021 mm。 |
| 失败与回归 | 索引 5 被拒绝，当前 Episode 保持可用。旧厨房场景能通过 WCA 加载和重置。临时移除导入包的初态文件后，加载返回 HTTP 400，厨房场景仍可查询，恢复文件后可重新载入 LIBERO。 |

证据包括[控制台截图](acceptance/console.png)、五个索引的[初态 0](acceptance/init-0.json)、[初态 1](acceptance/init-1.json)、[初态 2](acceptance/init-2.json)、[初态 3](acceptance/init-3.json)、[初态 4](acceptance/init-4.json)、[无效索引](acceptance/invalid-index.json)、[厨房与缺失文件回归](acceptance/kitchen-regression.json)、[验收阈值](acceptance/protocol.json)及[导入资产校验清单](acceptance/SHA256SUMS)。按原始固定版本导出，资产保持 LIBERO 目录内的相对层次。参见 [LIBERO 许可证](acceptance/LIBERO-LICENSE)和[基线属性与版本](acceptance/reference.json)。

源环境使用 MuJoCo 3.2.3，Steward 使用 3.17.0。最初直接用几何推导惯量时，汤罐和篮子质量分别偏差约 0.0279 kg 和 0.4171 kg。正式导出保留源模型的显式惯量后重跑，已通过冻结阈值。控制台 mesh 包围盒用实际顶点计算，离屏缓冲区匹配 Steward 的显示需求。这两项用于显示和元数据，物理模型直接采用导出的 MJCF。

## 复现

在 `4090-2` 上从 Steward 仓库根目录执行。每次使用一个新的工作目录。Step 1 环境和固定 OpenPI checkout 用于导出源资产。

```bash
cd /home/huawei/hqk/workspace/steward
out="$PWD/steward/var/step2/repro-$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$out"

bash ../pi05-step1/client.sh "$PWD/steward/scripts/export_libero_step2.py" "$out/libero-step2"
steward/.venv/bin/python steward/scripts/register_libero_step2.py "$out"

ffmpeg=$(../pi05-step1/client-mirror-venv/bin/python -c 'import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())')
MUJOCO_GL=egl steward/.venv/bin/python steward/scripts/probe_libero_step2.py "$out/libero-step2" "$out/drop" --ffmpeg "$ffmpeg"

MUJOCO_GL=egl steward/.venv/bin/steward --root "$out" world \
  --scenario libero-object-alphabet-soup --seed 7 --freeze-idle \
  --wca-port 17420 --robot-port 0 --ego-api "" --world-harness "" --robot-harness ""
```

在另一终端，从同一个仓库根目录将 `out` 指向上面实际创建的目录，执行：

```bash
MUJOCO_GL=egl steward/.venv/bin/python steward/scripts/verify_libero_step2.py \
  "$out/libero-step2" "$out/acceptance" --url http://127.0.0.1:17420
```

控制台地址为 `http://127.0.0.1:17420/console`。从本机查看可用 `ssh -N -L 18420:127.0.0.1:17420 rtx-4090-2`，然后在浏览器打开 `http://127.0.0.1:18420/console`。选择无后缀的 Scenario 对应索引 0，带 `-init-1` 到 `-init-4` 的对应其他初态。当前实测的数据根目录为 `/home/huawei/hqk/workspace/steward/steward/var/step2/final-root`，导出包和完整试验日志也保存在同级 `var/step2/`。

## 路线决定与 Step 3 交接

沿用现有 WSim 的 MJCF 加载与 Steward 世界侧管理。固定布局场景禁止运行时摆放、增删和别名编译。场景预览通过控制台，编辑器走通用编译时显式拒绝该固定场景。目标区域继续引用 `basket_1_main` 上的 `basket_1_contain_region`，局部坐标为 `[0, 0, 0.07185]` m，四元数 wxyz 为 `[0, 0, 0, 1]`，半尺寸为 `[0.06108, 0.06108, 0.06949]` m。

原始含 Panda 的 MJCF 已在 Steward MuJoCo 中[单独编译](acceptance/panda-compile.json)，可解析 7 个 Panda 关节和 9 个执行器。Panda 基座位置为 `[-0.6, 0, 0]` m，世界四元数 wxyz 为 `[1, 0, 0, 0]`。Step 3 需保留机器人有效惯量，接通执行器命名、OSC 调度、夹爪及控制权。Step 2 的正式 Scenario 使用空机器人列表。

## 软件回归

针对场景、WSim、Host 和编辑的 [44 项测试](acceptance/pytest-focused.log)与 [10 项聊天及任务调度测试](acceptance/pytest-chat.log)均通过。执行环境为 Steward 的 Python 虚拟环境和 EGL。

[更广的 Python 回归](acceptance/pytest-broader.log)在运行约 8 分钟后停止，已完成的用例为 48 通过、1 失败。失败用例是既有聊天取消测试，观察到 `release` 先于 `stop`。它在本分支的聊天专项中通过，也在提交前基线单测重跑的 8 次中通过。全量回归未跑完。
