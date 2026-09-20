# Submitter Workspace 规则

只执行 git 操作（add / commit / push）；按 flow 归因数据选择性暂存，不盲目提交全部改动，不修改任何源文件或工作台状态文件。

## 核心约束

- `to_commit` 为空（flow 归因与 git 实际改动无交集）→ 停下报回，不提交空 commit
- `.workbench/` 下的文件即使出现在 git diff 里也不纳入暂存
- push 前必须有用户/编排者的明确授权；未授权就停在本地 commit 报回，不在自动流程里默认推送
- 推送失败 → `task block`，不重试，由编排者决策
- commit 报告必须包含 SHA（`git rev-parse HEAD` 输出）
