# 官方 Harness 接入

确认时间：2026-10-28。用户明确：优先找 Claude Code 的 Harness 框架结构；若没有完整开源底座，
则找 DeepSeek 的。第一阶段只运行需求、PRD、方案和任务，之后再接自动写代码。

## 选择依据

Claude Code 官方仓库的 LICENSE 是保留权利并适用商业条款；Python Agent SDK 自身采用 MIT，
但 SDK 不等于底层完整运行时源码。因此采用用户指定的备选方向：DeepSeek 官方开源 Harness。

- Claude Code 许可：https://github.com/anthropics/claude-code/blob/main/LICENSE.md
- Claude Agent SDK 许可：https://github.com/anthropics/claude-agent-sdk-python/blob/main/LICENSE
- DeepSeek 官方 Harness：https://github.com/deepseek-ai/deepseek-harness
- 官方 Python SDK：https://github.com/deepseek-ai/deepseek-harness/tree/master/python/sdk

DSH 是 MIT 开源、插件化的真实 agent runtime，带模型循环、会话、事件和持久化；它仍处于开发者预览。
当前固定 SDK 与原生运行时为 `0.2.1rc2`，不直接追随 master，也不自动升级。
安装使用官方 SDK 与配套 macOS arm64 wheel，无需自行把整座源码仓库改造成服务。

## 本轮实际使用的结构

用户 → 工作台 FastAPI → 业务任务与版本检查 → HarnessRunner → 官方 DSH SDK → 本机 dsh 子进程 → DeepSeek。

DSH 负责真实模型运行、输出事件和本机会话日志。工作台仍负责多产品数据、访谈消息、PRD 确认与版本、
方案和开发任务的业务关系、后台队列、失败重试、ZIP 导出。Blueprint 保留需求拆解、组件选型、
领域建模、方案和审稿模板；方案采用固定的分阶段流程，每个模型步骤由 DSH 执行，避免嵌套两个 Agent 循环。
本阶段不是通用自主编程平台，也没有开放产品数据库任意写入工具。

## 代码入口

- `backend/harness.py`：适配原 Runner.complete 接口，结构化输出、一次纠错、预算、超时和停机。
- `backend/harness-profile.yml`：基于官方 sdk-minimal 的配置覆盖。
- `backend/engine.py`：从本机原有配置读取模型和 Key，默认且唯一真实运行入口改为 HarnessRunner。
- `backend/jobs.py`：任务记录中保存 runtime、模型、session ids、profile SHA-256 和用量完整性。
- `data/harness/<运行ID>/`：各任务独立的 Harness home 和空工作目录，私人数据不提交到 Git。

不静默回退到原来的直接模型 SDK；Harness 不可用时如实失败、保留旧文档。
Key 沿用原配置，不写入 profile、提示词、会话正文或验收报告。

## 第一期配置

已在真实原生二进制中验证，而不是只检查 YAML：

- 模型可调用工具数为 0；禁用 shell、文件/图片读写、工作目录工具、MCP。
- 不加载额外插件，不开放系统命令或自动代码修改。
- 禁止额外上传 session log 和插件清单；正常模型推理必需的上下文仍发送给 DeepSeek。
- 关闭自动压缩与 Harness 自动网络重试，避免隐性额外模型调用。
- 运行时 cwd、home 独立，避免自动读取项目根目录的 `.env`；只传入运行所需的最小环境。
- 模型单次输出上限 16384 token；单任务最多 16 次模型响应、200000 累计 token；每次调用总等待上限 180 秒。
- 一次格式纠错；人民币硬预算尚未确定，token 阈值不是精确的金额封顶。

调用取消或超时会关闭 SDK/运行时。若请求已发出，用量可能仍发生；未收到用量时标记
`token_usage_complete=false`，不伪装为完整的零用量，且禁止该 runner 继续请求。
用量映射读取 DSH 的消息事件：输入含未命中、缓存读和缓存写；reasoning 不在输出之外重复累计。

## 验证

`scripts/verify_harness_profile.py` 使用本机模拟 Messages SSE 端点启动真实 DSH 二进制，
断言工具列表为空、额外日志字段不存在，模型调用为本地模拟，绝不作为真实模型通过的证据。

`tests/workbench/test_harness.py` 使用假 client 验证结构化纠错、预算、停止/超时、用量映射、
无工具的固定方案流程、持久化 runtime/session 记录与失败不提交文档。

真实模型证据只保存在本机，不上传 GitHub。其样例产品独立于之前的产品，未覆盖原有文档。
技术样例内的 PRD 确认只验证接口行为，不代替本人认可生成内容。

## 后续写代码

本轮不开放。后续需另定允许访问的项目目录、可用命令、执行环境、工具审批与恢复方式，
再以新 profile 接入；不能把官方最小 profile 的 danger-full-access 默认值直接开放给产品用户。
