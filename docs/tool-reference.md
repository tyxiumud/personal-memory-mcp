# 工具与数据参考

本文承接 [README](../README.md) 的进阶说明。所有工具使用同一个本地 SQLite 数据库；项目或领域范围是检索过滤条件，不是权限边界。

## 记忆记录

`scope` 可选 `global`、`project`、`domain`。全局记录的 `scope_id` 必须为 `null`；项目和领域必须指定稳定的 `scope_id`。默认查询只读全局；查询项目或领域时可以用 `include_global=true` 同时读取全局记录。不同项目之间不会自动互查。

`type` 可选 `profile`、`preference`、`fact`、`episodic`、`decision`。一条记录包含标题、正文、来源、标签、置信度、重要度、有效期、修订号和可选的 `supersedes`。时间必须带时区，服务端统一保存为 UTC；有效期采用 `[valid_from, valid_to)`，`valid_to=null` 表示没有已知结束时间。

本仓库既有范围是 `scope=project`、`scope_id=project:personal-memory`。`memory_project_identity` 为**新项目**提供 Git origin 哈希标识，找不到 origin 时退回本机路径哈希；它只给建议，不迁移已有记录。本机路径标识不保证跨机器一致。

## MCP 工具

| 工具 | 用途 |
| --- | --- |
| `memory_status` | 数据库位置、数量、范围清单与能力状态 |
| `memory_context` | 按范围、字符预算读取上下文；支持 `full` 和 `compact` |
| `memory_search` | FTS5 搜索，支持多路关键词、有效期与诊断 |
| `memory_assess` | 检查回答所需各要点是否找到词面候选 |
| `memory_review_write` | 只读预审，返回四态裁决 |
| `memory_store_reviewed` | 同一事务内预审并在 `ACCEPT` 时写入 |
| `memory_store` | 原始写入入口，保留兼容性与显式纠错用途 |
| `memory_update` | 用 `expected_revision` 修订可编辑字段 |
| `memory_forget` | 软删除，正常检索不再返回，历史仍保留 |
| `memory_history` | 按修订查看快照，包括已遗忘记录 |
| `memory_project_identity` | 从明确指定的本地目录生成项目标识建议 |

### 审核写入

新记录优先调用 `memory_store_reviewed`，参数与 `memory_store` 一样放在 `memory` 对象中。`memory_review_write` 使用同一套规则，但不写入。

| `disposition` | 判断 | 是否写入 |
| --- | --- | --- |
| `ACCEPT` | 来源齐全，未发现精确冲突；或显式指定 `supersedes` | 是 |
| `DROP` | 同范围、同类型、标题与正文完全相同 | 否，返回旧记录 ID |
| `MERGE` | 正文完全相同、标题不同 | 否，需核对旧记录 |
| `DEFER` | 缺少来源、自动写入缺少证据，或同标题出现不同正文 | 否，需核对原因 |

来源至少写 `source.client` 和 `source.trigger=explicit|autonomous`；自主写入还需 `source.evidence` 或 `source.reference`。这些字段的存在不证明内容真实，预审也不会做语义去重。显式 `supersedes` 仍须通过旧记录存在、范围与有效期校验；预审的 `ACCEPT` 不保证随后一定写入成功。`MERGE`、`DEFER` 不会进入持久化审核队列；调用方需自行保留待处理提案。`memory_store` 仍可直接写入，不能把四态审核理解成数据库层的强制策略。

事实变化用新记录的 `supersedes` 原子关闭旧事实的有效期；同一条事实只能有一个后继。记录文字的纠错用 `memory_history` 查看修订，再带 `expected_revision` 调用 `memory_update`。`memory_forget` 保留数据库与历史中的原文，不是安全擦除。

### 搜索与证据

`memory_search` 的 `selection` 支持 `query`、最多 5 个 `query_variants`、范围、类型、`as_of`、分页与 `search_mode`。中文额外索引单字和双字；英文按 FTS5 词项匹配。每路严格查询取 AND，多个查询的结果按 RRF 合并；变体必须来自问题或已知背景，不能为了命中而编造。

默认 `search_mode="strict"`。`auto` 先跑严格检索，只在没有候选且未跳页时进行一次受限的宽松召回。宽松结果标记 `match_quality="relaxed"`，只是待核对候选。问密码、日期、编号等具体事实时，应使用严格检索加关键词变体；宽松命中不能直接当答案。

搜索和上下文都返回 `retrieval`。常见的 `reason` 包括：

| 原因 | 含义 |
| --- | --- |
| `empty_scope` | 范围、类型、时间与遗忘过滤后没有活动记录 |
| `no_lexical_match` | 范围内有记录，但关键词未命中 |
| `offset_beyond_pool` | 分页起点超过当前候选池 |
| `matched` | 严格检索或浏览返回候选 |
| `relaxed_match` | 宽松回退返回候选，必须核对相关性 |

`candidate_pool` 是实际收集的候选数，达到上限时只是下限。`memory_assess(selection, required_points)` 逐项列出词面候选 ID：`insufficient`/`partial` 表示当前结果不能覆盖所有要点，`incomplete_page` 表示候选池未看完，`review_required` 表示有候选但仍需核对正文、来源和时效。词面重合不等于事实得到证明。

`memory_context(view="compact")` 保留正文、有效期、置信度、重要度和确定性的来源摘要，不对正文做摘要或截断。`max_chars` 只约束序列化后的 `memories` 数组；放不下的整条会省略，并计入 `omitted_from_page`。`retrieval.returned` 是预算前的数量，`returned_after_budget` 是预算后的数量。完整来源和旧版本可用 `memory_history` 查看。

`as_of` 只改变事实有效期筛选，返回的仍是记录的当前修订；要查看旧正文，应读 `memory_history`。

## 归档与备份

CLI 支持 `status`、`serve`、`export`、`import`、`import-note`。MCP 工具不提供任意文件路径读写。

```powershell
# 输出文件必须尚不存在；不要将包含个人记忆的归档提交到 Git
.\.venv\Scripts\python.exe -m personal_memory export "$env:TEMP\personal-memory-backup.json" --include-forgotten
.\.venv\Scripts\python.exe -m personal_memory --db "$env:TEMP\personal-memory-check.sqlite3" import "$env:TEMP\personal-memory-backup.json"
```

JSON/Markdown 归档保留 ID、元数据与修订快照。相同 ID、内容和历史可重复导入；冲突会整批回滚，不覆盖现有记录。普通 Markdown 用 `import-note` 导入为一条事件记忆，不会执行其中指令。默认导出排除已遗忘记录；完整备份需 `--include-forgotten`。

数据库使用 WAL 与短事务，多个客户端可启动独立进程连接同一个本地文件。不要把运行中的 SQLite 单文件直接复制当备份，也不要把数据库放到网络盘或实时文件同步目录；跨机器迁移用导出归档。

## 实现与评测边界

服务是本地 stdio MCP；没有 HTTP、ACL、默认向量搜索、自动会话摄取或后台整理。客户端规则、Hook 和 system prompt 只能提醒模型调用工具，不能证明某轮确实读写了记忆。运行时需检查工具返回值与 `memory_status.database`。

检索回归用固定合成夹具，不代表真实会话回答准确率；正式库只读体检也不把“空查询比例”当成召回率。详细方法见 [评测口径](evaluation.md)，分层与扩展边界见 [架构](architecture.md)，跨客户端使用纪律见 [Agent 接入](agent-integration.md)。
