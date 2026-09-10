# ROMA references/ 公共参考层分析与借鉴判断（2026-09-07）

**状态：已实施（同日）。** 记的是「ROMA 把多 agent 公共参考文档抽到 `agents/references/` 解了什么问题、本项目抄哪一块、付什么代价」。对比材料是 ROMA v0.3.6 快照（`output/agents.tgz` 解包后的共享 agents 根 `output/agents/`）。与 [roma-comparison.md](roma-comparison.md) 同体例：行为以快照源码为据，结论按落地判据记。实施记录：`references/output-contract.md`（信封，按本文建议落地）、wb.py `GUARDED_PREFIXES` + `references/`、16 份角色定义（`.claude/agents/*.md` 与 `.codex/agents/*.toml`）插入必读指针与双保险底线。

## 机制：它到底是怎么组织的

快照里有三棵树：`.claude/`、`.codex/` 插件树之外还有一个**共享 agents 根**（`agents/`，各端经软链桥接到它）。这个根下：

- **7 个角色定义 × 双端**（`hotel-*.md` + `hotel-*.toml`，md 共 449 行，每个 ~65 行）——只装「身份、职责边界、必读清单、交付格式骨架」的薄壳；
- **`references/` 三份公共文档**（222 行，单份物理拷贝，双端共享）：
  - `output-contract.md`（9.9KB，最重）：全部 hotel-* 角色共用的输出信封（结论≤5 条 → `文件:行号` 证据指针 → OBSERVED 事实 → 阻塞项 → 建议）+ 禁止事项（禁止给 PASS/FAIL 判定、禁止回灌完整文件、改造类调研必须穷举全部引用点、禁止遗留未收敛后台任务）+ 执行记录落盘规范（路径、命名、`$RANDOM` 后缀防并行覆盖）；
  - `repo-routing.md`：仓库检索法则（符号链接穿透只有 `grep -R`/`find -L`/`rg -L` 三种、平台 Grep/Glob 不跟随软链、检索无结果≠不存在、`--include` 收窄）；
  - `toolchain.md`：Go 工具链按仓钉版（GOTOOLCHAIN 表）、zsh shell 陷阱、取退出码只用分支写法。

消费方式：每个角色 prompt 开头一行「**必读（进入调研前读完）**：`<绝对路径>`（`Read` 只接受绝对路径）」，按角色组合点名该读哪几份（code-scout 读路由+信封，test-runner 读工具链+信封）。

**一个关键细节：它没有把最致命的规则只放在 references 里。** `hotel-code-scout.md` 内联了检索法则的完整段落，同时又指向 `repo-routing.md`——刻意的双保险：agent 跳过预读时 prompt 里仍有底线，预读了就拿到全量。抄的时候要连这个分层一起抄。

## 好处（五条，各有代码证据）

1. **N 份消费 → 1 份维护。** 7 角色 × 双端 = 14 份定义，公共纪律内联就是 14 份拷贝；现在是 1 份 references + 14 个指针。且 references 是被读的文档、不是 prompt 载体，**不参与 md↔toml 转换**——双端同步面只剩薄壳。
2. **变更频率分离。** 角色身份很少变；操作事实（工具链版本表、挂载状态「23 已挂载 / 21 仅有画像」、取退出码写法修正）常变——后者进 references，改一处全体生效，prompt 不动。`toolchain.md` 里有「（2026-09-07 修正）」日戳，证明它在被持续修。
3. **经验教训的规范化闭环。** 三份文档全是实测教训：「实测 3 个遗留后台任务吃掉 3 个完整对话轮次」「两个并行 code-scout 都写 `01-hotel-code-scout.md`，丢了一份完整调研记录」「`ec=[]` 取空退出码，同一条命令换分支写法后确认实际成功」。教训不只躺在库里等人查，而是变成每次派发前的**强制预读**——相当于把 knowledge/ 从「可检索」升级成「按角色必读」。
4. **主线程上下文经济。** output-contract 第一句：「主 Agent 的上下文是稀缺资源，你的输出直接消耗它。」信封格式把 subagent 返回压到最小，完整材料落执行记录文件、返回路径指针。
5. **跨端确定性加载。** 各端对「项目指导文件是否注入 subagent」行为不一；prompt 里的「必读 + 绝对路径」指针不依赖注入行为，哪个端都生效——对本项目正在推进的多端适配方向（AGENTS.md 已收敛为单一正文 + 软链）是同一条路。

