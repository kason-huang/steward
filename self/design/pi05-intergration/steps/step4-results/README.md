# Step 4 初态 0 物理抓放实测

2026-11-10，`rtx-4090-2`。原始 LIBERO 和 WSim 均通过初态 0 的抓放验证。每套环境从同一预热结束状态执行 510 条固定动作，共 12,750 个物理子步、25.5 s 仿真时间。本次覆盖一个初态的正例，初态 0–4 正式批次和五类负例见[正式验收报告](formal-batch/README.md)。

先在固定版本的原始 LIBERO 调通接近、下降、闭合、抬起、悬停、搬运、下降、释放、撤爪和静置。冻结调试生成的 [动作表](protocol.json)，在原始环境从头重放一次，再从 WSim 的同一预热状态经机器人 HTTP 端口提交同一序列。WSim 验证了所有命名关节初值、动作序号和每周期 25 子步的完成回执。两套环境的冻结回放均按既定序列执行，汤罐位姿与接触数据只由世界侧采样。

| 验收指标 | 原始 LIBERO | WSim | 阈值 |
| --- | ---: | ---: | ---: |
| 悬停及搬运期间，汤罐最低抬升 | 198.45 mm | 198.56 mm | ≥ 100 mm |
| 同期两指各自的最小法向力 | 19.99 / 19.91 N | 19.00 / 18.97 N | 各 ≥ 0.1 N |
| 同期汤罐相对夹爪的最大位移 | 0.397 mm | 0.394 mm | ≤ 10 mm |
| 同期汤罐相对夹爪的最大转角 | 0.00169 rad | 0.00144 rad | ≤ 0.20 rad |
| 静置最后 125 步的最小篮底法向力 | 0.2536 N | 0.2536 N | ≥ 0.05 N |
| 同期最大线速度 | 3.74×10⁻¹² m/s | 2.78×10⁻¹⁰ m/s | ≤ 0.005 m/s |
| 同期最大角速度 | 3.27×10⁻¹¹ rad/s | 1.97×10⁻⁸ rad/s | ≤ 0.05 rad/s |
| 撤爪后末 0.25 s 的 LIBERO 判定 | 原版成功 | Step 2 对照谓词为真 | 每控制周期为真 |

在最后 125 个子步，汤罐唯一有正向接触力的外部几何是篮底 `basket_1_g1`。此时控制站点离汤罐至少 195 mm。两边最终罐体位置相差约 3.3 mm。原始任务谓词在下降时、张爪之前就变为真，因此报告同时保存了释放后接触与静置结果。[原始汇总](libero-summary.json) · [WSim 汇总](wsim-summary.json)

- 双机位录像左侧是外部视角，右侧是腕部相机，均为 510 帧、20 fps。录像保留两个环境各自的渲染方向。[原始 LIBERO](libero.mp4) · [WSim](wsim.mp4)
- `libero-trace.json.gz`、`wsim-trace.json.gz` 保留逐周期动作、反馈和成功判定。`*-physics.json.gz` 保留每个物理子步的命名接触对、法向力和位姿。`initial.json` 是原始预热结束的命名状态。
- 源版本见动作表。原始环境使用 MuJoCo 3.2.3 与 robosuite 1.4.1，WSim 使用 Step 3 验收环境的 MuJoCo 3.17.0。[资产校验值](asset-sha256.txt) · [归档校验值](SHA256SUMS)
- 4090-2 原始运行目录位于仓库下的 `steward/var/step4/libero-fixed-1/` 和 `steward/var/step4/wsim-fixed-1/`，后者也保存了逐帧 PNG。

## 复现

在 4090-2 从仓库根目录执行。已在独立目录验证以下命令，原始 LIBERO 和 WSim 的结果汇总均与归档逐字段一致。

```bash
cd /home/huawei/hqk/workspace/steward
results="$PWD/self/design/pi05-intergration/steps/step4-results"
out=$(mktemp -d "$PWD/steward/var/step4/repro.XXXXXX")
sha256sum -c "$results/asset-sha256.txt"
bash ../pi05-step1/client.sh "$PWD/steward/scripts/record_libero_step4.py" \
  "$out/libero" --protocol "$results/protocol.json" --video
ffmpeg_path=$(find "$PWD/../pi05-step1/client-mirror-venv" -name 'ffmpeg-linux*' -type f -print -quit)
cd steward
mkdir "$out/root"
ln -s "$PWD/var/step3/baseline-final-2" "$out/root/libero-step3"
.venv/bin/python scripts/register_libero_step2.py "$out/root" --panda
MUJOCO_GL=egl OPENBLAS_NUM_THREADS=1 .venv/bin/python scripts/verify_libero_step4.py \
  "$out/root" "$out/libero" "$out/wsim" --ffmpeg "$ffmpeg_path"
```
