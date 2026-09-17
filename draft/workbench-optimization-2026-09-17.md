# 全仓分析与优化点梳理（2026-09-17）

> 时点快照：对工作台本体（`repos.json` 为空的多仓库空壳，`.claude/hooks/` 约 8700 行 Python）做的一次优化面分析。本文是**分析结论**，不是待办清单里的每一项都要做 —— 已按「改动成本 × 收益」排序，P0/P1 值得做，P2 是机械整理，P3 是顺手清理。
>
> 方法：四路并行审计（守卫 / 状态内核 / 自检+脚本 / 文档与 skills），所有落在本文的高置信结论都经主线程**逐条实测复核**（探针脚本直接打 `wb.py hook pre-tool` / 跑 `selfcheck` / 跑 `generate_agents --check`），未复现或撤回的未计入。selfcheck 全绿（4.1s，464 断言）。

---

## 总览

| 层级 | 主题 | 一句话 |
| --- | --- | --- |
| P0 | 门禁零用例正则误判 | 测试数末尾是 0 的绿灯被判成 unverified（≈FAIL），挡正常工作 |
| P0 | `init` 先移指针后报错 | 一次失败的 init 静默改道全工作区 CLI 指针 |
| P0 | README 把在线防线写成「不生效」 | 读文档的人会去绕本该依赖的机制 |
| P1 | 状态完整性只靠 Bash 文本 denylist | 实测多条等价 shell 写法放行，文档宣称的 `st_nlink` 防线不存在 |
| P1 | 三处自动化缺口 | `knowledge_check.py` / `generate_agents --check` 未挂进 selfcheck、pre-commit 覆盖不全 |
| P1 | `cmd_selfcheck` 2165 行单函数 | 464 断言装一个函数，失败定位靠打印顺序 |
| P2 | 文档漂移一批 | FROZEN_ALWAYS 计数、wb-init 自相矛盾、布局旧路径、死锚点 |
| P3 | 清理 | `payload.json`、死函数、`role.md` stub、REPO_HINTS 通用词、draft 无索引 |

**别白花力气**：hook 延迟不是问题（实测 20 文件 apply_patch 仅 53ms，远低于 15s 超时）。

---

## P0：改动 1-2 行，立刻见效

### P0-1 门禁零用例正则把通过判成未验证（`wb_core.py:1193`）

```python
r"0\s+(tests?|passed|specs?)|No\s+tests?\s+ran|..."
```

正则 `0\s+passed` 无左边界锚定，会命中任何「数字末尾是 0」的计数。实测：

| 输出（真实工具格式） | 判定 |
| --- | --- |
| `Tests: 10 passed, 10 total`（Jest 默认输出） | unverified |
| `Tests  10 passed (10)`（Vitest） | unverified |
| `10 passed in 0.52s`（pytest） | unverified |
| `30 specs, 0 failures`（Mocha-ish） | unverified |
| `100 tests passed` | unverified |

- `unverified` 在门禁汇总里等同 FAIL，拒绝信息却写「零用例执行」，把人往错方向带。
- 后果：任何测试数量末尾是 0 的项目，develop / verify 门禁永远推不动。这**正是工作台存在的理由要避免的事**（门禁挡正常工作）。
- 修法：加左边界 `(?<![\d.])0\s+(?:tests?|passed|specs?)`。加 selfcheck 断言（目标禁用例 + 合法 10 用例各一个样例）。

### P0-2 `init` 失败会静默改道全工作区指针（`wb_cli.py:52-53`）

```python
set_current_flow(root, flow)                          # 先移指针
if state_path(root, flow).is_file() and not args.force:
    die(f"flow {flow} 已存在 state.json，如需重建请加 --force")
```

- 指针在报错**之前**已经切过去，且 `fd.mkdir` 已建好目录。
- 一次「忘了加 `--force`」的 init，会让其他会话的 `status` / `task` / `contract` 全部落到这条线上，且没有任何输出提示指针动过。`current-flow` 是全工作区共享的一份文件。
- 修法：把 `is_file()` 检查挪到 `set_current_flow` 之前（`die` 在改指针前）。加 selfcheck 断言（init 已存在 flow 不 `--force` → 指针不变）。

### P0-3 README 把两道在线防线写成「不生效」（`README.md:153`）

原文：`6. 未审核的 skill 调用与非主线程工具管控 —— **当前不生效（待恢复）**，见 AGENTS.md「权限守卫」`。

实测两层都在线（`wb_guard.py`）：

```
CronCreate / Workflow / SendMessage / Artifact → BLOCKED（subagent 载荷）
skill: graphify / caveman / skill-recommender  → BLOCKED（非白名单）
skill: wb-flow                                 → ALLOWED（已配置白名单，正确）
主线程调 graphify                              → ALLOWED（正确）
```

- 这是文档里最坏的一条：读的人以为该防线关闭，会去绕本该依赖的机制。
- 修法：删这句话，改成与 `AGENTS.md:191-192`、`docs/permissions.md:132` 一致的「已于 `29f9255` 恢复并有 selfcheck 断言」。