## 本项目现状对照

| 项 | 现状 | 差距 |
| --- | --- | --- |
| 角色定义 | 8 角色 × 双端（`.claude/agents/*.md` 693 行 + `.codex/agents/*.toml` 687 行），手工同步 | 漏同步静默漂移（knowledge 有判据） |
| 公共纪律 | 在 AGENTS.md（单一正文，主线程自动注入；subagent 注入行为各端不一） | subagent 侧的加载不保证确定 |
| 输出信封 | 无共享格式。reviewer 有自己的交付清单，各角色格式不一 | 硬规则 4「复核」靠编排者阅读理解，无机械格式可对 |
| 经验教训 | `knowledge/`（3 条，检索式，带依据/适用范围/失效条件） | 是「查」不是「必读」，派发时不保证进入上下文 |
| 项目操作事实 | knowledge/ + 各项目布局自己记 | 等价于 ROMA 的 toolchain/repo-routing，但按项目沉淀 |

## 建议：抄「workspace 级共享 references + 各端薄壳指针」

不是照搬 ROMA 的三份结构，而是借它的**位置学**：公共规范放 workspace 级单份目录，各端角色定义收缩成薄壳 + 指针。它同时攻击本项目两个痛点：8 角色间规范不一致、双端同步面过大。

**只抽一份，从输出信封开始**：`references/output-contract.md`（对应 ROMA 的 output-contract，按本项目改造：结论/证据指针/OBSERVED（命令原文+退出码+摘录）/阻塞项/建议 + 禁止 PASS 判定 + 执行记录规范）。8 角色 × 2 端各加一行必读指针。收益直接落在硬规则 4 上——结构化返回让「复核 agent 报告」从阅读理解变成对格式的机械校验。

`repo-routing.md` / `toolchain.md` **不进工作台模板**：它们强绑定 ROMA 的 44 仓库布局与 Go 栈；本项目的对应物按项目沉淀进 knowledge/ 更合适。

## 实现时别漏的三个细节

1. **references 要进 `GUARDED_PREFIXES`。** 它是规范层，性质等同角色定义；不收窄的话 reviewer 持有的 `*.md` 能写它——与 `knowledge/` 当初被收进守卫是同型问题。
2. **指针用绝对路径**（或可靠解析）。ROMA 特意标注「`Read` 只接受绝对路径」。
3. **与 knowledge/ 的边界写死**：references/ = 操作规范（normative，按角色必读）；knowledge/ = 经验判据（检索式，带依据/适用范围/失效条件）。不划清会变成两个互相漂移的杂物抽屉——刚在 AGENTS.md/CLAUDE.md 上修掉的那类问题。

## 明确不抄的

- 三份文档结构照搬（只需要信封一份）；
- 路径硬编码到具体机器（`/home/work/...` 写法跨机器即失效）；
- 它 prompt 与 references 之间无意的重复段落（code-scout 内联的那段与 repo-routing 几乎逐字相同）——内联底线是刻意的，但逐字全文内联就退化成了双份拷贝，内联应只留最致命的几行。

## 代价

一份 ~70 行共享文档 + 16 个指针插入 + 守卫一行（`GUARDED_PREFIXES`）+ `wb.py` 零改动；每次派发多 1 次文件读。风险集中在「必读被跳过」——用双保险（prompt 内联底线两三行）压住。

## 关联

- [roma-comparison.md](roma-comparison.md)：其余 ROMA 借鉴项的总账，本文可视为其第十一节候选。
- `knowledge/development/skills-and-agents-are-manual-copies.md`：双端手工同步的判据，本方案是其缓解手段之一。
- [AGENTS.md](../AGENTS.md)「多端适配」：references/ 与软链收敛是同一条多端路线的两个部件。
