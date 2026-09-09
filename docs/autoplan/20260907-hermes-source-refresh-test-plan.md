# 第一阶段测试计划

由 gstack 工程审查生成。目标是验证 10～20 条官方芯片来源的增量复查链路，同时证明现有业务功能和正式数据不受影响。

## 受影响入口

- `scripts/run_source_refresh.py`：dry-run、apply-state、参数校验、退出码和 JSON 输出。
- `chip_model/pipeline/source_refresh.py`：条件请求、正文规范化、哈希、状态机、退避、快照。
- `schema.sql`：三张控制表和索引的幂等初始化。

## 关键交互

- 显式传入 link IDs 后能预览选择范围。
- dry-run 不发请求、不写库、不写文件。
- apply-state 仅写控制表和快照，不写五张业务表及 `field_provenance`。
- changed、unchanged、retry_wait、failed 均有明确 JSON 结果和退出码。

## 边界与错误

- 首次 200、相同正文 200、不同正文 200、304。
- ETag、Last-Modified、Retry-After。
- 空正文、响应过大、非法协议、重定向过多。
- 连接超时、读取超时、DNS/证书错误、429、4xx、5xx。
- 快照目录无权限或磁盘写入失败。
- SQLite busy/locked、重复 schema 初始化、中途事务失败。

## 关键验收

1. `pytest -q tests/test_source_refresh.py` 全部通过。
2. `pytest -q` 现有回归测试全部通过。
3. 运行前后业务表内容哈希一致。
4. 两条来源小流量 apply-state 成功后，再扩大到 10～20 条。
5. 同一未变化来源重复执行不会产生重复正文快照。
