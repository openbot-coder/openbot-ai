---
title: vlcc-oil-price-report
type: Fact
scope: project
confidence: confirmed
strength: medium
activation_count: 0
created: 2026-06-04
last_activated: 2026-06-04
tags: [cron, vlcc, oil, shipping, wecom, cron]
related_sessions: [cron_6f807c25, wecom_wrcFjnTQAAe6iBnWroo7WTfoA8bc4nRA]
---

# vlcc-oil-price-report

## Summary
VLCC 油运日报 cron 任务。每天 9:00 搜索波罗的海交易所 VLCC 油运数据，输出含表格和 emoji 的中文简洁报告。

## Key Facts
- **Cron ID**: `6f807c25`
- **触发时间**: 每天 09:00
- **目标渠道**: 企微用户 qingjiaowodayima (小铭CEO)
- **数据源**: 波罗的海交易所 VLCC 数据 (TD3C/TD15 航线)
- **Skill**: `vlcc-oil-price-report-daily`
- **输出格式**: Worldscale 点 + TCE + 短期走势分析

## 状态
- 2026-05-25 OOM 崩溃后丢失，已重建
- 2026-06-04 正常运行中
- 2026-06-16, 2026-06-17 因模型错误未能执行

## Gotchas
- 是发给企微用户（非群），需要正确的 open_id
- VLCC 油运数据可能不稳定

## Related Entities
- [nanobot-gateway](knowledge/facts/nanobot-gateway.md) - 运行环境
- [qingjiaowodayima](knowledge/facts/qingjiaowodayima.md) - 接收者
- [宝爷](knowledge/facts/baoye.md) - 任务创建者