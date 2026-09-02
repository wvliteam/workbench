# docsvr 文档服务器设计

docsvr 是工作台的**文档写入中间层**：把「文档能被谁写的」这个决定权，从 agent 进程手里移到操作系统文件权限和独立服务手里。它是对契约冻结的另一层加固，不是替代。

## 目标

现有契约机制（`wb.py contract lock` + `PreToolUse` 守卫 + `contract verify` 哈希校验）已经解决了「agent 遵守规则时改不了冻结文档」。它的盲区是：**一切都跑在同一个 `work` 用户下**，`Bash` 走 `cp` / `mv` / `git checkout` / 跑脚本等守卫无法可靠枚举的路径时，agent 仍有机会直接改文档。

docsvr 的目标是把这条盲区从根上封死：

- 文档文件系统权限：**agent 进程（`work`）对锁定文档所在目录没有写权限。**
- 文档写入入口：**agent 只能通过 `docctl` 调 docsvr**，且写被锁定的文档时由 docsvr 拒绝。
- 解锁入口：**只有用户（专用管理路径）能触达**，agent 没有解锁能力。

换句话说，把「能不能改」从「hook 猜命令」升级为「进程权限 + 服务端状态判定」。

## 信任模型与权限边界

### 三个主体

| 主体 | 运行身份 | 能做什么 | 不能做什么 |
| --- | --- | --- | --- |
| agent（主线程与 subagent） | `work` | 读文档、调 `docctl read`、调 `docctl write`（未被锁定时）、调 `docctl verify` | 直接写锁定文档目录、解锁、伪造解锁凭证 |
| docsvr | 专用账户（`docsvr`） | 拥有文档目录与锁定状态、执行写/锁定/验签/解锁请求 | 不接受 agent 伪造身份 |
| 用户 | 交互终端 | 通过 `docctl unlock`（带一次性凭证）解锁、维修 docsvr | — |

### 关键边界：物理写权限

docsvr 同用户的 shell 里 `cp`/`mv`/脚本**无法**修改锁定文档，因为文件系统权限拒绝它。这是 docsvr 方案与「再加一条 Bash 正则」的本质区别：**约束来自操作系统，不是来自提示词或可枚举的命令。**

- 文档目录：`docsvr` 属主，`drwx-wx---` 或更严（`drwxrwx---` 时 `docsvr` 组内进程可写，`work` 不可写）。
- 文件：`docsvr:docsvr`，`0660`。agent 只读（`0440` 供读）。
- `.workbench/contracts/` 与 `.workbench/artifacts/` 若纳入 docsvr，同样归 docsvr 管理，`work` 只读。

**必须避免**的伪隔离：把 docsvr 脚本放在 agent 有写权限的共享目录里、或让 agent 能 `kill`/替换 docsvr 进程。那会把 docsvr 降级成又一个可被绕过的 hook。

### 为什么 socket 也要鉴权

Unix socket 的 `chmod`/group 只能限定「谁能连」，不能区分「连上来的是用户还是 agent」——因为 agent 与用户在同一台机、同一用户会话下时，都可能连接同一个 socket。所以：**socket 权限只做第一道粗粒度隔离，真正的身份判定靠 token**。

## docctl：agent 唯一入口

agent 不直接读写 socket 协议。它只调用 `docctl`：

```
docctl read <doc>               # 读文档
docctl write <doc> <content>    # 写文档；docsvr 查锁定，锁定则拒绝
docctl lock <doc>               # 写锁（仅授权路径）
docctl unlock <doc>             # 仅用户路径 + 一次性凭证
docctl verify <doc>             # 验签 / 哈希
docctl status                   # 锁定状态一览
```

`docctl` 以 `work` 身份运行，但它不自己碰文档文件——它把请求转发给 docsvr。**docctl 的价值不是「CLI 更安全」，而是：**

1. 收窄入口：agent 只看到稳定子命令，不接触 socket 协议细节。
2. 可审计：所有写/锁定/解锁都走同一条 `docctl → docsvr → 审计日志`。
3. 可替换：socket 换成 TCP/TLS 或 Unix socket 时，agent 侧无需改。

**`docctl` 本身是否可信，取决于 docsvr 的鉴权，而不是取决于「agent 用的是 CLI」。** 若 docsvr 对 `work` 的无凭证 `docctl write` 也放行，那锁定形同虚设。

## docsvr 服务

### 进程与账户布局

```
docsvr 以 docsvr 账户常驻
  ├── 拥有文档目录（只读数据 + 自身写）
  ├── 拥有锁定状态文件
  └── 监听 unix socket：/run/docsvr/docsvr.sock（docsvr:docsvr, 0660）
docctl（work 身份）
  └── 连接 socket，发送请求，读响应
用户终端
  ├── docctl unlock <doc>（带一次性凭证）
  └── 运维：docsvr 重启、日志查看
```

### 数据

文档内容存 docsvr 拥有目录；锁定元数据存 `.docsvr/state.json`（docsvr 独有写权限）。