---

## P1：结构性

### P1-1 状态的完整性只靠 Bash 文本 denylist，而它是漏的

**现状**：`state.json` 无哈希/签名/链式审计，`state_rev` 只是 CAS 计数器，`audit.jsonl` 是普通 append。整套「门禁与进度不可绕过」实际等于「Bash 守卫拦得住」。

实测（主线程探针，全部**放行**，都是可直接改写/删除冻结状态的等价写法）：

| 命令 | 结果 |
| --- | --- |
| `rm -rf .workbench` / `rm -rf .workbench/flows` | 放行 |
| `mv .workbench/flows /tmp/x` | 放行 |
| `cp /tmp/evil.json .workbench/contracts/user-api.json` | 放行 |
| `unlink .workbench/state.json` | 放行 |
| `find .workbench -name '*.json' -delete` | 放行 |
| `perl -i -pe 's/a/b/' .workbench/state.json` | 放行 |
| `sed -Eni 's/a/b/' .workbench/state.json` | 放行 |
| `F=state.json; echo x > .workbench/$F` | 放行 |
| `python3 <<'EOF' … open('.workbench/state.json','w')` | 放行 |
| `cp -l .workbench/flows/main/state.json innocent.md` | 放行 |
| `ln`（同一个硬链动作的另一写法） | **拦住** |
| `python3 -c "os.link(...)"` | **拦住** |

**结论**：拦住的是诚实写法，放行的是等价写法。给 Bash 分支加模式是打地鼠 —— 每加一条，下一个等价 shell 拼法照样过。

**真正的出路**：让状态**篡改可见**，不靠猜测写目标。两条路：

1. `state.json` + `audit.jsonl` 做哈希链（写时给文件带上前一份的摘要，读取/门禁时校验）。成本小，能拦住「无痕改写历史」。契约已有哈希兜底（`contracts_intact`），state 层没有 —— 补的是同一件事在状态层的空位。
2. 若不想加这条链，就把 `AGENTS.md` / `docs/permissions.md` 里「状态只能经 wb.py 改」「不可绕过」的措辞改成诚实的 best-effort，并把威胁模型写清楚（管的是不信任的 subagent，不是对抗性人类）。

**同一处文档与代码不符**：`AGENTS.md:218`「硬链不用解析侧解，用 inode 判……目标已存在且是普通文件且 `st_nlink > 1` 即拒」。实测 `st_nlink` 在整个 `.claude/hooks/` 一次都没出现（唯一 `unlink` 是删文件调用），建好 nlink=2 的别名后 Edit 被放行。**这条防线要么补上，要么从文档删掉。**

### P1-2 三处自动化缺口（各补 1-2 行）

- **`knowledge_check.py` 只被口头要求**（`knowledge/README.md:52`「升级后运行」）。它校验 knowledge 与 references 之间的双向路由链接 —— 正是会静默烂掉的东西。不上 selfcheck，不上 pre-commit。
- **`generate_agents.py --check` 形同虚设**：AGENTS.md:207 要求「改角色先改 TOML，再运行生成脚本和 `--check`」。**实测：给 `agents/qa.toml` 追加内容后，`wb.py selfcheck` 全绿，`generate_agents.py --check` 也报「角色文件已同步」**，exit 0 —— 两端都发现不了 `.md` 漂移（`wb_selfcheck_static.py` 只验 toml 语法合法，不比对 md）。
- **`pre-commit` 覆盖窄**：匹配是 `^(\.claude/hooks/|\.claude/agents/|\.claude/skills/|\.claude/settings\.json|\.agents/|\.codex/|agents/)`。`scripts/`（约 1000 行真逻辑）、`docs/`、`references/`、`knowledge/`、`AGENTS.md` 全在覆盖外 —— 改这些不会触发 selfcheck。

### P1-3 `cmd_selfcheck` 是 2211 行文件里的一个 2165 行函数

- 文件里只有 1 个模块级 `def`（`cmd_selfcheck:47(2165)`），装着全部 464 条 assert，无分节函数、无分节标记，失败定位靠打印顺序。
- 性质：这是全仓唯一的回归网，是拆分与升级的防线，值这个拆 —— 拆成一组带名字的检查函数，将来单测单跑、失败可点名。
- 现状可接受（全绿、4.1s），但随断言增多会先熵增到不可维护。

---

## P2：文档漂移一批（机械修，但会误导人）

- **`docs/permissions.md` 三处**（`:36` / `:69` / `:174`）写 `FROZEN_ALWAYS` 是六项；实际 **7 项** —— flow 归属闸门加进了 `sessions`（`wb_const.py:290-291`）。同文件 `:151` 又说「已并入 `FROZEN_ALWAYS`」—— **文档自相矛盾**。
- **`docs/wb-init.md` 两处自相矛盾**：
  - `:15`「明确不做：仓库知识沉淀（overview/setup/test 三件套）」vs `:48-49` 决策表就是画像三件套已落地（`wb_const.py:139 REPO_PROFILE_FILES`、`GATES.analyze` 含 `repos_notes_exist`）。
  - `:37`「落点 `repos/<name>`，不引入 `.source/` 第二层」vs `:86` 演进记录「已改 `repos/.source/<项目>/<仓库>`」。代码 `wb_core.py:1318/1346/1413` 证实是后者。
