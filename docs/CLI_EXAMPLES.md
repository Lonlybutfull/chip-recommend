# Parse1 CLI 命令示例全集

## 约定

- 所有命令在 `芯片+模型/parse1/` 目录下执行
- `--db-path` 指向 `芯片+模型/parse1.db`
- `-o json` 输出 JSON，`-o yaml` 输出 YAML，`-o table` 输出表格
- 模糊匹配：`--search` / `-m` / chip name / model name 均用 LIKE `%xxx%`

---

## 1. chip list — 芯片搜索

```bash
# 全量
python "芯片+模型/cli.py" --db-path "芯片+模型/parse1.db" chip list -o json

# 模糊搜索
python "芯片+模型/cli.py" --db-path "芯片+模型/parse1.db" chip list --search "H100" -o json

# 国产芯片
python "芯片+模型/cli.py" --db-path "芯片+模型/parse1.db" chip list --region domestic -o json

# 国外数据中心级芯片
python "芯片+模型/cli.py" --db-path "芯片+模型/parse1.db" chip list --region foreign --tier datacenter -o json

# 显存范围 + 用途
python "芯片+模型/cli.py" --db-path "芯片+模型/parse1.db" chip list --vram-min 64 --vram-max 192 --usage train -o json

# 特定厂商
python "芯片+模型/cli.py" --db-path "芯片+模型/parse1.db" chip list --vendor "NVIDIA" -o json

# 分页
python "芯片+模型/cli.py" --db-path "芯片+模型/parse1.db" chip list --limit 10 --offset 20 -o json
```

---

## 2. chip profile — 芯片画像

```bash
# 单芯片完整画像
python "芯片+模型/cli.py" --db-path "芯片+模型/parse1.db" chip profile "H100 SXM5" -o json

# 批量
python "芯片+模型/cli.py" --db-path "芯片+模型/parse1.db" chip profile "H100 SXM5" "昇腾910B" "MI300X" -o json

# 精简画像
python "芯片+模型/cli.py" --db-path "芯片+模型/parse1.db" chip profile "H100 SXM5" -f compact -o json

# 自定义格式串
python "芯片+模型/cli.py" --db-path "芯片+模型/parse1.db" chip profile "H100" -f "{{chip_model}}: {{vram_gb}}GB, {{precision_perf}}" -o json
```

---

## 3. chip filter — 芯片筛选

```bash
# 硬约束筛选
python "芯片+模型/cli.py" --db-path "芯片+模型/parse1.db" chip filter --region domestic --vram-min 64 --tdp-max 400 -o json

# 价格 + 生态成熟度排除
python "芯片+模型/cli.py" --db-path "芯片+模型/parse1.db" chip filter --price-max 20 --min-maturity 3 -o json

# 互联带宽要求
python "芯片+模型/cli.py" --db-path "芯片+模型/parse1.db" chip filter --interconnect-min 600 -o json

# 模型驱动：自动推算显存需求（训练）
python "芯片+模型/cli.py" --db-path "芯片+模型/parse1.db" chip filter --for-model "Qwen2.5-7B" --scenario train -o json

# 模型驱动 + 训练天数 + 最大卡数
python "芯片+模型/cli.py" --db-path "芯片+模型/parse1.db" chip filter --for-model "Qwen2.5-72B" --scenario train --training-days 7 --max-cards 16 -o json

# 模型驱动（推理）
python "芯片+模型/cli.py" --db-path "芯片+模型/parse1.db" chip filter --for-model "Llama-3.1-8B" --scenario inference -o json
```

---

## 4. chip recommend — 芯片推荐（核心）

```bash
# 基础训练推荐
python "芯片+模型/cli.py" --db-path "芯片+模型/parse1.db" chip recommend -m "Qwen2.5-7B" -s train -d 3 -o json

# 训练 + 硬约束（卡数/价格/成熟度）
python "芯片+模型/cli.py" --db-path "芯片+模型/parse1.db" chip recommend -m "Qwen2.5-7B" -s train -d 3 --max-cards 8 --max-price 30 --min-maturity 3 -o json

# 国产优先
python "芯片+模型/cli.py" --db-path "芯片+模型/parse1.db" chip recommend -m "Qwen2.5-7B" -s train -d 5 --domestic -o json

# 指定厂商优先
python "芯片+模型/cli.py" --db-path "芯片+模型/parse1.db" chip recommend -m "Qwen2.5-7B" -s train -d 3 --prefer-vendor "NVIDIA" -o json

# 推理推荐（吞吐SLA）
python "芯片+模型/cli.py" --db-path "芯片+模型/parse1.db" chip recommend -m "Qwen2.5-72B" -s inference --sla-tps 5000 -o json

# 大模型推理 + 价格上限
python "芯片+模型/cli.py" --db-path "芯片+模型/parse1.db" chip recommend -m "Llama-3.1-70B" -s inference --sla-tps 3000 --max-price 25 -o json

# MoE 模型推理
python "芯片+模型/cli.py" --db-path "芯片+模型/parse1.db" chip recommend -m "Mixtral-8x22B" -s inference -o json

# 不限级别（含 consumer）
python "芯片+模型/cli.py" --db-path "芯片+模型/parse1.db" chip recommend -m "Qwen2.5-7B" -s train -d 3 --tier all -o json

# 返回更多候选
python "芯片+模型/cli.py" --db-path "芯片+模型/parse1.db" chip recommend -m "Qwen2.5-7B" -s train -d 3 -n 10 -o json
```

