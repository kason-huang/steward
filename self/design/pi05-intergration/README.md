# π0.5 接入设计文档

2026-11-13。Steward 已接通真实 pi05_libero、LIBERO Panda、汤罐任务场景、机器人观测、Ego API 和聊天能力程序。汤罐初态 0 完整流程已有通过证据；固定场景任意目标抓放尚未通过。本文为文档导航，技术现状统一由[接入设计](pi05-integration-design.md)维护，遵循[Steward 架构约束](../../../steward.md)。

| 文档 | 用途 |
| --- | --- |
| [当前整体设计](pi05-integration-design.md) | 模型／机器人／场景补齐，模块与进程，π0.5 输入动作协议，EH 工具与 HTTP API，receipt／Job、控制和停止、部署与验证边界 |
| [接入 PRD](pi05-integration-prd.md) | 产品需求和原始范围，问题背景描述接入前基线 |
| [实施计划](pi05-integration-plan.md) | 八步实施顺序、交付物和结果入口，Step 8 收敛为初态 0 |
| [开工前差距分析](pi05-integration-gaps.md) | 2026-11-09 方案选择前的缺口，保留用于理解选型依据 |
| [多物体接口说明](../../../steward/docs/panda-pick.md) | pick 与复合抓放的使用、限制和候选能力安装 |
| [奶油奶酪对照诊断](steps/multi-object-pick-place-results/diagnosis/README.md) | 官方任务与固定汤罐布局的区别、抓错目标和碰撞预测保守问题、复现脚本、逐回合数据与录像 |

## 实测入口

| 阶段 | 方案 | 结果 |
| --- | --- | --- |
| 1 原始模型基线 | [Step 1](steps/step1.md) | [基线与固定批次](steps/step1-results/README.md) |
| 2 场景与物理兼容 | [Step 2](steps/step2.md) | [兼容性结果](steps/step2-results/README.md) |
| 3 Panda／夹爪／OSC | [Step 3](steps/step3.md) | [控制结果](steps/step3-results/README.md) |
| 4 真实接触抓放 | [Step 4](steps/step4.md) | [固定动作结果](steps/step4-results/README.md) |
| 5 相机、本体状态与 Ego State | [Step 5](steps/step5.md) | [观测结果](steps/step5-results/README.md) |
| 6 π0.5 闭环 | [Step 6](steps/step6.md) | [策略结果](steps/step6-results/README.md) |
| 7 聊天、能力与 Job | [Step 7](steps/step7.md) | [工具链与故障结果](steps/step7-results/README.md) |
| 8 初态 0 端到端验收 | [Step 8](steps/step8.md) | [单例验收结果](steps/step8-results/README.md) |

模型在官方基准的成功、程序接口支持、单例完整流程通过和固定布局多目标成功率分别验收。使用接口前先核对[技能范围](pi05-integration-design.md#51-技能接口与工具调用)，统计结论按[评测口径](pi05-integration-design.md#104-成功率评测口径)。