- **`docs/architecture.md` 仍是旧 `repos/<仓库>/` 布局**（`:177-192` 示例 `repos/backend/**`、`repos/frontend/**`、探针 `repos/<name>/src/probe`），与 `AGENTS.md:92-94` 的 `repos/.source/<项目>/**` 形状互斥。代码探针实为 `repos/.source/{name}/*/src/probe{ext}`（`wb_core.py:1467`）。
- **`docs/wb-init.md:51` 清单示例没有 `project` 字段**；`repos_apply.py:114-118` 要求它（或由 remote 推导，推不出就拒）。
- **3 处死锚点**：
  - `AGENTS.md:219`（CLAUDE.md 同）→ `docs/permissions.md#bash-绕过检查`，实际标题是 `Bash 分支：绕过检查`。
  - `docs/permissions.md:126` / `docs/roles.md:160` → `architecture.md#跨仓库同一个语义的反面`，该标题不存在。
  - `docs/permissions.md:112` → `gates.md#retro-经验已沉淀knowledge_written`，该标题不存在。
- **代码有、文档没写**：
  - `wb_selfcheck_static.py` 不在 `README.md:12-17` 模块表与 `architecture.md:34-41` 模块表。
  - Codex 端第 5 个 hook（`UserPromptSubmit` → `user-prompt`，`.codex/hooks.json:40-51`）在所有文档里都是「4 个 hook」。
  - `.workbench/task-agents.jsonl` 与 `sessions/` 不在 `README.md` / `architecture.md` 的状态表。
- **`draft/` 索引不全**：`docs/README.md:27-38` 只列 12 份，目录里 16 份，漏 `commit-bc9446b-review-2026-09-16.md`、`cross-flow-review-2026-09-14.md`、`guard-security-audit-2026-09-11.md`、`subagent-flow-assessment.md`。

---

## P3：清理

- **`payload.json`**（仓库根，199 字节，`git check-ignore` 判 NOT IGNORED，`git status` 是 `??`）—— 游离测试文件，下次 `git add -A` 就进版本。删。
- **`wb_core.py:774` `validate_task_contracts`** 全仓无引用，死函数。
- **`references/workspace/<role>/role.md`** 8 份、每份 1-3 行（共 26 行），内容是 `agents/<role>.toml` 的 `developer_instructions` 已写过的写入范围与禁忌，重复。连同 8 份 `index.md`（57 行）共 16 个文件 83 行，换来每次改角色要同步两处。
- **`wb_const.py:220` `REPO_HINTS` backend 元组含通用词 `"skills"`** —— 同一文件 `:244-250` 的注释正在警告通用词危害，`AGENTS.md:86` 写「只用实名、不留通用词」。后果是实测的：`client` 会命中 `mapclient`，让同一项目被前后端双认领，角色隔离失效。
- **`draft/` 16 份 240KB 全进 git、无索引** —— 定位正确（不是垃圾，`docs/README.md` 要索引它），但值得一份 `draft/README.md` 或按日期归档，先把漏掉的 4 份补进 `docs/README.md` 索引。

---

## 别白花力气

- **hook 延迟不是问题**，不要优化。实测：Bash 普通 60ms、Bash 复杂 60ms、Read 53ms、**20 文件 apply_patch 53ms** —— 完全平坦，远低于 15s 超时。成本在解释器启动 + import（23ms），不在守卫逻辑。想降就砍 import，但没必要。
- **核心机制扎实**：契约哈希与漂移校验、解锁窗口、争议熔断、flow 隔离（含跨 flow 窗口与归属）、任务租约与自依赖、嵌套根反查、角色范围按项目前缀重算 —— selfcheck 全覆盖且全绿。P0 的 2、3 与 P2 的文档漂移，恰恰是**因为**机制太细，文档与代码同步跟不上，不是机制本身坏了。
- **守卫拦住的大部分是对的**：冻结文件、角色越权写核心路径、敏感读取、灾难命令、特权子命令、非主线程工具、skill 白名单，实测都有效。

---

## 建议落地顺序

1. **P0-1 门禁正则加锚点 + 补断言**（1 行，救绿灯）。
2. **P0-2 init 顺序对调 + 补断言**（2 行）。
3. **P0-3 README 措辞改回来**（1 句）。
4. **P1-2 把 `knowledge_check.py` / `generate_agents --check --check` 挂进 selfcheck，pre-commit 覆盖补 `scripts/` `docs/` `references/` `knowledge/` `AGENTS.md`**（通用化触发条件，不是硬编码文件列表）。
5. **P2 文档漂移按批清理**（机械）。
6. **P1-1 状态哈希链** —— 这是设计决策，先定威胁模型（管不可信 subagent vs 对抗性人类）再决定加链还是改文档措辞。