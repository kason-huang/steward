# Step 4 正式批次

2026-11-10，`rtx-4090-2`。原始 LIBERO 和 WSim 均通过 Step 4 物理验收。正常抓放覆盖初态 0–4，每个初态独立复位两次，每套环境各通过 10/10。五类负例均以初态 0 独立复位两次，每套环境各通过 10/10。正常抓放与负例响应分别计数。

正常批次中，原始 LIBERO 和 WSim 的最低抬升分别为 198.45 mm 和 198.53 mm，最大相对夹爪滑移分别为 0.401 mm 和 0.397 mm。撤爪后的最后 125 个物理子步中，每个正常回合均由篮底独立承托，两边测得的最小承托力均约为 0.254 N。

| 负例 | 两套环境的实测结果 |
| --- | --- |
| 抓空 | 合爪时汤罐未触及指垫，随后留在地面。 |
| 偏位 | 抓取点横向偏移 40 mm，汤罐在两边最多只抬升约 6.4 mm 和 9.7 mm，未被稳定搬运。 |
| 夹持不足 | 闭合由正常的 20 个周期缩到 1 个周期，指垫未建立有效夹持，汤罐留在地面。调试阶段的 2 周期候选仍能抬罐，留在原始调试目录。 |
| 携物碰撞 | 双指夹持时汤罐与篮沿发生接触，原始 LIBERO 和 WSim 的法向力峰值约为 219.44 N 和 147.12 N，篮体移动约 2.76 mm 和 2.50 mm。两边均通过碰撞响应检查。原始 LIBERO 释放后汤罐留在篮沿 `basket_1_g2`，放置失败。WSim 释放后汤罐落到篮底，放置成功。两次独立复位均复现这一差异。 |
| 篮外释放 | 两边均先稳定搬运，松爪后汤罐由地面承托，篮内谓词为假。 |

逐回合表包含物理验收、任务结果、篮内谓词、失败原因、测量值、录像、原始轨迹位置及动作哈希。[逐回合 CSV](episodes.csv) · [完整 JSON](batch.json)

在原始 LIBERO 的调试试验中为每个初态生成动作表，正式运行前冻结。每个用例在两套环境和两次复位中使用同一份动作表。核对了 40 个回合的动作值、每周期 25 子步的连续轨迹和 MP4 解码帧数。录像每帧由外部视角和腕部画面组成，保持各环境自身的渲染方向。[协议校验值](protocols/SHA256SUMS)

原始日志、逐周期和逐子步数据在 4090-2 的 `/home/huawei/hqk/workspace/steward/steward/var/step4/formal-1/`，按回合及环境归档。候选动作的调试数据在同级 `batch-pilots/`，未计入正式批次。

## 复现

从 4090-2 的仓库根目录运行。该命令为每个回合启动独立的原始环境与 WSim，使用 Step 3 冻结资产和独立验证数据目录。

```bash
cd /home/huawei/hqk/workspace/steward
results="$PWD/self/design/pi05-intergration/steps/step4-results"
out=$(mktemp -d "$PWD/steward/var/step4/formal-repro.XXXXXX")
sha256sum -c "$results/asset-sha256.txt"
(cd "$results/formal-batch/protocols" && sha256sum -c SHA256SUMS)
ffmpeg_path=$(find "$PWD/../pi05-step1/client-mirror-venv" -name 'ffmpeg-linux*' -type f -print -quit)
cd steward
mkdir "$out/root"
ln -s "$PWD/var/step3/baseline-final-2" "$out/root/libero-step3"
.venv/bin/python scripts/register_libero_step2.py "$out/root" --panda
.venv/bin/python scripts/run_libero_step4_batch.py "$out/root" \
  "$results/formal-batch/protocols" "$out/runs" --ffmpeg "$ffmpeg_path"
```

## 双机位录像

| 用例 | 原始 LIBERO | WSim |
| --- | --- | --- |
| 初态 0 | [第 1 次](normal-0-r1/libero.mp4) · [第 2 次](normal-0-r2/libero.mp4) | [第 1 次](normal-0-r1/wsim.mp4) · [第 2 次](normal-0-r2/wsim.mp4) |
| 初态 1 | [第 1 次](normal-1-r1/libero.mp4) · [第 2 次](normal-1-r2/libero.mp4) | [第 1 次](normal-1-r1/wsim.mp4) · [第 2 次](normal-1-r2/wsim.mp4) |
| 初态 2 | [第 1 次](normal-2-r1/libero.mp4) · [第 2 次](normal-2-r2/libero.mp4) | [第 1 次](normal-2-r1/wsim.mp4) · [第 2 次](normal-2-r2/wsim.mp4) |
| 初态 3 | [第 1 次](normal-3-r1/libero.mp4) · [第 2 次](normal-3-r2/libero.mp4) | [第 1 次](normal-3-r1/wsim.mp4) · [第 2 次](normal-3-r2/wsim.mp4) |
| 初态 4 | [第 1 次](normal-4-r1/libero.mp4) · [第 2 次](normal-4-r2/libero.mp4) | [第 1 次](normal-4-r1/wsim.mp4) · [第 2 次](normal-4-r2/wsim.mp4) |
| 抓空 | [第 1 次](empty-0-r1/libero.mp4) · [第 2 次](empty-0-r2/libero.mp4) | [第 1 次](empty-0-r1/wsim.mp4) · [第 2 次](empty-0-r2/wsim.mp4) |
| 偏位 | [第 1 次](offset-0-r1/libero.mp4) · [第 2 次](offset-0-r2/libero.mp4) | [第 1 次](offset-0-r1/wsim.mp4) · [第 2 次](offset-0-r2/wsim.mp4) |
| 夹持不足 | [第 1 次](weak-0-r1/libero.mp4) · [第 2 次](weak-0-r2/libero.mp4) | [第 1 次](weak-0-r1/wsim.mp4) · [第 2 次](weak-0-r2/wsim.mp4) |
| 携物碰撞 | [第 1 次](rim-0-r1/libero.mp4) · [第 2 次](rim-0-r2/libero.mp4) | [第 1 次](rim-0-r1/wsim.mp4) · [第 2 次](rim-0-r2/wsim.mp4) |
| 篮外释放 | [第 1 次](outside-0-r1/libero.mp4) · [第 2 次](outside-0-r2/libero.mp4) | [第 1 次](outside-0-r1/wsim.mp4) · [第 2 次](outside-0-r2/wsim.mp4) |
