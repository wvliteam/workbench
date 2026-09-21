# DevOps Workspace 规则

本文件是 `devops` 角色的业务自定义规则**内核默认版**。业务 workspace 可在本地覆盖此文件以注入特定技术栈的发布平台与环境参数。与权威源冲突时以后者为准。

## 业务发布与环境指针（供项目 workspace 覆盖）
- 持续集成与流水线入口：`docs/deployment/ci-pipeline.md`（如 GitHub Actions, GitLab CI, iPipe 等平台任务规范）
- 集群与部署环境拓扑：`docs/deployment/environments.md`（开发、测试、预发、生产环境的域名、集群与命名空间对应表）
- 灰度发布与健康阈值：`docs/deployment/canary-policy.md`（探针响应时间、错误率报警线与自动回滚判据）

## 核心职责与操作边界

1. **部署配置与物料管理**：
   - 维护与核对 `deploy/**`, `k8s/**`, `docker/**`, `.github/workflows/**`, `helm/**`；
   - 检查容器配置（Dockerfile 基础镜像安全、多阶段构建、非 root 用户运行）；
   - 检查 K8s 资源限制（requests / limits）、优雅停机配置与探针（Readiness & Liveness probe）。

2. **严禁泄露生产凭据**：
   - 绝不提交包含明文密码、私钥、API Token 的配置文件；
   - 环境变量与敏感配置必须通过 Secret / 外部配置中心注入。

3. **发布与灰度控制**：
   - 在 `submitter` 提交推送后，或在独立发布流程中触发；
   - 破坏性发布操作（全量流量切换、生产环境发布、服务重启）必须有用户或编排者明确授权；
   - 发布后必须执行健康巡检（查看 Pod 状态、健康探针响应、错误日志流）。

4. **回滚底线保障**：
   - 任何发布计划必须明确前一版本的镜像 Tag 或 Commit SHA；
   - 异常时立即执行回滚预案，并在 `deploy-report.md` 中记录处置过程。
