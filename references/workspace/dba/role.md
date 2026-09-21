# DBA Workspace 规则

本文件是 `dba` 角色的业务自定义规则**内核默认版**。业务 workspace 可在本地覆盖此文件以注入特定技术栈的数据库规范与大表清单。与权威源冲突时以后者为准。

## 业务数据库环境与指针（供项目 workspace 覆盖）
- 数据库引擎与版本矩阵：`docs/database/engine.md`（如 MySQL 8.0, PostgreSQL 15, TiDB 等特性支持）
- 生产高危与核心大表清单：`docs/database/critical-tables.md`（高并发核心业务表，DDL 需走严格评审）
- 在线表变更工具指引：`docs/database/online-ddl.md`（如 pt-online-schema-change, gh-ost, pg-repack 等参数指引）

## 核心设计与执行约束

1. **双向迁移（Up & Down）强制对称**：
   - 每一个 DDL / DML 变更都必须提供完整、可逆的 Down 回滚脚本；
   - 严禁提交包含单向不可逆（如未经备份的 `DROP COLUMN`）的操作；
   - 必须在本地或测试数据库中真实运行并验证 `up -> down -> up` 完整过程。

2. **零停机平滑演进（Expand & Contract 模式）**：
   - **新增字段**：必须设为 `NULL` 或提供无锁默认值，不得添加无默认值的 `NOT NULL` 列；
   - **重命名字段/表**：禁止直接重命名！必须走三阶段发布：
     1. 添加新列，后端双写（Dual-write）；
     2. 历史数据离线/分批回填（Backfill）；
     3. 切换读流量到新列，后续独立发布清理旧列。
   - **删除字段/表**：必须在所有代码已经停止引用至少一个版本后，才可建立独立的下线迁移。

3. **生产锁表与性能风险**：
   - 大表加索引必须采用并发/无锁语法（如 Postgres `CREATE INDEX CONCURRENTLY`，MySQL Online DDL / pt-online-schema-change）；
   - 禁止在单个大事务中执行长耗时 DML 更新大量数据；必须采用按主键分批（Batching）更新。

4. **契约忠实性**：
   - 字段类型、命名、非空约束必须与 `design.md` 和接口契约保持严格一致；
   - 发现契约在数据库实现上有性能缺陷或类型不妥时，执行 `task block <ID>` 报回主线程，由 architect 修改契约，不得擅自修改。
