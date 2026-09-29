---
name: chip-compatibility
description: 当需要从开放互联网核验某颗 AI 芯片与模型、框架、精度、推理引擎或软件栈之间的支持关系时使用。
metadata:
  version: 1.2.0
  mode: test-only
---

# 芯片兼容资料发现

## 概述

查找“某芯片是否支持某对象”的明确证据。核心原则是兼容关系必须同时说明主体、对象和证据级别；理论上可能运行不等于已经验证。

## 何时使用

- 使用：核验芯片与模型、框架、精度、后端或软件栈的支持关系。
- 不使用：吞吐、时延和显存等实测指标交给 `chip-benchmark`；包含安装命令和拓扑的指南交给 `chip-deployment`。
- 只有“生态丰富”“广泛兼容”等泛化描述时不生成关系。

## 输入与运行入口

输入应尽量包含具体芯片；只查框架或兼容状态时可限定字段：

```bash
python scripts/run_open_web_test.py --skill chip-compatibility --chip "华为昇腾 910B"
python scripts/run_open_web_test.py --skill chip-compatibility --chip "华为昇腾 910B" --field framework
```

共享运行器负责搜索、URL 安全、去重、访问、网页快照、证据校验和隔离保存。目标表是 `chip_model_compatibility`。

## 负责字段

| 字段 | 含义 | 记录要求 |
|---|---|---|
| `model_id` | 被支持的模型 | 使用可识别的完整模型名或仓库 ID |
| `compat_status` | 兼容证据级别 | `verified`、`vendor_claimed` 或 `community`，不得夸大 |
| `framework` | 框架、后端或软件栈 | 保留名称与正文出现的版本信息 |
| `precision` | 可运行精度 | 只记录页面明确声明或验证的精度 |
| `notes` | 兼容限制与说明 | 版本、算子限制、适用范围等简要上下文 |

## 执行流程

1. 根据芯片名、兼容对象和“compatibility matrix / supported models / 兼容列表”等来源词生成搜索计划。
2. 粗筛要求同时出现目标芯片和兼容、支持、框架、模型或软件栈线索；只有性能数字的页面不进入本类。
3. 来源优先级为官方兼容矩阵、厂商开发者文档、官方仓库与版本说明，其次是可复现社区记录。运行器保存网页快照。
4. 精确复核支持关系：厂商声明标记 `vendor_claimed`，真实运行证据才可标记 `verified`，社区教程或报告使用 `community`。不从“支持某框架”推断“支持该框架所有模型”。
5. 每个结论保存网页中的逐字证据。若正文还包含性能或部署信息，将相应类别加入 `information_categories`；当前 `extracted_fields` 只使用上表字段。
6. 运行器校验字段白名单、实体、枚举和证据；芯片或兼容对象含糊时拒绝。

## 输出与留痕

- `url_assets.jsonl`：记录 URL、搜索词、粗筛结果、网页快照、`information_categories`、`extracted_fields` 和复核原因。
- `extracted_facts.jsonl`：记录候选兼容字段、逐字证据、置信度与拒绝原因。
- 分类和候选只写隔离副本中的 `test_url_classifications`、`test_extracted_records`。

本 Skill 不写正式数据库，也不会因为出现一次安装截图就自动升级为已验证兼容。

## 常见错误

- 将“支持 PyTorch”扩展成“支持所有 PyTorch 模型”。
- 把厂商兼容矩阵记成 `verified`，或把社区猜测记成官方声明。
- 只看到芯片和模型同页出现，就断言二者兼容。
- 将吞吐、时延等实测值混入兼容关系。

## 完成标准

每条关系都能回答“哪颗芯片、支持什么对象、在什么精度或版本下、证据级别是什么、原文在哪里”；其他类别只标记不混写；所有访问和拒绝可追溯；正式数据库保持不变。
