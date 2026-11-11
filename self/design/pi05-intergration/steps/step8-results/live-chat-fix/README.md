# 控制台英文抓放任务：权重加载修复

2026-11-11，4090-2。用户在当前控制台机器人聊天框发送 `pick up the alphabet soup and place it in the basket` 后，原服务多次以 action_outside_authorization 失败。修复模型加载后，在同一展示服务中两次独立模型启动、同一初态 0、原样英文指令均完成：verified-01 为 155 条动作，verified-02 为 150 条动作，均为 succeeded / achieved / confirmed。普通指令经过真实 ego profile、能力程序、Ego API 和 Panda Job；没有替换为固定动作或增加任务提示。

[全部录像](index.html) · [全部任务结果](attempts.json) · [独立对照摘要](diagnosis/comparison-summary.json) · [源码副本](source/) · [散列](SHA256SUMS)

## 复现与定位

原场景的首帧两路 224×224 策略图像与 Step 8 smoke-03 成功回合逐像素相同，8 项状态最大差为 0，prompt 和 target/on 参数正确。失败并非对象没有识别，或中文/英文指令不匹配。首次失败动作数 85，后续原地重试为 0；复位后的原服务为 82，一次简单重启后仍为 95，均未抓起汤罐。失败时保持原授权边界，未将越界拒绝改为成功。

隔离诊断进程加载同一 checkpoint 后，51 个参数块中只有视觉网络 `PaliGemma/img/Transformer/encoderblock/MlpBlock_0/Dense_1/kernel` 的散列不同，形状为 [27, 4304, 1152]、bfloat16。两进程变换后的输入与初始采样种子一致，其中一个进程的推理输出非有限。参数差异见 model-parameter-difference.json，两进程清单与输入见 diagnosis/instrumented-*。

使用 NumPy 在主机内存独立恢复两次，该张量均有限，散列一致为 `40eb1506905b358a291ebcf2663897ee6ab5b4791f886d06900403f75ebb4ed0`。它匹配其中一个 GPU 进程；另一 GPU 进程为 `8146105ab3d0dc6cb9b1f0975b2537aa592914e88f0fbcefaf31c43dfb29f1ab`。这些对照支持将当前故障定位到加载链路，而不是放宽运动边界。尚未进一步隔离 Orbax、JAX 传输或 CUDA 驱动内部的具体责任，不能把它写成 checkpoint 文件已损坏或 GPU 硬件故障的结论。

## 修复

