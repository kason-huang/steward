# 多物体抓放验证记录（2026-11-13）

系统验收：解除 libero_pick_place 仅接受 alphabet_soup 的限制。当前场景六种可抓物体均按当前 Ego State ID 与篮子 ID 准入，receipt 保存选中对象和目的地，pi05 收到与所选类别一致的完整抓放 prompt。单独 pick 保持原语义，独立 place 接口尚未扩展。能力模板 panda_pick_place 保持 candidate，未宣称已验证六物体策略成功率。接口、证据条件和安装方法见 [Panda 抓放说明](../../../../../steward/docs/panda-pick.md)。

实测：聊天发送 `pick up the cream_cheese and place it in the basket`，真实 pi05_libero@215abfb217dbac7d5f1273282331b9b1866c0479（端口 18065）收到 `pick up the cream cheese and place it in the basket`，目标绑定 e:1:seen-4。Job panda-job-b3824d784f4b419896bf44673eb54c1e 执行 39 个动作后因 Observed obstacle intersects predicted arm sweep 停止，state=failed、goalStatus=unconfirmed、stopStatus=confirmed、reasonCode=prediction_rejected。本轮没有完成抓取入篮；人工动作上限为 40，但实际由预测拒绝终止，未触发人工停止。Harness 随后的 pick/复合预测被故障锁拒绝，没有发起第二次实体动作。绑定和模型调用通过；真实策略操作成功验收未通过。

该次聊天使用了已有的 libero_soup_to_basket recipe 调用同一个复合 skill，因此该 recipe 的历史 verified 状态不表示奶油奶酪抓放已通过。工具说明已明确非汤罐使用 panda_pick_place 候选或直接工具；历史 soup recipe 的记录保留。该次原始 Job 在异常路径下 evidence 为空，latest-judgment.json 和 observations.jsonl 保留判定；当前实现已补全停止后的 Job evidence，拒绝轨迹回归已覆盖。

测试：最终抓取/放置证据模块 29 passed，真实 HTTP/WSim Job 模块 24 passed；工具构建通过，Node 工具与候选能力测试 17 passed。Job 测试覆盖六类别准入、完整 prompt、绑定奶油奶酪目标/篮子、空动作不报成功、停止、故障锁与 Reset、新鲜篮子观测。RGB-D 证据测试覆盖抓起/搬运/释放/撤爪的正例，以及未抓起、未松爪、遮挡、工具未撤离、移动未稳定、篮外、篮子消失、篮子小幅移动后使用当前几何和篮内原有同色物体的负例。视觉合成正例与空动作策略测试不等于真实 pi05 抓放成功。

首次组合回归为 46 passed、1 failed：原停止耗时断言 4 秒，实测 4.05 秒；单独复查 1 passed，后续最终 Job 模块 24 passed。原始失败日志 tests.log 保留，未放宽停止断言。

4090-2 原始文件位置：

- 总目录：`/home/huawei/hqk/workspace/steward/steward/var/pi05-generic-place/`
- 聊天、状态与 World/Ego 截图：`/home/huawei/hqk/workspace/steward/steward/var/pi05-generic-place/live-chat-cream-cheese/`
- 外部/腕部相机动画：该总目录的 `cream-cheese-external.gif`、`cream-cheese-wrist.gif`，由逐动作原始相机 PNG 按 20 fps 编码，包含停止清理阶段；格式为 GIF。
- 原始 Job、receipt、推理 prompt/目标、逐动作、停止确认、双机位 PNG 和 RGB-D：`/home/huawei/hqk/workspace/steward/steward/var/step4/wsim-root/runs/20261113-115354/ego-panda/policy-jobs/panda-job-b3824d784f4b419896bf44673eb54c1e/`
- 活跃服务：7400 控制台、7410 Robot、7420 Ego API；模型仍为 18065。验证后重启到初态 0，未自动下发任务。

复现（4090-2）：

```sh
cd /home/huawei/hqk/workspace/steward/steward
MUJOCO_GL=egl OPENBLAS_NUM_THREADS=1 .venv/bin/python -m pytest tests/test_panda_pick.py tests/test_panda_jobs.py -q
cd dsh
export PATH=/home/huawei/hqk/workspace/steward/steward/var/dsh-toolchain/bin:/home/huawei/hqk/workspace/steward/steward/var/dsh-toolchain/node/bin:$PATH
pnpm run build
node --test test/tools.test.mjs test/panda-pick.test.mjs test/panda-pick-place.test.mjs
```

人工交互复现：打开本地转发后的控制台，先 Reset 到初态 0，等待 Ego State 完成六物体识别，再向机器人聊天发送上述指令。物体 ID 由当前轮 Ego State 查询，不能复制报告中的旧 ID。预测拒绝后先 Reset 再发起下一次任务。
