"""从暂存源码和两个已知版本构建本机订阅升级包，不含用户数据。"""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import zipfile

BASES = ("f9a48974a025ad07224c545d74d80efb8d9e2ed2", "27ca3a3d6734b61e23e1d5bf5e5c2bbcdd98f429")


def git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args])


def build(repo: Path, output: Path) -> None:
    paths = git(repo, "diff", "--cached", "--name-only", BASES[-1], "--", "src", "frontend", "deploy/windows_shared.py", "VERSION", "update_manifest.json").decode().splitlines()
    manifest = {"files": {}}
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as zipped:
        prefix = "OB-Codex-Upgrade/"
        for name in paths:
            content = git(repo, "show", ":" + name)
            accepted = []
            for base in BASES:
                old = subprocess.run(["git", "-C", str(repo), "show", base + ":" + name], capture_output=True)
                digest = hashlib.sha256(old.stdout).hexdigest() if old.returncode == 0 else None
                if digest not in accepted:
                    accepted.append(digest)
            manifest["files"][name] = {"sha256": hashlib.sha256(content).hexdigest(), "accepted": accepted}
            zipped.writestr(prefix + "payload/" + name, content)
        for name in ("upgrade_codex.py", "repair_windows.py", "Upgrade.cmd"):
            zipped.writestr(prefix + name, git(repo, "show", ":deploy/" + name))
        zipped.writestr(prefix + "upgrade-manifest.json", json.dumps(manifest, indent=2))
        zipped.writestr(prefix + "START-HERE.txt", git(repo, "show", ":docs/CODEX_SUBSCRIPTION.md"))
    print(f"已构建 {output.name}，只更新 {len(paths)} 个源码与版本文件。")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    build(Path(__file__).resolve().parents[1], args.output)
