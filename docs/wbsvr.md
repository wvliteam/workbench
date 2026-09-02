# wbsvr：契约托管服务

wbsvr 把工作台的**流程状态与契约文档**从 agent 可寻址的文件系统里搬走，交给一个 agent 无法触达的专用账户保管。它不是又一层 hook，是把「能不能改」的判定从「猜命令」升级成「操作系统权限」。

> 本文取代早期的 docsvr 设计（本文件的前一版本，`git log --follow docs/wbsvr.md` 可查）。那一版的常驻 daemon、unix socket、bearer token 三样都被推翻了，理由见 [关键决策点](#关键决策点) D3、D4。

## 要解决的问题

现有机制（`contract lock` 哈希冻结 + `PreToolUse` 守卫 + `contract verify`）已经挡住了「agent 守规矩时改不了冻结文档」。它的结构性盲区有两条：

**盲区一：Bash 写入路径无法枚举。** 守卫靠正则匹配命令文本，`BASH_WRITE` 目前覆盖 `>` / `tee` / `sed -i` / `patch` / `dd` 等，但**不含 `cp` 与 `mv`**。更根本的是，`git checkout`、外部编辑器、任意脚本这些路径原理上就抽不出写入目标 —— 做半个检查比不做更坏，因为它让人误以为有保护。

**盲区二：哈希校验有结构性盲点。** agent 改文件 → 干完活 → `git checkout` 还原 → 哈希一致，全程检测不到。哈希只能告诉你「现在不一样」，不能告诉你「中间被动过」。

wbsvr 的做法不是把这些路径一条条堵上，而是**让被保护的文件在 agent 的地址空间里根本不存在**。

## 核心洞察：不可寻址 > 不可写

把文件设成只读，防的是「写」这一个动作，于是要逐个防：符号链接、临时文件 + `mv`、`git checkout`、`..` 穿越、权限位配错、属主意外正确。每一条都是一个可能配错的地方。

把文件挪到 agent 连路径都拿不到的地方，上面整类攻击一次性消失 —— 不是被防住，是无从下手。

同理，接口层面：**允许清单优于命令黑名单**。`read` / `commit` / `lock` / `verify` 枚举的是「好的」，是有限集；`BASH_WRITE` / `DENY_BASH` 枚举的是「坏的」，永远漏。

## 信任模型

| 主体 | 身份 | 能做 | 不能做 |
| --- | --- | --- | --- |
| agent（主线程 + subagent） | `work` | 经 `wb.py` 读契约、提交未锁定契约、上锁、验签、读 sealed 字段 | 寻址存储目录、解锁、写 sealed 字段、拿 shell |
| wbsvrd | `wbsvr` | 拥有存储目录、判定锁定状态、写审计日志 | 执行门禁命令（见 D8）、验证 agent 侧发生的事 |
| 用户 | 交互终端 | `sudo -u wbsvr wbsvrd unlock`（输密码） | — |

**没有 token。** 用户的 sudo 密码就是凭证 —— 它拿不到、不会漏进 transcript、不用管过期，比任何一次性字符串都强（D3）。

## 存储布局

```
/var/lib/wbsvr/<项目名>-<项目路径 sha256 前 8 位>/     wbsvr:wbsvr 0700
├── docs/<name>              托管的契约与流程产物正文
├── refs.json                {name: {sha, locked, unlock_until, version, phase, kind, reason}}
├── sealed.json              {phase, role_scopes, gate_commands, gate_timeout, tasks_graph}
└── audit.jsonl              append-only，只记 wbsvrd 自己处理的操作
```

`0700` 意味着 agent 连 `ls` 都不行。路径里的 hash 让同名项目在不同路径下互不干扰。

agent 侧只多两样东西：

```
.workbench/wc/<name>          工作副本，checkout 落这里，commit 后删除
.workbench/frozen.snapshot    wbsvr:work 0444，供 PreToolUse hook 零开销读（D15）
```

## 什么托管，什么不托管

这条线不是按「重不重要」划的，是按约束划的 —— 每一项都有不得不如此的理由。

| 数据 | 归属 | 理由 |
| --- | --- | --- |
| 契约正文、design.md、requirements.md 等纯流程产物 | wbsvr | 核心保护对象 |
| `phase` | wbsvr | 阶段推进是流程骨架 |
| `role_scopes` | wbsvr | 改它等于自己给自己发权限 |
| `gate_commands` **内容** | wbsvr | 改它等于让门禁永远 PASS |
| `gate_timeout` | wbsvr | 同上 |
| `tasks_graph`（`deps` / `role` / `title`） | wbsvr | 顺序由结构决定（D9） |
| `tasks_state`（`status` / `started` / `done`） | agent | `task done` 本来就是合法调用，托管无增量收益 |
| `seq` / `log` / `gates` / `project` / `max_parallel` | agent | 同上 |
| 产物日志 `artifacts.jsonl` | agent | 最高频、最低风险，托管等于给每次文件写入加一次 sudo（D10） |
| repo 内契约（`repos/backend/openapi.yaml`） | git，只存 sha | 它必须留在 git 里给团队用，拿不到不可寻址性 |

最后一行是个真实的能力边界，不是偷懒：**进了 git 的契约只能退回哈希校验那一档保护。** 想要完整保护，契约得放托管存储，代价是它不进任何仓库。两种都合法，选哪个看契约该不该跟服务一起发布。

## 接口

wbsvrd 的子命令。agent 不直接调它 —— 调用方是 `wb.py`（D5）。

```
wbsvrd ping                        存活 + 权限自检
wbsvrd init <项目路径>             建存储；已存在则硬拒（见「必须成立的前提」）
wbsvrd list                        名字 / 锁定状态 / 版本
wbsvrd read <name>                 正文 → stdout
wbsvrd commit <name>               stdin → 正文；已锁定则拒
wbsvrd lock <name>                 重算 sha，置 locked
wbsvrd unlock <name> <reason>      置 unlock_until = now + N；不在 agent 的 sudoers 里
wbsvrd verify [name]               sha 比对
wbsvrd sealed-get <key>            读 sealed 字段
wbsvrd sealed-set <key> <value>    写 sealed 字段；不在 agent 的 sudoers 里
wbsvrd selfcheck                   存储权限 / refs 完整性
```

agent 侧接口不变，仍是今天那套 `wb.py contract *`：

| agent 跑 | wb.py 内部 |
| --- | --- |
| `wb.py contract list` | `_svr("list")` |
| `wb.py contract read --name X` | `_svr("read", "X")` |
| `wb.py contract checkout --name X` | `_svr("read", "X")` + 落盘工作副本 |
| `wb.py contract commit --name X` | `_svr("commit", "X", stdin=工作副本)` |
| `wb.py contract lock --name X` | `_svr("lock", "X")` |
| `wb.py contract verify` | `_svr("verify")` |
| `wb.py contract unlock --reason` | **不转发** —— 打印命令让用户自己跑 |

`checkout` / `commit` 的工作副本模型保住了 Read/Write/Edit 的使用体验：subagent 照常用编辑器改文件，只是文件的权威副本在别处。

```python
def _svr(*args, stdin: str | None = None) -> dict:
    p = subprocess.run(
        ["sudo", "-n", "-u", "wbsvr", WBSVRD, *args],
        input=stdin, capture_output=True, text=True, timeout=30,
    )
    if p.returncode != 0:
        raise WbsvrError(p.stderr.strip() or f"wbsvrd exit {p.returncode}")
    return json.loads(p.stdout)
```

## sudoers

```
# agent 能跑的 —— 无 unlock、无 sealed-set、无 shell
work ALL=(wbsvr) NOPASSWD: /usr/local/libexec/wbsvrd ping, \
                           /usr/local/libexec/wbsvrd list, \
                           /usr/local/libexec/wbsvrd read *, \
                           /usr/local/libexec/wbsvrd commit *, \
                           /usr/local/libexec/wbsvrd lock *, \
                           /usr/local/libexec/wbsvrd verify, \
                           /usr/local/libexec/wbsvrd sealed-get *

Defaults:work timestamp_timeout=0
```

用户解锁走正常密码 sudo：

```bash
sudo -u wbsvr /usr/local/libexec/wbsvrd unlock design-doc '补回滚方案'
```

**sudoers 的通配符不能当安全边界。** `*` 在*参数*位跨 `/` 且不规范化 `..` —— `wbsvrd read *` 会放行 `wbsvrd read ../../etc/shadow`。参数校验必须在 wbsvrd 里做：

```go
var nameRe = regexp.MustCompile(`^[a-z0-9][a-z0-9._-]*$`)

func docPath(name string) (string, error) {
    if !nameRe.MatchString(name) || strings.Contains(name, "..") {
        return "", fmt.Errorf("bad name")
    }
    return filepath.Join(storeRoot, "docs", name), nil  // 路径由服务端拼
}
```

agent 传进来的只有 name，路径由 wbsvrd 自己拼。这是防穿越唯一必要、也唯一充分的检查。

## 必须成立的前提

`wb.py doctor` 逐条查，任何一条不过就**不启用托管模式**，退回 hook + 哈希，并在 `status` 里常驻显示原因。

1. `wbsvr` 账户存在
2. `sudo -n -u wbsvr wbsvrd ping` 成功
3. **`sudo -n -u wbsvr /bin/sh` 必须失败** —— 核心检查，能拿 shell 则整套隔离归零
4. 存储目录 `0700` 且属主是 `wbsvr`
5. **agent 账户不在 admin / wheel 组**
6. `.claude/` 不归 agent 所有（或识别为 bootstrap 模式）

`init` 另有一条独立要求：**见到已存在的存储必须硬拒**，否则 `init` 就是「一键清空所有冻结」。同一仓库重开需求走用户凭证。

第 5 条是最容易被忽略的一条，见下节。

## 已知边界

**agent 账户在 admin 组 = 整套归零。** sudo 密码缓存（默认 15 分钟）缓存的是**认证**不是授权，正常情况下 agent 跑 `sudo -u wbsvr wbsvrd unlock` 会因为不匹配任何 sudoers 规则被拒，跟缓存无关。但如果 agent 账户本身命中 `%admin ALL=(ALL) ALL`，用户在同一终端会话刚输过密码后的 15 分钟内，agent 可以直接 `sudo -u wbsvr /bin/sh`。`Defaults:work timestamp_timeout=0` 加上「agent 账户不进 admin 组」，两条都要。**在开发机上这个洞通常真实存在**，别当它不在。

**wbsvrd 无法验证它没执行的东西。** 门禁命令（`npm test`）必须在 agent 侧跑（D8），wbsvrd 只能信 wb.py 报上来的结论。这个洞的正解是保护 `.claude/wb.py` 本身不被改，不是把判定挪进服务端。

**Bash 正则拦截不能删。** 托管只覆盖四类拦截里的一类（锁定文档）。其余冻结文件、角色越权写、灾难命令、写出项目根这几类仍然只有正则这一道（D12）。

**repo 内契约只有哈希这一档保护。** 见「什么托管」表最后一行。

**并行归属仍按「角色 + 任务 started 时间」认领**，同一角色两个任务并行时分不开。托管不改变这一条。

**审计日志只覆盖 wbsvrd 自己处理的操作。** agent 侧发生的事它记不全 —— 刻意如此，半个日志比没有更坏。

## 关键决策点

每一条都是这次设计里真正掰扯过的，记的是理由不是结论。

**D1 — 不用 `chflags uchg` / `chattr +i`。** `chflags` 是 BSD/macOS 专有，Linux 不可用；Linux 的 `chattr +i` 需要 root。工作台要跨平台，只能用纯 POSIX 手段。实测 `chflags uchg` 确实能挡住 `sed -i` / `cp` / `mv` / `>` / `rm` / `git checkout`（报 `unable to unlink old`）/ `git reset --hard`，且不需要 root —— 好用，但平台锁死。

**D2 — 用不可寻址存储，不用目录属主。** 目录属主（`0555` + 文件 `0444`）是纯 POSIX 且实测有效的。但它只解决「不可写」，仍要逐个防符号链接、tmp + mv、`git checkout`、权限位配错。不可寻址一次性消掉整类。另外 POSIX 有条硬约束：**删除/改名权限来自父目录而非文件本身**，所以「可写草稿」和「只读锁定文件」不可能共存于一个目录 —— 这也逼着走存储分离。

**D3 — 没有 token。** 早期方案用 bearer token 区分「用户」与「agent」。但在「用户给自己解锁」这个模型里 token 是纯冗余：权限本身就是凭证，而且更强 —— token 是可复制的字符串，会漏进 transcript、会写进文件、要管过期；sudo 密码这三样都不会。早期文档自己也承认 token 会泄漏进 agent 上下文却没给解法，那就是信号。

**D4 — 不常驻，不用 socket。** 常驻 daemon 要 systemd unit / launchd plist、要管重启、要管 socket 权限、要定义服务不可用时的降级语义。而调用频率实际是「每次 subagent 派发读几次，写十几次/天」—— sudo 一次性调用完全够。少一个常驻进程 = 少一个可被 kill、可被替换、可能挂掉的东西。

**D5 — 没有 `wbctl`。** 早期方案是 `agent → wb.py → wbctl → sudo wbsvrd` 四层。`wbctl` 以 agent 身份跑、只做转发，零安全收益，而 `subprocess.run` 一行就能替代它。更重要的是 agent 已经只认 `wb.py`（硬规则第 1 条）—— **`wb.py` 本身就是那个「收窄的接口」**，不需要第二个。少一个二进制 = 少一份跨平台分发。

**D6 — wbsvrd 用 Go，wb.py 留 Python。** wbsvrd 是 sudo 目标，这一条决定选型：Python 解释器启动要解析 `PYTHONPATH` / `PYTHONSTARTUP` / `sitecustomize.py` / `site-packages`。sudo 的 `env_reset` 默认清掉这些，但那是「sudoers 配对了才安全」；Go 静态二进制**没有这个类别**，是「不需要配置就安全」。特权组件选后者。附带好处：单文件 sha256 可固定，被替换即可检测（Python 是「脚本 + 解释器」两个可变部分）。wb.py 不动 —— 它不特权，纯 stdlib，零构建就能拷走。代价是引入 Go 工具链和 4 个平台组合的分发，但这个代价只有启用托管的人付。

**D7 — 内容走 stdin/stdout，不走路径参数。** `wbsvrd checkout <name> <dest>` 要求 wbsvr 能写 agent 侧的工作副本目录，得开 others 写位或搞组共享 —— 又一处要配对才安全的权限。改成正文经 stdin/stdout 传递后，**wbsvrd 的文件操作全部关在自己的存储目录里，一次都不写 agent 侧路径**。顺带绕开大文档塞 argv 的长度限制。

**D8 — `gate_commands` 内容托管，执行不托管。** 这是被硬约束逼出来的，不是选的。实测：`os.setuid(nobody)` 从 euid 501 调用报 `PermissionError: Operation not permitted` —— **非 root 进程不能切换到其他用户**。所以 wbsvrd 无法「以 wbsvr 身份执行门禁命令再降回 agent 身份」。命令内容必须管（防篡改），执行必须留在 agent 侧（技术上没有别的选择）。同一实测还证明：**setuid 位在脚本上无效**，`-rwsr-xr-x s.sh` 仍以调用者身份运行 —— 所以只能走 sudo。

**D9 — `tasks` 拆成结构与状态两半。** 起初判断「`tasks[]` 托管无增量安全收益」，那对**状态**字段成立（`task done` 本来就是 agent 的合法调用），对**结构**字段不成立：`deps` 与 `role` 定义了执行顺序，改 `deps` 就绕过顺序，而顺序保证是本项目核心目标之一。拆开后：结构在 design 阶段定稿即冻结，进度照常写 —— 顺序由结构决定，进度由执行决定。

**D10 — 产物日志不托管。** 它是全系统最高频的写入（`hook_post_tool` 里并行 develop 下每个 subagent 的每次文件写入都触发），同时是最低风险的（追加日志，改它得不到任何流程收益）。托管它等于给每次文件写入加一次 sudo 往返。

**D11 — 状态文件不整体托管。** 曾提议把整个状态文件交给 wbsvr，被自己推翻：`save_state()` 有 **14 个调用点**，包括 session-start hook。整体托管会把攻击面从「两次移动操作」扩大到「所有状态写入」，且每次写都要一次 sudo。字段级拆分才是对的粒度。

**D12 — Bash 正则拦截保留。** 托管解决的是四类拦截里的一类。剩下三类（其余冻结文件、角色越权、灾难命令）和「写出项目根」仍然只有正则。**别把「Bash 没被拦」当成「这个写入是允许的」** —— 从任意 shell 命令里可靠抽出全部写入目标做不到。

**D13 — 解锁窗口超时必须在服务端。** 现方案靠 `SubagentStop` hook 关解锁窗口 —— 那是 agent 侧钩子，不触发就永久开着，冻结**静默失效**。静默降级是最坏的失败模式：「以为冻着其实没冻」比「知道没冻」更危险。托管后 `unlock_until` 由 wbsvrd 在每次操作前检查，agent 无法延长：

```go
func expireUnlocks(refs map[string]*Ref, now int64) {
    for _, r := range refs {
        if r.UnlockUntil != 0 && now > r.UnlockUntil {
            r.Locked, r.UnlockUntil = true, 0
        }
    }
}
```

**D14 — 审计日志值得做。** 一度按 YAGNI 砍掉，判断错了：核心目标是「契约不被非预期修改」，而**日志本身能被改，「非预期修改」就检测不到** —— 现在流程日志在状态文件里，agent 可写。append-only 审计还顺带覆盖「改了什么」（记 `old_sha` / `new_sha` / `reason`），不用另做版本历史。

**D15 — 冻结清单快照是性能优化，不是权威。** 托管后 hook 判断冻结要调 sudo，develop 阶段几百次 fork。wbsvrd 在每次 lock/unlock 后刷一份快照文件（`wbsvr:work 0444`）供 hook 零开销读。它只在 wbsvrd 改状态时同步刷新不会滞后；wbsvrd 崩在中间则快照偏旧 = 偏严 = **安全侧失败**。

## 不做的事

| 想法 | 为什么不做 |
| --- | --- |
| 门禁判定进 wbsvrd | 结构上做不到，见 D8 |
| 常驻进程定期校验 | 要 systemd/launchd，另一个量级；`verify` 在门禁时调就够 |
| 加密存储 | agent 已经读不到（`0700`），能读的人也能读密钥，纯仪式 |
| 全量版本历史 | git 已经做了；托管文档记 sha 链就够 |
| 远程 / 多机 | 超出范围 |
| `wbsvrd status` | `wb.py status` 已经是那个看板 |

## 实施顺序

**阶段 0（与 wbsvr 无关，现在就该做）**
- `.claude/settings.json` 的 `PostToolUse` 加 `Bash` matcher（现在只有 `Write|Edit|NotebookEdit|MultiEdit`）
- `BASH_WRITE` 补 `cp` / `mv` —— 当前 `cp` 覆盖状态文件直接通过，且状态文件**没有任何哈希兜底**

**阶段 1（只读骨架，验证核心假设）**
- wbsvrd + sudoers + 存储目录 + `doctor`
- 只实现 `ping` / `init` / `list` / `read` / `verify` / `selfcheck`
- 目标是先证明「agent 拿不到 shell、拿不到存储」这条前提在目标环境里真的成立

**阶段 2（写路径）**
- `checkout` / `commit` / `lock` / `unlock` + 超时重锁 + 审计日志
- `wb.py` 的 `contract` 子命令转发
- 冻结清单快照 + hook 改读快照

**阶段 3（sealed 字段迁移）**
- `phase` / `role_scopes` / `gate_commands` / `gate_timeout` / `tasks_graph` 搬进 sealed.json
- `load_state` / `save_state` 按字段分流
- `config set` 按 key 分流

**bootstrap 例外**：开发工作台自身时 `.claude/` 保持 agent 可写，不启用托管 —— 否则改不动 `wb.py`。`doctor` 识别这种情况并明说。
