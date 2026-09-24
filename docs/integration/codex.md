# Codex 快速接入

完整的共享规则、检索诊断和验收层级见 `../agent-integration.md`；本页只列本机接入步骤。

1. 运行 `scripts/render_client_configs.py`，把生成的 `codex.config.toml` 中 `personal_memory` 段合并到
   `%USERPROFILE%\.codex\config.toml`，不要覆盖其他 MCP server。
2. 运行 `.\.venv\Scripts\python.exe scripts\install_codex_hooks.py`，让安装器保留其他 Hook、备份
   原配置并部署标准库 Hook 到 `$CODEX_HOME/hooks/codex_memory_review_hook.py`。
3. 新 Codex 会话通过 `/hooks` 检查并信任这两项命令。配置发生变化后，信任哈希可能需要重新确认。
4. 调用 `memory_status`，核对 database 是否是三端共用的绝对路径。

Codex 的 MCP 服务仍由项目虚拟环境启动；Hook 则使用 Windows Python Launcher 启动用户目录中的
ASCII 路径副本。这两条运行链路用途不同，不应互相替换。

`UserPromptSubmit` 只注入读取与写入纪律，`Stop` 在结束前要求一次复核并防止循环。Hook 不会把整段
聊天自动发送给数据库，真正的记忆写入仍必须表现为一次 `memory_store`、`memory_update` 或
`memory_forget` 工具调用。

验收顺序：

```powershell
.\.venv\Scripts\python.exe scripts\check_connections.py --installed
```

随后在普通 Codex 任务中验证 `UserPromptSubmit Completed → Stop Blocked → Stop Completed`，并确认没有
使用跳过 Hook 信任的参数。
