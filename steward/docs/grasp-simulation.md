# 抓取仿真模式配置

本文是 WorldSim 抓取方式的配置参考。机器人通过注册本体 YAML 的 `mechanical.grasp.sim` 选择仿真实现，控制器和策略选择分别管理；不再用“是不是 Panda”决定能否理想附着。

## 模式与作用范围

| grasp.sim | 行为 |
| --- | --- |
| ideal-attach | 闭爪时按已有捕获规则绑定物体或把手，维持理想跟随，开爪释放；保留旧的碰撞与容器处理 |
| contact | 只驱动真实关节和夹爪，物体运动由接触、摩擦和重力产生；禁止理想捕获、物体跟随、碰撞屏蔽、理想释放重置和把手 PD 牵引 |

配置按机器人解析，同一世界允许不同机器人使用不同模式；当前一台机器人上的所有夹爪使用同一模式，尚未提供单夹爪覆盖。模式在新世界／Episode 加载时解析，运行中不因 YAML 修改而热切换；普通物体编辑重建保留当前模式，Reset 重新读取并校验配置。新配置不合法时拒绝 Reset，原世界及 Episode 保持有效。

默认配置：steward-bot 和 cobot-magic 为 ideal-attach，libero-panda 为 contact。现有 Panda 的 OSC、专用端口、控制代次、租约和 π0.5 Job 保持原有职责；本次没有统一这些控制器，也没有开放新的策略技能。

## 配置与能力校验

本体在 [embodiments.py](../src/steward/embodiments.py) 注册，profile 指向对应 YAML。`simulation_grasp()` 读取该文件，检查本体 id、机械配置和模式，返回每台机器人使用的不可变配置。缺失、损坏或未知的模式不会回退成 ideal-attach。带 grasp-point 的自定义机器人需要注册本体配置；无抓取点的未注册实体仍可用于展示。

普通 SceneState 机器人由 grasp-point 连杆下的 prismatic finger joints 推导驱动组。每个 contact 夹爪要求至少两个独立滑动指关节、对应机器人内的真实关节、执行器及有效碰撞几何。当前校验不证明该夹爪已经具有足够摩擦或能抓住某种对象，实际操作仍需单独物理验收。

prepared MJCF 的机器人可在 `mechanical.grippers[].finger_joints` 显式给出编译模型中的关节名。Panda 示例为现有配置片段：

```yaml
mechanical:
  grippers:
    - arm: arm
      type: parallel-2f
      control: physical-position
      finger_joints: [gripper0_finger_joint1, gripper0_finger_joint2]
  grasp:
    sim: contact
```

显式声明不得遗漏已有 grasp-point 夹爪，也不得用其他无关关节替代实际 `_set_fingers()` 驱动组。任一夹爪缺少驱动或碰撞体，整台机器人的 contact 配置被拒绝。prepared Panda 的场景描述没有普通 grasp-point 树，contact 使用声明的编译关节组验证；直接改成 ideal-attach 会因缺少理想抓取点而拒绝，不会隐式回退成 contact。

steward-bot 的夹爪没有实体物理指关节；当前 Cobot 的手指有驱动，但几何为 visual-only。它们不能只把配置改成 contact 就获得物理抓取，应先补齐对应资产再验证。当前 Cobot 默认模式不变，本次没有为其新增实体手指碰撞。测试中的非 Panda 物理后端使用明确标注的专用夹爪几何夹具。

## 安装与迁移

项目新增 PyYAML 依赖并锁定在 uv.lock。更新环境后使用：

```sh
cd /home/huawei/hqk/workspace/steward/steward
uv sync --locked
```

本体 YAML 通过 wheel force-include 装入 `steward/embodiment_profiles/`，安装包运行不依赖仓库中的实验目录。源码运行从 `steward/embodiments/` 读取；安装包从随包配置读取。修改已安装包的本体配置需要按部署流程更新该包或使用源码部署，不能只修改另一份未被运行进程读取的 YAML。

新增本体应先在 registry 登记 profile，保留与本体 id 一致的 YAML，明确 sim 模式，然后验证模型是否满足该模式。ideal-attach 需要 grasp-point；contact 需要物理指关节、执行器与碰撞。策略、控制器和模型输入输出兼容性仍由对应执行栈验证，不因 grasp.sim 合法而自动成立。

## 完成判定与验证

闭爪命令和抓住物体是不同状态。contact 路径不生成理想 attached 物体身份，机器人侧仍按允许的相机／本体反馈形成证据，世界侧真实接触只用于独立评估。π0.5 的目标选择和操作成功率也不由 grasp.sim 决定。

配置驱动实现、回归、Panda 固定动作物理验收及录像见[验证报告](../../self/design/pi05-intergration/steps/grasp-sim-config-results/README.md)。本次只验证仿真后端选择与既有行为，没有新增模型训练或跨任务成功率结论。
