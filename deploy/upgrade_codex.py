"""对已知 B 版源码做可回退升级，配置订阅模式，保留用户数据。"""

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import secrets
import socket
import subprocess
import sys

from repair_windows import choose_installer


def apply_upgrade(app: Path, package: Path, data: Path) -> Path:
    import yaml

    manifest = json.loads((package / "upgrade-manifest.json").read_text(encoding="utf-8"))
    originals = {}
    replacements = {}
    for name, item in manifest["files"].items():
        relative = PurePosixPath(name)
        if relative.is_absolute() or ".." in relative.parts or "\\" in name or ":" in name:
            raise ValueError("升级清单路径无效，未修改。")
        target = app / name
        if not target.resolve().is_relative_to(app.resolve()):
            raise ValueError("升级目标越过源码目录，未修改。")
        before = target.read_bytes() if target.exists() else None
        old_hash = hashlib.sha256(before).hexdigest() if before is not None else None
        if old_hash != item["sha256"] and old_hash not in item["accepted"]:
            raise RuntimeError(f"{name} 存在不兼容的源码修改，已停止，没有覆盖。")
        content = (package / "payload" / name).read_bytes()
        if hashlib.sha256(content).hexdigest() != item["sha256"]:
            raise ValueError("升级文件校验失败，未修改。")
        originals[target] = before
        replacements[target] = content
    config = data / "vault/config.yaml"
    original_config = config.read_bytes()
    loaded = yaml.safe_load(original_config.decode("utf-8-sig")) or {}
    if not isinstance(loaded, dict) or not isinstance(loaded.get("dehydration", {}), dict):
        raise ValueError("现有记忆配置格式不正确，未修改。")
    dehy = loaded.setdefault("dehydration", {})
    dehy["api_format"] = "codex"
    dehy.setdefault("codex_model", "")
    dehy["timeout_seconds"] = max(180, float(dehy.get("timeout_seconds") or 180))
    originals[config] = original_config
    replacements[config] = yaml.safe_dump(loaded, allow_unicode=True, sort_keys=False).encode("utf-8")
    mode = data / "codex-subscription.json"
    originals[mode] = mode.read_bytes() if mode.exists() else None
    replacements[mode] = b'{"enabled":true}'
    backup = data / "code-upgrade-backups" / ("codex-" + secrets.token_hex(5))
    backup.mkdir(parents=True)
    records = []
    for index, (target, content) in enumerate(originals.items()):
        saved = f"{index}.original"
        if content is not None:
            (backup / saved).write_bytes(content)
        records.append({"target": str(target), "backup": saved if content is not None else None})
    (backup / "restore.json").write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    written = []
    try:
        for target, content in replacements.items():
            current = target.read_bytes() if target.exists() else None
            if current != originals[target]:
                raise RuntimeError("升级期间文件发生变化，已停止并恢复。")
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(target.name + ".codex-upgrade-" + secrets.token_hex(4))
            try:
                temporary.write_bytes(content)
                os.replace(temporary, target)
                written.append(target)
            finally:
                temporary.unlink(missing_ok=True)
    except BaseException:
        for target in reversed(written):
            content = originals[target]
            if content is None:
                target.unlink(missing_ok=True)
            else:
                target.write_bytes(content)
        raise
    return backup


def main() -> int:
    with socket.socket() as connection:
        connection.settimeout(0.3)
        if connection.connect_ex(("127.0.0.1", 18001)) == 0:
            raise RuntimeError("请先到 OB 运行黑窗口按 Ctrl+C 停服，再重新打开 Upgrade.cmd。无需关闭微信。")
    target = choose_installer()
    app = target.parents[1]
    data = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "OmbreBrainShared"
    backup = apply_upgrade(app, Path(__file__).resolve().parent, data)
    print("源码与配置已备份：", backup)
    print("已启用 Codex 订阅模式。先选 2 启动记忆库；再选 9 安装并启用本地向量。")
    print("菜单 7 打开管理页，可测试 Codex 订阅连接。无需重装 Python 或依赖。")
    return subprocess.call([sys.executable, str(target)], cwd=app)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError, ImportError) as exc:
        print("升级未完成：", str(exc))
        input("按回车关闭，保留上方错误信息。")
        raise SystemExit(1)
