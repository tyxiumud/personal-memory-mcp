# Personal Memory MCP

> 让 Codex、WorkBuddy 和 DeepSeek Harness 共用一份由你掌管的长期记忆。

**本地 SQLite · 标准 MCP · 支持来源与修订追溯 · 不需要模型 API 或向量服务**

[快速开始](#快速开始) · [三端接入](#接入-ai-客户端) · [怎么使用](#怎么使用) · [工具参考](docs/tool-reference.md) · [设计说明](docs/philosophy.md)

## 它解决什么问题

你在一个 AI 客户端里说明过自己的偏好、长期决定或项目背景，换个客户端又得从头解释。Personal Memory MCP 把这些经过确认的信息存进**你自己的本地数据库**，让接入同一数据库的客户端按需读取。

每条记忆都可记录来源，并带有适用范围、有效期和修订历史。搜索没有命中时，工具会说明是范围里没有记录，还是关键词没有匹配；宽松检索返回的只是待核对候选。

```text
Codex ────────┐
WorkBuddy ────┼── MCP 工具 ── 同一份 SQLite 记忆库
DSH ──────────┘
```

## 适合谁

- 在 Codex、WorkBuddy、DSH 之间切换，希望它们共享个人偏好和长期决定。
- 想把记忆放在本机，能够查询来源、纠正旧事实、导出备份。
- 更看重可解释的写入和检索行为，愿意在客户端配置一次 MCP。

如果你希望“安装后自动读取所有聊天、自动整理成记忆”，这里目前没有后台会话采集。Codex Hook、WorkBuddy 规则和 DSH 提示只会提醒模型何时调用工具；**实际读写以 MCP 工具返回为准**。项目也没有 DSH 内嵌管理页面、HTTP 服务或跨设备实时同步。

## 现在能做什么

| 能力 | 具体行为 |
| --- | --- |
| 跨客户端共享 | 三端指向同一个本地数据库；全局、项目、领域范围分别过滤 |
| 审核写入 | `ACCEPT` 写入；`DROP` 跳过精确重复；`MERGE` / `DEFER` 留待核对 |
| 按需读取 | 中文关键词、FTS5、多路查询；返回命中原因与候选池诊断 |
| 保留变化 | 有效期、事实替代链、修订历史和软删除 |
| 自己带走 | JSON/Markdown 导出与导入，SQLite 数据库由用户持有 |

四态预审只能识别精确重复、同标题冲突和来源字段缺失，**不能证明一条记忆真实，也不能做语义去重**。证据检查同样只报告词面候选，回答前仍需核对正文和来源。完整参数见[工具与数据参考](docs/tool-reference.md)。

## 快速开始

需要 **Python 3.11+**、[uv](https://docs.astral.sh/uv/) 和带 FTS5 的 SQLite。以下命令从一个新克隆的仓库开始：

```powershell
git clone https://github.com/tyxiumud/personal-memory-mcp.git
cd personal-memory-mcp
uv sync --locked
uv run --locked python -m personal_memory status
```

默认数据库在用户数据目录：Windows 为 `%LOCALAPPDATA%\personal-memory-mcp\memory.sqlite3`，macOS/Linux 为 `~/.local/share/personal-memory-mcp/memory.sqlite3`。所有客户端必须使用**同一个绝对数据库路径**，才能共享记忆。

生成三端配置模板（输出目录需为新目录或空目录）：

```powershell
$configDir = Join-Path $env:TEMP 'personal-memory-configs'
uv run --locked python scripts/render_client_configs.py $configDir
```

模板不会自动改动客户端配置。按下一节把所需条目合并进去，再在客户端调用 `memory_status` 核对 `database`。若已有同名输出目录，换一个新目录即可。

## 接入 AI 客户端

| 客户端 | 接入方式 | 分步指南 |
| --- | --- | --- |
| Codex | 合并 MCP 配置；可选安装读取与结束前复核 Hook | [Codex](docs/integration/codex.md) |
| WorkBuddy | 合并 MCP 配置、信任服务；可选安装常驻规则 | [WorkBuddy](docs/integration/workbuddy.md) |
| DeepSeek Harness（DSH） | 在 profile patch 中挂载本地 stdio MCP | [DSH](docs/integration/dsh.md) |

这是 **MCP 服务**，不是在 DSH 插件页一键安装的 npm 插件。三端配置不同，但共用同一份数据库。配置探针可以验证启动命令、工具列表和数据库路径；它不代表客户端 UI 中的真实模型会话已成功读写。

## 怎么使用

接入后，先在客户端调用 `memory_status`，确认数据库路径。然后可以做一轮可见的跨客户端验收：

1. 在一个客户端用 `memory_store_reviewed` 写入一条不含敏感信息的测试记忆，检查 `disposition=ACCEPT` 且 `written=true`。
2. 在另一个客户端用 `memory_search` 查同一范围和关键词，确认返回同一个 ID。
3. 如果只想预览写入结果，用 `memory_review_write`；它不会写数据库。测试记忆可通过 `memory_forget` 软删除，历史仍会保留。

示例写入参数（工具名 `memory_store_reviewed`）：

```json
{
  "memory": {
    "title": "示例：技术文档语言偏好",
    "content": "用户希望技术文档优先使用中文。此条仅用于演示。",
    "scope": "global",
    "type": "preference",
    "source": {
      "kind": "conversation",
      "client": "codex",
      "trigger": "explicit",
      "evidence": "replace-with-real-user-confirmation"
    }
  }
}
```

实际使用时请替换示例内容和来源，不要把这条示例当作真实用户偏好。`source.trigger` 区分用户明确要求的 `explicit` 与模型判断的 `autonomous`；自主写入还需要证据或引用。新事实每条尽量只表达一件事，纠错与事实变化分别使用修订和替代关系。

## 数据与边界

- 记录存于本机 SQLite，支持按范围、类型和有效期过滤；范围**不是访问控制**。
- `memory_forget` 是软删除，历史和备份中可能仍有原文；它不是安全擦除。
- `memory_assess` 只能提示哪些要点找到词面候选，不能替模型证明结论。
- 本项目不默认使用 embedding、外部模型 API、云服务、自动会话摄取或自动合并。
- 项目仍处于 1.0 前的 alpha 阶段；客户端安装与 Hook 行为会随宿主版本变化。

导出完整备份时请使用 `--include-forgotten`，并将归档放在仓库外；不要把个人数据库或导出文件提交到 Git。导入、有效期、检索诊断和备份细节见[工具与数据参考](docs/tool-reference.md)。

## 文档

| 想了解什么 | 去哪里 |
| --- | --- |
| 工具参数、四态写入、搜索诊断、归档 | [工具与数据参考](docs/tool-reference.md) |
| 为什么这样设计、哪些能力暂不做 | [设计理念](docs/philosophy.md) |
| 数据如何存储、检索和修订 | [架构](docs/architecture.md) |
| 什么值得记、客户端何时读写 | [记忆工作流](docs/memory-workflow.md) · [Agent 接入](docs/agent-integration.md) |
| 评测指标能说明什么 | [评测口径](docs/evaluation.md) |
| 版本记录与后续提案 | [变更记录](CHANGELOG.md) · [v0.4 候选计划](docs/v0.4-plan.md) |
| 贡献与安全边界 | [贡献指南](CONTRIBUTING.md) · [安全策略](SECURITY.md) |

## 开发与验证

```powershell
uv sync --locked
uv run --locked python -m pytest -q
uv run --locked ruff check src tests scripts
```

测试和静态检查不需要个人数据库。三端安装后，可用 `scripts/check_connections.py --installed --database <共用数据库的绝对路径>` 验证实际启动配置；未安装三端时无需运行。固定检索夹具只验证后端候选行为，不代表真实会话回答准确率，详情见[评测口径](docs/evaluation.md)。

## 许可证

[MIT License](LICENSE)。
