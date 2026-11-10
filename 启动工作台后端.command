#!/bin/zsh
set -eu
cd "${0:A:h}"
if [[ ! -x .venv/bin/python ]]; then
  echo '未找到 Python 虚拟环境，请按 docs/workbench/README.md 的安装说明配置。'
  read -r '?按回车退出'
  exit 1
fi
exec .venv/bin/python scripts/local_workbench.py open
