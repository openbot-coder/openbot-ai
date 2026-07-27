---
title: financial-briefing
type: Fact
scope: project
confidence: confirmed
strength: medium
activation_count: 0
created: 2026-06-04
last_activated: 2026-06-04
tags: [cron, financial, broadcast, wecom, cron]
related_sessions: [cron_d56fd718, wecom_wrcFjnTQAAe6iBnWroo7WTfoA8bc4nRA]
---

# financial-briefing

## Summary
整点财经播报 cron 任务。每小时执行一次，跨市场搜索后按 emoji 模板格式化推送到企微群。

## Key Facts
- **Cron ID**: `d56fd718`
- **触发时间**: 每小时整点 (8:00-23:00)
- **目标渠道**: 企微群 (chat_id: wrcFjnTQAAe6iBnWroo7WTfoA8bc4nRA)
- **搜索维度**: A股/美股港股/大宗商品/央行政策
- **输出格式**: emoji 模板 (📊→🔥→🛢→📰)，每条 ≤30 字
- **Skill**: `scheduled-finance-broadcast` / `parallel-finance-search`
- **数据源 fallback 链**: Toutiao → QQ Finance → Nbd.com.cn → Sina Finance（bing 不可用时依次降级）
- **Fallback Skill**: `finance-multi-source-search-fallback`

## 状态
- 2026-05-25 OOM 崩溃后丢失，已重建
- 2026-06-04 正常运行中

## Gotchas
- OOM 崩溃后 cron 任务会丢失
- 需要 web-search skill 配合搜索
- 企微群消息有长度限制

## Related Entities
- [nanobot-gateway](knowledge/facts/nanobot-gateway.md) - 运行环境
- [宝爷](knowledge/facts/baoye.md) - 任务创建者