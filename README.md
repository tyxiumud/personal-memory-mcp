# Personal Memory MCP

**给 AI 一个你自己拥有、可审计、跨客户端共享的事实层。**

项目使用 Python、SQLite + FTS5 和官方 MCP Python SDK，通过 stdio 为 Codex、WorkBuddy 与
DeepSeek Harness 提供同一份本地长期记忆。它不依赖模型 API、embedding 服务、Docker 或 Go；
检索和写入裁决保持确定性，便于复现、诊断与审计。

这个项目主要防止三类失败：把推测和临时信息写成长期事实、换客户端后失忆，以及检索没有命中时
让模型自行补全。后端不会读取聊天；客户端规则或钩子只提醒模型何时读取和复核，真正写入仍是一次
可见的 MCP 工具调用。

## 文档地图

| 想了解什么 | 文档 |
| --- | --- |
| 为什么采用确定性、可诊断的小型实现 | `docs/philosophy.md` |
| 存储、检索、时间与修订如何分层 | `docs/architecture.md` |
| 什么该记、自动写入边界、Obsidian 如何分工 | `docs/memory-workflow.md` |
| 检索指标能证明什么、不能证明什么 | `docs/evaluation.md` |
| 三端共用规则、诊断与完整接入说明 | `docs/agent-integration.md` |
| Codex / WorkBuddy / DSH 快速接入 | `docs/integration/` |
| 下一阶段候选改动及验收门槛 | `docs/v0.4-plan.md` |
| 安全边界与漏洞报告 | `SECURITY.md` |
| 开发环境与贡献要求 | `CONTRIBUTING.md` |
| 开源许可 | `LICENSE`（MIT） |

当前明确不做：多租户 ACL、默认向量检索、自动整合、自主摄取和后台读取聊天。`extensions.py`
中的相关接口只是预留，不代表功能已经启用。

当前版本仍处于 1.0 之前的 alpha 阶段。MCP 核心可运行，但客户端安装与 Hook 行为会受宿主版本和
操作系统影响；公开发布前后的兼容性以 CI 与对应客户端实测为准。

## 快速开始

