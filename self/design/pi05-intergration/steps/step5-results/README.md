# Step 5 实测总结

2026-11-10，`rtx-4090-2`。Panda ER 已经通过机器人端口接收固定外部视角、腕部相机和九个编码器的同步观测，并向 Steward 发布 Ego State。WSim 从同一状态快照采集图像和反馈，数据带有 Episode、物理步、仿真时间和标定标识。Ego State 的抓放识别使用同步 RGB-D 和编码器。π0.5 预览使用两路 RGB 和 8 项本体状态。

## 观测与 OpenPI 输入

- 在物理步 0、25、50 核对了编码器推算的相机和末端位姿，位置及旋转矩阵元素误差均低于 10⁻⁸。重置后收到新 Episode 的第 0 步。[同步和运动学检查](transport/transport-fk.json) · [初次采样标定](transport/calibration.json)
- 将正常初态 0 的 4 帧策略预览与固定版原始 LIBERO 对照。两路图像均为 `224×224` RGB，状态为 8 个有限数值。末端位置分量最大差 0.547 mm，轴角分量最大差 0.000877 rad，手指位置分量最大差 0.104 mm。状态使用工具站点位置、Panda 手部轴角和两个手指位置。WSim 图像按方向对照做水平反转，渲染像素仍有差异。
- 观测消费者用实采数据验证了重复帧、较旧步号和旧 Episode 的拒绝，缺失腕视角时拒绝生成策略输入。该检查覆盖观测入口。观测年龄上限与推理返回后提交动作前的复核留给闭环准入验证。

原始对照位于 `steward/var/step5/openpi-reference/`。ER 策略预览在 `steward/var/step5/official-1/normal-0-r1/policy-preview.json`，其中两路 PNG 以 Base64 保存。原始同步 RGB-D 样本分别在 `steward/var/step5/{normal,offset,outside,rim}-rgbd/`。

## Ego State 固定动作回放

使用 Step 4 动作表通过真实的 Steward 和 Panda ER 进程重放。正常回合覆盖初态 0–4，各独立运行两次。五类负例使用初态 0，各运行两次。下表是每回合的最终结论。

| 用例 | 回合 | 抓取 | 放置 |
| --- | ---: | --- | --- |
| 正常抓放 | 10 | 确认 | 确认 |
| 抓空、偏位、夹持不足 | 6 | 失败 | 失败 |
| 篮外释放 | 2 | 确认 | 失败 |
| 携物碰撞 | 2 | 确认 | 未确认 |

两个携物碰撞回合的 WSim 物理证据显示汤罐落到了篮底，Ego State 对倾斜罐体缺少足够的放置证据。这两回合保留为未确认。当前记录覆盖固定场景及动作回放，完整的感知故障与自动完成判定验收另行核对。[逐回合结果](episodes.json) · [Step 4 独立物理结果](../step4-results/formal-batch/README.md)

20 个回合的完整 Ego State 序列、策略预览、重置记录和服务日志位于 4090-2 的 `steward/var/step5/official-1/`。

## Step 6 准入结论

可以开始真实 π0.5 只推理验证。开放短动作前缀前，需要在动作提交入口复核 Episode、控制代次及观测新鲜度，并验证缺帧、过期或重置后 WSim 未应用相关动作。首轮闭环用独立 WSim 物理证据评估抓放，Ego State 提供辅助判断。[Step 6 方案](../step6.md)

## 复现

在 4090-2 上从仓库的 `steward/` 子目录运行。下例验证传感器并重放一个正常回合。将动作表换为 `offset-0.json`、`outside-0.json` 或 `rim-0.json` 可复现对应负例。每次运行指定新的输出目录。

```bash
cd /home/huawei/hqk/workspace/steward/steward
out=$(mktemp -d "$PWD/var/step5/repro.XXXXXX")
p=../self/design/pi05-intergration/steps/step4-results/formal-batch/protocols
MUJOCO_GL=egl OPENBLAS_NUM_THREADS=1 .venv/bin/python scripts/probe_libero_step5.py \
  var/step4/wsim-root "$out/transport"
MUJOCO_GL=egl OPENBLAS_NUM_THREADS=1 .venv/bin/python scripts/verify_libero_step5.py \
  var/step4/wsim-root "$p/normal-0.json" "$out/normal-0"
```
