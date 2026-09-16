---
name: chip-compute
description: 从官方快照提取芯片支持精度、Dense/Sparse 峰值算力、架构、制程和计算单元候选。
---

# 芯片计算参数

负责 `precision_support`、`precision_perf`、`architecture`、`process_node_nm`、`compute_units`。必须区分 dense/sparse 和 TFLOPS/TOPS，不把整机或集群算力写成单芯片算力。没有明确口径时拒绝并说明歧义。
