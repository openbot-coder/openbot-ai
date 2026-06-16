# openbot-ai Memory System AMP Alignment Design Document

> Version: 0.1 | Date: 2026-06-16 | Status: Draft

---

## 1. 目标

将 openbot-ai 现有 memory 系统对齐到 AMP v1.0 规范，最小化破坏性改动，保留所有现有功能（Dream、Consolidator、GitStore、AutoCompact）。

**不动**：history.jsonl 格式、Dream Phase 1+2 流程、GitStore、session 管理。
**动**：目录结构重组、MEMORY.md 结构化拆分、frontmatter schema、Dream prompt 路由规则、lint/decay/promotion 逻辑。
**保留**：`memory/` 目录和 `MEMORY.md` 作为对外兼容层永久保留，不删除、不废弃。

---

## 2. 现状 vs AMP 对照

### 2.1 目录结构映射

```
现状                           AMP 目标
─────────────────────────────────────────────────────────────────────────────
memory/
├── history.jsonl       ──┐
├── context/            ──┼──► raw/events/              （原始事件流，只读）
├── sessions/           ──┤
├── entities/           ──┤    ├── MEMORY.md            ← 索引（≤200行，仅目录）
├── relations/          ──┤    ├── log.md                （已有 memory/log.md）
└── MEMORY.md           ──┘    ├── session/               ← context/ + sessions/ 迁入
                              ├── knowledge/             ← 项目级记忆（原 project/）
                              │   ├── facts/
                              │   ├── decisions/
                              │   ├── learnings/
                              │   ├── preferences/
                              │   ├── constraints/
                              │   ├── questions/
                              │   └── relations/          ← relations/ 迁入
                              ├── global/                （人工授权写入）
                              ├── promotion_candidates/  （新增）
                              └── archive/               （新增）
```

**索引与兼容层共存方案**：`memory/MEMORY.md` 同时承担 AMP 索引和对外兼容接口双重角色。索引部分 ≤200 行，仅保存目录；MemoryAdapter 将 `memory/knowledge/` 的摘要追加到该文件末尾，两者共存。当索引超过 200 行时，最老的目录条目被压缩为分类摘要，腾出空间。

### 2.2 字段 schema 对照

| 字段 | 现状 | AMP 要求 | 改动 |
|------|------|---------|------|
| type | 无 | 9种枚举 | Dream prompt 增加分类指令 |
| scope | 无 | session/project/global | 写入时根据来源判定 |
| confidence | 无 | observed/hypothesis/confirmed/deprecated | 新增事实为 observed，Dream 确认后 promoted |
| strength | 无 | weak/medium/strong | Dream 写入时根据重要性判定 |
| activation_count | 无 | int（≥0） | 每次 RECALL +1，存储在 frontmatter |
| last_activated | 无 | ISO 8601 | 每次 RECALL 时更新 |
| related_hash | 无 | SHA256 | 写入 related 链接时计算目标文件 hash |
| supersedes_hash | 无 | SHA256 | 同上 |

---

## 3. 组件级改动计划

### 3.1 MemoryStore（openbot/agent/memory.py）

**不动的方法**：`append_history`, `read_unprocessed_history`, `read_recent_history_for_prompt`, `compact_history`, `_write_entries`, `build_dream_prompt`, `raw_archive`

**修改方法**：`__init__` 中新增 `knowledge_dir`、`promotion_dir`、`archive_dir` 路径，同时更新 GitStore tracked_files 加入 `memory/` 下所有 `.md` 文件路径。`build_dream_tools` 需更新为包含 `memory/` 路径，使 Dream 可写入 `memory/knowledge/` 和 `memory/session/`。

**新增 MemoryAdapter 方法**：
- `sync_to_legacy_memory()` — 将 `memory/knowledge/facts/`、`learnings/`、`decisions/` 下的 frontmatter 摘要汇总为 Markdown，追加到 `memory/MEMORY.md`，保持对外兼容。
- `should_sync()` — 去重判断：比较 `memory/` 内容 hash 与上次 sync 记录，相同则跳过。调用时机：Dream run 完成后、lint 完成后。

**去重策略**：维护 `memory/.last_sync_hash` 文件，每次 sync 前计算 `memory/knowledge/` 下所有文件的 SHA256 合并值，与上次记录比对，一致则跳过。Lint 触发在 Dream 之后，大概率 hash 相同，不会重复写入。

### 3.2 Dream Template（openbot/templates/agent/dream.md）

**新增路由规则**（在现有 4 文件路由之外）：

