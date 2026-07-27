# llm-wiki-mcp

> LLM Wiki MCP 服务 — 将 Deep Agents LLM Wiki 工作流封装为 MCP 服务器
> 创建于 2026-07-01

## 核心工具

### 知识工具 (8)
- `init` — 初始化知识库
- `ingest` — 摄入文档
- `query` — 查询知识
- `lint` — 内容校验
- `search` — 搜索
- `read` — 读取
- `write` — 写入
- `index` — 索引

### 记忆工具 (5)
- `remember` — 存储记忆
- `recall` — 召回记忆
- `forget` — 遗忘记忆
- `consolidate` — 合并记忆
- `import` — 导入记忆

## 运行模式
- **知识模式**：领域知识库
- **记忆模式**：长期记忆库
- 记忆条目存储在 `/memory/` 子目录

## 设计文档
- 路径：`/home/openbot/workspace/projects/llm-wiki-mcp/docs/design.md`

## 待决事项
- FastMCP vs native MCP SDK
- LangSmith Sandbox vs local Docker
- ripgrep vs Whoosh 搜索
- write 后是否 auto-commit
- 记忆索引后端 SQLite vs JSON

## 安全规则
- 硬禁止写入 `/raw/`
- 硬禁止删除操作
- 删除走 `.trash/` 软删除

## 兼容性
- 兼容 OKF（Open Knowledge Format）
- 使用 Markdown frontmatter 元数据
- 使用 `wiki://` URI scheme

## 已有代码路径
- `projects/deepagents/examples/llm-wiki/`
- 文件：helpers.py / ingest.py / query.py / models.py / runner.py / index.py / log.py

## 关键已有模式
- review-then-apply 两阶段工作流
- source staging
- index 自动刷新
- structured log entries

## 记忆工具设计细节
- `memory.import` 支持从 MEMORY.md 和 history.jsonl 导入
- `memory-consolidate` prompt 模板用于合并旧记忆为摘要
- `/memory/*.md` 资源可读写含 frontmatter 校验
- `/raw/` 和 `/log.md` 只读
