# Commit Message 格式约定

> **模板文件（内核默认版）。** 业务 workspace 按自身 commit 规范覆盖本文件；
> 未覆盖时 submitter 角色使用下方默认格式。

## 默认格式（Conventional Commits 子集）

```
<type>(<scope>): <一句话摘要，≤72字符>

Flow: <flow名>
Tasks: T<id1>, T<id2>, ...
Files: <改动文件数>个

<可选：来自 requirements.md 的验收标准摘要，≤3条，每条一行>
```

### type 取值

| type | 含义 | 何时使用 |
| --- | --- | --- |
| `feat` | 新功能 | 新增用户可见的能力 |
| `fix` | 缺陷修复 | 修复已有功能的错误行为 |
| `refactor` | 重构 | 不改变外部行为的内部重写 |
| `docs` | 纯文档 | 只改文档、注释、README |
| `test` | 测试 | 只改测试代码 |
| `chore` | 杂项 | 依赖升级、配置调整等 |

### scope 取值

从 `requirements.md` 的需求标题或影响的主要模块推导；无法确定时省略括号。

## 业务 workspace 覆盖说明

在本文件中替换或追加以下内容以适配业务项目：

```markdown
## 项目约定覆盖

<!-- 以下覆盖内核默认值，submitter 角色优先读此处 -->

### 强制前缀
所有 commit 必须以 `[<项目代号>]` 开头，例如：
`[HOTEL] feat(search): 新增关键词搜索接口`

### type 扩展
| type | 含义 |
| `perf` | 性能优化 |
| `i18n` | 国际化 |

### Gerrit Change-Id
本项目启用 Gerrit，commit message 末尾必须附 Change-Id（由 commit-msg hook 自动追加）。
提交前确认 `.git/hooks/commit-msg` 存在且可执行；不存在时停下报回编排者。

### 禁用模式
- 禁止 `fix: typo` 这类无定位的提交（必须附文件名）
- 合并提交使用 `merge(<flow>): <需求标题>`
```
