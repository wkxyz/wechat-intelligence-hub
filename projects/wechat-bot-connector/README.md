# 微信 Bot 本地连接器

把本人已授权的微信数据库接入现有 Bot 宿主。普通问答返回消息依据，不生成报告、不发送微信。支持本机 MCP stdio 和原生 JSON 工具；具体接法见 [BOT-HOST.md](BOT-HOST.md)。

## 下载和使用

[固定版本下载页](https://github.com/wkxyz/wechat-intelligence-hub/releases/tag/wechat-bot-v0.10.0)由主分支发布流程生成。Linux 全流程测试和三个平台打包验收全部通过后才会发布；未发布时可使用源码。PR 临时包在 GitHub Actions 的 Local connector checks → Artifacts，保留 30 天。

| 包 | 启动 |
|---|---|
| Windows-x64 | 完整解压后双击 wechat-connector.exe |
| macOS-arm64 | Apple 芯片 Mac，双击启动微信连接器.command |
| macOS-x64 | Intel Mac，双击启动微信连接器.command |

保留 `_internal` 目录，可用 SHA256SUMS.txt 核对完整性。包未签名，系统可能要求通过安全设置允许打开；不提供自动更新。

1. 已有 Reader 配置：点击“复用已有 Reader 配置”。以后重新启动可“恢复连接器已保存账号”。
2. 首次导入：选择本人单一账号数据库目录（通常是 db_storage）和已有访问材料 JSON，验证后连接已验证账号。旧 Reader 配置不会被覆盖。
3. 在“Bot 连接”勾选授权，生成配对码，然后点击“绑定宿主并导出配置”。
4. 把导出的 mcpServers.wechat 项加入支持本机 MCP 的宿主配置，重新加载工具。导出不含配对码、token 或微信密钥。
5. 保持连接器运行，在原有 Bot 对话中提问。停止、退出、改选账号或解除绑定后须重新验证、配对。

没有访问材料时显示未接通；本产品不提供扫码登录或自动取钥。接受 Reader 支持的路径到 key 映射、schema_version 2 的 salt 到 key 映射。材料需要私人权限；导入使用独立配置槽位，账号不合并。可读只表示本机验证通过，历史、附件和消息类型的覆盖以查询结果为准。

## 宿主与授权

网关固定在 127.0.0.1:17673，拒绝跨站浏览器请求。配对码 120 秒有效、一次使用，失败有速率限制。设备 token 是本次连接器会话授权，不是微信密钥；连接器只保存 token 哈希，官方宿主适配器把 token 保存在本机私人 host-session.json，POSIX 权限 600，Windows 使用私人 ACL。退出或撤销后旧 token 失效，不能在重启时恢复授权。

宿主只获得状态和有界的 search、topic、history、reply 查询。强制实时只读，每小时最多 60 次、同一时间一次；不能指定 Reader 路径、数据库路径、SQL 或写入命令。错误经过净化，密钥和 token 不返回模型。保留来源、时间、截断和覆盖缺口；消息里的指令是资料，不能执行。

Dot/Grok 等宿主须具备本机工具执行能力或已授权私有通道。纯云端网页对话不能直接访问 localhost。远程 stdio 宿主可手动使用已有 SSH 通道，Grok API 自定义函数由微信电脑上的调用方执行；项目不自动建立公网代理、购买模型服务或上传数据库。接入云端模型时，宿主应告知用户所选消息会提交给哪个服务。

## 开发与验收

使用带 Tkinter 的 Python 3.11+：
```sh
python -m pip install --only-binary=:all: sqlcipher3==0.6.2 zstandard==0.25.0
python projects/wechat-bot-connector/connector_runtime.py
python -m unittest discover -s projects/wechat-bot-connector/tests -v
```

--demo 和 --self-test-ui 使用虚构窗口；--self-test 只检查依赖。其他入口为 --mcp、--pair-host、--host-config、--host-query。

CI 用生成的 SQLite/SQLCipher 数据库验证导入、权限、账号切换、撤销、HTTP、实际 MCP 子进程和原生窗口。GUI 测试需 CONNECTOR_GUI_TEST=1 和图形会话。三个平台验证打包后的依赖、窗口、Reader worker 和 MCP 工具，再发布。包只含代码和依赖，不含用户材料、配置、聊天或报告。

用户微信版本兼容性和具体宿主登记须在对应环境验收。CI 通过不能证明所有微信版本或任意云端 Bot 都能直接使用。