---

## 5. model list — 搜索模型

```bash
# 全量
python "芯片+模型/cli.py" --db-path "芯片+模型/parse1.db" model list -o json

# 名称搜索
python "芯片+模型/cli.py" --db-path "芯片+模型/parse1.db" model list --search "Qwen" -o json

# 按架构筛选
python "芯片+模型/cli.py" --db-path "芯片+模型/parse1.db" model list --architecture dense -o json
python "芯片+模型/cli.py" --db-path "芯片+模型/parse1.db" model list --architecture moe -o json

# 参数量范围
python "芯片+模型/cli.py" --db-path "芯片+模型/parse1.db" model list --params-min 30 --params-max 100 -o json

# 大模型（≥70B）
python "芯片+模型/cli.py" --db-path "芯片+模型/parse1.db" model list --params-min 70 -o json
```

---

## 6. model profile — 模型画像

```bash
# 单模型
python "芯片+模型/cli.py" --db-path "芯片+模型/parse1.db" model profile "Qwen2.5-7B" -o json

# 批量
python "芯片+模型/cli.py" --db-path "芯片+模型/parse1.db" model profile "Qwen2.5-7B" "DeepSeek-V3" "Llama-3.1-8B" -o json

# 精简
python "芯片+模型/cli.py" --db-path "芯片+模型/parse1.db" model profile "Qwen2.5-7B" -f compact -o json
```

---

## 7. model filter — 模型筛选

```bash
# 按芯片反查兼容模型
python "芯片+模型/cli.py" --db-path "芯片+模型/parse1.db" model filter --for-chip "H100" -o json

# 国产芯片能跑哪些模型
python "芯片+模型/cli.py" --db-path "芯片+模型/parse1.db" model filter --for-chip "昇腾910B" -o json

# 芯片 + 架构约束
python "芯片+模型/cli.py" --db-path "芯片+模型/parse1.db" model filter --for-chip "MI300X" --architecture dense -o json

# 芯片 + 参数量
python "芯片+模型/cli.py" --db-path "芯片+模型/parse1.db" model filter --for-chip "H100" --params-min 30 -o json
```

---

## 8. db status — 数据库状态

```bash
python "芯片+模型/cli.py" --db-path "芯片+模型/parse1.db" db status
```

输出示例：

```yaml
database: 芯片+模型/parse1.db
tables:
  chips: 12
  models: 10
  chip_model_benchmarks: 9
  chip_model_compatibility: 17
  field_provenance: 9
```

---

## 9. db migrate — 数据导入

```bash
# 全量重建
python "芯片+模型/cli.py" --db-path "芯片+模型/parse1.db" db migrate

# 分批导入
python "芯片+模型/cli.py" --db-path "芯片+模型/parse1.db" db migrate --chips-only
python "芯片+模型/cli.py" --db-path "芯片+模型/parse1.db" db migrate --models-only
python "芯片+模型/cli.py" --db-path "芯片+模型/parse1.db" db migrate --benchmarks-only
```

---

## 10. config — 配置管理

```bash
# 查看当前配置
python "芯片+模型/cli.py" config show

# 设置默认画像格式
python "芯片+模型/cli.py" config set profiles.default_chip_format compact

# 设置默认输出格式
python "芯片+模型/cli.py" config set output.default_format json
```

---

## 评分维度速查（chip recommend）

| 维度 | 权重 | 说明 |
|------|------|------|
| compute_power | 25% | 从 precision_perf 提取 BF16/FP16 TFLOPS |
| card_efficiency | 15% | 卡数越少分越高 |
| price_efficiency | 15% | TFLOPS/万元 + 总成本分档 |
| power_efficiency | 10% | GFLOPS/W |
| ecosystem_maturity | 10% | maturity_level × 0.8 + cloud_available |
| interconnect_quality | 10% | bandwidth / 200 + has_interconnect |
| sla_satisfaction | 10% | 预估天数 ≤ 目标则加分 |
| data_quality | 5% | production_status |
| production_readiness | — | 偏好 (domestic +3 / prefer_vendor +8) |

---

## 测试数据库速查（seed_db.py 产出）

| 表 | 条数 | 内容 |
|---|---|---|
| chips | 12 | NVIDIA×4, AMD×1, Huawei×2, Cambricon×1, Intel×1, Google×1, 景嘉微×1, Qualcomm×1 |
| models | 10 | Dense×7, MoE×3 (7B~671B) |
| chip_model_benchmarks | 9 | 推理×7, 训练×2 |
| chip_model_compatibility | 17 | verified×12, vendor_claimed×4, community×1 |
| field_provenance | 9 | 精度算力/价格/成熟度/兼容状态的来源追溯 |
