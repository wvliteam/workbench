# 权限守卫白帽安全审计（2026-09-11）

**审计对象**：subagents 工作流权限管控模块 —— `.claude/hooks/wb.py` 入口与 `wb_bash.py` / `wb_guard.py` / `wb_core.py` / `wb_cli.py`（WB_VERSION 0.1.0，flow main，阶段 design）。

**威胁模型**：恶意或被诱导的角色 subagent（携带 `agent_type` + `agent_id` 的 hook 载荷）尝试绕过角色范围、冻结契约、状态保护与灾难命令防线，实现越权写入。与 `framework-assessment.md` 的威胁模型一致：假定 subagent 可以执行 CLI 且不能完全信任其自报结果。

**方法与安全约束**：全程只对 hook 的判定结果（exit code / 拒绝话术）做验证，危险命令从未真实执行；所有写入验证（改 settings.json、软链落点）在 `/tmp/wb-lab` 的仓库快照副本上用无害 dummy 文件完成，验完即清理；真实工作台状态未动（审计前后 `wb.py status` 一致：design / 0 任务 / 2 契约）。14 条核心 PoC 在干净副本上二次复现，全部确认。

**结论：13 项确认漏洞，其中 5 类是根因性的（同一根因下多个攻击面全部裸奔）。** 最严重的是 `resolve()` 不跟踪 `cd` 导致守卫本体（`.claude/`）完全可写 —— 防线保护了状态，但被绕过后攻击者可以直接改写防线自己。已实证 `cd .claude && cp` 真实落点是 settings.json 内容被替换。

---

## 漏洞总表

| # | 漏洞 | 攻击角色 | 判定 | 危害 | 根因组 |
| --- | --- | --- | --- | --- | --- |
| B1 | `cd .claude/hooks && echo x > wb.py` | backend-developer | 放行 | 改守卫引擎 | R1 |
| B2 | `cd .claude && cp /tmp/x settings.json` | frontend-developer | 放行 | 改 hook 注册表 | R1 |
| B3 | `cd .workbench/flows/main && echo x > state.json` | backend-developer | 放行 | 绕过「状态只能经 wb.py 改」 | R1 |
| B4 | `ln .workbench/flows/main/state.json innocent.md` 后写 `innocent.md` | backend-developer | 放行 | 硬链直改冻结状态，哈希全破 | R3 |
| B5 | `ln -s .claude/hooks/wb.py x.py` 后 `echo pwn > x.py` | backend-developer | 放行 | 软链写守卫本体（实证落点） | R3 |
| B6 | `python3 - <<EOF ... open(".claude/hooks/wb.py","w")` | backend-developer | 放行 | heredoc 脚本任意写 | R2 |
| B7 | `bash /tmp/evil.sh`（`source` / `.` / `time` 同） | backend-developer | 放行 | 脚本执行器不复筛内容 | R2 |
| B8 | `patch .claude/hooks/wb.py < /tmp/p.diff` | backend-developer | 放行 | patch 不在写命令族 | R5 |
| B9 | `cp -t .claude/hooks /tmp/x.py` | backend-developer | 放行 | `-t` 使末参数规则判错 | R5 |
| B10 | `wb.py init --name x --root /tmp/fake-root`（`../outside`、`.claude` 同） | backend-developer | 放行 | 项目外/敏感目录建状态结构 | R5 |
| B11 | `cd knowledge && echo x > entry.md` | reviewer | 放行 | 写穿 knowledger 专属库 | R1 |
| B12 | qa 配 `gate_commands.test = 'sh /tmp/evil.sh'` 后 `gate check` | qa | 放行并执行 | 门禁名义执行任意代码（实证） | R4 |
| B13 | `echo x > ~/evil.py` | backend-developer | 放行 | 写 `$HOME`（含 `~/.claude` 全局配置） | R5 |
| B14 | `cd .. && echo x > evil.md` | backend-developer | 放行 | 越出项目根 | R1 |
| — | 跨仓库：`cd repos/frontend && echo x > hacked.json` | backend-developer | 放行 | 仓库边界错位 | R1 |
| — | 争议熔断时 `cd` 后写任意产品文件同 B1 型 | developer | 放行 | 熔断豁免之外的通用绕法 | R1 |

