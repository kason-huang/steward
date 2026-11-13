# Panda 多物体抓放

系统侧的 pick 与策略模型能否完成抓取分开验证。当前场景从 RGB-D 推断 alphabet_soup、milk、butter、cream_cheese、salad_dressing、tomato_sauce，支持按当前 Ego State 物体 ID 发起单独抓起任务；篮子和机器人不能作为 pick 目标。未命名但尺寸合适的可见对象也可按 ID 选择，其策略指令为 pick up the selected object，并同时携带所选目标元数据。`libero_pick_place(target, on)` 支持同样的可抓物体与当前观察到的篮子，策略输入根据目标生成完整抓放 prompt。

新接口为 `POST /ego/predict`，请求 `{"skill":"pick","params":{"target":"本轮 e:... ID"}}`。当前小物体、观测新鲜度、关节、可见自由空间及所选物体接触区域检查通过后，按原 predict → receipt → run → wait → stop 链路执行。目标引用、类别、尺寸、初始位置和指令绑定到 receipt，推理请求根据选中对象生成 pick up the milk 等 prompt，不读取 World State 名称、物体位姿或成功标志。

成功判定要求固定外部相机测得的目标彩色 RGB-D 完整可见连通区域超出初始物体顶部至少 0.035 m，夹爪闭合但非空闭合，连续至少 0.2 s 的目标/末端运动一致并保持稳定相对位置；至少两帧需有独立深度变化，且触及视野边界的截断区域不作为成功证据。目标关联锚定最近真实观测，不用夹爪预测逐次切换到其他同色区域。单纯抬高手爪、腕相机扫过静止表面、物体留在原处、重复读取同一帧或缺少视觉证据均不算成功。外部目标被遮挡时保留未确认，腕相机继续进入策略输入但不单独确认 pick。停止清理后仍需保留可见抓持证据。只 pick 的成功结果表示物体仍在手中，不自动投篮；重新抓另一物体前需释放或复位。复合抓放任务通过 `libero_pick_place` 执行；独立 `place` 接口尚未扩展。非汤罐物体沿用同一目标跟踪器，要求此前已确认抓起、随后张爪、目标可见、篮子仍可见且位置一致、工具撤离、目标相对任务初始深度具有新增占据证据（不能借用篮内旧同色物体）、连续至少 0.3 s 稳定，目标尺寸完全落在篮内安全边距并接近观察篮底高度后才确认放置。遮挡或证据不足时保持未确认。汤罐保留原有专用视觉判定。

物体识别复用静态类别模型和相机颜色/高度匹配，类别标记为 inferred，recognitionScore 是匹配误差，不是校准过的概率。已命名实体按类别模板补全尺寸；位置和可见表面仍来自相机。近夹爪目标残片保留原绑定身份，不另匹配成 butter。当前未完成任意物体语义和 6D 姿态验收，策略成功率另行记录。

聊天可发送 `pick up the milk`、`pick up the butter` 等，Ego Harness 应先读取本轮 ego_state / ego_query，选择 graspable=true 的目标，以 pick 预测后按 receipt 执行唯一 Job，按 state、goalStatus、stopStatus 报告结果。pick 轨迹被预测检查拒绝后，本轮策略故障会锁住后续策略 Job；需要控制台 Reset 再尝试，skill_stop 只确认停止，不清除此故障。抓放聊天可发送 `pick up the cream_cheese and place it in the basket`，Harness 从当前 Ego State 绑定 target 和 on，调用 libero_pick_place；不得将单独 pick 成功报告为放置成功。复合任务的动作拒绝同样锁住本轮后续策略运动。

通用候选能力模板为 `capabilities/panda_pick.json`，名称 panda_pick、参数为本轮 target ID，供真实 ego profile 的 capability_get → 嵌套 run_code 使用。当前 4090-2 活跃 robot-home 已安装；新建 profile 时将模板放入该 home 的 steward-capabilities/panda_pick.json，已有同名能力先核对版本，不覆盖运行记录。模板自身不自动重试，也不在未经真实成功前标为 verified。

复合抓放候选模板为 `capabilities/panda_pick_place.json`，参数为当前 target、on ID，安装到同一 profile 的 steward-capabilities 目录。模板只有在 Job 的 state、goalStatus、stopStatus 分别为 succeeded、achieved、confirmed 时返回成功，未经过真实成功验收前保持 candidate。

验证命令（在 4090-2 的 steward Python 包目录内运行）：

```sh
MUJOCO_GL=egl OPENBLAS_NUM_THREADS=1 .venv/bin/python scripts/verify_panda_multi_pick.py \
  --out var/pi05-multi-object-pick/system-batch-new
MUJOCO_GL=egl OPENBLAS_NUM_THREADS=1 .venv/bin/python -m pytest \
  tests/test_panda_pick.py tests/test_panda_jobs.py -q
```

批次脚本使用明确标注的空动作测试策略，验证六物体的目标准入、动态 prompt、Job 执行与停止，以及未抓起时不误报成功；它不代表六物体实际抓取成功率。真实 pi05 测试和相关回归状态见[多物体 pick 实测报告](../../self/design/pi05-intergration/steps/multi-object-pick-results/README.md)。

复合抓放的真实聊天实测、失败原因、双机位动画位置与回归日志见[多物体抓放验证记录](../../self/design/pi05-intergration/steps/multi-object-pick-place-results/README.md)。
