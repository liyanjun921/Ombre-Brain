"""订阅升级和本地向量菜单的隔离验收，不下载真实模型。"""

import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sys

import pytest
import yaml


DEPLOY = Path(__file__).resolve().parents[1] / "deploy"


def module(name):
    sys.path.insert(0, str(DEPLOY))
    try:
        spec = importlib.util.spec_from_file_location(name, DEPLOY / (name + ".py"))
        loaded = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(loaded)
        return loaded
    finally:
        sys.path.pop(0)


def sample(tmp_path):
    app = tmp_path / "原安装包/app"
    package = tmp_path / "upgrade"
    data = tmp_path / "private-data"
    (app / "src").mkdir(parents=True)
    (package / "payload/src").mkdir(parents=True)
    (data / "vault").mkdir(parents=True)
    (app / "src/a.py").write_bytes(b"old source")
    (package / "payload/src/a.py").write_bytes(b"new source")
    (data / "vault/config.yaml").write_text("custom: 保留\ndehydration:\n  model: kept-api-model\n  api_key: fake-key\nembedding:\n  enabled: false\n", encoding="utf-8")
    (data / "vault/real-memory.md").write_bytes(b"must stay")
    (package / "upgrade-manifest.json").write_text(json.dumps({"files": {"src/a.py": {
        "sha256": hashlib.sha256(b"new source").hexdigest(),
        "accepted": [hashlib.sha256(b"old source").hexdigest()]}}}))
    return app, package, data


def test_upgrade_preserves_data_api_settings_and_original_backup(tmp_path):
    upgrade = module("upgrade_codex")
    app, package, data = sample(tmp_path)
    original = (data / "vault/config.yaml").read_bytes()
    backup = upgrade.apply_upgrade(app, package, data)
    assert (app / "src/a.py").read_bytes() == b"new source"
    config = yaml.safe_load((data / "vault/config.yaml").read_text())
    assert config["dehydration"]["api_format"] == "codex"
    assert config["dehydration"]["model"] == "kept-api-model"
    assert config["dehydration"]["api_key"] == "fake-key"
    assert config["custom"] == "保留"
    assert (data / "vault/real-memory.md").read_bytes() == b"must stay"
    assert original in [p.read_bytes() for p in backup.glob("*.original")]
    assert json.loads((data / "codex-subscription.json").read_text())["enabled"]
    upgrade.apply_upgrade(app, package, data)


def test_unknown_custom_source_aborts_before_any_change(tmp_path):
    upgrade = module("upgrade_codex")
    app, package, data = sample(tmp_path)
    config = (data / "vault/config.yaml").read_bytes()
    (app / "src/a.py").write_bytes(b"unknown custom source")
    with pytest.raises(RuntimeError, match="不兼容"):
        upgrade.apply_upgrade(app, package, data)
    assert (data / "vault/config.yaml").read_bytes() == config
    assert not (data / "codex-subscription.json").exists()


def test_upgrade_write_failure_rolls_back_applied_files(tmp_path, monkeypatch):
    upgrade = module("upgrade_codex")
    app, package, data = sample(tmp_path)
    original_config = (data / "vault/config.yaml").read_bytes()
    replace = upgrade.os.replace

    def fail(source, target):
        if Path(target).name == "config.yaml":
            raise OSError("虚构写入失败")
        replace(source, target)

    monkeypatch.setattr(upgrade.os, "replace", fail)
    with pytest.raises(OSError):
        upgrade.apply_upgrade(app, package, data)
    assert (app / "src/a.py").read_bytes() == b"old source"
    assert (data / "vault/config.yaml").read_bytes() == original_config


def test_local_embedding_menu_only_tests_after_local_mode_is_effective(tmp_path, monkeypatch):
    setup = module("windows_shared")
    credentials = setup.initialize(tmp_path)
    monkeypatch.setattr(setup, "local_json", lambda *_: {"ok": True})
    requests = []
    responses = {
        "/auth/login": {"ok": True}, "/api/embedding/local/env": {"ok": True, "installed": False},
        "/api/embedding/local/install": {"ok": True},
        "/api/embedding/local/install/status": {"install": {"phase": "done"}},
        "/api/embedding/local/start": {"ok": True},
        "/api/embedding/local/status?model=bge-m3": {"ok": True, "has_model": False},
        "/api/embedding/local/pull": {"ok": True},
        "/api/embedding/local/pull/status": {"pull": {"status": "success"}},
        "/api/embedding/migrate": {"ok": True},
        "/api/embedding/migrate/status": {"status": {"phase": "completed"}},
        "/api/config": {"embedding": {"api_format": "ollama"}},
        "/api/test/embedding": {"ok": True},
    }

    class Opener:
        def open(self, request, **_):
            path = request.full_url.split(":18001", 1)[1]
            requests.append(path)
            return io.BytesIO(json.dumps(responses[path]).encode())

    monkeypatch.setattr(setup.urllib.request, "build_opener", lambda *_: Opener())
    setup.setup_local_embedding(tmp_path, credentials)
    assert requests[-2:] == ["/api/config", "/api/test/embedding"]
    requests.clear()
    responses["/api/config"] = {"embedding": {"api_format": "openai_compat"}}
    with pytest.raises(RuntimeError, match="不调用云端测试"):
        setup.setup_local_embedding(tmp_path, credentials)
    assert "/api/test/embedding" not in requests
