# Step 8：初态 0 端到端实测报告

2026-11-11，4090-2，分支 `feat-pi05-intergration`。本轮按缩小后的 [Step 8 方案](../step8.md)完成：**最终 smoke-03 的原始 LIBERO 参考和 WSim 真实 Ego Harness 链路均通过**。原始侧执行 280 条策略动作后物理成功、停止 confirmed；WSim 执行 150 条动作，唯一 Panda Job 为 succeeded，目标 achieved，停止 confirmed，独立物理评估成功。所有开发试运行均保留，包括 smoke-02 原始策略没有抓起汤罐的失败。

任务为 `pick up the alphabet soup and place it in the basket`，类别为抓取、搬运、容器内放置。场景 `libero-object-alphabet-soup-panda`，仅初态 0。此报告证明一条完整流程已打通，不代表多初态稳定成功率或 LIBERO 全任务验收。

## 1. 结果与验收

| 尝试 | 原始 LIBERO | WSim | 两侧完整通过 | 说明 |
| --- | --- | --- | --- | --- |
| smoke-01 | 280 动作，物理成功，停止 confirmed | 185 动作，succeeded / achieved / confirmed，物理成功 | 是 | 首轮；复核后的更严格抓取和放置核对也通过 |
| smoke-02 | 280 动作，物理失败，停止 confirmed | 155 动作，succeeded / achieved / confirmed，物理成功 | 否 | 原始侧没有形成双指抓起、搬运和释放，汤罐仍受地面支撑；正常耗尽动作预算，无超时 |
| smoke-03 | 280 动作，物理成功，停止 confirmed | 150 动作，succeeded / achieved / confirmed，物理成功 | 是 | 最终执行代码；两侧命令 exitCode=0、timedOut=false；严格核对通过 |

三轮是开发与复核记录，代码散列分别保留于各轮 frozen/manifest.json，不能合并为同一版本的正式成功率批次。smoke-01 的 metrics 在增加命令退出门槛前生成，原始命令日志保留正常退出证据；其复核结果独立保存在 after-review-completion.json。最终验收以 smoke-03 为准，未覆盖或删去 smoke-02。

smoke-03 两侧与冻结初态的 16 个关节 qpos/qvel 均匹配，阈值 1e-8。世界轨迹逐物理步连续：原始侧 7500 步，WSim 4275 步，均经历抓起、搬运、释放，最终连续 125 步满足篮底承托、撤爪和稳定阈值。WSim 机器人首次确认抓取在第 1775 步，独立轨迹确认双指接触、离地且无地面支撑；第 3725–4275 步的全部放置确认均对应已释放、篮内、篮底支撑、无其他外部支撑和足够撤爪距离。停止清理记录确认 owner 已释放、无时钟 hold、终态不可变且无迟到动作。

完成标准沿用 Step 6/7：双指接触、离地至少 0.015 m、夹持搬运至少 0.035 m；松爪后指垫不再承托，罐在篮内、篮底力至少 0.05 N、无其他外部支撑、末端距离至少 0.08 m；连续 125 步线速度不超过 0.005 m/s、角速度不超过 0.05 rad/s。物理评估器读取独立世界轨迹，未将世界物体标签、接触真值或成功信号输入机器人决策。机器人判断来自 RGB、深度和本体观测。

真实链路为：用户请求 → Steward chat → 真实 ego profile → 获取本轮 ES 的能力 v2 → 嵌套 run_code → Ego API → Panda Job → pi05 推理及动作准入 → 机器人观测确认 → 停止与资源释放。完整会话、能力、Job、receipt 和清理证据均已保存。

| 验收项 | 状态 |
| --- | --- |
| 初态 0 端到端、两侧物理成功 | 通过，以 smoke-03 为最终证据 |
| 真实能力 v2 和唯一 Job，目标/停止分别确认 | 通过 |
| 首次抓取确认、全部放置确认与独立物理状态对应 | 通过 |
| 针对性单元和关联回归 | 36 passed，60.96 s |
| 双机位视频、原始日志、复现命令 | 已归档 |
| 多初态批次、全量识别负例 | 本轮暂缓 |
| 全仓库回归 | 未声明通过；旧本体转向基线失败与快照依赖缺失仍见 Step 7 |

## 2. 固定条件与比较限制

模型为真实 pi05_libero，沿用原始权重与 pinned OpenPI。OpenPI 源码为 `215abfb217dbac7d5f1273282331b9b1866c0479`，LIBERO 为 `f78abd68ee283de9f9be3c8f7e2a9ad60246e95c`。原始环境为 Python 3.8.20 / MuJoCo 3.2.3，WSim 使用项目 Python 3.12 / MuJoCo 3.17.0。两侧加载同一预热 10 周期后的初态，20 Hz，每条动作 25 × 2 ms，执行前五条后重新推理；等待推理时冻结仿真。

能力 `libero_soup_to_basket` 固定使用 Step 7 N1-06 保存的 v2，code SHA256 为 `4ddc93dc69ea1160ef0c22179139cc6999c30656821e72132d0bd0a3e122c505`。WSim 不能通过修改能力或多起 Job 获得通过。每侧独立新加载策略服务，只使用一次相同输入预热；Python/NumPy 种子为 0，策略服务默认随机状态和 EH API 的采样未覆盖，故复现流程不保证逐动作相同。

两侧独立调用同版模型，不共享固定动作。原始参考按固定最多 280 条动作结束，不用世界成功信号提前停止；WSim 根据自身观测可提前确认完成，并有 receipt、owner、观测代次、FK、关节和可见扫掠检查。推理请求最多 15 s、执行最多 420 s、停止墙钟最多 20 s、验证子进程最多 600 s；停止观察为 500–2500 物理步。不能把动作数或结果差异单独归因于仿真器。

