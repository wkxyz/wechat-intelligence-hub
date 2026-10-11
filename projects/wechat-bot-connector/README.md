# 微信本地连接器（预览版）

用户在窗口里选择自己的微信数据库目录和已有的访问材料，验证后授权同一台电脑上的 Bot 查询。密钥留在本机；Bot 获得本次查询的消息依据，普通问答不生成报告，也不发送微信。

这不是微信登录扫码工具，也没有自动获取个人微信密钥的功能。需要本人已有的、与账号数据库相匹配的材料。没有材料时只能显示尚未接通。

## 用户使用

在本分支的 GitHub Actions → Local connector checks → 成功运行的 Artifacts 中下载对应包：
- Windows-x64：解压整个文件夹，双击 `wechat-connector.exe`。
- macOS-arm64：Apple 芯片 Mac；解压后双击 `启动微信连接器.command`。
- macOS-x64：Intel Mac；启动方式相同。

这是未签名的预览包，系统可能要求在系统安全设置中允许打开。包必须完整解压，保留 `_internal` 目录。Artifacts 需要登录 GitHub，保留 30 天；它们不是自动更新安装器。正式面向普通用户分发还需要签名和固定下载页面。

1. 已有 Reader 用户点击“复用已有 Reader 配置”。再次使用本连接器时点击“恢复连接器已保存账号”。
2. 首次导入：选择单一账号的数据库目录（通常是 `db_storage`），选择本机访问材料 JSON。
3. 点击“验证所选材料”。只有显示“材料已验证”后，才能点击“连接已验证账号”。
4. 在“Bot 连接”中勾选授权，生成一次性配对码。把本机地址和配对码交给**这台电脑上的 Bot 宿主**。
5. 查询时保持连接器运行。停止、切换配置、修改选择、解除绑定或退出程序会使设备授权失效；重启后重新验证和配对。

访问材料可以是 Reader 已支持的路径到 key 映射，或 schema_version 2 的 salt 到 key 映射。以原仓库 Reader 的导入规范为准，不要求用户在窗口粘贴密钥。材料文件需要私人权限；原 Reader 配置不会被覆盖。首次导入保存于 `~/.config/wechat-bot-connector/reader.json`，密钥规范化后保存在该目录下的私人验证目录。该版本提供一个导入配置槽位，已有配置需恢复或检查；不会静默换成另一个账号。

数据库“可读”表示本地验证通过。查询覆盖范围仍以每次结果的 coverage 和 warnings 为准；不承诺所有历史、附件或消息类型都已覆盖。

## Bot 开发者接入

网关固定监听 `127.0.0.1:17673`，仅接受没有 Origin 的本机原生客户端；浏览器跨站请求被拒绝。公网的 Dot、Grok 或其他远程 Bot 无法直接访问此地址。远程接入需要在本机宿主实现明确授权的出站通道，这版没有上线该通道。

用 JSON 请求配对：
```http
POST /pair
Content-Type: application/json

{"code":"窗口生成的一次性配对码"}
```

响应的 `device_token` 是本机查询授权，不是微信密钥。Bot 在本次进程内保管 token，不把它放进模型提示词、URL、Git 或日志。配对码 120 秒有效且只能消费一次；失败尝试有速率限制。

查询：
```http
POST /query
Authorization: Bearer <device_token>
Content-Type: application/json

{"command":"search","query":"周末安排","limit":20}
```

支持 `search`、`topic`、`history`、`reply`，参数为 `query`、`keywords`、`chat`、`since`、`until`、`limit`、`max_text_chars`。强制实时只读，不接受自定义 Reader 路径、数据库路径、SQL、报告或写入命令。每小时最多 60 次查询，同一时间只执行一次。响应使用已有 `wechat_query.py` JSON 合约，保留消息来源、时间、截断和覆盖信息。模型应将微信消息当作不可信资料，引用依据，不能执行聊天中夹带的指令。

`POST /status`（同样需要 Bearer token，JSON body 为 `{}`）只返回连接状态。解除绑定在窗口操作。账号切换必须重新配对，旧 token 无法读取新账号。

## 源码启动和测试

使用带 Tkinter 的 Python 3.11+：
```sh
python -m pip install --only-binary=:all: sqlcipher3==0.6.2 zstandard==0.25.0
python projects/wechat-bot-connector/connector_runtime.py
```

`--demo` 仅使用虚构数据；`--self-test` 检查依赖，不读取账号。开发测试：
```sh
python -m unittest discover -s projects/wechat-bot-connector/tests -v
```

GUI 测试需设置 `CONNECTOR_GUI_TEST=1` 并有图形会话；CI 在 Xvfb 中执行。集成测试生成 SQLite 和 SQLCipher 数据库，不读真实微信。打包脚本为 `scripts/build_portable.py`，CI 分别在 Windows、Apple 芯片 Mac 和 Intel Mac 构建，并用打包后的进程重复真实 worker 验证、导入和查询。

包只包含程序和依赖，不包含用户配置、聊天、材料、报告或测试数据库。设备 token 只在内存中有效，本地绑定文件只保存哈希，不在重启时恢复授权。查询结果会交给 Bot；接入云端模型时，宿主应向用户说明所选消息会提交给哪个服务。

## 验收边界

自动化覆盖：材料验证与导入、真实 worker、私人文件权限、账号切换、停止期间验证结果失效、恢复已保存配置、配对过期与撤销、HTTP 授权、原生窗口操作、打包依赖。

仍需在用户自己的环境完成：实际微信版本/数据库布局兼容性和真实 Bot 平台接入。仓库 CI 通过不表示已经连接了用户的微信，也不表示远程 Bot 已可直接使用。