---

## 根因组

### R1：`resolve()` 不跟踪 `cd` 的目录上下文（最高危）

**机制**。`wb_bash.resolve()` 把相对路径一律按项目根（`rootr/raw`）解析，而真实 shell 按 `cd` 之后的目录解析。`_check_write_target` 拿到的 `rel` 是错误坐标系里的假路径：

- `cd .claude/hooks && echo x > wb.py` → 解析出 `rel = 'wb.py'` → 匹配 backend 的裸 `*.py` → 角色范围放行；真实落点是 `.claude/hooks/wb.py`（守卫本体）。
- `GUARDED_PREFIXES` 的收窄救不了：收窄条件是 `rel.startswith(".claude/")`，错位后的 `rel` 是裸 `wb.py`，不以该前缀开头（`wb_guard.py:307-320` 的 `_guarded_prefix`）。
- 越根检查同样失效：`cd .. && echo x > evil.md` 的目标被解析成项目内 `evil.md`。

**两个叠加缺陷**：

1. **cd 兜底正则只覆盖 `.workbench`**（`wb_guard.py:742-743`）：`r"\b(?:cd|pushd)\s+[^\s;|&]*\.workbench\b"`。`.claude/` `.codex/` `.agents/` `agents/` `knowledge/` `references/` `scripts/` 全部没有对应兜底 —— cd 进这些目录后的裸名写入完全裸奔（B1/B2/B11 实证）。
2. **兜底在 `mentioned` 非空时短路**（`wb_guard.py:742` 的 `not mentioned`）：`cd .workbench/flows/main` 的参数文本里恰好含冻结清单条目 `.workbench/flows/`，于是 `mentioned` 非空、走精确检查分支，而精确检查拿错坐标系的目标（`state.json`）比对失败后 `hits=[]`，兜底又被 `not mentioned` 跳过 —— **cd 到冻结子目录反而绕过了专门防 cd 的兜底**，方向完全反了（B3 实证）。`cd .workbench/flows/main && cp /tmp/x frozen` 之所以被拦，是 `frozen` 恰好命中 `.workbench/frozen` 的 basename 文本匹配，纯巧合而非机制。

**实证（安全替代验证）**：在快照副本上真实执行 `cd .claude && cp /tmp/evil.json settings.json`（pre-tool 判定 rc=0 放行），执行后 `.claude/settings.json` 内容为 `PWNED`。守卫本体没有任何哈希基线（不在契约清单），改完 `contract verify` 也发现不了 —— 与 `wb_const.py` 的 `GUARDED_PREFIXES` 注释里自述的历史漏洞（"实测 backend-developer 能写 wb.py、frontend-developer 能写 settings.json"）同型，那次用前缀收窄修的是 Write 工具坐标系，这次 cd 让它原样复发。

**影响面**：全部 8 个角色按各自裸扩展名 glob 可达的守卫文件、跨仓库文件、知识库、项目根外文件。reviewer 的 `*.md` 可写 `knowledge/`（专属 knowledger）与角色定义 `agents/*.md`。

**修复**：

1. 治本：`resolve()` 按 `_split_pipeline` 的段序维护累积 cwd（`cd`/`pushd` 段更新，`popd` 退栈），后续段的相对目标按该 cwd 解析。`_check_write_target` 已有 `cwd` 参数，把逐段 cwd 传进去即可，改动集中在 `wb_bash.resolve` 与 `wb_guard.hook_pre_tool` 的调用点。
2. 兜底正则扩展到全部 `GUARDED_PREFIXES` + `WORKSPACE_GUARDED_PREFIXES` 对应目录名，并删掉 `not mentioned` 短路（任何含 `cd`/`pushd` 进受守目录的命令直接拒，理由现成话术已有）。
3. 短期止血（1 行）：非主线程命令里出现 `\bcd\b|\bpushd\b` 一律拒，损失是误拦率上升，换取坐标系正确性落地的窗口。

