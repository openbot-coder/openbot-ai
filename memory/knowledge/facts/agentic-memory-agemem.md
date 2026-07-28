# Agentic Memory (AgeMem)

## 基本信息
- **论文**：《Agentic Memory: Learning Unified Long-Term and Short-Term Memory Management for Large Language Model Agents》
- **作者**：Yi Yu, Liuyi Yao, Yuexiang Xie, Qingquan Tan, Jiaqi Feng, Yaliang Li, Libing Wu（武汉大学 + 阿里巴巴）
- **arXiv**：[2601.01885](https://arxiv.org/abs/2601.01885)（v2, 2026-04-30）
- **收录日期**：2026-01-05

## 核心思路
- 把 LTM + STM 统一为一个可学习的记忆策略，不再分模块独立管理
- 六类记忆动作封装为 agent 可调用的工具，由 LLM 自主决策何时执行
- 三段式渐进 RL + step-wise GRPO 训练，解决记忆奖励稀疏问题

## 六类工具

| 工具 | 所属 | 功能 |
|------|------|------|
| `ADD` | LTM | 写入新长期记忆 |
| `UPDATE` | LTM | 更新已有长期记忆 |
| `DELETE` | LTM | 删除冗余/低质量长期记忆 |
| `RETRIEVE` | STM | 从 LTM 检索相关记忆到 STM |
| `SUMMARY` | STM | 压缩上下文，生成摘要 |
| `FILTER` | STM | 过滤 STM 中的冗余信息 |

## 训练方法
- **三段式渐进 RL**：
  1. 一阶：学习长效知识沉淀（存储质量）
  2. 二阶：在干扰样本中练习上下文筛选（抗干扰能力）
  3. 三阶：双记忆联动完成闭环推理（LTM↔STM 联动）
- **Step-wise GRPO**：每个记忆动作独立分配奖励，反向传导至全流程
- **奖励设计**：
  - R_task：任务完成度（LLM judge）
  - R_context：上下文管理（压缩效率 + 预防式管理 + 信息保留）
  - R_memory：记忆管理（存储质量 + 维护操作 + 语义相关性）
  - P_penalty：溢出惩罚 + 超轮次惩罚

## 实验结果
- **基准**：ALFWorld, SciWorld, HotpotQA 等 5 个长文本基准
- **基座**：Qwen 全系列
- **性能**：相比 Mem0/LangMem/A-Mem 平均提升 4.8~8.5 个百分点
- **效率**：HotpotQA 对比原生 RAG 节省 3.1%~5.1% 上下文 token

## 与 openbot 现状对比

| 维度 | openbot 现状 | AgeMem 启示 |
|-----|------------|------------|
| 记忆分层 | MEMORY.md（长期）+ history.jsonl（短期）+ USER.md（画像） | 统一为可调用工具集 |
| 检索方式 | grep/read_file 手动检索 | 模型自主决定 RETRIEVE/SUMMARY/FILTER |
| 维护机制 | Dream 自动维护 + 人工规则 | RL 学习何时 ADD/UPDATE/DELETE |
| 上下文压缩 | 用户偏好驱动的对话压缩 | Formalize 为 SUMMARY/FILTER 工具 |
| 奖励信号 | 无显式记忆质量奖励 | R_storage/R_maintenance/R_relevance |

## 关键洞察
1. **预防式压缩**（R_preventive）：在 token 溢出前主动调用压缩工具，而非等系统崩溃
2. **存储质量**（R_storage）：按高质量记忆比例评分，避免冗余存储
3. **维护操作**（R_maintenance）：奖励主动 UPDATE/DELETE，鼓励动态记忆管理
4. **无额外调度模型**：全程由 LLM 自身决策，不需要辅助记忆控制器

## 标签
- agent-memory, long-term-memory, short-term-memory, reinforcement-learning, GRPO, unified-memory-management
