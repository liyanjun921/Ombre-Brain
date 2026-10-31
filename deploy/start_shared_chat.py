"""空实例安全启动：首次关闭未配置的向量服务，已有配置永不覆盖。"""

import os
from pathlib import Path
import sys

INITIAL_CONFIG = "transport: streamable-http\nembedding:\n  enabled: false\n"


def initialize(config_path: Path) -> bool:
    config_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        fd = os.open(config_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return False
    with os.fdopen(fd, "w", encoding="utf-8") as output:
        output.write(INITIAL_CONFIG)
        output.flush()
        os.fsync(output.fileno())
    return True


if __name__ == "__main__":
    configured = os.environ.get("OMBRE_CONFIG_PATH", "")
    if not configured or not Path(configured).is_absolute():
        raise SystemExit("需要设置指向持久磁盘的绝对路径 OMBRE_CONFIG_PATH")
    initialize(Path(configured))
    server = Path(__file__).resolve().parents[1] / "src" / "server.py"
    os.execv(sys.executable, [sys.executable, str(server)])