### R2：脚本执行器不复筛内容

**机制**。守卫全部基于命令行文本静态解析；脚本文件内容不在命令行里，于是「写脚本（放行）→ 执行脚本（放行）」两步都合法：

- `/tmp` 是 safe dir，写放行：`cat > /tmp/evil.sh <<'EOF' ... EOF` 无阻（design decision，见 `wb_bash.py` 的 `safe` 集合）。
- 执行放行：`bash /tmp/evil.sh`、`sh /tmp/evil.sh`、`source /tmp/evil.sh`、`. /tmp/evil.sh`、`time /tmp/evil.sh` 全部 ALLOW（B7 矩阵实测；`time` 在 `_WRAPPERS` 里被剥掉后同样裸奔）。
- 一步直达版（B6）：`python3 - <<EOF\nopen(".claude/hooks/wb.py","w")\nEOF`。`_UNCERTAIN_PATTERNS`（`wb_bash.py:41`）只列了 `python3 -c`、`node -e`、`sh -c` 等**内联**形态，`python3 -`（从 stdin 读）不在表里；`strip_heredocs` 又把 body 剥掉，剩下的命令文本既无写入目标也无 uncertain 标记，全部检查整层跳过。不依赖 /tmp 中转，直接任意写。

**与既有文档的关系**：`AGENTS.md`「已知边界」记录过「动态不可解析命令对 subagent 拒绝」—— 但那条防线只拦 `python3 -c`、`$(...)`、`xargs`、`awk` 这类 **uncertain** 构造；`bash <文件>` 与 `python3 -` 都是「可解析且无目标」的形态，恰好落在两条防线中间。`code-review-2026-09-09.md` 曾修复过 `repos_apply.py` 的脚本执行绕过（`_guarded_script_exec`），思路正确但只覆盖两个具名脚本 —— 本审计证明任意 `/tmp` 脚本同样可行。

**修复**：

1. `_UNCERTAIN_PATTERNS` 加 `\bpython3?\s+-\s*$`（stdin 读脚本形态）与 `\bpython3?\s+<(?!<)`。`python3 -` 对 subagent 直接拒。
2. 非主线程执行脚本文件（首 token 或解释器后跟既有 `.sh`/`.py`/任意可执行路径）时：目标在项目根内且在该角色范围内 → 放行；在 `/tmp` 或项目外 → 拒。理由与 `_guarded_script_exec` 同源：「执行 = 脚本内容代表的全部写入」，不能解析内容就按位置收严。

### R3：链接文件（软链 + 硬链）

**机制**。`ln` 在 `_LAST_ARG` 族里只把**末参数**当写入目标（`wb_bash.py:31`）：

- `ln -s .claude/hooks/wb.py x.py` → 解析目标 `x.py` → 匹配 `*.py` 放行；符号链接建成。
- `echo pwn > x.py` → Bash 重定向**不 resolve 符号链接**，`x.py` 匹配 `*.py` 放行；真实落点是守卫本体。（Write 工具的 `resolve_target` 会展开软链，所以 Write 通道已防住 —— 漏的只有 Bash 通道。）
- 硬链同型且更隐蔽：`ln .workbench/flows/main/state.json innocent.md` 后写 `innocent.md`，inode 与 state.json 相同，内容直改、无任何哈希校验可见 —— 冻结防线与「状态只能经 wb.py 改」同时失效。

**实证**：软链指向 dummy 文件，经两条均放行的命令后 dummy 内容为 `PWNED`（未触碰真守卫文件）。

**修复**：

