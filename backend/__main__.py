import os
import uvicorn
from backend.app import create_app

if __name__ == "__main__":
    os.umask(0o077)
    # 单进程、本机监听；不提供公网监听开关。
    uvicorn.run(create_app(), host="127.0.0.1", port=int(os.environ.get("WORKBENCH_PORT", "8788")),
                workers=1, access_log=False)
