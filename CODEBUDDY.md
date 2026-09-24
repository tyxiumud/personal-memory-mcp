# Personal Memory 项目协作

请先阅读项目根目录 AGENTS.md，遵循其中的记忆读写、来源记录与 Git 协作约定。
本项目统一 scope=project、scope_id=project:personal-memory。
开始任务调用 personal_memory 的 memory_context。用户明确要求记忆时立即写入；普通聊天中由
模型判断并自动保存新确认、长期有用的偏好、事实、决策、里程碑和阻塞。写前查重，
source.trigger 标记 explicit 或 autonomous，每轮少量原子记录，不保存秘密、猜测或原始日志。
WorkBuddy 的内置记忆与本项目 MCP 是不同系统；不要声称它们已经自动同步。
