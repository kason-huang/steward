# 程序内学习策略接口契约

状态：待实现，2026-11-17。本文定义调用方与 ER 的义务；示例字段均为拟议协议，不是当前可执行接口。[总体设计](design.md)定义互补关系和能力选择；[现状设计](../pi05-intergration/pi05-integration-design.md)记录旧技能入口。

## 1. 工具与传输

所有工具仅经现有 PTC 的 `run_code` 使用，不新增 LLM 原生入口。工具层 JSON、HTTP 请求和配置读取处验证输入；不依赖程序绕过检查。新增策略工具通过 Steward 的 Service Definition、HTTP provider 和工具 consumer 组成完整调用链，机器人安全守卫同时覆盖 `skill_run` 与 `policy_run`，不得直接调用工具实现绕过守卫。

| 工具 | 请求概要 | 义务 |
| --- | --- | --- |
| policy_describe | 可选 policy ID；省略则列出当前可用入口 | 返回能力描述、兼容条件、验证范围和限制；不运动 |
| policy_prepare | policy、instruction、bindings、completion、budget | 校验准入并签发一次性 policy 收据；不运动、不宣称预测完整轨迹 |
| policy_run | receipt、requestKey、wait、waitTimeoutS | 消费收据并启动或找回同一 Job；默认异步，等待长度受服务配置限制 |
| policy_stop | job、可选 waitTimeoutS | 只停止调用权限覆盖的指定策略 Job，幂等，返回实际停止状态 |
| skill_status | 沿用 job 参数 | 扩展为统一查询确定性技能和策略 Job；不隐式重启 |
| body_stop | 沿用现有调用形式 | 停止机器人全部运动，撤销受影响当前程序的运动授权 |

拟议 HTTP 映射为 GET `/policy/describe?policy=…`、POST `/policy/prepare`、POST `/policy/run`、POST `/policy/stop`；查询复用 GET `/skill/status?job=…`，机器人级停止沿用现有 POST `/skill/stop`。不增加另一套独立 Job 存储。Python 内部 ID 与 DSH 的 receipt／job 字段统一在适配层映射。

## 2. policy_describe

下面是单个入口的描述示例。`validated` 必须有可定位的实测证据和明确条件；具体支持范围以实际结果为准，不将初态 0 的完整流程外推到其他初态。

```json
{
  "policy": "panda.libero_manipulation",
  "provider": "pi05_libero",
  "description": "基于语言、外部和腕部图像、本体状态执行 Panda 桌面操作",
  "taskScope": {
    "operations": ["pick", "pick_and_place"],
    "environment": "适配的 LIBERO 场景"
  },
  "requirements": {
    "embodiment": "libero-panda",
    "observations": ["external_rgb", "wrist_rgb", "proprioception"],
    "simulationGraspMode": "contact"
  },
  "validation": [
    {
      "task": "alphabet soup 放入篮子",
      "conditions": "当前汤罐任务场景、初态 0、已验收版本组合",
      "status": "validated",
      "evidence": "self/design/pi05-intergration/steps/step8-results/README.md"
    },
    {
      "task": "同一汤罐场景中 cream cheese 放入篮子",
      "status": "known_failure",
      "evidence": "self/design/pi05-intergration/steps/multi-object-pick-place-results/diagnosis/README.md"
    }
  ],
  "conditioning": {
    "supported": ["language", "rgb", "proprioception"],
    "spatialConditioning": false,
    "bindingsUsage": ["tracking", "verification", "execution_checks"]
  },
  "verifiers": ["panda.object_in_basket"],
  "limitations": ["对象 ID 不作为模型空间目标输入，不输出未来视频"]
}
```

provider 地址、凭据、完整文件路径不作为模型可选择参数。入口注册还需保存预算上限、模型／adapter 版本和详细兼容信息；面向 LLM 的摘要仅投影必要内容。`operations` 是发现标签，不是强制执行动作枚举。缺失输入、不支持的本体或无效引用应明确拒绝加载或在首次可解析时拒绝，不能隐式换模型。

## 3. policy_prepare

```json
{
  "policy": "panda.libero_manipulation",
  "instruction": "pick up the alphabet soup and place it in the basket",
  "bindings": {"target": "ego-object-id", "destination": "ego-basket-id"},
  "completion": {
    "kind": "verifier",
    "verifier": "panda.object_in_basket",
    "args": {"object": "ego-object-id", "basket": "ego-basket-id"}
  },
  "budget": {"maxActions": 280, "maxWallS": 120}
}
```

