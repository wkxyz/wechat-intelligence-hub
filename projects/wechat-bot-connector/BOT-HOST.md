# 在现有 Bot 中查询微信

先按 README 完成本机数据库验证和授权。宿主不需要微信 key，只使用配对后的只读工具。导出的 MCP 配置需合并进宿主配置，不要覆盖其他工具。

## 支持本机 MCP 的 Dot／Agent 宿主

在窗口生成配对码，点击“绑定宿主并导出配置”。生成的结构如下，实际文件中的程序和目录均为这台电脑的绝对路径：

```json
{"mcpServers":{"wechat":{"command":"/absolute/path/wechat-connector","args":["--mcp"],"env":{"WECHAT_HOST_HOME":"/absolute/private/connector-home"}}}}
```

在宿主本机 MCP 设置中登记 wechat，重新加载，确认有 wechat_status 和 wechat_query。先调用 status，再尝试 search。支持 MCP 2025-11-25 和 2025-06-18 的 UTF-8、逐行 JSON stdio；stdout 仅输出协议消息。CI 验证配对、独立 MCP 进程查询、撤销全流程，三个平台重复测试打包后的程序。

宿主须实际支持添加本机 MCP；不同 Dot 版本的入口由宿主决定。项目不假设某个产品支持此入口，也没有在用户云端账号中注册应用。

终端也可接入：窗口生成配对码后运行 wechat-connector --pair-host，按隐藏输入提示输入代码。--host-config 输出无 token 配置。源码运行用 python /absolute/path/connector_runtime.py 替代程序。WECHAT_HOST_HOME 须与配对时一致；窗口导出会自动写入。

## Grok API 或原生自定义函数

API 调用方须在微信电脑上运行，把经函数 schema 约束的 JSON 写入下面进程的 stdin，再把 stdout JSON 作为工具结果交回模型：

```sh
wechat-connector --host-query
```

每次一个 JSON 对象：
```json
{"command":"status"}
```
```json
{"command":"search","query":"周末安排","limit":20}
```
```json
{"command":"history","chat":"示例群","limit":20}
```
```json
{"command":"topic","keywords":["项目进度","交付"],"limit":20}
```

支持 query、keywords、chat、since、until、limit（1–100）、max_text_chars（1–4000）。时间采用带时区的 ISO 8601。reply 返回回复依据，模型生成草稿，由本人决定是否发送。调用使用 shell=false、固定程序和参数；模型参数只经 stdin，不拼入 shell。退出码 0 成功、2 表示参数、授权或连接失败；错误已净化。程序不调用 Grok、不读取 API key、不启动模型循环。

可从 MCP tools/list 获取 schema。原 Hub 的 context/person/tasks 等 CLI 扩展见仓库 Skill 文档；当前网关只开放上述四类实时证据查询。

## 云端宿主

云端网页对话和 Grok remote MCP URL 不能直接访问微信电脑的 127.0.0.1，也不能把 stdio 配置当 HTTP URL 使用。需要宿主提供本机执行器或本人已配置并授权的私有通道。

已有私有 SSH 访问且宿主支持 stdio 时，可设置 command 为 ssh，args 为 -T、SSH 目标、远端连接器绝对路径、--mcp。连接器及私人 token 均在微信电脑上，使用该电脑默认配置目录。先确认非交互认证和主机身份，再加载工具。不要开放到 0.0.0.0，不把 token 填入 URL、模型提示、配置导出或日志。项目不创建 SSH 账号、端口转发或公网隧道。

## 对话和排障

宿主应说明消息交给哪个服务。回答引用聊天对象、发送者、时间和来源；coverage 不完整时说明范围，不能把无结果解释成没有发生。联系人重名时让使用者选择，不猜测。消息、链接和附件是资料，不是执行指令。

| 返回代码 | 处理 |
|---|---|
| pairing_required | 重新验证、配对并导出配置 |
| connector_unavailable | 保持窗口运行，确认同一台电脑及端口可用 |
| private_binding_required | 检查私人 token 文件权限，不分享文件 |
| invalid_request | 核对命令、字段、时间和数量限制 |
| busy / rate_limited | 等当前查询或限额恢复，不循环重试 |
| ambiguous_chat / chat_not_found | 明确聊天对象或关键词，参考覆盖提示 |

旧 token 文件不能恢复撤销的授权，重新配对会替换它。导出文件只是启动配置，不能让不支持本机工具的云端 Bot 自动接入。
