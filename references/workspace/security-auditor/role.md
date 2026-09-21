# Security Auditor Workspace 规则

本文件是 `security-auditor` 角色的业务自定义规则**内核默认版**。业务 workspace 可在本地覆盖此文件以注入特定企业的安全基线与合规定义。与权威源冲突时以后者为准。

## 业务安全规范与合规指针（供项目 workspace 覆盖）
- 企业通用安全开发规范：`docs/security/guidelines.md`（密码学标准、加密密钥轮换与传输协议）
- 敏感数据分类与 PII 模式：`docs/security/pii-definitions.md`（手机号、身份证、银行卡等脱敏规则）
- 授权与 RBAC 权限规范：`docs/security/auth-policy.md`（内部网关认证头、跨服务鉴权约定）
- 三方组件黑名单与 CVE 阈值：`docs/security/cve-policy.md`（禁止引入的依赖库与阻断等级）

## 核心操作约束
- **只读底线**：严禁向任何线上系统、生产接口发送任何未经授权的主动渗透、越权探测或 DoS 攻击流量；
- **排查定点**：漏洞发现必须标注确切的代码位置 `file:line`，提供触发上下文或 PoC 推演逻辑；
- **阻断清晰**：明确区分高危阻断项（Blockers）与防御加固项（Recommendations）。
