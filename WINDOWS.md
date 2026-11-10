# Windows 开发与使用

目标：Windows 11 x64，Edge / Chrome。ARM64 Windows 与真实 GPU、摄像头、麦克风仍需独立验收。

## 首次安装

先安装 Git、Python 3.12 x64（含 Python Launcher）与 Node.js 22.13+。登录有权限的 GitHub 账号，克隆 **源码仓库**（名称不带 `-showcase`）。展示仓库只有介绍，不能运行。

```powershell
git clone --recurse-submodules https://github.com/oniszczukthommy-wq/blue-pm-workbench.git
cd blue-pm-workbench
# 先安装 Python 3.12 x64，并启用 Python Launcher（py）
powershell -NoProfile -ExecutionPolicy Bypass -File .\Windows.ps1 Setup
powershell -NoProfile -ExecutionPolicy Bypass -File .\Windows.ps1 Test
```

脚本只对这一次执行放行，不修改系统执行策略。安装命令失败会停止；既有 `.env` 不会被覆盖。

「原版工具」中的 Agent 工坊和 AI 产品开发工具包以固定版本 Git 子模块提供。已有检出可运行 `git submodule update --init --recursive` 下载；Setup 在发现原版文件缺失时也会执行该步骤。ZIP 下载不包含子模块内容。

Agent 工坊使用独立数据目录 `data/agent-workshop/` 和独立模型配置，首次启动从当前工作台的 `.env` 创建一份配置，不会覆盖之后在工坊中自行修改的配置。两个产品列表不会自动同步。AI 产品开发工具包仍需在编码 AI 中打开其目录；上游主要支持 macOS，Windows 下完整流程尚未验证。工坊的 macOS 文件夹选择器在 Windows 不可用，请手动输入项目路径。不要将两个项目内的 `.env`、生成结果和用户项目提交到 Git。

## 日常启动

双击 `Start-Windows.cmd`，或运行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\Windows.ps1 Start
```

在浏览器打开 http://127.0.0.1:8788/ 。终端按 Ctrl+C 停止。

需安装 Node.js 22.13+。Setup 会使用 pnpm 11.25.0 构建界面。编辑本机 `.env` 配置模型密钥；模型调用需要自己的账号额度。原 macOS `.app` 和 launchd 安装器不适用于 Windows。固定版本 Harness 0.2.1rc2 的真实原生进程初始化已在 Windows 通过。Test 会启动并关闭该进程，使用测试占位 Key、不发送模型请求；付费模型会话仍需实机验收，不自动升级 Harness。

## 继续开发与迁移

- 在 Windows 用 VS Code 等编辑器打开源码目录，Python 解释器选择 `.venv\Scripts\python.exe`。
- 用 Git 同步源码；虚拟环境、node_modules、Mac 的二进制工具都要重新安装，不能直接复制。
- 迁移前停止旧服务，单独备份整个 `data/`、用户工程、上传素材与相关数据库附属文件。新机器恢复后保留原备份，再核对项目数量和附件。
- 密钥只在新机器本机填写；不要提交 `.env`、个人数据或模型权重。
- 后续更新执行 `git pull --ff-only`，重新 Setup 与 Test。若已有本机改动，先保存提交再更新。

## 验证边界

`.github/workflows/windows.yml` 在 Windows runner 上执行同一安装与测试入口。
自动测试不调用付费模型，不代表真实模型质量、GPU 加速或硬件输入已经验收。具体结果看仓库 Actions；首次运行失败时不得视为已支持。

Windows 数据目录依赖 NTFS 账户权限，POSIX 0600/0700 位在 Windows 不适用。请在自己的用户目录（例如 `$env:USERPROFILE\projects`）克隆和保存项目，不使用多账户共享目录。自动测试不把 POSIX 权限断言当成 Windows ACL 验证。

## 前四项目优先验收：本项目待办

安装后先运行 `Windows.ps1 Test`；必须同时看到原生 Harness 初始化通过和 Python 测试通过，再填写自己的 API 配置。随后用一个专门的测试产品检查聊天、产品拆解、PRD、方案与任务生成，确认导出包可打开；关闭并重启后确认该测试产品仍在。自动测试不代替真实模型内容质量验收。