```
| 新增 Type | 目标路径 | 触发条件 |
|-----------|---------|---------|
| Observation | memory/session/<sid>/observations.md | 会话中观察到的新现象 |
| Learning | memory/knowledge/learnings/<slug>.md | 已验证的踩坑经验 |
| Decision | memory/knowledge/decisions/<slug>.md | 技术/架构决策 |
| Preference | memory/knowledge/preferences/<slug>.md | 用户偏好 |
| Constraint | memory/knowledge/constraints/<slug>.md | 红线规则 |
| Hypothesis | memory/session/<sid>/questions.md | 未验证推测 |
| Fact | memory/knowledge/facts/<slug>.md | 客观事实 |
| Skill | skills/<name>/SKILL.md | 已有流程，不改变 |
| Question | memory/knowledge/questions/<slug>.md | 悬而未决问题 |
```

**新增 frontmatter 指令**：Dream 写入每个文件时必须在 YAML frontmatter 中填写 `type/scope/confidence/strength/activation_count/created/tags`。

**枚举值白名单**：在 Dream prompt 中附录枚举表，禁止写入非法值。

### 3.3 ContextBuilder（openbot/agent/context.py）

**修改点**：`build_system_prompt` 中 memory 注入部分改为从 `memory/MEMORY.md` 导航加载，而非直接注入整个 `MEMORY.md`。

**RECALL hook 规范**：`build_system_prompt` 执行时必须：
1. 首先加载 `memory/MEMORY.md` 并导航到相关子目录
2. 对于每个加载的 memory 文件，调用 `update_activation()` 以递增 `activation_count` 并刷新 `last_activated`
3. 用基于 index 的导航替换当前直接注入 `MEMORY.md` 的方式

### 3.4 Consolidator（openbot/agent/memory.py 内）

**新增 lint 触发点**：
- `Consolidator.archive()` 方法末尾（memory.py 中所有调用 `archive()` 的地方：`compact_idle_session`、`_archive_idle_session` 等）
- Dream run 完成后（commands.py 中 Dream 执行成功后）

**lint 实现**：
- 断链扫描：解析所有 `related:` 和 `supersedes:` 链接，检查目标文件存在性
- 矛盾检测：同一 topic 下多个 `confidence: confirmed` 且内容矛盾 → 旧条目 deprecated + 新建 Decision
- 过时检测：`last_activated > 90 天` + `strength: weak` → deprecated
- 孤儿检测：无入链 + 无 tag 匹配 + 从未激活 → 30 天后迁移 `archive/`
- 晋升检测：`promotion_candidates/<slug>/credits.md` 累计 ≥ 2 → 建议晋升 `knowledge/`

### 3.5 GitStore（openbot/utils/gitstore.py）

**扩展 tracked_files**：新增 `memory/` 目录下所有 `.md` 文件。

**修改方法**：引用现有 `line_ages()` 方法，无需新增。`auto_commit` 保持现有签名 `def auto_commit(self, message: str) -> str | None` 不变，通过 `__init__` 中 `tracked_files` 列表控制提交范围，将 `memory/` 下 `.md` 文件加入 `tracked_files` 即可，无需新增 `files` 参数。

### 3.6 AGENTS.md 集成模板

在项目 `AGENTS.md` 中追加 AMP 规则段（替换现有 Memory 维护规则）：

```markdown
## AMP 记忆规则

### 写入规则
- 新事实 → memory/knowledge/facts/<slug>.md (type: Fact)
- 踩坑经验 → memory/knowledge/learnings/<slug>.md (type: Learning)
- 技术决策 → memory/knowledge/decisions/<slug>.md (type: Decision)
- 用户偏好 → memory/knowledge/preferences/<slug>.md (type: Preference)
- 红线规则 → memory/knowledge/constraints/<slug>.md (type: Constraint)
- 会话观察 → memory/session/<sid>/observations.md (type: Observation)

### 检索规则
- 每次任务开始前读取 memory/MEMORY.md
- READ 后必须更新 last_activated + activation_count

### 维护规则
- 每 10 轮会话执行 Lint
- promotion_candidates/ 累计 ≥ 2 次激活 → 建议晋升 knowledge/
- 90 天未激活的 weak 记忆 → deprecated
- 30 天孤儿的 → 迁移 archive/
```

### 3.7 AMP Vulnerability 修复计划

| 编号 | 问题 | 修复方案 |
|------|------|---------|
| ⑥ | prompt injection / HTML 注入 | `write_amp_memory` 中对 body 做 HTML escape（strip HTML tags, escape `<` `>`），过滤 prompt injection 指令 |
| ⑦ | 矛盾处置 | lint 检测到同一 topic 多个 `confidence: confirmed` 条目时，按 `created` 时间戳，较旧的标记 `deprecated`，新建 Decision 记录修正 |
| ⑧ | 孤儿处置 | lint 检测后写入 orphan registry 文件，30 天后在 lint 时迁移到 `archive/` |
| ⑩ | 项目生命周期 | v0.1 不实现，标记为 v0.2 |
| ⑫ | 衰减公式 | 采用 AMP §7 公式，`half_life = 30 * (1 + activation_count)`，lint 时计算并更新 `strength` |

---

## 4. 迁移策略（零停机）

