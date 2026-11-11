# Step 6 初态 0 验证

2026-11-11。初态 0 的真实 π0.5 完整抓放与独立 WSim 篮底承托验证通过。最终回合每五条动作重新观测和推理，共 31 次推理、155 条动作。松爪撤离后连续 125 个物理步由篮底稳定承托，撤销租约后再次确认承托及机器人停稳。全部输入、动作、逐步物理日志和双机位录像已归档。[完整抓放报告与复现命令](full-task/README.md) · [最终成功录像](full-task/full-20261111T022014Z/external-wrist.mp4) · [全部尝试](full-task/attempts.json)

成功回合在 4090-2 的原始目录为 `/home/huawei/hqk/workspace/steward/steward/var/step6/full-20261111T022014Z/`。归档副本位于本目录下 `full-task/full-20261111T022014Z/`。新加载策略服务使用 18066。实时模式、EH 工具和正式成功率按后续方案验收。

## 2026-11-10 只推理与悬空前缀记录

在 4090-2 的独立 Panda WSim 和 ER 上，从初态 0 取同一物理步的外部相机、腕相机及本体状态。使用 Step 1 固定版 OpenPI 客户端访问本机 `pi05_libero`，输入为两路 224×224 RGB、8 项状态和字母汤罐任务指令。OpenPI 提交为 `215abfb217dbac7d5f1273282331b9b1866c0479`。策略服务监听回环端口 18065。

只推理获得了 10 条七维有限数值动作。独立只推理运行的完整前后 WSim 状态一致，物理步为 0，执行器租约未取得，应用记录为空。初次实测端到端策略往返耗时约 8.91 s，第二轮的已预热请求约 0.08 s。

策略试验使用独占租约。WSim 在双机位同一状态快照采集后签发观测凭据，在入队和物理应用时检查 Episode、代次、物理步和墙钟年龄。每个观测允许最多三个 25 子步周期，墙钟有效期 20 s。策略试验脚本先检查整个短前缀，WSim 再检查逐条提交的动作。故障门禁通过后才执行短前缀。

| 故障 | 实测 |
| --- | --- |
| 缺少腕图 | 输入生成被拒，WSim 拒绝无凭据的动作。推进 25 步后无应用记录。 |
| 观测步过期 | 推进超过观测步窗口后提交被拒，应用记录为空。 |
| 排队后重置 | 重置前动作处于排队，重置后旧 Episode 的后续提交被拒，新 Episode 推进后无旧动作。 |
| 排队后墙钟过期 | 推进前凭据到期，WSim 撤销控制并清空待执行动作。 |

首轮只在预设悬空区域内验证动作周期与停稳。单条平移和轴角分量分别限于归一化的 ±0.20 和 ±0.06，目标位置限制在 x 为 −0.20 至 −0.10 m、y 为 −0.05 至 0.05 m、z 为 0.20 至 0.30 m。最初的试验仅允许张爪，实际模型给出闭爪指令并被拒绝。复测允许同一悬空区域内双向夹爪，沿用 Panda 本体的夹爪限幅。该试验覆盖悬空前缀和防旧动作门禁，抓放与任务成功率属于后续验证。

只推理原始结果在 `steward/var/step6/inference-1/`，第一次拒绝记录在 `steward/var/step6/validated-1/`，复测数据在 `steward/var/step6/validated-2/`。其中 `observation.json` 含实际送入推理的两路 PNG，`inference.json` 含原始候选序列。每段的 `applied.json` 记录实际应用的动作、序号、起止步和末端位置。

## 短前缀实测

| 轮次 | 观测步 | 三条应用动作的结束步 | 推理往返 | 段末工具位置 |
| --- | ---: | --- | ---: | --- |
| 1 | 0 | 25、50、75 | 0.086 s | (-0.15279, -0.00596, 0.24594) m |
| 2 | 75 | 100、125、150 | 0.075 s | (-0.15221, -0.00568, 0.24220) m |

每轮丢弃剩余 7 条候选动作。第 6 条动作后撤销租约，推进至第 650 步时，WSim 报告停稳且无待执行动作。末端线速度为 2.42e-05 m/s，角速度为 8.1e-05 rad/s。[故障门禁](validated-2/fault-gates.json) · [应用记录](validated-2/prefixes.json) · [停稳记录](validated-2/stop.json)

针对性回归通过 20 项。Step 5 的普通 OSC 动作与同步观测探针也通过。[回归日志](pytest-focused.log) · [原有入口复验](legacy-osc-smoke.json)

## 复现

在 4090-2 启动固定版本策略服务，若端口 18065 已由同一固定版本服务占用，可复用。

```bash
cd /home/huawei/hqk/workspace/pi05-step1
bash server.sh --port 18065
```

在另一个终端中执行。每个命令使用新的输出目录。第一条只推理，第二条先验证四项门禁，随后执行两轮、每轮三条真实策略动作。

```bash
cd /home/huawei/hqk/workspace/steward/steward
out=$(mktemp -d "$PWD/var/step6/repro.XXXXXX")
.venv/bin/python scripts/verify_libero_step6.py var/step4/wsim-root "$out/inference" --port 18065
.venv/bin/python scripts/verify_libero_step6.py var/step4/wsim-root "$out/validated" --port 18065 --short-prefix
```
