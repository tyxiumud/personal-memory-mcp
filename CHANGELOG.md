# 变更记录

本文件记录本地项目里程碑；尚未标记为正式发布的内容放在“未发布”下。

## 未发布

- 项目正式采用 MIT License。
- 普通写入的精确查重不再依赖 `source.trigger`；auto 宽松回退成功时明确报告
  `retrieval.strategy=relaxed`。
- 将客户端配置改为无个人路径的公开模板，新增本机渲染器。
- Codex Hook 安装器支持 `CODEX_HOME`，在安装时生成本机命令而非提交用户名与绝对路径。
- 增加跨 Windows/Linux 的 GitHub Actions、Dependabot、安全策略和贡献指南。
- 清理本机验收报告与内部提交纪要；公开文档不再声明某台电脑的安装状态。
- 修复 Codex 0.153.4 自动记忆 Hook 启动：安装器将标准库 Hook 部署到 Codex 用户目录，
  Windows 下通过 Python Launcher 启动，`command` 与 `commandWindows` 使用同一条已验证命令。
- 整理项目定位、设计理念、架构、评测边界和三端快速接入文档。
- 增加经过代码复核的 v0.4 候选计划；计划尚未实施。

## v0.3 里程碑

- 增加 retrieval 诊断、scope 清单和 `search_mode="auto"` 受控宽松回退。
- compact 视图保留 verification 与 epistemic_status，避免截断可信度限定语。
- 增加正式库只读体检脚本与更完整的检索评测。

## v0.2 里程碑

- 增加 `query_variants` 与 RRF 多路融合、compact 上下文和字符预算。
- 增加 Codex 读取规则注入、Stop 记忆复核和三端写入纪律。

## v0.1 里程碑

- 建立 SQLite + FTS5 本地事实层、七个 MCP 工具、时间/修订模型和归档命令。
- 完成 Codex、WorkBuddy 与 DeepSeek Harness 的初始接入。
