# openbot-ai Memory System AMP Alignment Design Document

> Version: 0.3 | Date: 2026-06-17 | Status: Implemented (Phase 1-4, Phase 3 N/A)

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
memory/                         memory/
├── history.jsonl      ──►      ├── history.jsonl
├── MEMORY.md           ──►     ├── MEMORY.md            ← 索引 + 对外兼容
                                ├── knowledge/             ← 项目级记忆
                                │   ├── facts/
                                │   ├── decisions/
                                │   ├── learnings/
                                │   ├── preferences/
                                │   ├── constraints/
                                │   ├── questions/
                                │   └── relations/
                                ├── session/               ← 会话观察
                                ├── global/                （人工授权写入）
                                ├── promotion_candidates/  （晋升候选）
                                └── archive/               （归档）
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

**修改点**：`build_system_prompt` 中 memory 注入部分采用混合策略：直接注入 `MEMORY.md` 内容作为上下文，同时导航相关知识文件并更新激活状态。

**RECALL hook 规范**：`build_system_prompt` 执行时必须：
1. 加载 `memory/MEMORY.md` 并作为 "Memory Index" 注入上下文
2. 解析 MEMORY.md 中的 `related:` 引用，扫描 `memory/knowledge/` 下的相关文件
3. 对于每个加载的 memory 文件（包括 MEMORY.md 本身），调用 `update_activation()` 以递增 `activation_count` 并刷新 `last_activated`

### 3.4 Consolidator（openbot/agent/memory.py 内）

**lint 触发点**（仅 Dream 完成后）：
- Dream cron job 完成后（`cli/commands.py` 中 Dream 执行成功后）
- 手动 `/dream` 命令完成后（`command/builtin.py` 中 `_run_dream()` 的 finally 块）
- Consolidator.archive() 本身不触发 lint（lint 仅在 Dream 上下文中运行）

**lint 实现**：
- 断链扫描：解析所有 `related:` 和 `supersedes:` 链接，检查目标文件存在性
- 矛盾检测：同一 topic（type + tags）下多个 `confidence: confirmed` 条目 → 按 `created` 时间戳，较旧的标记 `deprecated`（不新建 Decision 记录，由 Dream 处理后续修正）
- 过时检测：`last_activated > 90 天` + `strength: weak` → deprecated
- 孤儿检测：无入链 + 无 tag 匹配 + 从未激活 → 30 天后直接迁移 `archive/`（无需中间 orphan registry 文件）
- 晋升检测：`promotion_candidates/<slug>/credits.md` 累计 ≥ 2 → 建议晋升 `knowledge/`
- strength 自动更新：lint 时调用 `compute_strength()` 重新计算衰减值，自动写回 frontmatter

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

| 编号 | 问题 | 实现方案 |
|------|------|---------|
| ⑥ | prompt injection / HTML 注入 | ✅ `write_amp_memory` 中对 body 做 HTML strip（正则 `<[^>]+>`）、转义 `&<>`，过滤 prompt injection 模式（`Ignore previous`、`You are now`、`System:`、`[SYSTEM]`） |
| ⑦ | 矛盾处置 | ✅ lint 检测到同一 topic（type + tags）多个 `confidence: confirmed` 条目时，按 `created` 时间戳，较旧的标记 `deprecated`。不新建 Decision 记录，由 Dream 处理后续修正 |
| ⑧ | 孤儿处置 | ✅ lint 检测到无 `last_activated` + 无 `tags` + `created > 30 天` 的条目，直接迁移至 `archive/`（无需中间 orphan registry 文件） |
| ⑩ | 项目生命周期 | v0.2 不实现，标记为 v0.3 |
| ⑫ | 衰减公式 | ✅ 采用 AMP §7 公式，`half_life = 30 * (1 + activation_count)`，lint 时调用 `compute_strength()` 计算并自动更新 `strength` |

---

## 4. 迁移策略（零停机）

### Phase 1：目录结构创建（✅ 已完成）
1. ✅ 创建 `memory/` 目录及子目录（knowledge/facts, learnings, decisions, preferences, constraints, questions; session; global; promotion_candidates; archive）
2. ✅ 创建 `memory/MEMORY.md`（初始内容为现有 entities/index.md 分类）
3. ✅ 现有 `memory/` 目录保留不动
4. ✅ GitStore tracked_files 动态包含 `memory/**/*.md`

### Phase 2：MEMORY.md 拆分 + MemoryAdapter（✅ 已完成）
1. ✅ Dream 下次运行时，对新增/修改的事实写入 `memory/knowledge/` 下的对应文件（带 frontmatter）
2. ✅ 旧 `memory/MEMORY.md` **永久保留**，作为对外兼容层，不标记 deprecated
3. ✅ `memory/MEMORY.md` 由 `sync_to_legacy_memory()` 自动同步：Dream run 完成后，将 `memory/knowledge/` 下的 frontmatter 摘要汇总追加到 `memory/MEMORY.md`
4. ✅ 手动 `/dream` 和 cron Dream 均触发 lint + sync

### Phase 3：entities/ 全部迁移到 knowledge/（❌ 不适用）

