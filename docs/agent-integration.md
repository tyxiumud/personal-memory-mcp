# Agent 接入与共享规则

本阶段维护 Codex、WorkBuddy 与 DeepSeek Harness（DSH）三种接入。客户端配置不同，但必须遵守同一
数据和记忆纪律。各客户端的最短安装步骤见 `integration/`。

## 单一数据库

所有客户端必须把 `PERSONAL_MEMORY_DB` 指向同一个绝对路径。默认位置是用户级数据目录：

- Windows：`%LOCALAPPDATA%\personal-memory-mcp\memory.sqlite3`
- macOS/Linux：`~/.local/share/personal-memory-mcp/memory.sqlite3`

运行 `scripts/render_client_configs.py` 可以把公开模板渲染成本机绝对路径配置。复制配置后，在每个
客户端调用 `memory_status`，以返回的 database 字段为准，不要只相信配置文件看起来一致。

## 项目身份与范围

这个仓库自身使用：

```text
scope     = project
scope_id  = project:personal-memory
```

`scope` 与 `scope_id` 是两个字段。scope 是分类过滤，不是权限控制；不要把两者再次拼接成
`project:project:personal-memory`。处理其他项目时，应使用该项目稳定的 scope_id。

任务范围不明确或发生切换时，先调用 `memory_status` 查看 scopes 清单，再选择范围。知道某个范围存在，
不表示应该读取其中所有记录。

## 启动读取

需要持续背景的任务开始时，推荐调用：

```json
{
  "selection": {
    "scope": "project",
    "scope_id": "project:personal-memory",
    "include_global": true,
    "limit": 8
  },
  "max_chars": 6000,
  "view": "compact"
}
```

已经在同一会话读到且范围未改变时，可以复用；话题切换、用户纠正或工具报告变化后重新检索。
翻译和简单格式化等自包含任务无需读取长期记忆。

## 词法检索与 query_variants

自然语言整句经常包含记录里没有的词。客户端应从用户问题和已知背景中提取少量、真实存在的关键词，
通过 `query_variants` 执行多路严格查询：

```json
{
  "selection": {
    "query": "数据库",
    "query_variants": ["SQLite", "FTS5"],
    "scope": "project",
    "scope_id": "project:personal-memory",
    "include_global": true
  }
}
```

后端使用词法匹配和 RRF 融合，不是向量或语义检索。关键词必须来自问题或已知背景，不能为了命中而
编造事实。严格查询没命中时可以换一组关键词再查一次；仍无结果就说明没有找到依据。

## 检索诊断

`memory_search` 与 `memory_context` 返回 `retrieval`：

| reason | 含义 |
| --- | --- |
| `empty_scope` | 范围、类型、时间和遗忘过滤后没有活动记录 |
| `no_lexical_match` | 范围内有记录，但关键词没有命中 |
| `offset_beyond_pool` | 当前分页越过候选池 |
| `matched` | 当前严格或浏览策略有结果 |
| `relaxed_match` | auto 宽松回退返回候选，必须核对相关性 |

`scoped_active` 是 FTS 前的范围记录数；`candidate_pool` 是当前策略收集的候选池。多路查询的精确数量
看 `per_query_matches`。达到候选上限时，池大小只是下限，不应被解释成总匹配数。

`search_mode="auto"` 是候选发现模式，不是可靠事实答案。返回记录带
`match_quality="relaxed"`；只有记录正文直接回答问题时才能引用。对于密码、账号、证件、日期和编号
等具体事实，坚持使用 strict + query_variants。

## compact 视图

`view="compact"` 保留正文、时间、范围、可信度和固定来源摘要，不调用模型改写，也不截断正文。
`verification` 与 `epistemic_status` 完整保留。字符预算放不下时整条省略，并由
`omitted_from_page` 报告；需要完整来源和历史时调用 `memory_history`。

## 写入纪律

写入分为两种触发：

- 用户明确要求记住、纠正或忘记：立即处理，`source.trigger=explicit`。
- 普通交流中出现新确认、长期有用的信息：模型可以少量写入，`source.trigger=autonomous`。

每次写前先搜索目标范围，避免重复。通常一轮最多保存 1–3 条原子记录。不要保存秘密、猜测、原始
聊天、原始工具日志或短期状态。计划不是已完成事项，关注列表不是持仓。

事实发生变化时，新建记录并用 `supersedes` 关闭旧事实；原记录写错时使用带
`expected_revision` 的 `memory_update`。删除采用 `memory_forget` 软删除，历史仍保留。

## 客户端自动化边界

### Codex

Codex 使用 MCP 配置启动服务。可选 Hook 通过 `UserPromptSubmit` 注入纪律，并用 `Stop` 要求一次结束前
复核。安装器尊重 `$CODEX_HOME`，保留其他 Hook 并备份原配置。非托管 Hook 必须在 `/hooks` 中检查并
信任后才会运行；Hook 定义变化后可能需要重新确认。

### WorkBuddy

WorkBuddy 的 MCP 信任决定服务器能否启动；用户级 always-apply 规则决定模型是否主动读写。两者缺一
都会出现“工具可见但不使用”或“规则存在但工具不可用”。WorkBuddy 内置记忆与本项目数据库不是同一
系统，不能声称自动同步。

### DeepSeek Harness

DSH 通过 profile patch 中的 stdio insert 条目挂载。环境变量可能被宿主清理，因此 database、command
和 cwd 都应写成绝对路径。修改 profile 或持久 persona 后重启 DSH。不要同时在 profile 和启动参数中
重复插入同一 server。

## 验收层级

从弱到强依次是：

1. 配置能解析；
2. MCP 子进程能启动并列出七个工具；
3. `memory_status` 返回预期数据库；
4. 客户端真实模型会话调用工具；
5. 一个客户端写入、另一个客户端检索到同一记录。

仓库提供只读探针：

```powershell
.\.venv\Scripts\python.exe scripts\check_connections.py --installed --database <shared-db>
```

该命令验证配置启动、工具 schema、数据库路径和共享上下文，但不会调用模型，也不会写记忆。

## 回退与备份

- 客户端配置变更前先保留原文件；安装器会为 Codex Hook 自动创建备份。
- 数据库运行在 WAL 模式，不要复制正在打开的单个 `.sqlite3` 文件作为备份。
- 使用 CLI export 创建一致归档；恢复时先导入到新的数据库路径检查。
- 自动化规则失效不会损坏数据库，但模型可能停止主动读取或写入。

## 共同开发

- 修改前查看 Git 状态，保留用户未提交更改。
- 正式数据库、导出、日志、备份和本机渲染配置不得提交。
- 核心测试：`.venv/Scripts/python.exe -m pytest -q`
- 静态检查：`.venv/Scripts/ruff.exe check src tests scripts`
- 正式库只读体检：`.venv/Scripts/python.exe scripts/audit_memory.py --db <shared-db>`
- 合成检索报告：`.venv/Scripts/python.exe scripts/report_retrieval.py`
