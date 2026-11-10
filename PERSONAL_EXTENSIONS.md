# 个人二次开发说明 · Blue PM 工作台

本仓库在开源项目 **[agent-blueprint](https://github.com/hubooooooo/agent-blueprint)**（MIT 许可，根目录 `LICENSE`；第三方技能声明见 `licenses/`）之上二次开发。引擎层 `blueprint/`、`s01`–`s17` 组件参考实现与上游 README 原文保留在下文及对应目录；本文件只记录 **本人新增 / 改造** 的部分，避免读者把上游能力误记为个人作品。

## 改造目标

把「一句话点子 → 方案推导引擎」包装成单人本机可用的产品经理工作台：研究、访谈、PRD、方案、任务在同一条流程里闭环，且 AI 不直接改写正式文档。

## 个人新增 / 改造清单

### 1. 产品助理聊天首页（`backend/assistant.py`、`workbench-ui/`）
- 默认首页为产品助理聊天；未立项也能自由讨论，选中产品后引用其文档与任务，按话题保留会话。
- 输入框可选「问题定义 / 优先级与范围 / PRD 审查」三种产品方法，或手动引用当前文档与段落。
- 聊天结论可保存为文档库条目；联网拆解、PRD 草稿、方案、任务生成均由用户确认触发；聊天本身不直接修改正式 PRD。

### 2. 需求访谈与人工确认 PRD（`backend/jobs.py`、`backend/app.py`）
- 访谈 job 产出草稿；`/prd/confirm` 确认后才进入正式 PRD；`/prd/import` 支持 Markdown 导入。
- AI 对 PRD、需求草稿、技术方案的修改先展示差异，确认后保存新版本；`draft / prd / plan / tasks` 版本链按 `prd_id` 校验一致性，旧稿覆盖新稿时返回 409。

### 3. 文档库与任务看板（`backend/store.py`、`backend/workspace.py`）
- 产品、文档、对话结论支持回收站与恢复；有关联产物的文档阻止单独移除；全文搜索覆盖产品、文档正文、任务与历史对话。
- 方案生成后派生开发任务；任务可更新状态；PRD 或方案升版后旧任务自动标记 `stale`；生成记录保留调用次数与 token 用量，支持重试。

### 4. 联网产品拆解（`backend/research.py`、`backend/teardown.py`）
- 多路联网研究汇总为拆解报告，可导出带 CSP 的自包含 HTML；研究快照挂在 job 上可追溯。

### 5. 模型运行层与成本护栏（`backend/harness.py`）
- 固定 DeepSeek 官方 Harness SDK `0.2.1rc2`；预算上限 `max_calls=16, max_tokens=200000`，上一轮用量不完整则不再发起下一轮。
- SDK 环境显式隔离：清空无关环境变量，仅保留 PATH / TZ 等必要项，避免把其他服务凭据传给运行时；运行时解包缓存用后回收。

### 6. 本机安全边界（`backend/app.py`）
- 仅监听本机；Host 白名单 + Origin 校验 + `sec-fetch-site` 检查，防 DNS rebinding；请求体 450 KB 上限；响应带 `no-store / nosniff / no-referrer`。
- 导出 zip 只含文档版本与任务状态，不含密钥、数据库与引擎缓存。

## 上游保留与复用

| 上游部分 | 位置 | 处理方式 |
| --- | --- | --- |
| Agent Blueprint 方案推导引擎与 17 个 Harness 组件参考 | `blueprint/`、`s01_*`–`s17_*` | 保留 MIT 原文与 `BLUEPRINT.md` |
| Simin Lab 技能参考 | `skills/`、`licenses/Simin-Lab-MIT.txt` | 保留版权声明；数据仍用本项目 SQLite 与不可变版本，未启用其文件 / 命令执行工具 |
| 安装与启动脚本 | `setup.sh`、`*.command` | 按工作台依赖锁定版本（Python 3.12、Node 22.13、pnpm 11.25.0） |

## 仓库内阅读顺序

1. 本文件（改造边界）→ 2. `README.md` 前半段工作台使用说明 → 3. `docs/workbench/README.md` 本机使用说明 → 4. 下文上游 agent-blueprint 原始文档（引擎方法论）。
