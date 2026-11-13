# 奶油奶酪抓放：原始 LIBERO 与 Steward 对照

2026-11-13，4090-2，使用当前真实 pi05_libero 服务 18065。本文为诊断记录；程序支持选择不同物体，不代表模型在固定场景中已经具备任意目标抓放能力。

## 结论与边界

本次确认两个独立问题：模型在汤罐任务的场景布局中未正确按语言切换到奶油奶酪；Steward 的近似碰撞预测在本次第 40 个动作上偏保守。原始 LIBERO 也复现了抓错目标，因此失败并非只在 Steward 中发生。当前模型在官方奶油奶酪任务中能够成功，不能将本次失败概括为“模型不会抓奶油奶酪”。

官方任务使用各自的物体组合、初态和布局。当前 Steward 场景仍是汤罐任务初态 0；在其中发送奶油奶酪指令，与运行官方奶油奶酪任务不同。原始 BDDL 中奶油奶酪任务的目标区域为 x=0.025–0.075、y=-0.125–-0.075；当前 Steward 所见奶油奶酪中心约为 [-0.1504, 0.0584, 0.0090]。两种官方任务的其他物体组合也有差异。场景与语言组合的目标定位／泛化不足是实验支持的解释；尚未排除 WSim 渲染差异的附加影响，也未验证所有固定布局、物体和随机样本。

## 实测结果

| 环境与条件 | 结果 | 原始记录 |
| --- | --- | --- |
| 历史官方 LIBERO 奶油奶酪任务，50 个初态 | 50/50 成功 | [全量逐回合记录](../../step1-results/full-benchmark/20261110T040128Z/libero_object.jsonl)，task_id=1；该文件含其他任务，不能把其他任务计入奶酪分母 |
| 当前同一模型服务，官方奶油奶酪任务，初态 0 | 123 个策略动作成功，原生独立成功信号为 true | [结果](native-cream-cheese-init0/result.json) · [逐步轨迹](native-cream-cheese-init0/trajectory.jsonl) · [录像](native-cream-cheese-init0/episode.mp4) |
| 原始 LIBERO 汤罐布局，改为奶油奶酪指令和奶酪入篮判据，第一次 | 280 动作预算耗尽，奶酪未成功；该轮未记录独立汤罐入篮布尔值 | [结果](native-soup-layout-cream-goal/result.json) · [轨迹](native-soup-layout-cream-goal/trajectory.jsonl) · [录像](native-soup-layout-cream-goal/episode.mp4) |
| 同一原始汤罐布局，重复 1 | 280 动作失败；汤罐曾入篮=true，奶酪曾入篮=false | [结果](native-soup-layout-cream-repeat1/result.json) · [轨迹](native-soup-layout-cream-repeat1/trajectory.jsonl) · [录像](native-soup-layout-cream-repeat1/episode.mp4) |
| 同一原始汤罐布局，重复 2 | 280 动作失败；汤罐曾入篮=true，奶酪曾入篮=false | [结果](native-soup-layout-cream-repeat2/result.json) · [轨迹](native-soup-layout-cream-repeat2/trajectory.jsonl) · [录像](native-soup-layout-cream-repeat2/episode.mp4) |
| Steward 真实聊天，奶油奶酪入篮 | 39 个动作后 prediction_rejected，目标未抓起，stopStatus=confirmed | [前次报告](../README.md)及本次 raw Job 路径见下文 |

三次原始汤罐布局对照均使用原始 LIBERO 控制器，不使用 Steward 碰撞预测。仅改模型 prompt 和独立目标判据，不改变物体布局；成功判据为 In(cream_cheese_1, basket_1_contain_region)。后两次额外记录 In(alphabet_soup_1, basket_1_contain_region)，避免把汤罐入篮误算成奶酪成功。初态、种子和控制配置一致，模型采样噪声未锁定；这些是独立复位回合，不代表所有随机条件的成功率。

模型输入核对：OpenPI 实际收到 `pick up the cream cheese and place it in the basket`、两路 224×224 RGB 和 8 项本体状态。Ego State 的目标 ID 与坐标绑定在 receipt 并用于跟踪和动作检查；当前 OpenPI 客户端没有将这些额外 target 元数据传成模型可使用的目标区域或坐标输入。因此系统选中正确对象，仍需模型根据图像与语言生成朝向该对象的动作。

## 归档脚本复跑

提交前使用本报告的 native_layout_comparison.py 再独立复跑四个回合：官方奶酪任务 160 步成功；汤罐布局改奶酪目标三次均 280 步失败，并均记录汤罐入篮=true、奶酪入篮=false。与最初诊断分开统计，总计当前模型的官方奶酪对照 2/2 成功，汤罐布局改目标 0/6 成功，其中五次有独立汤罐入篮布尔值，五次均抓放了汤罐。[脚本复跑汇总](reproduction-run/comparison.json)，同目录保存四回合 MP4、结果、逐步轨迹及初始输入图像。原始输出目录为 `/home/huawei/hqk/workspace/steward/steward/var/pi05-generic-place/diagnosis-publish-repro-20261113/`。

## 轨迹和碰撞预测对照

[原始双机位画面](raw-camera-montage.jpg)显示机器人向汤罐一侧移动。开始时末端离奶酪约 0.248 m、离汤罐约 0.305 m；39 个动作后分别约 0.321 m 和 0.0965 m。距离和逐段位置保存在 [offline-analysis.json](offline-analysis.json)。

