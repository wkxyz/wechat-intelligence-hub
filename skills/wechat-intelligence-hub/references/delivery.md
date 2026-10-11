# 交付方式

默认在当前 Bot 对话直接回答。消息多少、涉及多少联系人、是否跨群，都不会自动触发文件输出。普通问答统一使用 `hub.sh query`，详见 [bot-query.md](bot-query.md)。

## 默认文字答复

给出结论、当前阶段或建议，以及 `[聊天对象｜时间｜发言人]` 的必要原文依据。说明数据截止时间、截断或读取失败，不展示内部 JSON 或路径。

“今日有什么进展”“本周项目总结”“过去几天有什么值得关注”仍可直接答复。报告文件只在用户明确要求保存、导出、报告文件、网页或指定文件格式时生成。

文字问答不运行自动写文件的 group-daily、contact-daily、brief 或 render-bundle；也不为得到答案自动写索引。

## 明确要求导出时

| 用户明确要求 | 交付 |
|---|---|
| 保存 Markdown | 最终编辑后的 Markdown |
| 网页/HTML | 最终分区 Markdown 与安全渲染的 HTML |
| 导出证据/CSV/JSON | 请求的结构化格式 |
| 调试/审计全部中间产物 | 明确范围内的既有报告流程 |

旧报告命令参数见 [commands.md](commands.md)。不要将这里的交付名称当作全局 CLI 参数。

群聊报告按 [group-editorial.md](group-editorial.md) 对机器初筛做语义编辑，保留话题与重点群两种阅读方式；私信按既有联系人规则编辑。需要完整网页时对同一运行目录执行：

```bash
"${CODEX_HOME:-$HOME/.codex}/skills/wechat-intelligence-hub/scripts/hub.sh" render-bundle \
  /path/to/run-directory \
  --out /path/to/run-directory/wechat_daily_report.html
```

HTML 只使用现有安全渲染器，缺少 nh3/Pandoc 时说明缺口，不能关闭净化或 CSP。机器初筛、CSV 和证据附录只在明确需要时作为用户交付。
