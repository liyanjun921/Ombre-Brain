"""Windows 本机共享记忆助手：独立安装、保留旧微信改动、显式连接。"""

from __future__ import annotations

import ctypes
import getpass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import tomllib
import urllib.error
import urllib.request
import venv
import webbrowser
import zipfile

SOURCE = Path(__file__).resolve().parents[1]
PACKAGE = SOURCE.parent
PORT = 18001
TUNNEL_PORT = 18002
MCP_NAME = "ombre_brain_shared"


def data_home() -> Path:
    return Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "OmbreBrainShared"


def write_new(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as output:
        output.write(content)


def secure_windows_directory(root: Path) -> None:
    # 只解析输出里的 ASCII SID，不解码可能含中文的账户名或计算机名。
    output = subprocess.check_output(["whoami.exe", "/user", "/fo", "csv", "/nh"], timeout=10)
    matches = re.findall(rb'\bS-1-(?:[0-9]+-)+[0-9]+\b', output)
    if len(matches) != 1:
        raise RuntimeError("未能读取 Windows 当前账户编号，未修改目录权限。")
    sid = matches[0].decode("ascii")
    # 先恢复当前账户的显式权限，再移除继承权限；可修复旧版初始化中断的目录。
    subprocess.run(["icacls.exe", str(root), "/grant:r",
                    f"*{sid}:(OI)(CI)F", "*S-1-5-18:(OI)(CI)F"],
                   check=True, stdout=subprocess.DEVNULL, timeout=10)
    subprocess.run(["icacls.exe", str(root), "/inheritance:r"],
                   check=True, stdout=subprocess.DEVNULL, timeout=10)


def initialize(root: Path) -> dict:
    root.mkdir(parents=True, exist_ok=True)
    # 先处理权限，再访问文件，避免旧版留下的目录权限让恢复流程提前失败。
    if os.name == "nt":
        secure_windows_directory(root)
    secret_file = root / "secrets.json"
    if not secret_file.exists():
        write_new(secret_file, json.dumps({
            "bridge_token": secrets.token_urlsafe(36),
            "mcp_token": secrets.token_urlsafe(36),
            "dashboard_password": secrets.token_urlsafe(24),
        }, indent=2))
        secret_file.chmod(0o600)
    credentials = json.loads(secret_file.read_text(encoding="utf-8"))
    if any(not isinstance(credentials.get(k), str) or len(credentials[k]) < 32
           for k in ("bridge_token", "mcp_token", "dashboard_password")):
        raise ValueError("本机密钥文件无效；不会自动覆盖，请保留文件并联系排查。")
    vault = root / "vault"
    vault.mkdir(exist_ok=True)
    config = vault / "config.yaml"
    if not config.exists():
        write_new(config, "transport: streamable-http\nembedding:\n  enabled: false\n")
    return credentials


def server_env(root: Path, credentials: dict) -> dict:
    # 清除父进程遗留的 OB 设置，防止误连另一份真实记忆库。
    env = {k: v for k, v in os.environ.items() if not k.startswith("OMBRE_")}
    env.update({
        "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8",
        "OMBRE_BIND_HOST": "127.0.0.1", "OMBRE_PORT": str(PORT),
        "OMBRE_TRANSPORT": "streamable-http",
        "OMBRE_BUCKETS_DIR": str(root / "vault"),
        "OMBRE_CONFIG_PATH": str(root / "vault" / "config.yaml"),
        "OMBRE_MCP_REQUIRE_AUTH": "true", "OMBRE_MCP_AUTH_MODE": "hybrid",
        "OMBRE_MCP_TOKEN": credentials["mcp_token"],
        "OMBRE_DASHBOARD_PASSWORD": credentials["dashboard_password"],
        "OMBRE_SHARED_CHAT_ENABLED": "true", "OMBRE_SHARED_CHAT_SCOPE": "companion",
        "OMBRE_SHARED_CHAT_TOKEN": credentials["bridge_token"],
    })
    return env


def python_path(root: Path) -> Path:
    return root / "venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def install(root: Path) -> None:
    initialize(root)
    if sys.version_info[:2] != (3, 12):
        raise RuntimeError("请用安装包的 Install.cmd 安装 Python 3.12 后打开。")
    python = python_path(root)
    if not python.exists():
        venv.EnvBuilder(with_pip=True).create(root / "venv")
    subprocess.run([str(python), "-m", "pip", "install", "--require-hashes", "-r",
                    str(SOURCE / "requirements.lock.txt")], check=True)
    print("依赖与本机数据目录已准备好。请选择启动记忆库。")


def local_json(root: Path, credentials: dict) -> dict:
    request = urllib.request.Request(f"http://127.0.0.1:{PORT}/api/shared-chat/status",
                                     headers={"Authorization": "Bearer " + credentials["bridge_token"]})
    with urllib.request.urlopen(request, timeout=3) as response:  # nosec B310
        return json.load(response)


def occupied(port: int) -> bool:
    with socket.socket() as connection:
        connection.settimeout(0.3)
        return connection.connect_ex(("127.0.0.1", port)) == 0


def new_console(args: list[str], *, env: dict, cwd: Path) -> subprocess.Popen:
    flags = subprocess.CREATE_NEW_CONSOLE if os.name == "nt" else 0
    return subprocess.Popen(args, env=env, cwd=cwd, creationflags=flags)


def start_server(root: Path, credentials: dict) -> None:
    if occupied(PORT):
        local_json(root, credentials)
        print("这一份记忆库已在运行，不会重复启动。")
        return
    python = python_path(root)
    if not python.exists():
        raise RuntimeError("请先选择安装依赖。")
    child = new_console([str(python), str(SOURCE / "src/server.py")],
                        env=server_env(root, credentials), cwd=SOURCE)
    for _ in range(90):
        if child.poll() is not None:
            raise RuntimeError("记忆库提前退出，请查看新打开的运行窗口。")
        try:
            local_json(root, credentials)
            print("本机记忆库已通过带密钥的状态检查。运行窗口需保持打开。")
            return
        except (OSError, ValueError):
            time.sleep(1)
    raise RuntimeError("90 秒内未就绪；保留运行窗口，先查看报错，不要重复启动。")


def select_path(title: str, *, directory: bool = False) -> Path:
    import tkinter as tk
    from tkinter import filedialog

    window = tk.Tk()
    window.withdraw()
    window.attributes("-topmost", True)
    try:
        result = (filedialog.askdirectory(title=title) if directory else
                  filedialog.askopenfilename(title=title))
    finally:
        window.destroy()
    if not result:
        raise RuntimeError("已取消，未修改。")
    return Path(result).resolve()


def patch_paths(manifest: dict) -> list[str]:
    paths = manifest["paths"]
    for name in paths:
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts or "\\" in name or ":" in name:
            raise ValueError("补丁清单包含非法路径。")
        if not name.startswith("src/") or not name.endswith(".js"):
            raise ValueError("安装器只允许修改清单里的微信源代码。")
    return paths


def apply_bridge(repo: Path, payload: Path, backups: Path) -> str:
    manifest = json.loads((payload / "cyberboss-files.json").read_text(encoding="utf-8"))
    paths = patch_paths(manifest)
    patch = payload / "cyberboss-shared.patch"
    if hashlib.sha256(patch.read_bytes()).hexdigest() != manifest["patch_sha256"]:
        raise ValueError("微信补丁校验失败，未修改。")
    for name in paths:
        target = repo / name
        if target.resolve().is_relative_to(repo.resolve()) is False:
            raise ValueError("微信代码路径越过所选目录，未修改。")
    if all((repo / p).is_file() and hashlib.sha256((repo / p).read_bytes()).hexdigest()
           == manifest["result_sha256"][p] for p in paths):
        return "already"
    if not shutil.which("git"):
        raise RuntimeError("未找到 Git for Windows，无法安全检查和应用补丁。")
    args = ["git", "-c", "core.autocrlf=false", "apply", "--whitespace=nowarn",
            "--ignore-space-change"]
    originals = {p: (repo / p).read_bytes() if (repo / p).exists() else None for p in paths}
    # 所有补丁操作先在隔离副本中进行，避免父目录的其他 Git 仓库影响相对路径。
    with tempfile.TemporaryDirectory() as temporary:
        candidate = Path(temporary)
        subprocess.run(["git", "init", "--quiet", str(candidate)], check=True, capture_output=True)
        for name in paths:
            if originals[name] is not None:
                target = candidate / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(originals[name])
        reverse = subprocess.run([*args, "--check", "--reverse", str(patch.resolve())],
                                 cwd=candidate, capture_output=True)
        if reverse.returncode == 0:
            return "already"
        check = subprocess.run([*args, "--check", str(patch.resolve())], cwd=candidate,
                               capture_output=True)
        if check.returncode == 0:
            subprocess.run([*args, str(patch.resolve())], cwd=candidate, check=True, capture_output=True)
            patched = {p: (candidate / p).read_bytes() for p in paths}
    if check.returncode:
        # 诊断只含固定源文件的校验值，不收集源码、配置、账号或对话。
        report = {"result": "patch_conflict", "files": {
            p: hashlib.sha256((repo / p).read_bytes()).hexdigest()
            if (repo / p).is_file() else "missing" for p in paths}}
        backups.mkdir(parents=True, exist_ok=True)
        (backups / "compatibility.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        raise RuntimeError("检测到现有微信定制与补丁不兼容，原文件未改动。请把 compatibility.json 发来排查。")
    if any(((repo / p).read_bytes() if (repo / p).exists() else None) != b
           for p, b in originals.items()):
        raise RuntimeError("兼容检查期间微信源文件发生变化，未应用补丁，请先停止修改后重试。")
    snapshot = backups / (time.strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(3))
    snapshot.mkdir(parents=True)
    previous = originals
    for name in paths:
        path = repo / name
        if previous[name] is not None:
            saved = snapshot / name
            saved.parent.mkdir(parents=True, exist_ok=True)
            saved.write_bytes(previous[name])
    (snapshot / "restore.json").write_text(json.dumps({
        "repo": str(repo.resolve()), "missing_before": [p for p, b in previous.items() if b is None],
        "paths": paths,
    }, indent=2), encoding="utf-8")
    try:
        # git apply 先整体校验；一旦失败再逐文件恢复，绝不使用强制覆盖。
        for name, content in patched.items():
            path = repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_name(path.name + ".ob-tmp-" + secrets.token_hex(4))
            try:
                temporary.write_bytes(content)
                os.replace(temporary, path)
            finally:
                temporary.unlink(missing_ok=True)
    except BaseException:
        for name, content in previous.items():
            path = repo / name
            if content is None:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes(content)
        raise
    print(f"微信源代码补丁已应用，原文件备份：{snapshot}")
    return "applied"


def add_codex_config(config: Path) -> bool:
    raw = config.read_bytes() if config.exists() else b""
    existing = tomllib.loads(raw.decode("utf-8-sig"))
    expected = {"url": f"http://127.0.0.1:{PORT}/mcp",
                "bearer_token_env_var": "CYBERBOSS_OB_MCP_TOKEN"}
    servers = existing.get("mcp_servers", {})
    if MCP_NAME in servers:
        if servers[MCP_NAME] != expected:
            raise RuntimeError("Codex 中已有同名不同配置，未覆盖。")
        return False
    # 顶层内联表不能在后面重新定义；检测到就停下，不重写其他 MCP 设置。
    addition = (f'\n[mcp_servers.{MCP_NAME}]\nurl = "{expected["url"]}"\n'
                'bearer_token_env_var = "CYBERBOSS_OB_MCP_TOKEN"\n')
    result = raw.decode("utf-8-sig") + addition
    tomllib.loads(result)
    config.parent.mkdir(parents=True, exist_ok=True)
    if config.exists():
        backup = config.with_name(config.name + ".ob-backup-" + secrets.token_hex(4))
        backup.write_bytes(raw)
    temporary = config.with_name(config.name + ".ob-tmp-" + secrets.token_hex(4))
    try:
        temporary.write_text(result, encoding="utf-8")
        os.replace(temporary, config)
    finally:
        temporary.unlink(missing_ok=True)
    return True


def configure_wechat(root: Path) -> None:
    print("请先关闭正在运行的微信助手窗口。接下来依次选：实际代码目录、伴侣工作区、原来的启动文件。")
    repo = select_path("选择实际微信代码目录（其中有 src/core/app.js）", directory=True)
    if not (repo / "src/core/app.js").is_file():
        raise ValueError("所选目录里没有 src/core/app.js，未修改。")
    workspace = select_path("选择现有伴侣工作区（通常 .cyberboss-xlc/workspace）", directory=True)
    launcher = select_path("选择你平时双击启动微信助手的 .cmd 或 .bat 文件")
    if launcher.suffix.lower() not in (".cmd", ".bat"):
        raise ValueError("请选择原来的 .cmd 或 .bat 启动文件，未修改。")
    apply_bridge(repo, PACKAGE, root / "wechat-code-backups")
    codex_home = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
    add_codex_config(codex_home / "config.toml")
    (root / "wechat.json").write_text(json.dumps({
        "repo": str(repo), "workspace": str(workspace), "launcher": str(launcher),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print("微信补丁和 Codex 工具配置已保存；以后从本助手选择启动微信。")


def start_wechat(root: Path, credentials: dict) -> None:
    local_json(root, credentials)
    configured = root / "wechat.json"
    if not configured.exists():
        raise RuntimeError("请先选择接入现有微信。")
    saved = json.loads(configured.read_text(encoding="utf-8"))
    env = {
        "CYBERBOSS_OB_URL": f"http://127.0.0.1:{PORT}",
        "CYBERBOSS_OB_TOKEN": credentials["bridge_token"],
        "CYBERBOSS_OB_WORKSPACE_ROOT": saved["workspace"],
        "CYBERBOSS_OB_MCP_TOKEN": credentials["mcp_token"],
    }
    # 由 Windows Shell 打开用户原启动文件，保留其中所有定制启动逻辑。
    previous = dict(os.environ)
    try:
        os.environ.update(env)
        os.startfile(saved["launcher"], cwd=str(Path(saved["launcher"]).parent))
    finally:
        os.environ.clear()
        os.environ.update(previous)
    print("已打开原来的微信启动文件。请选择原有启动选项；微信 /status 可检查接入状态。")


def download_tunnel(root: Path) -> Path:
    import platform

    arch = {"amd64": "amd64", "x86_64": "amd64", "arm64": "arm64", "aarch64": "arm64"}.get(
        platform.machine().lower())
    if not arch:
        raise RuntimeError("仅支持 Windows x64 或 ARM64。")
    request = urllib.request.Request("https://api.github.com/repos/openai/tunnel-client/releases/latest",
                                     headers={"User-Agent": "OmbreBrain-Local-Setup"})
    with urllib.request.urlopen(request, timeout=30) as response:  # nosec B310
        release = json.load(response)
    tag = release["tag_name"]
    if not re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+", tag):
        raise ValueError("无法识别官方隧道版本，未下载。")
    name = f"tunnel-client-{tag}-windows-{arch}.zip"
    asset = next(a for a in release["assets"] if a["name"] == name)
    digest = asset.get("digest", "")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise ValueError("官方附件没有 SHA-256 校验值，未下载。")
    url = asset["browser_download_url"]
    if url != f"https://github.com/openai/tunnel-client/releases/download/{tag}/{name}":
        raise ValueError("官方附件地址异常，未下载。")
    destination = root / "tools" / tag
    executable = destination / "tunnel-client.exe"
    checksum = destination / "executable.sha256"
    if executable.exists():
        if checksum.is_file() and hashlib.sha256(executable.read_bytes()).hexdigest() == checksum.read_text():
            return executable
        raise ValueError("隧道下载缓存不完整或已改变，请移走 tools 下该版本目录后重试。")
    with tempfile.TemporaryDirectory() as temporary:
        archive = Path(temporary) / name
        with urllib.request.urlopen(url, timeout=60) as response, archive.open("wb") as output:  # nosec B310
            total = 0
            while chunk := response.read(1024 * 1024):
                total += len(chunk)
                if total > 256 * 1024 * 1024:
                    raise ValueError("官方附件下载超过体积限制。")
                output.write(chunk)
        if hashlib.sha256(archive.read_bytes()).hexdigest() != digest.removeprefix("sha256:"):
            raise ValueError("官方隧道附件校验失败，未执行。")
        with zipfile.ZipFile(archive) as zipped:
            member = next(m for m in zipped.infolist()
                          if PurePosixPath(m.filename).name == "tunnel-client.exe")
            if member.file_size > 256 * 1024 * 1024:
                raise ValueError("官方程序超过体积限制。")
            destination.mkdir(parents=True, exist_ok=True)
            temporary_exe = destination / "tunnel-client.exe.part"
            try:
                with temporary_exe.open("wb") as output, zipped.open(member) as source:
                    shutil.copyfileobj(source, output)
                checksum.write_text(hashlib.sha256(temporary_exe.read_bytes()).hexdigest())
                os.replace(temporary_exe, executable)
            finally:
                temporary_exe.unlink(missing_ok=True)
    return executable


def connect_gpt(root: Path, credentials: dict) -> None:
    local_json(root, credentials)
    print("打开 OpenAI 的 Tunnels 页面；为当前 ChatGPT 工作区创建或复用隧道。")
    webbrowser.open("https://platform.openai.com/settings/organization/tunnels")
    tunnel_id = input("粘贴 tunnel_id（没有入口或权限就按回车退出）：").strip()
    if not tunnel_id:
        print("本机微信功能可继续使用；ChatGPT 远程接入尚未完成。")
        return
    if not re.fullmatch(r"tunnel_(?:[a-z0-9]{4}_)?[a-z0-9]{32}", tunnel_id):
        raise ValueError("tunnel_id 格式不正确。")
    if occupied(TUNNEL_PORT):
        raise RuntimeError("隧道状态端口已被占用；请先检查旧隧道窗口。")
    executable = download_tunnel(root)
    subprocess.run([str(executable), "help", "quickstart"], check=True)
    key = getpass.getpass("在本机粘贴具有 Tunnels Read + Use 的运行密钥（不回显、不保存）：").strip()
    if not key or "\n" in key or "\r" in key:
        raise ValueError("未提供有效运行密钥。")
    env = dict(os.environ)
    env.update({
        "CONTROL_PLANE_API_KEY": key, "CONTROL_PLANE_TUNNEL_ID": tunnel_id,
        "MCP_SERVER_URL": f"http://127.0.0.1:{PORT}/mcp",
        "OMBRE_MCP_TOKEN": credentials["mcp_token"],
        "MCP_EXTRA_HEADERS": "Ombre-MCP-Token: env:OMBRE_MCP_TOKEN",
        "MCP_DISCOVERY_EXTRA_HEADERS": "Ombre-MCP-Token: env:OMBRE_MCP_TOKEN",
        "HEALTH_LISTEN_ADDR": f"127.0.0.1:{TUNNEL_PORT}",
    })
    subprocess.run([str(executable), "doctor", "--explain"], env=env, check=True)
    child = new_console([str(executable), "run"], env=env, cwd=root)
    for _ in range(60):
        if child.poll() is not None:
            raise RuntimeError("隧道已退出，尚未连接 ChatGPT。")
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{TUNNEL_PORT}/readyz", timeout=2) as response:  # nosec B310
                if response.status == 200:
                    print("私有隧道已就绪；这还不等于 ChatGPT 工具调用验收成功。")
                    print("ChatGPT 添加自定义 MCP → Connection: Tunnel → 选择同一隧道。")
                    print("此私有入口选 No authentication；隧道运行器向本机注入独立 MCP 密钥。")
                    print("请让 GPT 调用 chat_status，再完成双方虚构消息写入、检索和回读。")
                    webbrowser.open("https://chatgpt.com/#settings/Connectors")
                    return
        except OSError:
            pass
        time.sleep(1)
    raise RuntimeError("隧道未在 60 秒内就绪，保留新窗口查看具体错误。")


def backup(root: Path) -> None:
    if occupied(PORT):
        raise RuntimeError("请先在记忆库运行窗口按 Ctrl+C 停服，再备份，避免数据库副本不一致。")
    destination = select_path("选择本机备份保存目录", directory=True)
    name = destination / ("OB-memory-backup-" + time.strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(3) + ".zip")
    inputs = [p for p in root.rglob("*") if p.is_file()
              and p.relative_to(root).parts[0] not in ("venv", "tools")]
    with zipfile.ZipFile(name, "x", compression=zipfile.ZIP_DEFLATED) as zipped:
        for path in inputs:
            zipped.write(path, str(path.relative_to(root)))
    print(f"本机记忆库、配置和凭据备份已保存：{name}")
    print("备份含私密内容，请自行保管。微信自身的 outbox 与原工作区还需另行备份。")


def main() -> None:
    if os.name != "nt":
        raise SystemExit("此入口供 Windows 使用。")
    ctypes.windll.kernel32.SetConsoleOutputCP(65001)
    root = data_home()
    credentials = initialize(root)
    while True:
        print("\nOB 本机共享记忆 B\n1 安装依赖\n2 启动记忆库\n3 接入现有微信（兼容检查与备份）"
              "\n4 用原来的启动文件打开微信\n5 连接 ChatGPT 私有隧道"
              "\n6 查看状态\n7 打开记忆管理页面\n8 停服后的本机备份\n0 退出安装助手")
        choice = input("选择编号：").strip()
        try:
            if choice == "0":
                return
            if choice == "1":
                install(root)
            elif choice == "2":
                start_server(root, credentials)
            elif choice == "3":
                configure_wechat(root)
            elif choice == "4":
                start_wechat(root, credentials)
            elif choice == "5":
                connect_gpt(root, credentials)
            elif choice == "6":
                status = local_json(root, credentials)
                print(json.dumps(status, ensure_ascii=False, indent=2))
                print("状态可用不代表已保存每个窗口的全部聊天。")
            elif choice == "7":
                local_json(root, credentials)
                print("管理密码仅在本机显示，请勿把这一屏截图发出：", credentials["dashboard_password"])
                webbrowser.open(f"http://127.0.0.1:{PORT}")
            elif choice == "8":
                backup(root)
            else:
                print("请选择菜单里的编号。")
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
            print("尚未完成：", str(exc))


if __name__ == "__main__":
    main()
