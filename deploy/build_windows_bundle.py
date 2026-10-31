"""只使用 Git index 中的公开源码构建 Windows 包，不收集运行数据或凭据。"""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import zipfile


def git(repo: Path, *args: str) -> bytes:
    return subprocess.check_output(["git", "-C", str(repo), *args])


def build_repair(ob: Path, output: Path) -> None:
    replacement = git(ob, "show", ":deploy/windows_shared.py")
    manifest = {
        "accepted_sha256": ["ac75a94095d5937cb4257a5b2b6d8e333f034dc47e1b2c41f9ac75c6d3f1bef2"],
        "replacement_sha256": hashlib.sha256(replacement).hexdigest(),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as zipped:
        prefix = "OB-Windows-SID-Fix/"
        zipped.writestr(prefix + "Repair.cmd", git(ob, "show", ":deploy/Repair.cmd").replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
        zipped.writestr(prefix + "repair_windows.py", git(ob, "show", ":deploy/repair_windows.py"))
        zipped.writestr(prefix + "windows_shared.fixed.py", replacement)
        zipped.writestr(prefix + "repair-manifest.json", json.dumps(manifest, indent=2))
        zipped.writestr(prefix + "START-HERE.txt", "完整解压后双击 Repair.cmd，选择上次解压的 OB 安装包文件夹。\n只修复安装助手，不重装 Python，不删除数据，不修改微信。\n菜单打开后先选 1 安装依赖，再选 2 启动记忆库。\n".encode("utf-8-sig"))
    print(f"已构建小修复包 {output.name}。")


def build(ob: Path, cyber: Path, output: Path) -> None:
    base = "373ab17d283f1e3b304a6a36e17e9e8d44f1acfc"
    paths = git(cyber, "diff", "--cached", "--name-only", base, "--", "src").decode().splitlines()
    if not paths or any(not p.endswith(".js") for p in paths):
        raise ValueError("无法确认微信增量源码清单。")
    patch = git(cyber, "diff", "--cached", "--binary", base, "--", *paths)
    files = {}
    for prefix, repo in (("app", ob), ("reference/cyberboss", cyber)):
        for path in git(repo, "ls-files", "-z").decode().split("\0"):
            if path:
                files[f"{prefix}/{path}"] = git(repo, "show", f":{path}")
    files["cyberboss-shared.patch"] = patch
    files["cyberboss-files.json"] = json.dumps({
        "base": base, "source": "https://github.com/liyanjun921/cyberboss/pull/3",
        "paths": paths, "patch_sha256": hashlib.sha256(patch).hexdigest(),
        "result_sha256": {p: hashlib.sha256(git(cyber, "show", f":{p}")).hexdigest() for p in paths},
    }, indent=2).encode()
    files["Install.cmd"] = b'@echo off\r\ncall "%~dp0app\\deploy\\Install.cmd"\r\n'
    files["START-HERE.txt"] = files["app/docs/WINDOWS_SHARED_CHAT.md"]
    files["PACKAGE-MANIFEST.json"] = json.dumps({
        "version": git(ob, "show", ":VERSION").decode().strip(),
        "ob_tree": git(ob, "write-tree").decode().strip(),
        "cyberboss_tree": git(cyber, "write-tree").decode().strip(),
        "files": {p: hashlib.sha256(b).hexdigest() for p, b in sorted(files.items())},
    }, indent=2).encode()
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as zipped:
        for path, content in sorted(files.items()):
            zipped.writestr("OB-Shared-Memory-Windows/" + path, content)
    print(f"已构建 {output.name}，{len(files)} 个公开源码文件，未打包任何本机运行数据。")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--cyberboss", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--repair-output", type=Path)
    args = parser.parse_args()
    repository = Path(__file__).resolve().parents[1]
    build(repository, args.cyberboss, args.output)
    if args.repair_output:
        build_repair(repository, args.repair_output)
