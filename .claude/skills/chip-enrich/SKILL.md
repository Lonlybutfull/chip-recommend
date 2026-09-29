---
name: chip-enrich
version: 2.0.0
description: 为已有芯片补充硬件规格、软件生态、价格与生命周期信息，并通过统一数据库接口记录每个字段的来源。
allowed-tools:
  - Bash
  - Read
  - Write
  - Grep
  - Glob
  - AskUserQuestion
  - WebSearch
  - WebFetch
triggers:
  - 补充芯片数据
  - 查找芯片规格
  - 填充硬件参数
  - 扩充芯片信息
---

# 芯片信息扩充

## 何时使用

芯片已经存在于 `chips` 表，但显存、精度、功耗、架构、互联、软件或价格等字段缺失时使用。芯片身份目录应先由 `chip-catalog` 建立。

## 工作目录与依赖

从项目根目录运行：

```bash
cd E:/BUPT_PS/P_0/chip-recommend
```

依赖 `data/data.db`、`schema.sql`、`chip_model/database.py` 和查询 CLI。所有业务值在 SQLite 中按 TEXT 保存，写入前必须符合字段格式。

## 字段格式

| 字段 | 格式 | 示例 |
|---|---|---|
| `vram_gb`、`vram_bw_gb_s`、`tdp_w` | 纯数字字符串 | `"80"`、`"3350"`、`"700"` |
| `process_node_nm`、`die_size_mm2`、`transistors_b` | 纯数字字符串 | `"4"`、`"814"`、`"80"` |
| `precision_support` | 逗号分隔的精度标签 | `"FP32,FP16,BF16,FP8,INT8"` |
| `precision_perf` | `精度=数值单位` 列表 | `"BF16=1980TF,FP8=3960TF"` |
| `price_cny_wan`、`price_usd` | 纯数字字符串 | `"18"`、`"24000"` |
| `maturity_level` | `"0"` 到 `"5"` | `"5"` |
| `cloud_available`、`is_chiplet` | `"0"` 或 `"1"` | `"1"` |

## 字段组与边界

按以下组逐项处理：

1. `memory`：显存容量、类型、位宽、带宽、频率。
2. `precision`：支持精度和各精度峰值算力。
3. `clock_power_physical`：功耗、频率、板卡尺寸、供电、接口与形态。
4. `architecture`：架构、代号、代际、制程、晶圆厂、面积、晶体管、封装、Chiplet。
5. `interconnect`：互联带宽、互联技术和网络接口。
6. `compute_units`：计算单元、Tensor/RT 核心、着色器和 SM 数量。
7. `cache`：L1、L2 和片上 SRAM。
8. `software`：软件栈和兼容框架。
9. `pricing`：采购价、人民币价格、价格时间与说明。
10. `description`：介绍、亮点、限制、目标工作负载、典型部署和竞品对比。
11. `ecosystem`：成熟度、框架、软件栈、云可用性、集群规模和优劣势。
12. `lifecycle`：发布日期、量产状态、EOL、目标市场和未确认信息。
13. `identity`：由 `chip-catalog` 负责，本 Skill 不改。
14. `meta`：系统维护，本 Skill 不改。

其中 1—9 为关键字段组，必须来自真实网页证据；10—12 可在确无公开来源时使用模型整理，但必须标记 `source_type="llm_curated"`、`source_url="LLM curated"`、`confidence="medium"`、`is_official="0"`。

## 来源质量

- `high`：厂商数据表、官方产品页、MLPerf 官方结果。
- `medium`：可靠技术媒体、行业报告或多来源一致结论。
- `low`：单一社区来源、传闻或尚未证实的信息。
- 只有厂商自有网站或正式数据表可标记 `is_official="1"`。
- 找不到可靠证据时保留 NULL；不得复制其他型号数据，也不得编造“合理数值”。

## 写入方式

必须调用 `chip_model.database.update_chip_fields()`。同一页面提取的多个字段可以批量写入，共用同一来源对象；该接口会读取旧值、更新字段，并为每个字段写入一条 `field_provenance`。

```python
from chip_model.database import update_chip_fields

source = {
    "source_type": "official_datasheet",
    "source_url": "https://www.nvidia.com/en-us/data-center/h100/",
    "source_detail": "规格表 > 显存",
    "confidence": "high",
    "is_official": "1",
    "field_label": "H100 数据表",
    "notes": "",
}
fields = {
    "vram_gb": "80",
    "vram_type": "HBM3",
    "vram_bw_gb_s": "3350",
    "tdp_w": "700",
}
update_chip_fields(conn, chip_id=3, fields=fields, source=source)
```

禁止直接执行 `UPDATE chips` 或自行插入 `field_provenance`。

## 执行流程

1. 用 CLI 找出身份存在但关键规格缺失的芯片：
   ```bash
   python scripts/run_cli.py chip search --limit 200
   python scripts/run_cli.py db status
   ```
2. 明确本次芯片范围，优先小批量验证；不得无边界地修改全库。
3. 对每颗芯片按字段组搜索，优先厂商官网，再选可靠第三方来源。
4. 打开具体页面，提取原文证据，将数值归一化到项目字段格式。
5. 按来源调用 `update_chip_fields()`；来源不同的字段分开写入。
6. 遇到矛盾、口径不明或无法确认具体型号时不写值，记录冲突。
7. 每颗芯片完成后验证：
   ```bash
   python scripts/run_cli.py chip profile "<chip_model>"
   ```

## 输出要求

逐颗报告已补充字段、仍为空的关键字段、采用的来源、来源等级、冲突项和新增溯源记录数。最终汇总处理芯片数、网页证据字段数、模型整理字段数和失败原因。

## 完成标准

- **完成**：指定芯片已处理，所有写入均有字段级来源并通过 CLI 验证。
- **带问题完成**：流程完成，但部分关键字段因无可靠证据保持为空。
- **阻塞**：数据库为空、网络不可达或依赖文件缺失。
- **需要确认**：同一字段存在无法自动裁决的权威来源冲突。
