# Debugger Workspace 规则

本文件是 `debugger` 角色的业务自定义规则**内核默认版**。业务 workspace 可在本地覆盖此文件以注入特定技术栈的排障约定。与权威源冲突时以后者为准。

## 业务排障入口指针（供项目 workspace 覆盖）
- 日志检索系统：`docs/observability/logging.md`（如 Kibana / Elasticsearch / 内部日志流索引）
- 监控指标系统：`docs/observability/metrics.md`（如 Prometheus / Grafana / 告警策略对照）
- 链路追踪系统：`docs/observability/tracing.md`（如 Jaeger / SkyWalking / TraceID 查询约定）
- 常见故障库：`docs/troubleshooting/`（已知偶发问题、环境抖动与既有 Workaround）

## 核心操作约束
- **只读底线**：严禁向任何线上生产数据库、外部服务或消息队列发送写入请求；
- **排查定点**：必须给出具体到 `file:line` 的根因代码，严格区分事实（有确凿日志/断言证据）与推断（理论调用路径）；
- **最小复现**：仅在本地临时环境或只读单元测试框架中构造最小可复现用例。