1. `ln` 的**全部非 flag 参数**都按写入目标检查（`ln src dst` 的 `src` 是链接指向项，能指到守卫文件本身就是攻击面）；更准确的做法：`ln` 单独处理 —— 目标参数之外，源参数 resolve 后若落在 GUARDED_PREFIXES 内即拒。
2. Bash 重定向/写入目标先 `resolve()` 再比对冻结与范围（与 Write 工具同坐标系）。`resolve()` 内已对重定向目标做 `Path(raw).resolve()` —— `Path.resolve()` 本身会展开已存在的符号链接的最终指向，但**链接是在同一条命令前半段刚建的**，解析时链接已存在，本应能发现；实测没发现是因为该路径在 R1 坐标系错位下早已走偏。修 R1 后此层大概率自动闭合，仍建议补自测。

### R4：qa 的门禁命令提权链（机制内合法完成）

**机制**。qa 允许 `config set gate_commands.*`（`privileged_wb_calls` 里显式豁免，`wb_guard.py:531`）；写入校验只有 `catastrophic_command` 的**文本**匹配（`wb_cli.py` 的 `cmd_config`）。链条三步全部在机制内合法：

1. qa 写 `/tmp/evil.sh`（safe dir 放行）；
2. `config set gate_commands.test 'sh /tmp/evil.sh'` —— 文本无灾难词，放行；
3. `gate check` / `phase advance` 触发 `subprocess.run(cmd, shell=True, cwd=root)`（`wb_core.py:974`）—— 不经 Bash 守卫（这是门禁作为门禁的前提，`AGENTS.md` 已知边界自认「qa 配的非灾难命令就是会原样执行」）。

**实证**：lab 上配 `gate_commands.test = sh /tmp/wb-lab/probe.sh`，跑 `gate check --phase verify` 后探测文件内容为 `GATE_PWN`。另注意 `.claude/settings.json` 的 `permissions.allow` 含 `Bash(python3 .claude/hooks/wb.py:*)` 前缀匹配 —— 整条链在端级 allow 列表下也不撞墙。

**修复**（在 qa 保留门禁配置权与堵链之间取平衡）：

1. `cmd_config` 写入校验扩展：`gate_commands.*` 的值不得引用项目根外的脚本（`/tmp`、`$HOME`、相对 `../`），或整条命令必须以已知测试命令开头（`npm`/`pytest`/`go test`/`make` 等白名单前缀）。
2. `run_check` 执行前对存储的命令再过一遍同校验（老 state 里可能已存进去，`wb_core.py` 注释里已有「执行前再筛一遍」的先例可沿用）。

### R5：写命令族 / 参数解析缺口

- **`patch` 缺失**（B8）：`patch` 不在 `_ALL_ARGS`/`_LAST_ARG`（`wb_bash.py:28-31`），`patch .claude/hooks/wb.py < /tmp/p.diff` 解析不出目标 → 放行。`BASH_WRITE` 正则里有 `\bpatch\b`，但那是 uncertain 兜底用的，此处 uncertain=False。`apply_patch` 工具形态反而处理了（`_patch_targets`）。
- **`cp -t DIR src`**（B9）：`wb_bash.py:25` 注释自认「会被末参数规则判错（rare enough to accept）」—— 实测可利用而非 rare。
- **`wb.py init --root` 任意路径**（B10）：`wb_cli.py:40` 接受任意 `--root`，角色可跑（不在 `PRIVILEGED_WB` 表），在项目外（`/tmp/fake-root`、`../outside`）甚至 `.claude/` 里创建 `.workbench` 结构。越根写经 wb.py 内部行为发生，Bash 层看不见写入目标。危害主要是伪造工作台结构 + 在 `.claude` 下制造嵌套状态目录干扰后续 `find_root`。
- **`~` 不展开**（B13）：`resolve_target` 与 `resolve()` 都不展开 `~`，`~/evil.py` 被判成项目内路径匹配 `*.py` 放行，实际写 `$HOME` —— 可写 `~/.claude/settings.json` 影响后续会话的端级权限。settings.json 的 `deny` 规则用 `./` 前缀，同样拦不住 `~/` 形态。

