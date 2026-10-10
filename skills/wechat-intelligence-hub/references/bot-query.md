# Bot 查询接口

此入口为本机 Bot 宿主提供结构化证据，Bot 负责理解问题和撰写答复。它不启动聊天平台 Bot、不提供 HTTP 服务、不调用模型、不发送微信消息，不生成报告。

## 使用

```bash
bash skills/wechat-intelligence-hub/scripts/hub.sh query status
bash skills/wechat-intelligence-hub/scripts/hub.sh query search --query "报价" --chat "示例客户甲"
bash skills/wechat-intelligence-hub/scripts/hub.sh query history --chat "示例客户甲" --live --limit 20
bash skills/wechat-intelligence-hub/scripts/hub.sh query context --message-id 42 --radius 2
bash skills/wechat-intelligence-hub/scripts/hub.sh query topic --query "云渲染" --keyword "远控" --since "2026-10-01T00:00:00+08:00" --until "2026-10-11T00:00:00+08:00"
bash skills/wechat-intelligence-hub/scripts/hub.sh query person --chat "示例客户甲"
bash skills/wechat-intelligence-hub/scripts/hub.sh query reply --chat "示例客户甲" --live
bash skills/wechat-intelligence-hub/scripts/hub.sh query tasks
```

独立 Python 入口位于 `projects/wechat-intelligence-hub/wechat_query.py`；不需要报告的 nh3/Pandoc 依赖。

`hub.sh query` 优先复用同套安装的 Reader 包装器。独立 Python 调用默认使用 PATH 中的 `rion-wechat-cli`；可用 `--reader /path/to/reader.sh` 或可信的 `RION_WECHAT_CLI_BIN` 配置覆盖。

默认索引 `~/.wechat-intelligence-hub/radar.db`，可用 `WECHAT_HUB_DB` 或 `--db` 指定既有索引。查询通过 SQLite `mode=ro` 与 `query_only` 读取，不创建缺失数据库、不执行迁移、不导入消息。实时模式不依赖索引，不将结果写回索引；Reader 自己的既有访问/解密缓存策略保持不变。

## 命令约定

| 命令 | 数据 | 实时模式 |
|---|---|---|
| status | 索引消息/聊天数量与新鲜度 | 否 |
| search | 单个字面关键词，最新命中优先 | 是 |
| topic | 最多 5 个字面关键词取并集 | 是 |
| history | 指定聊天最新若干条，按时间正序输出 | 是 |
| context | 本地锚点前后消息，保持同一聊天 | 否 |
| person | 聊天记录及已保存机会 | 是；实时结果不加载管线 |
| reply | 同 person，标记 draft_only | 是 |
| tasks | 已保存且未结束的机会/任务，按优先级 | 否 |

所有命令均返回一个 JSON 文档；成功退出 0，错误退出 2。参数错误也是 JSON。只有 `--help` 返回普通帮助文字。

- `--since` 含起点，`--until` 不含终点；建议提供带时区的 ISO 8601。无时区的旧记录沿用 Reader 所在电脑的本地时区。
- `--limit` 默认 30，范围 1–100；`--max-text-chars` 默认 2000，范围 1–4000。
- `--radius` 默认 2，范围 0–10；上下文最多 21 条，limit 必须容纳整个窗口。
- `--timeout` 为一次实时查询的总 Reader 超时预算，默认 30 秒，最多 30 秒，不自动重试。topic 最多 5 次关键词读取，联系人解析共用预算。
- `--query` 与可重复的 `--keyword` 都是字面字符串，不是 SQL、通配符或正则。长度上限 256。
- history/person/reply 需要 `--chat`；search/topic 可限定聊天。不唯一时返回 `ambiguous_chat`，不会选第一个。
- tasks 的 `--chat` 使用完整已保存聊天名称精确过滤；消息时间窗口不适用于管线状态。
- 结果只包含必要消息字段，不包含 source_file、密钥、原始诊断或媒体本机路径。消息原文可能自带敏感信息，宿主仍应控制其模型数据范围。
- 查询不会提取所有附件、OCR 或新承诺；reply 只返回证据，最终措辞由 Bot 生成。

## JSON 契约