新入口 `scripts/launch_pi05_libero.py` 在原 GPU venv 中运行 `serve_pi05_libero.py`，保留固定 OpenPI、checkpoint、官方有序输入/输出变换及默认采样。OpenPI 本身提供 [NumPy 恢复参数的接口](https://github.com/Physical-Intelligence/openpi/blob/215abfb217dbac7d5f1273282331b9b1866c0479/src/openpi/models/model.py)。本适配器在主机恢复后逐张量传输，等待完成并回读，比较形状、dtype 和全部字节的 SHA256；失败则不对外提供动作。模型构造后和首次编译推理后再次检查参数。

两次独立启动的全部 51 张量清单相同，参数清单总 SHA256 为 `77f91766948ba88b9256849b68ce74880420b90d0acafaf35b94c46ff9ab8880`。具体清单分别保存在两个 verified 回合的 parameter-integrity.json。首次预热动作的差值如实保存在 diagnosis/comparison-summary.json；只根据受校验参数和实际任务结果验收，不以挑选推理输出的方式选择模型服务。

Step 7/8 启动脚本已使用新入口，Step 8 冻结清单纳入三个新源码文件，故障注入仍核验专用进程及端口。冷启动编译与校验的预热上限为 60 秒；任务推理 15 秒、动作检查、预算、初态、授权区域及停止标准保持本次原配置。

## 当前控制台实测

每轮复位至初态 0，速度为 1，推理时冻结仿真，World/Ego 视图与两路机器人相机可用。表内所有失败保留，不能将它们合并为两次通过的成功率统计。verified-02 的 SSH 监视连接曾中断，Panda Job 与 EH 独立继续运行；随后只读取已存在 Job 的终态，没有重新发起任务，job-status.jsonl 因此不是完整墙钟轮询记录。

| 尝试 | Episode | 动作数 | Job | 目标 | 停止 |
| --- | --- | ---: | --- | --- | --- |
| before-cecb418b | 1 | 85 | failed | unconfirmed | confirmed |
| before-86145c3e | 1 | 0 | failed | unconfirmed | confirmed |
| before-f9fc07d7 | 2 | 82 | failed | unconfirmed | confirmed |
| before-1ab65ccd | 4 | 95 | failed | unconfirmed | confirmed |
| verified-01 | 5 | 155 | succeeded | achieved | confirmed |
| verified-02 | 6 | 150 | succeeded | achieved | confirmed |

成功两轮的抓起和放置判断均 confirmed；末端撤离后汤罐留在篮中，见双机位录像和保存的 console/ego 状态。本次是当前控制台功能复核，没有为正在运行的 WSim 注入新的世界逐物理步记录器，因此不额外宣称本报告重新做了完整的独立接触力验收；原 Step 8 的独立物理验收材料保留。

40 项回归通过：4 项新检查及原 36 项关联检查。新检查覆盖传输时单元素被改变、非连续主机数组、模型构造后参数被改变、非有限 checkpoint，以及保留虚拟环境解释器路径。启动器路径检查的原失败与修复后的完整日志均在 diagnosis/ 下。未重复全仓库测试。

## 位置与查看

报告：`/home/huawei/hqk/workspace/steward/self/design/pi05-intergration/steps/step8-results/live-chat-fix/`。

原始目录：`/home/huawei/hqk/workspace/steward/steward/var/pi05-live-20261111T072702Z/diagnosis/`。逐回合 PNG 和传感器包在 attempts.json 各行的 raw 路径。两个主机恢复张量的 uint16 原始文件仅保留原目录，未将模型参数写入 Git。本报告保存散列、统计、首帧输入、首批推理、完整聊天结果和转码视频。

成功录像：[verified-01 双机位](attempts/verified-01/videos/dual.mp4) · [verified-02 双机位](attempts/verified-02/videos/dual.mp4)。对应目录另有 external.mp4 和 wrist.mp4。录像按 20 fps 仿真时间播放，不含推理冻结等待。

## 完整启动与复现命令

以下使用 4090-2 已有 venv、OpenPI 和模型、var/step4/wsim-root、已安装的 Step 7 profile。先正常停止占用相应端口的展示服务，再在两个终端启动，不与已有服务争用同一端口。

终端 A：

```bash
cd /home/huawei/hqk/workspace/steward/steward
# 用新的目录编号保存每次独立启动的证据。
test ! -e var/pi05-verified-manual-01
.venv/bin/python scripts/launch_pi05_libero.py --port 18067 \
  --evidence var/pi05-verified-manual-01
```

终端 B：模型监听就绪后，做一次原始输入预热，再启动控制台。

```bash
cd /home/huawei/hqk/workspace/steward/steward
export MUJOCO_GL=egl OPENBLAS_NUM_THREADS=1
export PATH="$PWD/var/dsh-toolchain/bin:$PWD/var/dsh-toolchain/node/bin:$PATH"
timeout 60 bash /home/huawei/hqk/workspace/pi05-step1/client.sh \
  "$PWD/scripts/probe_pi05_step6.py" \
  /home/huawei/hqk/workspace/pi05-step1/runs/20261110T021656Z/target-qualification/policy/000.npz \
  "$PWD/var/pi05-verified-manual-01/preflight.json" --port 18067
.venv/bin/steward --root var/step4/wsim-root up \
  --scenario libero-object-alphabet-soup-panda \
  --panda-policy-config var/step7/policy.toml \
  --wca-port 7400 --robot-port 7410 --api-port 7420 \
  --world-harness "$PWD/var/dsh/world" \
  --robot-harness "$PWD/var/step7/dsh/robot-1" \
  --seed 0 --speed 1 --freeze-idle --no-dispatch-task
```

本机另开终端 `ssh -N -L 17400:127.0.0.1:7400 rtx-4090-2`，访问 `http://127.0.0.1:17400/console`，选择机器人聊天并发送原句英文。不要把任务发给 World Harness。当前已经运行的修复服务使用 18065，控制台仍为 7400；不需要照上述命令再启动一份才能使用当前页面。

```bash
.venv/bin/python -m pytest tests/test_pi05_verified_restore.py tests/test_step8.py \
  tests/test_panda_jobs.py tests/test_chat.py -q
```