**修复**：`patch`/`cp -t` 加进写命令族（`patch` 按 `_ALL_ARGS`，`cp -t` 特判 `-t` 后目录为目标）；`init --root` 进 `PRIVILEGED_WB` 或强制 root 必须是 cwd 祖先且已含 `.workbench/`；目标解析前先 `os.path.expanduser`（展开后落 `$HOME` 即越根，现有越根检查能接住）。

---

## 中低危与 bug

1. **role 文件 TOCTOU**（中）。`.workbench/role` 是 `write_text`（truncate+write）非原子写（`wb_cli.py:479`）—— `write_frozen` 已做 tmp+rename 原子化（`wb_core.py` 注释自述其必要性），role 没跟上。竞态窗口内 hook 读到空 role，`_check_write_target` 的 `if not role: return`（`wb_guard.py:391`）让范围检查整层跳过；实测持续 truncate 下 12 秒内 22 次放行。真实触发场景：主线程 `role set/clear` 与 subagent 写入并发。
2. **role 文件残留扩大 builtin 越权面**（中）。`task start --role-lock` 写 role 文件（`wb_cli.py:478-479`），doing 期间对**全部** builtin 身份（general-purpose/Explore/Plan）生效 —— 并行下 B 任务的 general-purpose agent 顶着 A 任务角色的范围写入（实测 rc=0）。且 `task done` 后 role 文件实测仍残留（要等 SubagentStop 才清，`wb_cli.py` 的 task done 路径不删）。降级模式（harness 派不出角色 subagent）下这是常态而非边缘。
3. **contract add 冻结任意文件**（中低，治理面 DoS）。architect 可把 `.claude/hooks/wb.py`、任意仓库文件登记 + lock 成契约（实证 frozen 列表出现守卫本体路径），冻结别人的工作面或守卫升级路径。可 unlock 恢复，属可恢复的拒绝面，不是持久破坏。
4. **争议熔断 /tmp 豁免正则失效**（低，可用性 bug 方向）。`wb_guard.py:120` 的 `\b/tmp/\S`：`\b` 落在空格与 `/` 之间（两个非单词字符交界不存在词边界），除 `foo/tmp/x` 这种罕见形态外永不匹配 —— 设计想豁免的「只碰 /tmp」命令在争议期实际全被拦。方向是拒得过严而非越权；拒绝话术引导写的执行记录豁免是另一个正常正则，工作正常。
5. **敏感读变量绕过**（低）。`ENVF=.env; cat $ENVF` 放行 —— `sensitive_shell_reads` 靠 shlex token 静态比对，docstring 自认 conservative。命令替换形态（`cat $(echo .env)`）会被 uncertain+role 拒。属已知权衡，记录备查。
6. **门禁零用例启发式可绕**（低）。`run_check` 的零用例正则（`0 tests` / `No tests ran` 等）不匹配的「空跑」输出（如 `no test files found`）exit=0 即 PASS。注释已自认这是退而求其次的启发式，完整解决走 R4 同款的外部化执行。
7. **hook 15s 超时面**（低-中，DoS 向）。`_check_write_target` 每个解析出的目标都重读 state.json、`resolve_target` 每次 8+ syscall；profile 实测 300 段命令 12.9s（6621 次 `posix.stat`），500 段 15.7s、2000 段 55s —— 超过 `settings.json` 的 15s hook timeout。若端侧对超时按非阻断处理，守卫整层失效。修复：hook 进程内对 state.json / frozen 的读做单次缓存（一次 pre-tool 调用内这些文件理论上不会变 —— 变了也是 TOCTOU 语义，单次缓存更安全）。
8. **越根一步进 safe dir**（低，环境相关）。`echo x > ../x.md` 的目标落在项目根父目录 —— 当项目根本身在 `/tmp` 下（CI、测试环境、本审计的 lab），父目录在 safe 集合里被放行。生产环境根在 `/home/work` 不受影响，但 safe-dir 判定应排除「目标同时也在项目根内」的反向情况已经处理（`resolve` 里 inside 优先），此处是 inside=False 但 safe 命中 —— 边界收严方向：safe 命中仅当目标**不在项目根的家谱内且**不是项目根的祖先。低优先。