```json
{
  "schema_version": 1,
  "ok": true,
  "command": "search",
  "data": {
    "messages": [
      {
        "id": 42,
        "id_source": "local_index",
        "chat": "示例客户甲",
        "sender": "客户",
        "time": "2026-10-10T09:00:00+08:00",
        "content": "请发报价",
        "content_truncated": false
      }
    ]
  },
  "coverage": {
    "source": "local_index",
    "complete": false,
    "live_refreshed": false,
    "latest_message_time": "2026-10-10T09:00:00+08:00",
    "last_indexed_at": "2026-10-10T10:00:00+08:00",
    "truncated": false,
    "requested_since": null,
    "requested_until": null,
    "warnings": []
  }
}
```

`complete` 始终为 false：现有本地索引或可读数据库不是完整微信保证。`latest_message_time` 在本地模式是索引的最新消息，在实时模式是本次返回的最新消息；`last_indexed_at` 是最近保存的索引运行时间，不是账号全部聊天刷新时间。未返回消息不等于没有聊天。

Reader 消息标记 `id_source: wechat_reader`，使用 `reader_local_id` 和可用的 `reader_chat_id`；这些 ID 不能作为本地 context 的 `--message-id`。实时消息不会虚构本地 ID。

错误形如：

```json
{"schema_version":1,"ok":false,"command":"history","error":{"code":"ambiguous_chat","message":"存在多个聊天对象，请使用完整名称缩小范围。","candidates":["示例客户乙","示例客户甲"]}}
```

错误码包括 invalid_request、database_missing、chat_not_found、ambiguous_chat、message_not_found、reader_failed、reader_timeout 和 query_failed。候选最多 20 个，`candidates_truncated` 表示需要再缩小名字。实时候选包含 name 与 reader_chat_id。Reader 原始 stderr 不进入错误输出。

## Dot / Grok 接入

**能够执行本机命令的 Dot 或其他 Agent 宿主：** 加载本 Skill，并将唯一允许的微信工具绑定到 `hub.sh query`。具体 Bot 产品的工具配置不同；本仓库不声称安装 Skill 就能在任意聊天客户端自动接入。

**Grok API 宿主：** 将 [bot-tool.json](bot-tool.json) 注册为自定义函数 `wechat_query`；模型产生函数参数，宿主在微信所在电脑执行查询，再把 JSON 作为函数结果交回模型。参考 [xAI Function Calling](https://docs.x.ai/developers/tools/function-calling)。该文件使用 Responses API 的函数形状；使用其他 API 时按其格式包装。Grok 网页对话不会因此获得本机数据库权限。

宿主负责验证参数、身份和用户已授权的数据范围。数据库路径、Reader 路径与可执行命令由可信配置固定；不要把模型给出的字符串拼成 shell 命令，不暴露旧报告/状态编辑命令。多用户宿主必须隔离每人的索引和访问材料；第一版建议单用户本机运行。

参数转命令使用数组且 shell=False。例如通过校验后：

```python
argv = [configured_python, configured_query_script, "--db", configured_database, request["command"]]
for field in ("query", "chat", "since", "until", "limit", "max_text_chars", "message_id", "radius"):
    if field in request:
        argv.extend(["--" + field.replace("_", "-"), str(request[field])])
for keyword in request.get("keywords", []):
    argv.extend(["--keyword", keyword])
if request.get("live"):
    argv.append("--live")
# reader_path 来自宿主配置，不来自模型。
argv.extend(["--reader", reader_path])
result = subprocess.run(argv, shell=False, capture_output=True, text=True, timeout=35)
# 检查退出码，解析单个 JSON；将 ok:false 作为工具错误返回，勿发送原始 stderr。
```

宿主仍需实现消息通道、模型调用循环和认证。本版本没有部署 Dot/Grok Bot，没有配置 API key，也没有自动把微信数据发送到任何模型。

## 验证

```bash
cd projects/wechat-intelligence-hub
python3 -m unittest discover -s tests -p test_bot_query.py -v
```

测试只使用虚构 SQLite 与替身 Reader，验证不生成报告、索引只读、日期边界、重名、结果限制、ID 来源和失败处理。真实微信、不同平台首次访问以及 Bot 模型端连接仍需在用户自己的电脑验证。
