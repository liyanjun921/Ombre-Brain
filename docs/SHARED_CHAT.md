# OB 跨端共享对话 B · 开发版

本功能在原版 OB 上增加独立的对话档案与接入接口。没有引入 Serein，也没有改动 OB 的遗忘曲线、情感评分、记忆桶、dream、hold 或 grow。folio 的界面移植留在后续阶段。

## 数据分工

- 长期经历仍由模型通过原版 `hold`／`grow` 写入 Markdown 桶。
- 共享对话存放在 `<vault>/_shared_chat/archive.sqlite3`，保存已接入的 user／assistant 原话、角色、平台、窗口、发生时间和接收时间。
- 原版 `_sources` 证据层仍不可由模型回读；新对话档案没有绕开或重新开放这层证据。
- 本实例的共享范围由管理员固定；HTTP 调用不能通过参数切换到其他范围。不同所有者应使用不同实例与密钥。
- 同一个 `platform + conversation_id + message_id` 可安全重试。原话和时间有冲突时整批拒绝并回滚，不覆盖已保存消息。
- 第一版只归档文字；不自动上传附件、截图、健康、定位、系统提示词或工具返回。接入前应选择需要共享的伴侣工作区。

## 配置

先在全新测试实例验收，再接真实窗口。名称以 `ENVIRONMENT_VARIABLES.md` 为准。

```dotenv
OMBRE_SHARED_CHAT_ENABLED=true
OMBRE_SHARED_CHAT_TOKEN=<独立生成的至少32字符随机密钥>
OMBRE_SHARED_CHAT_SCOPE=companion
```

通过原有部署方式传入以上环境变量，数据根目录必须挂持久存储。使用现成的上游 Docker Hub 镜像不会包含这份二改，需要从本分支源码构建。源码用的 `deploy/docker-compose.yml` 已透传这三个变量；其他编排需要自行透传。

源码 Docker 路线在根目录执行 `docker compose --env-file deploy/.env -f deploy/docker-compose.yml up -d --build`。先填写原有必需密码和模型配置；对话桥默认关闭，启用需另填以上三个变量。本次执行环境没有 Docker，尚未完成镜像构建验收。

MCP 继续使用 OB 原有的 OAuth／静态 Token，与桥接 HTTP Token 分开。启用对话工具时 `mcp_require_auth` 必须为 true。服务重启后刷新客户端工具列表。

## GPT 官端接入

私人插件连接原有 `https://<实例>/mcp`，使用现有 MCP 鉴权。额外出现四个工具：

- `chat_append`：归档当前窗口真实发生的对话；每条需要稳定的 message_id、role、text、带时区的 created_at，speaker 可选。
- `chat_search`：按原话子串、平台、窗口、时间查询；空 query 查最近归档。结果按接收序号倒序，发生时间独立显示，延迟补传不会伪装成刚发生。
- `chat_read`：按查询结果中的数字 id 读取单条全文。查询片段若 truncated=true，必须读全文后才可称为完整原话。
- `chat_status`：查看成功归档条数和最后接收时间。

每个 GPT 窗口使用独立且稳定的 conversation_id；同一条消息重试使用相同 message_id、原话和发生时间。不要把改写后的摘要当成聊天原话，也不要补造其他窗口的内容。

普通 GPT 窗口不会仅因接入 MCP 就自动导出每条消息。第一版支持工具调用保存；只有实际保存的内容才可供微信读取。自动收集 GPT 全部聊天和官端事件唤醒不属于本次实现。

## 微信 Cyberboss 接入

使用配套 Cyberboss 开发分支，配置：

```dotenv
CYBERBOSS_OB_URL=https://<OB实例根地址>
CYBERBOSS_OB_TOKEN=<与OMBRE_SHARED_CHAT_TOKEN一致>
CYBERBOSS_OB_WORKSPACE_ROOT=<现有谢临川工作区的绝对路径>
CYBERBOSS_OB_TIMEOUT_MS=3000
```

只对指定工作区启用。发送前自动调用 `/api/shared-chat/context`，把 OB 原有记忆检索和其他会话最近四条已归档原话作为带来源的参考交给模型；失败时正常聊天，不编造检索结果。当前微信会话使用稳定的对话编号；换 Codex thread 不改变它。

用户文字在进入合并队列前按消息编号保存；助手只保存成功送达的气泡。失败、延迟发送和系统唤醒文本不冒充成功聊天。图片不自动上传。当前归档的是微信发送链路使用的气泡文字，格式可能已经过原有微信 Markdown 转换。

原版 OB 长期工具的主动写入仍需配置给微信的运行时。Codex 可在自己的配置中另接原有 `/mcp`，不要把桥接 HTTP Token 当作 MCP Token：

```toml
[mcp_servers.ombre_brain]
url = "https://<OB实例>/mcp"
bearer_token_env_var = "CYBERBOSS_OB_MCP_TOKEN"
```

`CYBERBOSS_OB_MCP_TOKEN` 放在运行 Codex 的服务端环境中，与 OB 的静态 MCP Token 对应，OB 选 token 或 hybrid 模式。该配置是显式接入步骤，本分支不会修改用户现有 Codex 配置文件。

补传队列在 `CYBERBOSS_STATE_DIR/ombre-outbox`。网络失败保留 .json，后台轮询时重试；一个周期最多 20 条，重试间隔至少 30 秒。请求超时默认 3 秒。冲突、格式错误移为 .blocked，不删除原话，也不堵住后续消息；.sent 只留去重回执，不含原话。`npm run doctor` 或微信 `/status` 可查看待传与隔离条数。

这不是绝对零丢失承诺：磁盘写满、权限错误、超大消息、收到消息前崩溃，以及送达微信与本地落盘之间的进程崩溃仍需诊断。状态会报告相关错误；不要把“没有报网络错”当成全部已同步。

## HTTP API

所有接口独立要求 `Authorization: Bearer <OMBRE_SHARED_CHAT_TOKEN>`，不接受 URL 密钥或 Dashboard Cookie。公网必须用 HTTPS。

- `POST /api/shared-chat/append`：`{platform, conversation_id, messages}`。
- `POST /api/shared-chat/search`：query、platform、conversation_id、since（含）、until（不含）、limit、before_id 可选。
- `GET /api/shared-chat/status`：成功归档状态。
- `POST /api/shared-chat/context`：`{query, conversation_id}`。返回 memory、memory_status、recent、injected=false；后者明确表示只是取回材料，宿主仍需实际交给模型。

每批最多 50 条、每条最多 64 KiB UTF-8，HTTP 请求最多 1 MiB。子串检索不是语义检索；长期语义检索继续使用原版 OB。没有模型 Key 时也能验证原话归档和查询，但真实长期记忆的压缩、向量化和语义召回必须另做实测。

## 备份与验收

原版 GitHub 同步和旧备份清单没有为新增 SQLite 档案扩展兼容保证。请停服后备份整个 `_shared_chat` 目录；运行中备份应使用 SQLite backup API，不能只复制活动数据库主文件。还需保留微信侧 outbox 和运行配置。不要把原话档案提交到代码仓库。

建议验收顺序：微信写入一条虚构消息 → GPT 查询并回读；GPT 写入一条虚构消息 → 微信上下文实际带入；断网后重启微信服务 → 补传并去重；同编号不同内容 → 明确冲突；重启 OB → 档案仍在。工具发现、HTTP 成功、材料交付和真实模型回复应分别验收。

目前源码测试覆盖档案、HTTP、MCP 和 Cyberboss 组装链路。真实用户 Windows、ChatGPT 插件安装及云端部署验收需要在目标环境完成，不能用本地测试代替。