> **结论**：`memory/entities/`、`memory/concepts/`、`memory/cron/`、`memory/persons/` 目录在代码库中从未存在。设计文档 §2.1 的"现状"描述的是理想化旧状态，实际代码库直接在 Phase 1 中建立了 `memory/knowledge/` 子目录结构，无需迁移。Phase 3 标记为不适用。

### Phase 4：lint + decay 上线（✅ 已完成）
1. ✅ 将 lint 逻辑接入 Dream cron（每 2h Dream 运行后执行）+ 手动 `/dream`
2. ✅ 实现 `compute_strength()` 衰减计算，在 lint 时自动更新 `strength` 字段
3. ✅ promotion_candidates/ 逻辑接入（credits ≥ 2 → 建议晋升）
4. ✅ 矛盾检测（同 type+tags 多个 confirmed → 旧的 deprecated）
5. ✅ 孤儿迁移（>30 天无标签 → archive/）

### Phase 5：并行运行（逐步淘汰旧路径）（部分完成）
1. ✅ `memory/MEMORY.md` **永久保留**，由 `sync_to_legacy_memory()` 自动同步（只追加，不删除旧内容）
2. ❌ Phase 3 不适用，无需迁移 `memory/entities/`
3. ⬜ `memory/sessions/` 旧摘要不再保留，AMP `memory/session/` 承担会话观察功能
4. ✅ `memory/` 作为内部主存储，所有新写入走 AMP 格式

---

## 5. 文件修改清单

| 文件 | 改动类型 | 说明 |
|------|---------|------|
| `openbot/agent/memory.py` | ✅ 已实现 | write_amp_memory, read_amp_memory, update_activation, lint_memories, compute_strength, sync_to_legacy_memory（MemoryAdapter）、migrate_orphan_to_archive、HTML escape + prompt injection guard、GitStore tracked_files 动态包含 memory/**/*.md、knowledge 子目录 6 个 |
| `openbot/templates/agent/dream.md` | ✅ 已实现 | 4文件 → 9 type + frontmatter 指令 + 枚举白名单 |
| `openbot/agent/context.py` | ✅ 已实现 | 混合策略：注入 MEMORY.md + 扫描 related 文件并 update_activation |
| `openbot/command/builtin.py` | ✅ 已实现 | 手动 `/dream` finally 块中添加 lint_memories() + sync_to_legacy_memory() |
| `openbot/cli/commands.py` | ✅ 已实现 | Cron Dream完成后触发 lint + sync（已包含） |
| `openbot/utils/gitstore.py` | ✅ 已实现 | GitStore tracked_files 通过 glob 动态扩展，无需静态改动 |
| `AGENTS.md` | ✅ 已实现 | 追加 AMP 记忆规则段（Write/Read/Maintenance Rules） |
| `openbot/config/schema.py` | ⬜ 未改动 | 可选：增加 memory.amp_alignment 配置开关（暂不需要） |
| Phase 3 迁移 | ❌ 不适用 | `memory/entities/` 目录从未存在，无需迁移脚本 |

---

## 6. 风险与缓解

| 风险 | 影响 | 缓解 |
|------|------|------|
| Dream 写入新格式导致旧系统无法读取 | 高 | ✅ Phase 2 增量迁移，旧 MEMORY.md 保留 |
| frontmatter 解析失败 | 中 | ✅ 容错读取：解析失败时 fallback 到纯 body（read_amp_memory 已实现） |
| lint 性能开销 | 低 | ✅ lint 仅在 Dream 完成后执行，非每次请求 |
| 并发写入冲突 | 低（当前单进程） | ✅ AMP §6.4 协议预留，当前用 threading.Lock |
| orphan 迁移误删活跃文件 | 低 | ✅ 只迁移无 last_activated + 无 tags + created > 30 天的文件 |

---

## 7. 决策记录

| # | 决策 | 结论 |
|---|------|------|
| 1 | 是否保留 `memory/MEMORY.md`？ | **保留**，作为 AMP 索引 + 对外兼容层，由 MemoryAdapter 按新规则自动同步摘要。 |
| 2 | entities/ 是否全部迁移到 `memory/knowledge/facts/`？ | **不适用**，`memory/entities/` 目录在代码库中从未存在，无需迁移。 |
| 3 | lint 是独立 cron 还是整合进 Dream？ | **整合进 Dream**，复用 LLM 上下文，Dream 完成后自动触发。Consolidator.archive() 不触发 lint。 |
| 4 | 是否保留 `memory/sessions/` 摘要？ | **不保留**，AMP `memory/session/` 替代原有 sessions/ 摘要功能，旧摘要随迁移逐步淘汰。 |
| 5 | RECALL hook 采用纯导航还是混合注入？ | **混合策略**：保留 MEMORY.md 内容注入作为上下文，同时扫描 related 文件并调用 update_activation。纯导航会丢失 MEMORY.md 中的项目级上下文。 |
| 6 | 矛盾检测是否新建 Decision？ | **不新建**，仅 deprecated 旧条目。新建 Decision 由 Dream 处理更合适，lint 保持轻量。 |
| 7 | 孤儿迁移是否需要中间 registry？ | **不需要**，直接迁移到 archive/。简化实现，registry 文件增加维护成本。 |