全部 21 个视频文件解码通过，33 个本地链接存在，九个最终执行源码散列与冻结清单一致；原始与压缩会话的凭据检查未发现泄漏。成功及失败录像的[画面抽查](checks/video-contact.png)与各自结果一致，详见 [产物核对记录](checks/artifact-audit.json)。

本轮新增原始环境相机录制和统一运行、冻结、命令回执、独立物理评估及完成核对。相机兼容探针第一次暴露原版 MuJoCo 不支持 mj_copyData，已改为私有 MjData 上复制状态并 forward；第二次探针通过，不计入任务回合。录像写入使用有截止时间的非阻塞管道，渲染器与编码器失败可释放资源。开发阶段检查日志和审查修复摘要见 checks/。

## 3. 报告、逐回合数据与录像位置

4090-2 报告根目录：`/home/huawei/hqk/workspace/steward/self/design/pi05-intergration/steps/step8-results/`。打开 [HTML 录像索引](index.html)可查看全部三轮，两侧均为左外部视角、右腕部视角，另提供拆分机位。视频按 20 fps 仿真时间播放，不包含模型/LLM 等待墙钟时间。

最终成功录像：

- [smoke-03 原始 LIBERO 双机位](attempts/smoke-03/libero/videos/dual.mp4)，[外部](attempts/smoke-03/libero/videos/external.mp4)，[腕部](attempts/smoke-03/libero/videos/wrist.mp4)。
- [smoke-03 WSim 双机位](attempts/smoke-03/wsim/videos/dual.mp4)，[外部](attempts/smoke-03/wsim/videos/external.mp4)，[腕部](attempts/smoke-03/wsim/videos/wrist.mp4)。
- [smoke-02 原始策略失败录像](attempts/smoke-02/libero/videos/dual.mp4)。

核心结果为 [attempts.json](attempts.json)、[最终指标](attempts/smoke-03/metrics.json)、[逐回合结果](attempts/smoke-03/episodes.json)、[完成判断核对](attempts/smoke-03/wsim/completion-check.json)。各轮还保留 frozen 配置、逐次推理输入输出和动作、逐物理步轨迹、初态比较、EH 原始压缩会话、Job、清理证据、逐侧执行回执、模型服务日志及 commands.jsonl 的实际命令。

完整原始目录：`/home/huawei/hqk/workspace/steward/steward/var/step8/`。本报告的 raw-evidence 软链接指向此处。未纳入 Git 的大体积传感器原包和逐帧 PNG 保留在各 smoke-*/libero/sensors/、smoke-*/wsim/runtime/ego-panda/policy-jobs/panda-job-*/external/ 和 wrist/ 下；报告内已有可解码视频和核心证据副本，不依赖该软链接才能查看结果。Step 8 专用端口 18068 在测试后关闭，现有演示服务保留。

## 4. 在 4090-2 复现

使用本机现有依赖：项目 .venv、var/step4/wsim-root、var/step7 的 policy.toml 与已安装真实 profile、pi05-step1 下的原始客户端/服务端环境和模型、Step 7 的 v2 能力快照。DeepSeek key 从 `steward/.env` 读取，不能复制到命令、日志或报告。启动前确保独立端口 18068 空闲；运行入口拒绝替换占用端口的进程。

```bash
ssh rtx-4090-2
cd /home/huawei/hqk/workspace/steward/steward
export MUJOCO_GL=egl OPENBLAS_NUM_THREADS=1
export PATH="$PWD/var/dsh-toolchain/bin:$PWD/var/dsh-toolchain/node/bin:$PATH"
# 输出目录须不存在，每次重跑更换编号；每侧各一个回合，不自动重试。
.venv/bin/python -u scripts/run_libero_step8.py var/step8/smoke-reproduce-01
# 即使上一条返回失败，也应归档保留结果；该命令收集所有 smoke-* 完整尝试。
.venv/bin/python scripts/archive_libero_step8.py var/step8 ../self/design/pi05-intergration/steps/step8-results
```

针对性检查：

```bash
.venv/bin/python -m pytest tests/test_step8.py tests/test_panda_jobs.py tests/test_chat.py -q
cd /home/huawei/hqk/workspace/steward/self/design/pi05-intergration/steps/step8-results
sha256sum -c SHA256SUMS
# 可在本机终端打开报告目录文件；通过 HTTP 查看使用独立临时端口。
python3 -m http.server 17408 --bind 127.0.0.1
```

如从自己电脑查看，在另一终端运行 `ssh -N -L 17408:127.0.0.1:17408 rtx-4090-2`，浏览器打开 `http://127.0.0.1:17408/index.html`。HTTP 服务为手动查看命令，未作为常驻服务启动。

统一入口自动生成 frozen/initial.json、calibration.json、manifest.json、能力和策略配置，拒绝初态不匹配，记录各侧独立命令和退出状态。底层逐侧命令见 [smoke-03/commands.jsonl](attempts/smoke-03/commands.jsonl)，可直接核对执行路径和参数；只运行统一入口即可完成新目录下的两侧流程。

最终执行源码副本在 source/，散列与 smoke-03 的九个冻结源码文件逐一核对。提交前仅清理运行文件 libero_physical_evidence.py 的末尾空行以满足仓库格式检查，AST 完全相同；执行时原件及冻结散列保留，规范化前后散列见 checks/artifact-audit.json。SHA256SUMS 覆盖本报告、核心证据、源码、日志和视频，不跟随 raw-evidence。未来归档会纳入新尝试；本次已提交记录以 Git 提交为准。

后续当前控制台普通英文任务的权重加载问题、两次独立修复验证和启动命令见[控制台修复报告](live-chat-fix/README.md)。本报告原三轮数据及对应执行时源码保留。
