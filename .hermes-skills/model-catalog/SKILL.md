---
name: model-catalog
description: 增量维护 HuggingFace 主链路及 ModelScope 补充来源中的模型身份、分类、参数和上下文信息。
---

# 模型目录

HuggingFace API 是当前已经接通的主源；ModelScope 尚未接通，只能把发现的 ModelScope 官方页面作为补充来源，不能声称已完成 API 同步。负责模型 ID、架构、总参数、激活参数、权重大小、上下文和能力。MoE 必须分别记录总参数与每 token 激活参数，不得用激活参数替代权重常驻显存计算。
