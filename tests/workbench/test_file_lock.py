"""Verify cross-process exclusion and recovery, including Windows msvcrt."""
import subprocess
import sys
from backend import file_lock


def test_lock_excludes_other_process_and_releases(tmp_path):
    path = tmp_path / "server.lock"
    code = "from backend import file_lock; import sys; f=open(sys.argv[1], 'a'); file_lock.acquire(f); file_lock.release(f)"
    with path.open("a") as handle:
        file_lock.acquire(handle)
        result = subprocess.run([sys.executable, "-c", code, str(path)], capture_output=True)
        assert result.returncode != 0
        assert b"BlockingIOError" in result.stderr
        file_lock.release(handle)
        result = subprocess.run([sys.executable, "-c", code, str(path)], capture_output=True)
        assert result.returncode == 0, result.stderr
