"""只修复已知版本的本机安装助手，不修改微信代码、依赖或用户数据。"""

import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys


def repair(target: Path, payload: Path, manifest: dict) -> bool:
    replacement = payload.read_bytes()
    digest = hashlib.sha256(replacement).hexdigest()
    if digest != manifest["replacement_sha256"]:
        raise ValueError("修复文件校验失败，未修改原文件。")
    original = target.read_bytes()
    before = hashlib.sha256(original).hexdigest()
    if before == digest:
        return False
    if before not in manifest["accepted_sha256"]:
        raise ValueError("这不是已知的旧版安装助手，未覆盖；请确认选择了上次解压的安装包。")
    backup = target.with_name(target.name + ".before-sidfix-" + secrets.token_hex(4))
    with backup.open("xb") as output:
        output.write(original)
    temporary = target.with_name(target.name + ".sidfix-tmp-" + secrets.token_hex(4))
    try:
        temporary.write_bytes(replacement)
        if target.read_bytes() != original:
            raise RuntimeError("修复期间原文件有变化，已停止，未覆盖。")
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return True


def choose_installer() -> Path:
    import tkinter as tk
    from tkinter import filedialog

    window = tk.Tk()
    window.withdraw()
    window.attributes("-topmost", True)
    try:
        folder = filedialog.askdirectory(title="选择你上次解压 OB 安装包的文件夹（其中有 Install.cmd）")
    finally:
        window.destroy()
    if not folder:
        raise RuntimeError("已取消，没有修改。")
    root = Path(folder)
    candidates = [root / "app/deploy/windows_shared.py", root / "deploy/windows_shared.py"]
    candidates.extend(child / "app/deploy/windows_shared.py" for child in root.iterdir() if child.is_dir())
    matches = list(dict.fromkeys(path.resolve() for path in candidates if path.is_file()))
    if len(matches) != 1:
        raise ValueError("没有找到唯一的安装助手，请选择更里面一层、含 Install.cmd 的文件夹。")
    return matches[0]


def main() -> int:
    package = Path(__file__).resolve().parent
    manifest = json.loads((package / "repair-manifest.json").read_text(encoding="utf-8"))
    print("Python 已安装，无需重装。请选择上次解压的 OB 安装包文件夹。")
    target = choose_installer()
    changed = repair(target, package / "windows_shared.fixed.py", manifest)
    print("旧安装助手已备份并修复。" if changed else "安装助手已是修复版。")
    print("现在继续打开原安装菜单：先选 1 安装依赖，再选 2 启动记忆库。")
    return subprocess.call([sys.executable, str(target)], cwd=target.parents[1])


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError) as exc:
        print("修复未完成：", str(exc))
        input("按回车关闭，保留上方错误信息。")
        raise SystemExit(1)