离线使用第 39 动作后的同一 RGB-D／编码器快照和下一动作，复现 `segment=6, clearance=0.030701` 的拒绝。预测器使用 FK 线段扫掠、点云障碍及 0.035 m 距离阈值。另在隔离 WSim 中重放已接受的 39 个动作，并应用被拒绝的第 40 个动作：已接受部分的末端位置与实测最大误差为 0；全段未出现汤罐与机器人的物理接触；第 40 动作结束时，两者真实碰撞几何最近距离约 0.04027 m。[物理重放结果](physics-replay.json)

这个对照确认本次下一动作的预测偏保守；没有证明继续放行整轮都不会碰撞，也没有证明放宽阈值就能完成奶酪任务。诊断没有修改线上碰撞阈值、关闭检查或向线上机械臂提交额外动作。

只改变 prompt 的离线推理共 18 次：初始及临近拦截两帧，各以奶酪、汤罐、牛奶指令推理三次。模型输出随 prompt 改变，排除了客户端始终替换成汤罐指令的解释；采样噪声未固定，不能将输出差异全部归因于 prompt。[汇总](prompt-only/summary.json)，各次完整模型输出位于同目录子文件夹。

## 验收状态与下一步

程序侧不同物体准入、动态完整 prompt、绑定目标、候选抓放能力、证据判断和停止逻辑已实现，候选能力维持 candidate。固定汤罐布局中任意物体的实际策略抓放能力尚未通过。本次不修改模型、不降低碰撞阈值，也不把官方奶酪任务成功当成当前 Steward 奶酪任务成功。

下一步先把原始奶油奶酪任务的场景和初态接入 WSim，做同任务、同布局的对照；另用真实碰撞几何与 OSC 执行校准预测器。若产品要求在一套固定布局里任意选目标，还需单独验证模型的语言目标定位，或接入适合的目标条件策略接口；解除入口限制不足以交付该能力。

## 复现命令

在 4090-2 上复用已验证的真实模型服务 18065，确认线上没有正在运行的推理任务后执行。以下命令创建独立环境，不会重置或驱动正在展示的 Steward 场景。输出目录必须不存在，避免覆盖原证据。

```sh
cd /home/huawei/hqk/workspace/steward/steward
report=../self/design/pi05-intergration/steps/multi-object-pick-place-results/diagnosis
out=$(mktemp -d "$PWD/var/pi05-generic-place/repro.XXXXXX")

# 原始 LIBERO：官方奶酪对照一次，汤罐布局改奶酪目标三次；保存结果、轨迹和 MP4。
.venv/bin/python "$report/scripts/native_layout_comparison.py" \
  --port 18065 --out "$out/native"

# 已归档 RGB-D 与动作的预测离线重放。
.venv/bin/python "$report/scripts/analyze.py" --out "$out/analysis"

# 隔离 WSim：原样重放39个动作，再执行被拒绝的单个下一动作并检查真实几何。
MUJOCO_GL=egl OPENBLAS_NUM_THREADS=1 .venv/bin/python \
  "$report/scripts/physics_replay.py" --out "$out/physics"

# 18次只推理对照；不会下发动作。
.venv/bin/python "$report/scripts/prompt_probe.py" --out "$out/prompt-only"
```

后面三个脚本默认读取本次归档 Job 的原始 RGB-D，允许用 `--job` 指定其他同结构 Job 目录。原始 evaluator 的快照为 [reference-evaluate.py](reference-evaluate.py)，当时修改目标判据的完整实验脚本为 [native_soup_layout_cream.py](native_soup_layout_cream.py)。环境仍依赖已锁定的 OpenPI／LIBERO 与 pi05-step1 客户端环境，脚本不安装依赖、不启动或更换模型。

## 4090-2 原始资料位置

- 本轮聊天记录、结果与输入：`/home/huawei/hqk/workspace/steward/steward/var/pi05-generic-place/manual-chat-20261113T044728Z/`
- 本轮原始 Job、两路 PNG、RGB-D、模型请求与动作：`/home/huawei/hqk/workspace/steward/steward/var/step4/wsim-root/runs/20261113-115939/ego-panda/policy-jobs/panda-job-9ae791345f7445b6963996551c161524/`
- 本次所有诊断、原始推理输入和四个原生 LIBERO 回合：`/home/huawei/hqk/workspace/steward/steward/var/pi05-generic-place/diagnosis-cream-cheese/`

八段 MP4、逐步轨迹、结果 JSON、18 次模型输出和汇总随本报告提交。大体积 RGB-D／编码图像保留在上述原目录。详细结论与已知局限见 [diagnosis-summary.json](diagnosis-summary.json)；文件校验值见 [SHA256SUMS](SHA256SUMS)。

验证记录见 [validation.json](validation.json)。本次相关代码回归沿用已通过的 53 项 Python 和 17 项工具测试；新归档脚本的原生四回合、离线预测与隔离物理重放已实跑。全仓 doc-sync 在新增报告前后均为 38 passed、5 failed，失败项为已有文档配对、仓库引用、用词、笔记格式，以及 npm 缺失造成的文档类型检查失败；未修改其规则或绕过 Git hook。
