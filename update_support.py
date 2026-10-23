"""更新器与主程序共用的安装锁；仅依赖标准库，可在 Qt 加载前使用。"""

import os
import time
from pathlib import Path


class InstallationLock:
    """持有文件字节锁直到 close；进程异常退出时由系统释放。"""

    def __init__(self, root):
        self.path = Path(root) / "update_temp" / "install.lock"
        self._file = None

    def acquire(self, timeout=0):
        import msvcrt

        if self._file is not None:
            return True
        self.path.parent.mkdir(parents=True, exist_ok=True)
        stream = open(self.path, "a+b")
        if stream.tell() == 0:
            stream.write(b"\0")
            stream.flush()
        deadline = time.monotonic() + timeout
        while True:
            try:
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                self._file = stream
                return True
            except OSError:
                if time.monotonic() >= deadline:
                    stream.close()
                    return False
                time.sleep(0.2)

    def close(self):
        if self._file is not None:
            self._file.close()
            self._file = None


def incomplete_update_path(root):
    return os.path.join(root, "update_temp", "install-incomplete.json")
