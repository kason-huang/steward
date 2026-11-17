# 本体配置驱动抓取仿真验证

2026-11-17，4090-2，基线 ad8dd400e9，本次工作区源码校验见 [source-sha256.json](source-sha256.json)。设计已更新至 [整体设计 §4.3.1](../../pi05-integration-design.md#431-抓取方式由本体配置选择)，字段、迁移和安装方法见[抓取配置参考](../../../../../steward/docs/grasp-simulation.md)。

## 结论

WorldSim 读取机械配置 mechanical.grasp.sim，按机器人选择 ideal-attach 或 contact。现有 steward-bot、cobot-magic、libero-panda 的默认值分别为 ideal-attach、ideal-attach、contact。控制器选择仍独立，Panda 的 OSC、租约、Job 和专用端口没有改为通用控制器。

contact 模式不允许理想捕获、跟随、碰撞屏蔽、释放速度重置和抽屉把手合成牵引；普通二指驱动仍正常执行。每只物理夹爪均校验指关节、执行器和碰撞。显式 finger_joints 不能遗漏其他夹爪或替代实际驱动组；未知模式、无效模型与身份不匹配不会回退。Reset 在新配置通过校验后才发布新世界，无效变更保持旧 model／Episode／模式可用。

## 测试结果

| 检查 | 结果 | 证据 |
| --- | --- | --- |
| 新增配置、后端隔离、混合机器人和 Reset 测试 | 16 passed | [focused-final.log](focused-final.log) |
| WorldSim、Cobot、抽屉、本体装配、Panda Job 和目标证据回归 | 75 passed | [regression-final.log](regression-final.log) |
| 构建 wheel、安装到独立目录、读取三本体配置 | 通过，导入来自 wheel-install，不从源码读取 YAML | [wheel-final.log](wheel-final.log) |
| Panda 初态 0 正常固定动作物理抓放 | grip、placement、task_success、pass 均 true | [独立物理结果](panda-contact-summary.json) · [外部／腕部双机位录像](panda-contact-dual-view.mp4) |
| Spec／Standards 审查 | 已修复全部夹爪覆盖问题，无剩余重要发现 | 新负例已纳入上述16项；本次审查未重复全仓测试 |

Panda 抬升约 0.19856 m，双指最小夹持力分别约 18.998 N／18.972 N，最大滑移约 0.000394 m；篮底最小承托力约 0.25360 N，撤爪最小距离约 0.19506 m，撤爪后原生篮内谓词为 true。该验证使用冻结的 Step 4 固定动作，通过真实机器人 HTTP 控制及 MuJoCo 接触完成，不使用理想附着，也不调用 π0.5。它验证抓取物理行为保持一致，不构成新的模型操作成功率。

当前 Cobot 的手指是 visual-only，切 contact 会按预期拒绝；非 Panda contact 正例使用测试专用、补有实体指碰撞的夹具，不修改正式 Cobot 资产。当前 prepared Panda 无普通 grasp-point 描述，切 ideal-attach 也会明确拒绝，不能靠改一个字段补齐后端所需的模型／控制描述。

配置首次实际解析还暴露了既有 Cobot YAML 的 flow-list 引号问题，本次仅修正该字符串的合法写法。新增 PyYAML 依赖已锁定，三个本体 YAML 随 wheel 分发。带抓取点的自定义机器人现在需要登记有效本体配置；无抓取点的未注册观察实体仍可加载。

## 复现命令

在4090-2仓库的 steward Python 包目录执行。物理验证输出目录必须不存在；使用独立 WSim 环境，不重置或驱动线上展示服务。

```sh
cd /home/huawei/hqk/workspace/steward/steward
/home/huawei/.local/bin/uv sync --locked
MUJOCO_GL=egl OPENBLAS_NUM_THREADS=1 .venv/bin/python -m pytest tests/test_grasp_mode.py -q
MUJOCO_GL=egl OPENBLAS_NUM_THREADS=1 .venv/bin/python -m pytest \
  tests/test_world.py tests/test_cobot.py tests/test_cobot_drawer.py \
  tests/test_embodiments.py tests/test_panda_jobs.py tests/test_panda_pick.py -q
MUJOCO_GL=egl OPENBLAS_NUM_THREADS=1 .venv/bin/python scripts/verify_libero_step4.py \
  var/step4/wsim-root var/step4/formal-1/normal-0-r1/libero \
  var/grasp-sim-config/panda-contact-repro \
  --ffmpeg /home/huawei/hqk/workspace/pi05-step1/client-mirror-venv/lib/python3.8/site-packages/imageio_ffmpeg/binaries/ffmpeg-linux64-v4.2.2
/home/huawei/.local/bin/uv build --wheel --offline --out-dir var/grasp-sim-config/repro-wheels
```

wheel 独立安装验证可用 uv pip install --no-deps --target 指向新目录，再令 PYTHONPATH 指向该目录；检查导入的 steward.embodiments.__file__ 确实来自安装目录，并读取三个默认模式。本次实际安装目录为 `/home/huawei/hqk/workspace/steward/steward/var/grasp-sim-config/final-wheel-install/`。

## 原始文件

运行记录总目录为 `/home/huawei/hqk/workspace/steward/steward/var/grasp-sim-config/`。最终固定动作的逐物理步接触、动作及控制回执、外部和腕部 PNG、MP4 位于其中 `panda-contact-final/`；报告只提交摘要、录像和最终测试日志。早期测试与首次固定动作回放保留在原目录，不计入最终通过项。未修改线上碰撞阈值，也未增加真实模型评测或修改现有服务进程。