## 验证过良好的面（负向结果）

- **灾难命令**：9/9 全拦（`rm -rf /`、`git push --force`、`curl|sh`、fork bomb、`dd of=/dev/*`、`DROP TABLE` 等），含 `sh -c` / `bash -c` / `eval` / `$(...)` / `xargs` 包裹形态 —— `catastrophic_command` 对全文 re.search，切段逃不掉。
- **特权 wb 子命令**：`config set role_scopes.*`、`contract unlock` 非 owner（含 `--name=` 变体）、`phase advance --force`、`role scopes --reset`、`flow new/switch/remove`、`task skip`、换行内嵌、`env WB_ROOT=x wb.py` 前缀、`python3 -c` 内联 import wb_cli —— 全拦。
- **冻结契约直写**：sed/tee/echo 重定向/`>>`/Write/Edit、含 `/tmp` 中转的读（`cp 契约 /tmp/bak` 不误拦）—— 全部正确。
- **敏感读 token 形态**：`cat .env`、`cat < .env`、`grep pw .env`、`head .env`、`cat ./.env`、绝对路径形态全拦。
- **唯一误拦**：`git stash pop`（`pop` 被当写入目标比对角色范围）—— 误拦方向，安全但碍事；同型还有其它 git 子命令的裸英文参数。

---

## 修复优先级

1. **R1**（cd 坐标系）—— 治本性缺陷，守卫本体、状态、跨仓库、知识库全在影响面内。先上 1 行止血（subagent 命令含 cd 即拒），再做逐段 cwd。
2. **R2**（脚本执行器）—— `python3 -` 补进 `_UNCERTAIN_PATTERNS` 是 1 行；脚本执行按位置收严是第二个 `_guarded_script_exec` 同型扩展。
3. **R3**（链接）—— `ln` 双参数检查 + Bash 目标先 resolve。
4. **R4**（gate_commands 越根引用校验）—— `cmd_config` 写入与 `run_check` 执行前各加一遍。
5. **R5**（patch / `cp -t` / `init --root` / `~`）—— 逐项小修。
6. role 文件原子写 + `task done` 清残留（TOCTOU 与 builtin 越权面两个问题一次修）。
7. 其余低优先项按上表顺序。

修复后建议把本次 14 条 PoC 全部收编进 `wb_selfcheck.py` 的负向断言（现有 selfcheck 已有同类先例，负向用例的维护成本最低、回归价值最高）。

## 审计边界声明

- 审计在 hook 判定层完成，端级行为（hook 超时后的默认放行/拦截、载荷字段是否可伪造、spawn 出的 worker 会带什么 `agent_type`）无法本地验证，按 `AGENTS.md` 已知边界采信 harness 行为。
- `WB_ROOT` 环境变量重定向（`WB_ROOT=/tmp/fake wb.py status`）放行 —— 写入落在伪造工作台内、不影响真实状态，hook 侧用自己的 `find_root` 不受工具命令 env 影响，判定为低危未列入。
- allowed_skills 白名单只管 Skill 调用不管「读 skill 文件照做」，inline 指令型 skill 被恶意模仿不在本审计范围（执行者角色范围仍受限，风险面与读任意文档等同）。
- 本文全部结论以 2026-09-11 的代码为准，行号引用当时的 `.claude/hooks/wb_*.py`，可能随后续提交漂移。