instruction 非空，按 adapter 协议传入模型，ER 不隐式替换成汤罐任务。bindings 引用当前 ES 的对象，负责跟踪、验证和检查，不授权任意接触、关闭碰撞检查或假装完成模型空间条件输入。未来 conditioning 扩展需要显式声明输入方式和观测帧身份；不支持的点、框或其他条件一律拒绝，不能静默丢弃。

completion 为判别联合：`{kind:"verifier",verifier,args}` 使用注册验证器；`{kind:"agent"}` 只执行受预算约束的操作段，ER 保持 goalStatus=unconfirmed，Agent 判断另行记录。验证器名称、参数、对象适用范围必须在准备时校验，不能执行任意用户谓词或读取 WSim 特权真值作为机器人证据。

ER 解析 budget 为部署允许的明确值，调用方只能缩小上限。maxActions 计实际应用的高层策略动作，不计每个物理积分步；maxWallS 计入推理与执行等待。父任务的累计动作、耗时和重试预算跨多个子 Job 生效，重新准备不能刷新父任务预算。

```json
{
  "admitted": true,
  "supportStatus": "validated",
  "receipt": "opaque-policy-receipt",
  "expiresAt": "service-defined-timestamp",
  "conditioning": {
    "applied": ["instruction", "current_rgb", "proprioception"],
    "bindingsUsage": ["tracking", "verification", "execution_checks"],
    "spatialConditioning": false
  },
  "limitations": ["对象绑定不直接约束模型选择目标"]
}
```

admitted 只表示当前准入，不表示整段轨迹已预测或目标必能完成。supportStatus 为 validated／unverified／known_failure；未验证或已知失败任务是否可实验由部署配置决定，不接受程序自行声明已验证。拒绝时 admitted=false，返回 reasonCode、原因和可恢复性，不签发可执行收据。

policy 收据与原有计划 Prediction Receipt 通过类型区分，不能交叉消费。它绑定调用授权、机器人、Episode、必要观测版本、控制代际、解析后的入口／模型／adapter 配置、指令、对象、验证器和预算；调用方不能修改其内容。run 时原子地重检新鲜度、权限、锁存状态和控制权，过期或失效要求重新观测与准备。服务重启后不恢复旧收据执行资格。

## 4. policy_run 与统一 Job

请求必须提供 requestKey，在调用身份和程序运行范围内唯一；服务原子绑定 key、收据与规范化请求。首次调用仅创建一个 Job；相同 key 和相同请求返回原 Job，不同请求返回 idempotency_conflict。重试原 key 的查找先于“收据已使用”拒绝；更换 key 不能重复消费收据。

幂等记录在 Job 可查询期间保留。服务重启后旧执行授权失效，不能因查不到旧 key 自动重发动作；需要明确返回不可恢复状态并确认机器人停止，再重新准备。

Job 内部复用现有生命周期，不建立独立策略状态机。拟增加 executionStatus、goalStatus、stopStatus；保留现有 state 字段供旧消费者使用，由单一映射生成。执行 completed 不直接映射为物理 succeeded；只有实际目标 achieved 且停止 confirmed 时才可返回旧 succeeded。旧接口与能力程序迁移测试必须覆盖这些映射。

```json
{
  "job": "opaque-job-id",
  "kind": "policy",
  "policy": "panda.libero_manipulation",
  "executionStatus": "running",
  "phase": "waiting_inference",
  "goalStatus": "unconfirmed",
  "stopStatus": "unknown",
  "reasonCode": null,
  "actionsApplied": 35,
  "evidence": []
}
```

executionStatus 为 running／completed／failed／cancelled，goalStatus 为 achieved／not_achieved／unconfirmed，stopStatus 为 confirmed／unknown。运行中的 unknown 本身不构成故障；终止时仍 unknown 必须阻止新运动。完成清理前维持 running 和 stopping 阶段；清理确认或清理失败后进入相应终态，并保留停止结果。

预算耗尽可产生 completed+not_achieved 或 completed+unconfirmed；动作被拒绝产生 failed；用户取消产生 cancelled。非零动作次数、代码返回或模型请求成功均不能证明目标成功。注册验证器在执行期间检查实际观测，达到条件后请求结束，并在停止后复核；目标被遮挡时保留不确定性。等待超时仅返回当前 Job，绝不创建第二个 Job，也不暗示已停止。

## 5. policy_stop 与 body_stop

