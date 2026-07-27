---
title: memory-wiki
type: Fact
scope: project
confidence: confirmed
strength: medium
activation_count: 0
created: 2026-06-04
last_activated: 2026-06-04
tags: [memory, knowledge-base, wiki, agent-memory, concept]
---

# MemoryWiki

## Summary
LLM 驱动的持久化记忆系统，融合 Wiki 知识积累 + 会话记忆 + 实体关系图。支持会话续接、实体追踪、上下文快照。已有具体项目实现（llm-wiki-mcp），MEMORY.md 保留为备份/导出格式。

## Key Facts
- **四层架构**: Context → Sessions → Entities → Raw Sources
- **四种操作**: Remember / Recall / Relate / Reflect
- **存储路径**: `/home/openbot/workspace/memory/`
- **MEMORY.md**: 长期事实（由 Dream 自动维护）
- **history.jsonl**: 追加式日志

## 目录结构
```
memory/
├── MEMORY.md          # 长期事实
├── context/           # 上下文快照
├── sessions/          # 会话记忆
├── entities/          # 实体 (人/项目/概念)
├── relations/         # 关系图
└── raw/              # 原始资料
```

## Gotchas
- MEMORY.md 不可手动编辑（由 Dream 管理）
- SOUL.md / USER.md 同样由 Dream 管理
- history.jsonl 是追加式的，用 grep 搜索

## Related Entities
- [nanobot-gateway](knowledge/facts/nanobot-gateway.md) - 部署环境
- [llm-wiki-mcp](knowledge/facts/llm-wiki-mcp.md) - 具体项目实现（MCP 服务器封装）