需要 Python 3.11+ 与 [uv](https://docs.astral.sh/uv/)。克隆仓库后在项目根目录执行：

```powershell
uv sync --locked
.\.venv\Scripts\python.exe -m personal_memory status
.\.venv\Scripts\python.exe -m personal_memory serve
```

serve 会等待 MCP 客户端通过标准输入通信，没有网页或交互菜单；手动运行时用 Ctrl+C 退出。客户端配置后会按需启动进程。未设置数据库时默认使用 `%LOCALAPPDATA%\personal-memory-mcp\memory.sqlite3`（非 Windows 使用用户数据目录）；`--db` 优先于环境变量。所有客户端指向同一个绝对路径才会共享记忆。

先渲染不含个人路径的公开配置模板：

```powershell
$configOutput = Join-Path $env:TEMP 'personal-memory-configs'
.\.venv\Scripts\python.exe scripts\render_client_configs.py $configOutput
```

脚本默认使用当前虚拟环境与用户级数据库；也可传入 `--python`、`--database` 和
`--project-root`。输出目录必须是新目录或空目录，避免覆盖已有配置。

## 接入客户端

当前维护范围为 **Codex、WorkBuddy 和 DeepSeek Harness（dsh）**。实际接入状态、三类配置与共同开发约定见 `docs/agent-integration.md`。根目录 `AGENTS.md` 是共享交接规则。

`examples` 保存带明确占位符的公开模板；先用上面的脚本渲染，再只合并 `personal_memory` 条目，
保留用户已有配置。搬动项目或数据库后要重新渲染并核对三个客户端。

| 客户端 | 示例 | 合并位置 |
| --- | --- | --- |
| Codex | `examples/codex.config.toml`；`docs/integration/codex.md` | `%USERPROFILE%\.codex\config.toml` 的 MCP 配置 |
| WorkBuddy | `examples/workbuddy.mcp.json`；`docs/integration/workbuddy.md` | `%USERPROFILE%\.workbuddy\mcp.json` |
| DeepSeek Harness | `examples/deepseek-harness.cordis.yml`；`docs/integration/dsh.md` | 合并到 web profile 的 `cordis.patch.yml` |

Codex 的自动检查模板在 `examples/codex.hooks.json`。运行 `scripts/install_codex_hooks.py` 后，安装器
会把机器相关命令渲染进 `$CODEX_HOME/hooks.json`（默认 `%USERPROFILE%\.codex\hooks.json`）。新会话中
用 `/hooks` 审查并信任后生效。该钩子每轮注入写入纪律，并在结束前强制一次记忆判断；不会把整段
聊天直接交给后端。

Windows 安装器会把仅依赖标准库的钩子脚本部署到 `$CODEX_HOME/hooks/`，再由 Windows Python
Launcher 启动。生成后的 `command` 与 `commandWindows` 使用同一条本机命令，避免依赖 Hook runner
展开环境变量。MCP 服务仍从项目虚拟环境启动，两条链路互不混淆。修改钩子源码后重新运行安装器
即可同步部署副本。

配置重载后，先让客户端调用 `memory_status`，核对返回的 database 路径；再用单独测试记忆验证一个客户端写入、另一个客户端检索。`examples/agent-instructions.md` 提供通用使用约定。WorkBuddy 使用用户级 always-apply 规则（模板 `examples/workbuddy.RULE.mdc`），DeepSeek Harness 使用持久 system prompt；三端都支持指定写入和模型判断的自动写入。

独立 MCP 进程测试不等于客户端 UI 或真实模型会话已经连接。安装后应在每个客户端调用
`memory_status`，核对数据库绝对路径和工具数量，再做一次跨客户端读写验收。

## 数据模型和工具

scope 支持 global / project / domain；global 的 scope_id 必须为空，后两者必须指定稳定标识。默认只查 global，查询某个 project/domain 可带上 global，不会自动跨项目或跨领域检索。scope 是分类过滤，不是权限控制。

type 支持 profile / preference / fact / episodic / decision。每条记录包含 title、content、valid_from、valid_to、confidence、importance、supersedes、source、tags，以及 id、revision、created_at、updated_at、forgotten_at。时间必须带时区，保存为 UTC；有效期是 `[valid_from, valid_to)`，null valid_to 表示不限结束时间。confidence 和 importance 为 0..1。

| 工具 | 参数和行为 |
| --- | --- |
| memory_store | `memory` 对象；写入记忆，返回 id/revision；supersedes 原子关闭旧记忆有效期；精确活动重复返回原 ID 和 deduplicated=true |
| memory_search | `selection` 对象；query、query_variants、scope、scope_id、include_global、type、as_of、limit、offset、search_mode；返回 `memories` 与 `retrieval` 诊断 |
| memory_context | selection + max_chars + view；在字符预算内返回记录（full 完整 / compact 精简元数据），报告本页遗漏条数、`retrieval` 诊断与 `returned_after_budget` |
| memory_update | memory_id、changes、expected_revision；修改正文/分类/置信度/重要度/来源/标签，记录修订 |
| memory_forget | memory_id、expected_revision；软删除，退出所有检索，历史仍保留 |
| memory_history | memory_id、limit、offset；按修订倒序返回完整快照，包括已遗忘记录 |
| memory_status | 无参数；数据库路径、schema、数量、能力状态，以及 `scopes` 范围清单与统计使用的 `as_of` |

写入示例（memory_store）：

```json
{
  "memory": {
    "title": "示例：项目数据存储决定",
    "content": "本项目第一阶段使用 SQLite。此条仅为示例。",
    "scope": "project",
    "scope_id": "project:personal-memory",
    "type": "decision",
    "confidence": 1.0,
    "importance": 0.8,
    "source": {"kind": "conversation", "client": "codex", "trigger": "explicit", "reference": "replace-with-real-source"}
  }
}
```

查询示例（memory_search / memory_context）：

```json
{"selection":{"query":"SQLite","scope":"project","scope_id":"project:personal-memory","include_global":true,"limit":20}}
{"selection":{"query":"职业","query_variants":["数字 IC","工作"],"scope":"global","include_global":true}}
{"selection":{"query":"投资","query_variants":["稳健","红利"],"scope":"global"},"max_chars":6000,"view":"compact"}
```

英文使用 FTS5 词检索，中文额外索引单字和双字；多项查询取 AND，先按 BM25，再按 importance/confidence/更新时间排序。不是语义搜索；中文双字能改善子串召回，也可能命中双字分散出现的文本。scope_id 精确匹配并区分大小写，不自动归一化路径。

### 多路关键词查询

自然语言整句常常命中不了：`match_expression` 会把中文按双字 AND 起来，像“我是做什么工作的”这种
问题几乎没有记录能同时包含全部双字。v0.2 增加可选的 `query_variants`（最多 5 项，每项最多 100
字符，去空白去重、拒绝空串）：

- 不传该字段时，行为与 v0.1 完全一致。
- 传入后，query 与 variants 先统一去首尾空白并按文本去重，每个不同查询执行一次严格 FTS，
  再按记录 ID 合并；重复传入原 query 不会增加它的融合权重。
- 合并分数 `score = Σ 1/(60+rank)`（rank 从 1 开始）；分数相同依次按 importance、confidence、
  updated_at、id 排序。
- 每路候选上限 100 条，`offset`/`limit` 在合并排序之后应用；深分页超过合并候选池时返回为空。
- 每一路都先经过 scope、type、有效期和 forgotten 过滤，variant 不能绕过任何过滤。
- 空 query 只用于明确的上下文浏览；variants 非空时会跳过空 query，不会额外混入全局浏览结果。

这是词法（关键词）融合，**不是向量或语义检索**：variant 必须真的出现在记录里才可能命中。关键词
由客户端从当前问题和已知背景中提取，后端只负责合并。

### compact 上下文视图

`memory_context` 的 `view` 参数默认为 `full`。`view="compact"` 每条保留 id、revision、title、
content、scope、scope_id、type、valid_from、valid_to、updated_at、confidence、importance，以及
来源摘要。来源摘要按固定字段（kind、client、trigger、date、reference、conversation_id、files、
evidence、verification、epistemic_status）确定性提取，不调用模型改写；缺失字段为 null，不猜测。
只精简元数据，正文不截断；`max_chars` 以实际输出对象计算，`omitted_from_page` 继续报告遗漏条数。
需要完整证据时用 memory_history。建议客户端启动读取使用 `view="compact"`、`max_chars=6000`、
`limit=8`。

`verification` 与 `epistemic_status` 完整保留、不截断：限定语通常写在字段末尾，截断会把“未逐项
外部核实”变成看起来确定的事实。放不下时整条省略并计入 `omitted_from_page`，不会为塞进去而截断。
这两项是固定键，所以在来源字段稀疏的记录上，compact 的固定开销可能接近甚至超过原始 source；
来源字段冗长（长 evidence、多 files、白名单外的 quote 等）时节省明显。

## 读取诊断

`memory_search` 与 `memory_context` 都返回 `retrieval` 对象：`strategy`（strict / browse /
rrf_variants / relaxed）、`variants_used`、`scoped_active`（scope、type、as_of、遗忘过滤后、FTS 之前的条数）、
`candidate_pool`（当前策略实际收集并去重的候选数）、`total_matches`、`per_query_matches`、
`candidate_limit_reached`、`returned`、`offset`、`limit`、`has_more_in_pool`、`reason`。

`reason` 区分：`empty_scope`（范围内没有当前有效记录）、`no_lexical_match`（范围内有记录但
关键词没命中）、`offset_beyond_pool`（offset 越过候选池末尾）、`matched`，以及 auto 回退成功后的
`relaxed_match`。任何取值都不等于“用户
从未记录过相关内容”；`no_lexical_match` 只说明词法没命中，本后端没有语义检索。
`candidate_limit_reached` 为真时 `candidate_pool` 是下限而非匹配总数，多路查询的精确数字看
`per_query_matches`（此时 `total_matches` 为 null，不猜测并集大小）。`memory_context` 另外返回
`returned_after_budget`，且 `retrieval.returned` 是应用预算之前的命中数，所以被预算省略的记录不会
被误报成没命中。

## 范围清单与范围发现

`memory_status` 返回 `scopes`：每项分开给出 `scope`、`scope_id`、`total_count`、`active_count`、
`newest_updated_at`，并附带本次统计使用的 `as_of`。`active_count` 与检索同一规则（未遗忘、
`valid_from` 不晚于该时刻、`valid_to` 为空或晚于该时刻）；`total_count` 与 `newest_updated_at`
包含已遗忘和已过期记录。`scope` 与 `scope_id` 始终是两个独立字段，不会拼接成
`project:project:personal-memory` 这类难以区分类型与标识的字符串。任务范围不明确或发生切换时先看
清单再选范围；范围存在不等于要读取它的全部内容。

## search_mode：严格与受控宽松回退

`selection.search_mode` 默认 `"strict"`，即原有行为。`"auto"` 先跑完全相同的严格查询（含 variants），
只有在候选池为空且没有跳过任何分页时才执行一次宽松回退：把 query 与 variants 拆成关键词片段
（中文单字与双字、英文单词），**先生成全部唯一片段并逐片计算范围内的 DF**，然后

1. 丢掉在范围内匹配不到任何记录的片段——它们召回不了东西，只会把覆盖要求抬高（`投资偏好` 的中间
   双字 `资偏` 就是这种）；
2. 丢掉在范围内过于常见的片段（DF ≥ 3 且占有效记录 ≥ 50%）——它们没有区分度，是通用问句误召回的
   主要来源；
3. 从剩余片段里按 **DF 从低到高**取最多 12 个（相同 DF 保持原始顺序），因此长问句中部或末尾的核心词
   不会被位置性截断丢掉；生成总数、实际使用与被上限丢弃的片段分别见 `fragments_generated`、
   `fragments`、`dropped_by_cap_fragments`；
4. 候选按命中的不同片段数过滤（默认要求 2 个；只剩 1 个可用片段时降为 1）并排序；
5. 候选召回上限 50，达到上限时 `candidate_limit_reached` 与 `uninspected_candidates_possible` 为真。

候选池口径：`recalled_candidates` 是 OR 召回并实际检查的数量，`surviving_pool` 是通过覆盖筛选的数量，
`candidates_rejected` 是被拒数量。顶层 `candidate_pool` 与 `has_more_in_pool` 基于 `surviving_pool`——
被拒候选不会出现在后续页，用召回数报分页会给出错误的全量感；达到召回上限时 surviving_pool 只是下限。

scope、type、有效期、遗忘过滤全程沿用；分页越界不触发回退；空结果仍是合法答案。回退命中的记录带
`match_quality="relaxed"`，`retrieval.fallback` 公开方法、触发原因、使用的片段、丢弃的片段与截断情况，
并提示“宽松匹配，需核对相关性”；这些记录的 `confidence` 不被改写。compact 视图同样保留该标记。

**已知限制**：只凑出 1 个可用片段时覆盖要求降为 1，此时一个与问题同词但答非所问的记录也会被返回。
在正式库上复核还发现这不限于 `coverage_required == 1`：证件、宠物类问题只是命中了“之前”“还记/记得”
这类口语套话，其 `coverage_required` 仍为 2——基于文档频率的通用词过滤在只有几十条记录的小型个人库
里并不稳定。**所有 relaxed 结果都必须做语义核对。**

## auto 的定位：候选发现模式，不是可靠答案

`search_mode="auto"` 是**候选发现模式**：它扩大召回，返回待核对的候选，不是可直接引用的记忆答案。

| 场景 | 用法 |
| --- | --- |
| 问“有没有记过某个具体事实”（密码、证件、账号、日期、编号等） | 用 `strict` + `query_variants`；宽松命中不能回答这类问题 |
| 探索职业、学习、投资、项目等宽泛背景 | 可以用 `auto`，并尽量给 `query_variants` |
| 返回 `match_quality="relaxed"` 的记录 | 只能引用记录中直接回答该问题的内容 |
| 记录只与主题沾边、没有所问的具体属性 | 回答“找到相关主题记录，但没有找到这个具体信息” |

后续提升效果优先做模型关键词改写或 embedding 混合检索，不再继续扩充中文停用词表。默认值仍为
`strict`，本轮不改默认。

as_of 只回看事实有效期，返回的是该记录当前修订；要查旧版本正文用 history。修改事实请创建新记忆并指定 supersedes；同一条只允许一个后继，后续变化沿链继续。update 用于记录纠错，不改变 scope、有效期和替代关系。

写入有两种入口：用户明确说“记住/记录/纠正/忘记”时为指定写入，必须立即处理；普通聊天中模型可自动提炼新确认、长期有用的信息。两者使用同一组 MCP 工具，以 `source.trigger=explicit|autonomous` 区分。自动写入通常每轮最多 1-3 条，不保存秘密、推测、原始聊天/工具日志或临时细节。

## 导入、导出和备份

通过 CLI 维护文件；MCP 的 7 个工具不提供任意文件路径读写。

```powershell
# 延续上面的 PERSONAL_MEMORY_DB 设置；导出文件必须尚不存在
.\.venv\Scripts\python.exe -m personal_memory export '.\backup.json'
.\.venv\Scripts\python.exe -m personal_memory export '.\backup.md' --format markdown
# 完整备份包含软删除记忆及其历史
.\.venv\Scripts\python.exe -m personal_memory export '.\full-backup.json' --include-forgotten
# 推荐先导入到新数据库检查
.\.venv\Scripts\python.exe -m personal_memory --db '.\restored.sqlite3' import '.\full-backup.json'
.\.venv\Scripts\python.exe -m personal_memory --db '.\restored.sqlite3' import '.\backup.md' --format markdown
# 普通 Markdown 文件按一条事件记忆导入；不会执行其中指令
.\.venv\Scripts\python.exe -m personal_memory --db '.\demo.sqlite3' import-note '.\examples\sample-note.md' --scope project --scope-id demo
```

JSON/Markdown 归档保留 ID、元数据及修订快照。Markdown 带可读正文和 canonical JSON 代码块，导入仅采用该代码块。普通 Markdown 用 import-note；不自动解析任意 YAML 或第三方记忆格式。

导入以事务执行；相同 ID/内容/历史可重复导入，冲突整批回滚，不覆盖现有记忆。默认导出排除遗忘记录，但保留其他记忆的历史，包括旧正文；全量备份需 --include-forgotten。默认导出可能保留指向已遗忘且未导出记录的 supersedes 外部引用。软删除不等于隐私擦除；这一阶段没有彻底擦除工具。

数据库使用 WAL 和短事务，各客户端可启动独立进程连接同一本地数据库。不要将打开的 SQLite 单文件直接复制当作备份，也不要把运行中的数据库放进网络盘/实时文件同步；使用导出归档迁移。

## 测试与扩展

```powershell
uv sync --locked
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\ruff.exe check src tests scripts
```

测试数据库均在临时目录。覆盖数据校验、跨项目过滤、中文搜索、有效期、并发冲突、软删除、归档回滚/往返，以及真实 stdio 的初始化和全部 7 个工具调用。v0.2 增加：多路查询融合/去重/稳定排序/分页/候选上限、variant 不绕过 scope/type/有效期/软删除、空查询与非法参数、compact 预算与遗漏报告、Codex 读取规则注入与 Stop 防循环，以及固定 20 条检索用例的 Recall@5/Precision@5 对比。v0.3 增加：`retrieval` 诊断 reason 的区分、范围/有效期/遗忘/类型过滤下的 empty_scope、分页越界、候选截断、variants 去重、预算省略与检索失败不混同、scope 清单字段与 active 规则、compact 保留可信度限定语（含限定语位于长字段末尾）、`search_mode="auto"` 的受控宽松回退（含通用片段规则、覆盖筛选、候选上限、过滤沿用与不回退条件），以及 `scripts/audit_memory.py` 的只读保证、无正文泄漏与和 store 规则的一致性。

```powershell
# 检索验收报告（只用合成夹具，绝不打开正式库）
.\.venv\Scripts\python.exe scripts/report_retrieval.py
```

报告写入 `outputs/v0.3-report.md`：问题原文/严格关键词/多路融合/auto 五种口径的 Recall@5、
Precision@5、首条正确率与无答案误召回率，调参集与验收集分开统计，并附逐条前后对比与 compact
成本对比。历史版本 `outputs/v0.2-report.md` 保留备查。固定用例通过只代表后端行为，不代表客户端
会正确改写问题。

### 真实库只读体检

```powershell
# 默认输出汇总；--format json 输出机器可读结果；--query 覆盖内置探针查询
.\.venv\Scripts\python.exe scripts/audit_memory.py --db '.\data\memory.sqlite3'
# 审计输出描述个人数据，写到临时目录，不要提交仓库
.\.venv\Scripts\python.exe scripts/audit_memory.py --format json --query '投资偏好' | Out-File -Encoding utf8 "$env:TEMP\audit.json"
```

`scripts/audit_memory.py` 用 SQLite `mode=ro` 加 `PRAGMA query_only=ON` 打开数据库，所有语句在同一个
延迟读事务里执行，得到一致快照；连接失败直接报错，**不**回退到 `immutable=1`（那会读到 WAL 的旧
快照）。它不实例化 `MemoryStore`，因为构造函数可能建表、迁移或改 PRAGMA，而是复用
`scoped_filters`、`match_expression` 等纯函数，并用测试保持与 store 规则一致。

输出包含记录数与有效记录数、source 字段覆盖率、scope 分布、compact 字段保留情况与查询诊断。默认只
打印汇总：不含正文、标题、标签、会话编号或单条 id；`--detail` 也只增加 `record-00N` 这类位置编号。
真实库的数字会随时间变化，因此脚本只报告不判定，任何测试都不把具体条数或缺口写成固定断言。
内置探针查询只统计“返回空的比例”，**不是召回率**：召回率需要预先标注相关记录，那是检索评测
（`tests/fixtures/retrieval_cases.json`）的职责。审计输出描述的是个人数据库，不要提交到仓库。

`extensions.py` 预留 EmbeddingProvider、HybridRetriever、AccessPolicy、Consolidator 协议，当前均未启用。schema 使用 PRAGMA user_version 管理；不支持的高版本数据库拒绝打开。未来 embedding 索引必须可重建并按模型/维度版本化；hybrid 候选必须再次经过范围/有效期/遗忘过滤。ACL 上线前要覆盖全部操作并引入可信身份；整理器先生成提案，再经现有修订检查写入。

## 实现取舍与参考

项目支持 Python 3.11+，依赖 SQLite FTS5。实现参考了本地事实源、记忆生命周期与 MCP/CLI 分层
思路，但没有复制或 fork 其他记忆项目，也不包含 TUI、HTTP 或云同步。直接运行依赖为官方 MCP
Python SDK 和 Pydantic，间接依赖由 `uv.lock` 固定。

设计与配置参考：

- [Engram](https://github.com/Gentleman-Programming/engram)
- [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk)
- [Codex MCP 配置](https://developers.openai.com/codex/mcp)（OpenAI Docs 用于核对 TOML 配置）

## 许可证

本项目采用 [MIT License](LICENSE)。
