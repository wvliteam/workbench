---
name: wb-knowledge
description: 工作台知识库操作。沉淀（把可复用经验按判据写进 knowledge/）与查找（按主题检索过往经验，返回带依据与失效条件的条目）。当用户要沉淀经验、查找过往经验、问"之前有没有做过 X"、"上次那个坑怎么解的"、或复盘后要落知识条目时使用。
---

# 知识库：沉淀与查找

工作区的长期经验库在 `knowledge/`（跟着仓库走 git，跨 flow 存活），**按知识类别分目录、一条经验一个文件**。判据、分类对照表、条目格式的完整版在 `knowledge/README.md`，本 skill 是它的操作入口。

**先读 `knowledge/README.md` 再动手** —— 它是冻结契约 `knowledge-convention`，格式以它为准。

## 沉淀判据（唯一标准）

> 换一台机器、下个月再做一次，这条结论还成立吗？

- 成立的进 `knowledge/`：环境约束、正确的构建/测试命令、服务依赖与启动顺序、反复出现的坑与规避、实测推翻文档的结论。
- 不成立的留在 `.workbench/artifacts/`：本次的 PASS/FAIL、日志原文、一次性抖动、验证过程记录。
- 「本次验证通过」不是知识 —— 它是状态，会过期。凭据只记名称与来源位置，永不记值。

## 沉淀步骤

1. **归类**：按「这是什么知识」选 `knowledge/<类别>/`（九类对照表在 `knowledge/README.md`）。现有类别都不合适时**扩展分类** —— 加一行到对照表、建 `knowledge/<新类别>/index.md`，不要塞进最近的目录，也别平铺在 `knowledge/` 根下。
2. **查重**：`grep -ril "<关键词>" knowledge/`。已有相近条目 → 修订它（只改被实测推翻或已失效的内容，写清原记录错在哪里），不要新增平行条目。
3. **一条经验一个文件**：`knowledge/<类别>/<主题>.md`，文件名即主题（kebab-case，如 `troubleshooting/test-needs-docker-first.md`）。
4. 按模板写，四个章节缺一不可：

```markdown
# <一句话结论>

## 依据
怎么实测出来的：命令、`file:line`、日期。没有依据的结论不要沉淀。

## 适用范围
对哪个仓库 / 技术栈 / 布局成立。不写「通用」，除非真验证过。

## 失效条件
什么情况下这条不再成立。写不出来 = 还没想清楚边界，先别写。

## 来源
哪条 flow / 哪次复盘 / 谁实测的。
```

5. **更新类别索引**：新增、移动、修订条目后，同步 `knowledge/<类别>/index.md` 的 `Knowledge Map`（`index.md` 是路由文件，不算沉淀条目）。
6. 权限边界：主线程与 `knowledger` 角色可写 `knowledge/**`；其他角色与 subagent 会被守卫拦 —— 拦了就交回主线程，别换写法。`knowledge/README.md` 是冻结契约，改它走 `contract unlock --name knowledge-convention`。

## 查找步骤

1. `grep -ril "<关键词>" knowledge/`，多换几组词（中英文、同义词、具体技术名）；或 `ls knowledge/` 看有哪些类别、`cat knowledge/<类别>/index.md` 按类别索引浏览。
2. 命中条目**完整读一遍**，返回时每条必须带：结论、依据、失效条件。只转述结论不给依据的结果不可信。
3. 没有命中就明说「知识库无相关条目」。不脑补、不拿通识冒充沉淀经验 —— 沉淀的价值在于它是本工作区实测过的。

## 与六阶段流水线的关系

- **retro 出口**：reviewer 在 `retro.md` 沉淀节列候选 → 编排者派 `knowledger` 角色过滤、查重、落盘 → retro 门禁查 `knowledge_written`（有条目，或 retro.md 显式「无可沉淀：<理由>」）。
- **analyze / design 入口**：编排者派发这两个阶段前先查本库，把相关条目连同依据、失效条件注入派发 prompt。
- 独立会话随时可用本 skill，不必在流水线里。