policy_stop 验证 Job 身份和调用权限，只停止指定策略 Job，撤销其动作代际、丢弃未执行动作块、取消推理请求并拒绝迟到结果。控制器执行停止清理并确认，不得仅标记模型线程已退出。返回同一个 Job 和实际 stopStatus；未知 Job 明确报错，已终止 Job 重复停止不重复运动。因超时返回 unknown 时，后台继续清理且机器人保持禁止新运动。

停止保留实际 goalStatus，不把取消等同于目标失败。停止 confirmed 后 CaP 可以重新观测、准备和启动其他技能；安全故障锁存仍按既有规则处理。当前 prediction_rejected 需要 Reset 的事实不能被普通 policy_stop 清除，也不能自动切换程序技能绕过。

body_stop 是机器人级停止，取消受影响的所有运动 Job，并撤销当前执行程序范围的运动授权及未使用收据，防止其循环再次启动动作。新任务只有取得新的执行授权且停止已确认后才可启动；纯查询和观测可继续。

程序超时、异常或取消同样撤销该程序范围并清理其未完成运动子 Job。成功返回前如果仍有运动子 Job，必须等待或停止，不默许脱离程序所有权继续运动。program 记录与 motion Job 分开，父程序等待子任务不持有机器人运动租约。

## 6. 统一错误与安全规则

| reasonCode 类别 | 调用方处理 |
| --- | --- |
| unknown_policy／incompatible_configuration／unsupported_conditioning | 修正入口或配置，不静默降级 |
| unsupported_task／invalid_verifier／invalid_binding | 核对任务范围或重新观测，不改写验证等级 |
| stale_receipt／expired_receipt／receipt_used | 重新准备；若是响应丢失则先用原 requestKey 查回 |
| busy／scope_revoked／budget_exhausted | 等待可授权的新任务或结束，不循环抢占 |
| inference_failed／action_rejected | 查看证据与故障处置；不自动关闭检查 |
| stop_unconfirmed／fault_latched | 阻止后续运动，按停止或人工复位流程处理 |
| idempotency_conflict／job_not_found／forbidden | 修正请求身份，不重新生成 key 盲目执行 |

错误返回结构化 reasonCode、用户可读原因、关联 Job 和恢复要求；未知停止状态不能通过抛错掩盖。一次执行中的策略动作与程序技能动作共享同一控制权，切换执行器必须停止确认、获取新观测后再准入。程序内嵌套调用与其他路径使用同一权限和守卫。

## 7. CaP 使用示意

下面是拟议 JavaScript 函数体示例，tools 与 args 由现有 run_code／能力程序机制提供；operationId 在一次操作的重试期间保持不变。

```javascript
const prepared = await tools.policy_prepare({
  policy: "panda.libero_manipulation",
  instruction: args.instruction,
  bindings: args.bindings,
  completion: args.completion,
  budget: args.budget,
});
if (!prepared.admitted) throw new Error(prepared.reasonCode);
const started = await tools.policy_run({
  receipt: prepared.receipt,
  requestKey: args.operationId,
  wait: false,
});
return await tools.policy_stop({ job: started.job });
```

该例只说明异步启动与主动停止，不是抓放成功程序。正式 CaP 必须按目标等待／观测、判断真实结果，并在退出前完成子 Job 清理。能力库继续存 `(tools,args)` 异步函数体；运行时从当前观测解析对象，不能固化 Ego ID、收据或旧控制权限。

## 8. 迁移与验收

1. 从当前 Panda 执行代码抽出 adapter 与注册验证器，保留原汤罐路径作为回归基准。
2. 在 Steward 包和 Ego API 中增加策略工具、注册与安全守卫，复用现有 Job 和机器人控制权；旧入口暂时转发同一执行器。
3. 更新能力摘要、工具说明、PANDA_BRIEF 和 CaP 程序；旧的 `ego_predict → skill_run` 仍服务确定性技能，不把策略继续塞进技能枚举。
4. 首期用同一版本模型、场景、contact 本体和初态 0，验证临时程序与已保存 CaP 的汤罐抓放，保存真实逐步证据和外部／腕部录像。
5. 验证主动 policy_stop、body_stop 后程序不得重启、程序退出清理、停止后技能接管、迟到推理丢弃、推理故障、收据过期／重复消费、幂等重试和并发互斥。
6. 用单元／集成和无密钥会话快照覆盖请求、守卫、生命周期、提示与日志；真实模型测试负责物理行为，二者分别报告。旧消费者全部迁移并回归后再删除旧 Panda 策略特例。

验收结果必须区分程序结束、物理目标、停止确认，以及抓取物理模式。当前文档更新不计为上述开发或实测已通过；本专题不改变已有实验结论，不自动扩展到任意物体、任意子任务或原生工具调用。
