---
name: chip-basic
description: 从已有官方快照提取芯片显存、显存带宽、功耗、形态和 PCIe 基础规格候选。
---

# 芯片基础规格

只处理 `vram_gb`、`vram_type`、`vram_bw_gb_s`、`tdp_w`、`form_factor`、`bus_interface`。值必须来自指定快照原文，数字去除单位后保存；无法唯一对应芯片或规格版本时拒绝。输出字段值、逐字段原文证据、芯片精确型号和来源 URL，不直接写库。