### Phase 1：目录结构创建（无破坏）
1. 创建 `memory/` 目录及子目录
2. 创建 `memory/MEMORY.md`（初始内容为现有 entities/index.md 分类）
3. 创建 `memory/log.md`（初始为空）
4. 现有 `memory/` 目录保留不动

### Phase 2：MEMORY.md 拆分 + MemoryAdapter（Dream 增量）
1. Dream 下次运行时，对新增/修改的事实写入 `memory/knowledge/` 下的对应文件（带 frontmatter）
2. 旧 `memory/MEMORY.md` **永久保留**，作为对外兼容层，不标记 deprecated
3. `memory/MEMORY.md` 由 MemoryAdapter 自动同步：Dream run 完成后，将 `memory/knowledge/facts/`、`learnings/`、`decisions/` 下的 frontmatter 摘要汇总追加到 `memory/MEMORY.md`
4. 逐步迁移：Dream 处理时遇到旧条目，写入新路径后通过 adapter 同步到 legacy 文件

### Phase 3：entities/ 全部迁移到 knowledge/
1. 所有现有 entity 文件（projects/、concepts/、cron/、persons/）补全 AMP frontmatter 字段（type/scope/confidence/strength/activation_count）
2. 迁移路径：`memory/entities/projects/<name>.md` → `memory/knowledge/facts/<name>.md`；`concepts/` → `memory/knowledge/facts/`；`cron/` → `memory/knowledge/facts/`；`persons/` → `memory/knowledge/facts/`
3. 不保留 `memory/entities/` 子目录，迁移完成后该目录清空删除
4. 更新 `memory/MEMORY.md` 引用

### Phase 4：lint + decay 上线
1. 将 lint 逻辑接入 Dream cron（每 2h Dream 运行后执行）
2. 实现 decay 计算，在 lint 时更新 `last_activated` 和 `activation_count`
3. promotion_candidates/ 逻辑接入

### Phase 5：并行运行（逐步淘汰旧路径）
1. `memory/MEMORY.md` **永久保留**，由 MemoryAdapter 自动同步（只追加，不删除旧内容）
2. `memory/entities/` 迁移完成后清空删除
3. `memory/sessions/` 旧摘要不再保留，AMP `memory/session/` 承担会话观察功能
4. `memory/` 作为内部主存储，所有新写入走 AMP 格式

---

## 5. 文件修改清单

| 文件 | 改动类型 | 说明 |
|------|---------|------|
| `openbot/agent/memory.py` | 新增方法 + 扩展 __init__ + 修改 build_dream_tools | write_amp_memory, read_amp_memory, update_activation, lint_memories, compute_strength, sync_to_legacy_memory（MemoryAdapter） |
| `openbot/templates/agent/dream.md` | 修改路由规则 | 4文件 → 9 type + frontmatter 指令 + 枚举白名单 |
| `openbot/agent/context.py` | 修改 build_system_prompt | 从 memory/MEMORY.md 导航加载，注入 RECALL 更新逻辑 |
| `openbot/agent/memory.py (Consolidator)` | 新增 lint 触发 + MemoryAdapter | archive/Dream 完成后调用 lint + sync_to_legacy_memory |
| `openbot/utils/gitstore.py` | 扩展 tracked_files | 加入 memory/ 下所有 .md |
| `openbot/config/schema.py` | 无改动 | 可选：增加 memory.amp_alignment 配置开关 |
| `AGENTS.md` | 修改记忆规则段 | 替换为 AMP 集成模板 |
| `openbot/cli/commands.py` | Dream 完成后触发 lint + sync | Dream run 成功后调用 Consolidator.lint() 和 sync_to_legacy_memory() |

---

## 6. 风险与缓解

| 风险 | 影响 | 缓解 |
|------|------|------|
| Dream 写入新格式导致旧系统无法读取 | 高 | Phase 2 增量迁移，旧 MEMORY.md 保留 |
| frontmatter 解析失败 | 中 | 容错读取：解析失败时 fallback 到纯 body |
| lint 性能开销 | 低 | 每 10 轮执行，非每次请求 |
| 并发写入冲突 | 低（当前单进程） | AMP §6.4 协议预留，当前用 threading.Lock |

---

## 7. 决策记录

| # | 决策 | 结论 |
|---|------|------|
| 1 | 是否保留 `memory/MEMORY.md`？ | **保留**，作为 AMP 索引 + 对外兼容层，由 MemoryAdapter 按新规则自动同步摘要。 |
| 2 | entities/ 是否全部迁移到 `memory/knowledge/facts/`？ | **是**，全部迁移，不保留 `memory/entities/` 子目录。 |
| 3 | lint 是独立 cron 还是整合进 Dream？ | **整合进 Dream**，复用 LLM 上下文，Dream 完成后自动触发。 |
| 4 | 是否保留 `memory/sessions/` 摘要？ | **不保留**，AMP `memory/session/` 替代原有 sessions/ 摘要功能，旧摘要随迁移逐步淘汰。 |
