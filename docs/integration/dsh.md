# DeepSeek Harness 快速接入

推荐采用 profile patch 永久挂载：先运行 `scripts/render_client_configs.py`，再把生成的
`deepseek-harness.cordis.yml` 中 insert 条目合并进
`%USERPROFILE%\.dsh\profiles\web\cordis.patch.yml`。正常启动只需：

```powershell
npx -y @deepseek-ai/dsh --profile web
```

不要同时再次传入 `examples/deepseek-harness.cordis.yml`，否则会重复插入同一 server。模板用于新机器
接入或恢复配置时参考，合并时保留 profile 中其他条目。

三个关键条件：

1. `PERSONAL_MEMORY_DB` 必须在 patch 中显式写成三端共用的绝对路径；宿主可能清理环境变量。
2. `cwd` 与 Python 命令也使用绝对路径，避免宿主工作目录变化。
3. profile patch 和 system prompt 通常在启动时加载，修改后要重启 DSH。

接入后先调用 `memory_status`、`memory_context` 和 `memory_search` 做只读验收，再用一条可删除的测试
记忆验证跨客户端读写。独立启动 MCP 进程成功，不等于 DSH 的真实模型会话已经加载工具。
