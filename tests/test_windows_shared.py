"""本机安装器的安全边界，所有操作均在隔离虚构目录内。"""

import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import tomllib

import pytest


spec = importlib.util.spec_from_file_location(
    "windows_shared", Path(__file__).resolve().parents[1] / "deploy/windows_shared.py")
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)


def test_credentials_config_and_parent_environment_are_isolated(tmp_path, monkeypatch):
    root = tmp_path / "local"
    first = setup.initialize(root)
    config = root / "vault/config.yaml"
    custom = "transport: streamable-http\ncustom: 保留\n"
    config.write_text(custom, encoding="utf-8")
    assert setup.initialize(root) == first
    assert config.read_text(encoding="utf-8") == custom
    assert len(set(first.values())) == 3
    monkeypatch.setenv("OMBRE_VAULT_DIR", "/another-real-vault")
    monkeypatch.setenv("OMBRE_MCP_REQUIRE_AUTH", "false")
    env = setup.server_env(root, first)
    assert "OMBRE_VAULT_DIR" not in env
    assert env["OMBRE_BIND_HOST"] == "127.0.0.1"
    assert env["OMBRE_MCP_REQUIRE_AUTH"] == "true"
    assert env["OMBRE_BUCKETS_DIR"] == str(root / "vault")
    assert env["OMBRE_SHARED_CHAT_TOKEN"] != env["OMBRE_MCP_TOKEN"]


def test_invalid_saved_credentials_are_never_replaced(tmp_path):
    setup.initialize(tmp_path)
    path = tmp_path / "secrets.json"
    path.write_text('{"bridge_token":"broken"}', encoding="utf-8")
    previous = path.read_bytes()
    with pytest.raises(ValueError):
        setup.initialize(tmp_path)
    assert path.read_bytes() == previous


def test_codex_preserves_comments_tools_and_bytes_on_repeat_or_conflict(tmp_path):
    config = tmp_path / "config.toml"
    original = '# 原有定制\nmodel = "example"\n[mcp_servers.existing]\ncommand = "example"\n'
    config.write_text(original, encoding="utf-8")
    assert setup.add_codex_config(config)
    saved = config.read_bytes()
    assert saved.startswith(original.encode())
    assert tomllib.loads(saved.decode())["mcp_servers"]["existing"]["command"] == "example"
    assert setup.add_codex_config(config) is False
    assert config.read_bytes() == saved
    assert len(list(tmp_path.glob("*.ob-backup-*"))) == 1
    config.write_text('[mcp_servers.ombre_brain_shared]\nurl = "https://custom.example/mcp"\n')
    saved = config.read_bytes()
    with pytest.raises(RuntimeError):
        setup.add_codex_config(config)
    assert config.read_bytes() == saved


def make_patch(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    subprocess.run(["git", "init", "--quiet", str(source)], check=True)
    path = source / "src/core/app.js"
    path.parent.mkdir(parents=True)
    path.write_text("// 原有文件\nconst original = 1;\n" + "// 保持上下文\n" * 15, encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=source, check=True)
    path.write_text(path.read_text() + "const bridge = true;\n", encoding="utf-8")
    new = source / "src/services/bridge.js"
    new.parent.mkdir()
    new.write_text("module.exports = {};\n")
    subprocess.run(["git", "add", "-N", str(new)], cwd=source, check=True)
    patch = subprocess.check_output(["git", "diff", "--binary"], cwd=source)
    payload = tmp_path / "payload"
    payload.mkdir()
    (payload / "cyberboss-shared.patch").write_bytes(patch)
    paths = ["src/core/app.js", "src/services/bridge.js"]
    (payload / "cyberboss-files.json").write_text(json.dumps({
        "paths": paths, "patch_sha256": hashlib.sha256(patch).hexdigest(),
        "result_sha256": {p: hashlib.sha256((source / p).read_bytes()).hexdigest() for p in paths},
    }))
    repo = tmp_path / "installed-custom"
    (repo / "src/core").mkdir(parents=True)
    original = subprocess.check_output(["git", "show", ":src/core/app.js"], cwd=source)
    (repo / "src/core/app.js").write_bytes(original)
    return repo, payload


@pytest.mark.parametrize("crlf", [False, True])
def test_patch_preserves_custom_code_backups_and_is_idempotent(tmp_path, crlf):
    repo, payload = make_patch(tmp_path)
    app = repo / "src/core/app.js"
    custom = b"// additional unrelated custom code\n" + app.read_bytes()
    if crlf:
        custom = custom.replace(b"\n", b"\r\n")
    app.write_bytes(custom)
    backups = tmp_path / "backups"
    assert setup.apply_bridge(repo, payload, backups) == "applied"
    assert b"additional unrelated custom code" in app.read_bytes()
    assert b"const bridge = true;" in app.read_bytes()
    snapshot = next(backups.glob("*/restore.json")).parent
    assert (snapshot / "src/core/app.js").read_bytes() == custom
    assert json.loads((snapshot / "restore.json").read_text())["missing_before"] == ["src/services/bridge.js"]
    saved = app.read_bytes()
    assert setup.apply_bridge(repo, payload, backups) == "already"
    assert app.read_bytes() == saved


def test_patch_conflict_or_digest_error_never_changes_original(tmp_path):
    repo, payload = make_patch(tmp_path)
    app = repo / "src/core/app.js"
    app.write_text("// Completely different customized program\n")
    original = app.read_bytes()
    backups = tmp_path / "backups"
    with pytest.raises(RuntimeError, match="不兼容"):
        setup.apply_bridge(repo, payload, backups)
    assert app.read_bytes() == original
    assert not (repo / "src/services/bridge.js").exists()
    report = json.loads((backups / "compatibility.json").read_text())
    assert "Completely" not in json.dumps(report)
    (payload / "cyberboss-shared.patch").write_bytes(b"tampered")
    with pytest.raises(ValueError, match="校验失败"):
        setup.apply_bridge(repo, payload, backups)
    assert app.read_bytes() == original


def test_patch_rolls_back_originals_if_file_replacement_fails(tmp_path, monkeypatch):
    repo, payload = make_patch(tmp_path)
    app = repo / "src/core/app.js"
    original = app.read_bytes()
    replace = setup.os.replace

    def fail_second(source, target):
        if str(target).endswith("bridge.js"):
            raise OSError("虚构磁盘错误")
        replace(source, target)

    monkeypatch.setattr(setup.os, "replace", fail_second)
    with pytest.raises(OSError):
        setup.apply_bridge(repo, payload, tmp_path / "backups")
    assert app.read_bytes() == original
    assert not (repo / "src/services/bridge.js").exists()


def test_backup_refuses_live_database(monkeypatch, tmp_path):
    monkeypatch.setattr(setup, "occupied", lambda _: True)
    with pytest.raises(RuntimeError, match="停服"):
        setup.backup(tmp_path)
