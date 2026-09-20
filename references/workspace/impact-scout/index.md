# impact-scout 调研规则（通用默认）

本文件是 `impact-scout` agent 的业务自定义钩子的**内核默认版**。各 workspace 应以本地实体 `references/workspace/impact-scout/index.md` 覆盖本文件，写入项目专属规则。与 `repos/index.md`、`docs/` 权威源冲突时，一律以后者为准。

## 仓库拓扑
- 先用 `repos/index.md` 的「当前仓库」表把现象路由到仓库、「仓库关系」节定上下游方向，不要凭仓库名猜。

## 优先核对的跨仓入口
- 跨仓链路与契约入口：`docs/architecture/`
- 线上应用名 ↔ 仓库名对照：`docs/environment/`
（具体文件名由各 workspace 在本地覆盖版中写明。）

## 权威源指针
- 外部供应商中文名/短名：`docs/integrations/`
- 产品概念：`docs/product-spec/`
- 已知现象/排障：`docs/troubleshooting/`

## 检索
- `repos/.source/` 下仓库入口是软链：递归检索用 `grep -R`/`rg -L`/`find -L`；判断挂载用 `find -L repos/.source -maxdepth 3 -name .git`。
