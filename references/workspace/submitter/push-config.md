# 推送配置

> **模板文件（内核默认版）。** 业务 workspace 覆盖本文件以适配不同推送模式；
> 未覆盖时 submitter 角色使用「模式 A：直接推送」。

## 推送模式选择

在本文件的「当前项目配置」节填写 `push_mode`，submitter 角色据此决定推送方式：

```markdown
## 当前项目配置

push_mode: A          # A | B | C，见下方说明
remote: origin        # 默认 origin
```

| 模式 | 命令 | 适用场景 |
| --- | --- | --- |
| A | `git push <remote> <当前分支>` | 直接合入主干或功能分支，无评审系统 |
| B | `git push <remote> HEAD:refs/for/<gerrit_target>` | Gerrit Code Review |
| C | `git push <remote> HEAD:<branch_prefix>/<flow名>` | GitHub/GitLab PR 工作流，需另行发起 PR |

## 模式 B（Gerrit）配置项

```markdown
## 当前项目配置

push_mode: B
remote: origin
gerrit_target: main          # Gerrit 的目标合入分支，默认 main
gerrit_topic: ""             # 可选，%topic=<topic> 附加到 refs/for/<target> 后
gerrit_reviewers: []         # 可选，%r=<email> 追加评审人，多人用列表
commit_msg_hook: true        # Gerrit commit-msg hook 是否已安装（Change-Id 必须）
```

完整推送命令示例（含 topic 与评审人）：
```bash
git push origin HEAD:refs/for/main%topic=hotel-search,r=alice@example.com,r=bob@example.com
```

若 `commit_msg_hook: true` 但 `.git/hooks/commit-msg` 不存在或不可执行，submitter 角色**停下报回**，不执行推送——没有 Change-Id 的 Gerrit 提交会被服务端拒绝。

## 模式 C（PR 分支）配置项

```markdown
## 当前项目配置

push_mode: C
remote: origin
branch_prefix: flow          # 推送到 flow/<flow名>，也可用 feature / pr 等
```

submitter 角色推送后只报告远端分支 URL，不自动发起 PR——发起 PR 需要平台凭据，属于主线程或人工操作。

## 多仓库场景

若工作区有多个仓库（`repos/.source/<项目>/<仓库>`），在本文件中为每个仓库单独指定配置：

```markdown
## 仓库级配置覆盖

# 默认（未列出的仓库使用全局 push_mode）
push_mode: A
remote: origin

# 仓库专属覆盖
repos/.source/bddev/maphotel:
  push_mode: B
  gerrit_target: master
  gerrit_topic: "hotel-{{flow}}"   # {{flow}} 会被替换为当前 flow 名

repos/.source/map-hotel-fe/hotel-web:
  push_mode: C
  branch_prefix: feature
```

submitter 角色按 `to_commit` 文件所属仓库逐仓库分组执行推送；不同仓库的推送失败独立报告，不因一个仓库失败而跳过其余。

## 业务 workspace 操作步骤

1. 复制本文件到项目的 `references/workspace/submitter/push-config.md`（已在 index.md 引用）。
2. 填写「当前项目配置」节，删除不需要的模式说明。
3. 多仓库时补充「仓库级配置覆盖」节。
4. 若启用 Gerrit，确认 commit-msg hook 已安装（`scp -p -P <port> <gerrit-host>:hooks/commit-msg .git/hooks/`）。
5. 提交本文件进 git（它是工作区级配置，不含凭据）。
