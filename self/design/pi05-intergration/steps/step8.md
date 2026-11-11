# Step 8 初态 0 单例端到端验证

2026-11-11。按本次范围调整，仅使用初态 0，先完成一次可复现的完整测试流程。多初态、固定批次成功率和全量感知负例留待后续。对应[总计划第 8 步](../pi05-integration-plan.md#8-完成判断与成功率验收)，原始证据参考 [Step 7 实测报告](step7-results/README.md)。状态：初态 0 单例端到端验收通过；最终代码的 smoke-03 两侧物理成功，WSim 真实能力链路通过。三轮尝试及一次原始策略失败均保留，见 [Step 8 实测报告](step8-results/README.md)和[录像索引](step8-results/index.html)。

## 1. 本轮目标

冻结模型、场景、初态、预算、能力和评估器，先运行原始 LIBERO 的初态 0 策略参考，再由 Steward 真实 ego profile 通过嵌套 run_code、Ego API 和 Panda Job 完成同一字母汤罐入篮任务，最后核对机器人判断、独立物理结果和完整产物。策略为真实 pi05_libero，prompt 为 `pick up the alphabet soup and place it in the basket`。

本轮每侧计划一个正常回合。报告分开给出单例物理结果和 WSim 完整链路结果，不将一例通过宣传为稳定成功率。失败与额外重试均保留，不能覆盖原记录。

## 2. 冻结配置

| 项目 | 本轮配置 |
| --- | --- |
| 场景 | libero-object-alphabet-soup-panda，初态 0；与原始 LIBERO 对齐同一预热 10 周期后的快照 |
| 策略与能力 | 沿用 Step 7 模型、权重和 OpenPI；能力取 N1-06 保存的 libero_soup_to_basket v2，冻结 code 哈希 |
| 随机性 | 原始客户端和 WSim Python/NumPy 基础种子 0；策略服务默认随机状态、EH API 不可控采样如实记录，不宣称全部确定性 |
| 控制 | 20 Hz，每动作 25 个 2 ms 物理步；执行前五条后重新观测推理，推理等待期间冻结仿真 |
| 预算 | 最多 280 条动作；推理请求 15 s，Job 执行 420 s，停止墙钟 20 s，EH/验证器 600 s |
| 停止观察 | 至少 500、最多 2500 物理步；停止确认和目标结论独立 |
| 服务 | 使用独立专用策略端口，每侧新加载同版服务，只做一次相同原始输入预热，不按动作内容挑服务 |
| 观测 | 保持策略 RGB 预处理，深度使用独立无 MSAA 渲染；使用真实机器人传感器和本体反馈 |
| 执行准入 | 保留 Step 7 的 receipt、owner、Episode/控制代次、FK、关节范围、工作区和可见扫掠检查 |

运行前生成 `frozen/manifest.json` 和校验清单，保存原始初态、标定、能力和代码版本。实际初态与冻结快照逐关节比较，不能只在记录里写 initialState=0。新增输出目录不可覆盖。

## 3. 执行流程

| 阶段 | 操作 | 必须保留的证据 |
| --- | --- | --- |
| 8A 准备 | 核对初态、能力 v2、配置；做相机和状态探针、针对性回归 | 快照比较、标定、代码/配置/能力散列、检查日志 |
| 8B 原始参考 | 在原版 LIBERO 运行初态 0 的真实策略 | 实际模型输入输出、动作、逐物理步轨迹、停止结果、双机位录像 |
| 8C Steward 完整链路 | 新会话、新模型服务，真实 profile 获取冻结 v2 并嵌套执行，查询本轮 ES 引用 | EH 原始会话、能力快照、receipt、唯一 Job、应用/完成回执、机器人观测、停止与清理 |
| 8D 判定核对 | 从保存的观测和世界轨迹独立核对抓起、搬运、释放、撤爪与稳定承托 | 判断时序、终态对照、原始失败及原因；不把 GT 送入机器人决策 |
| 8E 归档 | 生成结果索引、报告、完整命令和录像，复核散列与脱敏 | 可打开的报告、可解码视频、SHA256SUMS 和原始目录位置 |

原始参考没有 EH，按固定最多 280 条动作或既定墙钟/故障条件结束，再停止和观察，不用世界成功信号提前截断策略。WSim 可依据自身观测提前完成。两侧停止协议、MuJoCo 和动作检查差异在报告中说明；不把结果差值单独归因于仿真器。

WSim 必须经过真实 ego profile 和嵌套 run_code，不能用直接 API 或固定动作替代本轮完整链路。能力引用来自本轮 ES；遇到 failed、stopped、unconfirmed 或 unknown 时结束依赖程序，禁止自动重试。若 EH 改写能力代码，保留偏离记录且不能记功能通过。

## 4. 判定和记账

物理评估沿用 Step 6/7：双指接触并离地至少 0.015 m，夹持搬运至少 0.035 m，松爪后无指垫承托；最终罐在篮内、篮底力至少 0.05 N、无其他外部支撑，末端距离至少 0.08 m，连续 125 步线速度不超过 0.005 m/s、角速度不超过 0.05 rad/s。采样必须连续、物理结果必须可信；只曾短暂入篮不足以代替最终承托。

完整链路通过要求同一 Episode 的真实能力执行、唯一 Job 为 succeeded、目标为 achieved、停止为 confirmed、世界物理成功、动作检查与信息隔离成立、无人工干预且资源释放。世界成功但机器人未确认时，只记物理成功；能力库 verified/runs 与辅助 run_code 的计数不能代替物理回合数。

另核对机器人首次抓取/放置确认与对应世界状态，记录判断时刻、释放/撤爪及稳定窗口；声明完成后不能再发送策略动作来补救该次声明。缺帧、旧观测、Episode 重置和停止未知的已有针对性回归继续保留。本轮不启动五初态的全量识别负例采集。

加载/复位失败、初态不符或世界轨迹缺失记为评估无效；模型、LLM、工具、越界拒绝、完成未确认和停止未知按实际失败保留。额外尝试使用新目录，不覆盖原失败。单例可以报告 0/1 或 1/1，但不据此判断稳定性或跨任务能力。

## 5. 实施与产物

4090-2 工作区为 `/home/huawei/hqk/workspace/steward`，使用 `feat-pi05-intergration` 分支。代码、文档和核心验收材料在此分支本地提交，不推送。既有展示服务与历史结果保留。

运行入口为 `steward/scripts/run_libero_step8.py`，负责独立目录、冻结清单、模型加载/预热、两侧运行和汇总；`archive_libero_step8.py` 生成报告与双机位录像。原始数据位于 `/home/huawei/hqk/workspace/steward/steward/var/step8/`，报告位于 `/home/huawei/hqk/workspace/steward/self/design/pi05-intergration/steps/step8-results/`。完整依赖和逐侧实际命令见实测报告及各轮 `commands.jsonl`。

```bash
ssh rtx-4090-2
cd /home/huawei/hqk/workspace/steward/steward
export MUJOCO_GL=egl OPENBLAS_NUM_THREADS=1
export PATH="$PWD/var/dsh-toolchain/bin:$PWD/var/dsh-toolchain/node/bin:$PATH"
.venv/bin/python -u scripts/run_libero_step8.py var/step8/smoke-reproduce-01
.venv/bin/python scripts/archive_libero_step8.py var/step8 ../self/design/pi05-intergration/steps/step8-results
```

输出目录必须不存在；再次复现更换末尾编号。入口使用独立端口 18068，发现占用即退出。它每侧只运行一个回合，不在失败后自动重试。

每侧保存配置和初态、模型调用、同步观测、独立物理轨迹及双机位录像；WSim 另存完整 EH 会话、能力代码、receipt、Job 和停止/清理证据。报告包含所有尝试、两侧结论、差异和保留项。媒体解码、链接、散列与凭据泄漏检查通过后归档。

| 项目 | 当前状态 |
| --- | --- |
| 范围调整为初态 0 单例 | 已完成 |
| 运行入口、冻结配置、初态核对 | 通过，16 个关节 qpos/qvel 误差不超过 1e-8 |
| 原始 LIBERO 策略参考 | smoke-03 通过，280 条动作，物理成功且停止 confirmed；smoke-02 未抓起，保留失败 |
| WSim 真实能力完整链路 | smoke-03 通过，150 条动作，唯一 Job succeeded / achieved / confirmed |
| 完成判断与物理结果核对 | 通过，首次抓取确认和全部放置确认均有对应独立物理证据；最终稳定承托 125 步 |
| 针对性回归 | 36 项通过；不等同全仓库回归通过 |
| 报告、录像、复现命令 | 已归档，见 step8-results；原始传感器与帧图保留 var/step8 |
| 多初态批次、全量识别负例 | 按本轮范围暂缓 |

旧本体基线转向失败和缺失快照依赖仍按 Step 7 的记录单独跟踪；未解决时保留验收状态。本轮单例完成不等于全套回归或原先多初态成功率验收完成。

2026-11-11 后续控制台复核发现直接 GPU 恢复 checkpoint 时，跨进程的视觉网络权重可能不一致。现在 Step 7/8 的模型启动入口改为主机恢复、逐张量传输与回读校验，首次编译后复核参数；冷启动预热上限为 60 秒，任务请求仍为 15 秒。当前控制台原样英文任务已在两次独立加载后完成，详见[控制台修复报告](step8-results/live-chat-fix/README.md)。原三轮结果、失败和执行时源码散列保持原记录。
