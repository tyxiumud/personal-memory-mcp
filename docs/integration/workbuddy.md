# WorkBuddy 快速接入

1. 运行 `scripts/render_client_configs.py`，把生成的 `workbuddy.mcp.json` 中 `personal_memory` 条目合并到
   `%USERPROFILE%\.workbuddy\mcp.json`。
2. 安装用户级 always-apply 规则：

   ```powershell
   .\.venv\Scripts\python.exe scripts\install_workbuddy_rule.py
   ```

3. 在 WorkBuddy MCP 管理页信任该 server。批准记录通常保存在
   `%USERPROFILE%\.workbuddy\mcp-approvals.json`；配置内容改变会改变哈希，可能需要重新信任。
4. 重启或新建会话，确认工具列表出现 `mcp__personal_memory__*`，再调用 `memory_status` 核对数据库。

MCP 信任只表示 WorkBuddy 可以启动服务器；always-apply 规则才负责提醒模型何时读取和复核记忆。
只完成其中一个步骤，可能出现“工具存在但长期不写”或“规则存在但工具不可用”。

WorkBuddy 内置记忆与本项目数据库是两个系统，不能把一方显示的内容自动视为另一方已经同步。
