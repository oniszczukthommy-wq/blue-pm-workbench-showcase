"""Initialize the actual pinned native runtime without sending a model request."""
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.harness import HarnessRunner

with tempfile.TemporaryDirectory(prefix="blue-runtime-") as temporary:
    runner = HarnessRunner(api_key="runtime-check-not-a-real-key", model="deepseek-flash",
                           root=Path(temporary), request_timeout=30)
    try:
        runner.client.start()
        print("PASS: actual Harness process initialized; no model request sent")
    finally:
        runner.close()
