# 使用本机 Codex 额度整理 OB 记忆

此版让 OB 的整理任务调用你已安装、已用 ChatGPT 登录的官方 Codex CLI。不会把 Plus 变成通用 API Key，不读取、复制或上传 auth.json，不绕过额度。是否可用取决于该账号的 Codex 权限、额度、登录、网络和模型访问；不能承诺无限调用。

## 已装 B 版：只需升级原文件夹

1. 到 OB 正在运行的黑窗口按 Ctrl+C 停服，微信窗口可以保持。升级器检测到 18001 仍在监听时会停下，不会强制结束进程。
2. 完整解压小升级包，双击 `Upgrade.cmd`，选择原来含 Install.cmd 的文件夹，例如 `D:\OB-Shared-Memory-Windows-B\OB-Shared-Memory-Windows`。
3. 它核对旧文件哈希，在本机私有数据目录备份，然后只更新指定源码。遇到未知二改会整体停止。原话、记忆桶、账号、微信启动文件与依赖不会被换掉。
4. 升级后打开原菜单，选 **2 启动记忆库**；选 **7** 打开管理页，在设置的引擎区点击 **测试 Codex 订阅连接**。测试会消耗一次实际 Codex 调用。
5. 需要完整语义检索时选 **9 安装并启用本地向量**。首次下载 Ollama 与 bge-m3 合计约 2–3GB，模型需要额外内存；已有安装会复用。安装失败不会改成云端，也不会把失败说成已经就绪。

无需重装 Python 或已装依赖。新用户可以使用完整 Windows 安装包，首次默认选择订阅压缩。

## 费用和工作分工

- Codex 负责原版 OB 提示词要求的整理、压缩、打标、拆条及保守判断，继续使用原来的记忆语义和原文留存规则。
- 本地 bge-m3 负责向量化，不调用云端 Embedding；模型首次需要下载，以后仍需本机服务运行。
- OB 负责持久化、检索与 MCP/微信共享。接通整理模型不会自动把所有 GPT 历史导入，也不代表微信桥和远程 GPT 插件已经验收。
- Codex 一次只运行一个整理任务，沿用原有脱水缓存。额度不足、超时、旧 CLI、不完整输出会明确失败，不自动重试或回退付费 API。其它功能里若你曾单独配置了云端服务，其原配置仍保留，需要另外查看；这次没有重写其它模块的供应商。

## 设置与兼容

格式选择 **Codex 本机订阅**。`dehydration.codex_model` 留空使用 CLI 默认，另有 API `model` 不参与此模式。不要填 DeepSeek 等 API 模型名到 Codex 模型框。你可以在已有访问范围内显式填写 Codex 模型名；不固定某个未来可能停用的模型。

窗口升级器会备份并设置 `api_format: codex`，保留原 API 字段。`codex-subscription.json` 的 enabled=true 会在 Windows 启动时显式设置 OMBRE_COMPRESS_FORMAT=codex，防止旧 .env 在重启后把后端改回 API。若以后主动改用 API，需要将此标记改成 false，并按设置页提示保存、重启；不要删除真实记忆。

CLI 需支持 `exec --ignore-user-config --ephemeral --output-schema`；先检查本机登录确为 ChatGPT，再发起整理。只运行官方 CLI，不做兼容中转接口。Windows npm 的 codex.cmd 会定位同目录官方 JS，再交给 node 直接执行，避免命令行解释聊天原话。

如 CLI 版本太旧，先在你原来的 Codex 环境更新和确认微信仍能用，再进行 OB 测试。本次升级不会静默升级你用于微信的 Codex，也不会修改其 config.toml 或已有 app-server。

临时整理不加载用户 MCP 配置，关闭命令执行和联网搜索；不会递归调用 OB 或给微信发消息。提示词与正文经 stdin 传递，最终结果做结构和体积校验；不将 stdout/stderr 原文记入 OB 日志。CLI 自身的账号级运行记录仍受官方客户端行为约束，不承诺完全无日志。

## 验证边界

开发环境用虚构 CLI 子进程验证 Unicode/多行原话、命令字符不解释、独立模型、串行执行、不转发 API Key、拒绝 API 登录、旧版 CLI、失败、无重试、异常输出、超时与取消。官方当前 CLI 的帮助参数已核对。

开发者环境没有你的 Windows、真实 Codex 登录或本地 GPU，不能把这些测试当成账号和模型已连好。必须在目标电脑通过“测试 Codex 订阅连接”和本地向量实际请求，再通过真实 hold/grow、跨窗口检索和微信模型回复验收。升级前的虚构 API/MCP 回环不能代替本轮订阅模型验收。

官方文档：https://learn.chatgpt.com/docs/non-interactive-mode