```jsonc
{
  "docs": [
    {
      "id": "design-doc",
      "path": ".workbench/artifacts/design/design.md",
      "locked": true,
      "sha": "sha256:...",
      "unlock_nonce": null,        // 解锁时签发的一次性凭证
      "locked_at": "...",
      "unlock_reason": null
    }
  ],
  "audit": []
}
```

### 锁模型

- **读**：任意授权身份可读，永远放行。
- **写**：`locked=false` 可写；`locked=true` 拒绝（除非持有有效 unlock 凭证）。
- **验签**：返回文档当前哈希与锁定哈希比对结果。
- **unlock**：**只允许用户路径**。docsvr 收到带**有效一次性凭证**的 unlock 才把 `locked=false` 设置，并记录理由。
- **lock**：写完后由授权路径（architect 定稿、或用户确认）调用，重新捕获 `sha` 并置 `locked=true`。

### 鉴权与认证（关键）

docsvr 对每个写/锁定/解锁请求做**认证 + 授权**两层：

- 认证：请求必须带有效 token（`Bearer <token>`）。`work` agent 通常只有「只读 + 非锁定写」的低级 token；用户有「解锁」的高级 token。
- 授权：从 `state.json` 查目标文档的锁定状态，`locked=true` 且请求为写/解锁时拒绝。

**解锁凭证生成**：用户端（如在主线程未接入 agent 的终端会话）执行一条离线/交互命令，例如：

```
docsvr issue-unlock design-doc --reason '补回滚方案'
# 打印一次性 short-lived token，120s 内有效
docctl unlock design-doc --token <token>
```

凭证**不进 agent 上下文**：它只在用户终端生成、由用户亲手传给 `docctl unlock`。若 agent 能读到该 token（例如被写进文件/回显到 transcript），则该 token 立即视为已泄露，docsvr 应拒绝或该文档重新锁定。

### 审计

docsvr 把所有「写」「锁定」「验签漂移」「解锁尝试（成功/失败）」写进 `.docsvr/audit.jsonl`（docsvr 独有写权限）。失败解锁也记录——它可能是 agent 伪造身份的试探，是安全事件。

## Unix socket 协议

请求/响应用独立一行 JSON，字段充分 ID 化：

```jsonc
// 请求（docctl --json 编码）
{ "cmd": "write", "doc": "design-doc", "content": "...", "token": "..." }
// 响应
{ "ok": false, "error": { "code": "LOCKED", "message": "design-doc 已锁定，须用户解锁" } }
// 响应
{ "ok": true, "sha": "sha256:..." }
```

事件码建议：`OK`、`LOCKED`、`BAD_TOKEN`、`NOT_FOUND`、`BAD_ARG`、`DRIFT`、`UNAUTHORIZED`。

## 故障与失败语义

- **docsvr 离线**：`docctl` 应返回明确错误（`EDOCSVR_DOWN`），**不得静默降级为直接写文件**——降级就是绕过。
- **token 过期/伪造**：拒绝并记审计。
- **写失败**：`docctl write` 返回非零退出码 + 错误，agent 应据此 `task block` 上报主线程。
- **同步点**：`wb.py contract lock` 应通过 docsvr 落锁定，避免「wb.py 说锁了、docsvr 没锁」分叉。docsvr 是锁定状态的权威，`wb.py` 保留审计/门禁逻辑作为上层看板。

## 与现有工作台的关系

| 层 | 职责 | 谁 |
| --- | --- | --- |
| `wb.py` 门禁 + 契约 + 守卫 | 流程决策、阶段推进、哈希核对、审计 | 主线程 / 编排 |
| `docsvr` | 文档物理写权限 + 锁定状态 + 写/解锁判定 | 专用账户服务 |
| `docctl` | agent 调 docsvr 的唯一 CLI | agent / 用户 |

- `wb.py contract verify` 仍可做哈希核对，但它的结果来自「读 docsvr 拥有的文档」+「读 docsvr 的锁定状态」，不是直接信任 agent 写的文件。
- 需要解冻时，流程不变：`contract unlock --reason` 的申报由主线程/用户走，实际放行由 docsvr 的解锁凭证完成。用户预留给解锁：**agent 无解锁能力**。

## 安全边界与已知限制

| 项 | 说明 |
| --- | --- |
| docsvr 同机、docsvr 账户 | 若 docsvr 账户被拿到 root 或可被 `kill`/替换，边界失效 |
| token 泄露 | 解锁 token 若进入 agent 上下文/磁盘共享区，等同泄露；需 short-lived + 审计 |
| 强攻击 | `docsvr` 账户被攻破、或用户恶意操作，超出本设计目标 |
| 文档文件系统权限 | 必须由部署环节正确设置；若目录仍可被 `work` 写，docsvr 无意义 |

## 演进路径

1. **阶段一（原理验证 demo）**：单机用两个账户或最小化权限模拟，验证「`work` 不能写、docsvr 能写」+「锁定写被拒」+「解锁带凭证」。
2. **阶段二（接入）**：`docctl` 接到 `wb.py contract lock/verify/unlock/bump`；agent 定义里写明只准用 `docctl`。
3. **阶段三（收口）**：用户在正式环境以系统服务/专用账户部署 docsvr，`work` 彻底失去对文档目录的写权限。

