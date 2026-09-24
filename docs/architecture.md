# 架构

## 运行分层

```text
Codex / WorkBuddy / DeepSeek Harness
        │ 客户端规则、Hook 与 MCP 工具调用
        │ stdio · MCP JSON-RPC
        ▼
src/personal_memory/
    models.py      数据模型与参数校验
    store.py       SQLite、FTS5、范围过滤、检索诊断
    retrieval.py   RRF 融合、宽松回退、compact 投影
    server.py      七个 MCP 工具
    cli.py         status / serve / export / import / import-note
    archive.py     JSON 与 Markdown 归档
    extensions.py  尚未启用的扩展 Protocol
```

后端不读取客户端聊天记录。客户端让模型决定是否调用工具；服务器只处理收到的结构化请求。

## 单一数据源

- 所有客户端连接同一个绝对路径的 SQLite 数据库。
- 数据库采用 WAL 与短事务，允许不同客户端各自启动 stdio 服务进程。
- `memories` 保存当前状态和完整 JSON 文档，`history` 保存每次修订快照。
- FTS5 索引由外部内容表和触发器维护，是可重建的派生数据。
- 中文检索额外索引单字与 bigram，因为 SQLite `unicode61` 不会按中文词语切分。

默认数据库位于用户级数据目录，不放在仓库中。也可以通过 `PERSONAL_MEMORY_DB` 指定其他绝对路径；
迁移时必须同时更新并验证所有客户端，不能只改 README 或单个配置。

## 检索路径

1. 对 scope、type、有效期和软删除状态做统一过滤；宽松模式也不能绕过这些条件。
2. 单查询使用严格 FTS AND 匹配和 BM25 排序。
3. `query_variants` 让每路查询独立严格检索，再用 RRF 合并；每路候选数有明确上限。
4. `search_mode="auto"` 只在严格候选池为空且未跳页时，执行一次受控宽松回退。
5. 宽松回退测量查询片段在当前范围内的文档频率，过滤不可用或过于常见的片段，再以 OR 召回、
   覆盖数过滤和稳定排序生成待核对候选。

严格与宽松检索都返回候选池、分页、截断和失败原因诊断；宽松结果不会篡改记录原有的 confidence。

## 时间、替代与修订

- 有效期是 `[valid_from, valid_to)`；`valid_to=null` 表示没有已知结束时间。
- `supersedes` 在新记录写入时原子关闭旧事实的有效期，并形成可追踪的替代链。
- `memory_update` 修改可编辑字段并增加 revision，不改变 scope、有效期或替代关系。
- `memory_forget` 是软删除：记录退出正常检索，但修订历史仍可审计。
- `as_of` 回看的是事实有效期；旧版本正文通过 `memory_history` 查询。

## 客户端自动化边界

- Codex 使用 `UserPromptSubmit` 注入规则，并由 `Stop` Hook 在结束前要求一次记忆复核。
- WorkBuddy 使用用户级 always-apply 规则；MCP 信任和规则安装是两件事。
- DSH 通过 profile patch 挂载 MCP，并在持久 system prompt 中保存读写纪律。

这些机制只会提示或约束模型调用工具，不能证明某一轮已经发生读写。真实验收必须查看工具结果和
`memory_status` 返回的数据库路径。

## 预留但未启用

`extensions.py` 定义 `EmbeddingProvider`、`HybridRetriever`、`AccessPolicy` 与 `Consolidator`
Protocol。它们是未来扩展边界，不是已启用能力；启用条件见 `philosophy.md`。
