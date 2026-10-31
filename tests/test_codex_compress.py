"""使用虚构 CLI 子进程验收订阅适配，不调用真实账号或模型。"""

import asyncio
import json
import sys

import pytest

from dehydrator import Dehydrator
from ombrebrain.integrations import codex_compress as bridge


@pytest.fixture
def fake_cli(tmp_path, monkeypatch):
    program = tmp_path / "fake_codex.py"
    program.write_text('''import sys,json,os,time
from pathlib import Path
args=sys.argv[1:]
mode=os.environ.get("CODEX_TEST_MODE", "ok")
if args == ["exec","--help"]:
 print("--ignore-user-config --ephemeral --output-schema" if mode != "old" else "old cli");sys.exit()
if args == ["login","status"]:
 print("Logged in using API key" if mode == "api" else "Logged in using ChatGPT");sys.exit()
if mode == "slow": time.sleep(30)
if mode == "failure": sys.exit(17)
assert "OPENAI_API_KEY" not in os.environ and "CODEX_API_KEY" not in os.environ
assert "CYBERBOSS_OB_TOKEN" not in os.environ
assert 'forced_login_method="chatgpt"' in args
payload=json.loads(sys.stdin.buffer.read())
result=payload["input"]
if mode == "malformed": result=None
if mode == "empty": result=""
if mode == "large": result="x"*300000
Path(args[args.index("--output-last-message")+1]).write_text(json.dumps({"result":result}),encoding="utf-8")
''', encoding="utf-8")
    monkeypatch.setattr(bridge, "resolve_command", lambda _: [sys.executable, str(program)])
    monkeypatch.setenv("OPENAI_API_KEY", "fake-must-not-use")
    monkeypatch.setenv("CODEX_API_KEY", "fake-must-not-use")
    monkeypatch.setenv("CYBERBOSS_OB_TOKEN", "fake-must-not-forward")
    return program


@pytest.mark.asyncio
async def test_real_child_handles_unicode_stdin_and_no_paid_credentials(fake_cli, tmp_path):
    marker = tmp_path / "must-not-exist"
    text = f'虚构原话\n"; touch {marker}; $(echo ignored) & 末行'
    assert await bridge.chat("只整理正文", text) == text
    assert not marker.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode,error", [("api", "ChatGPT 登录"), ("old", "隔离执行"),
                                          ("failure", "整理失败"), ("malformed", "空结果"),
                                          ("empty", "空结果"), ("large", "完整结果")])
async def test_rejects_unready_auth_cli_failures_and_invalid_output(fake_cli, monkeypatch, mode, error):
    monkeypatch.setenv("CODEX_TEST_MODE", mode)
    with pytest.raises(RuntimeError, match=error):
        await bridge.chat("虚构指令", "虚构原话")


@pytest.mark.asyncio
async def test_timeout_and_cancellation_stop_owned_child(fake_cli, monkeypatch):
    monkeypatch.setenv("CODEX_TEST_MODE", "slow")
    with pytest.raises(RuntimeError, match="超时"):
        await bridge.chat("虚构", "虚构", timeout=0.05)
    task = asyncio.create_task(bridge.chat("虚构", "虚构"))
    await asyncio.sleep(0.2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_codex_dehydrator_uses_separate_model_and_never_api_or_automatic_retry(tmp_path, monkeypatch):
    active = peak = calls = 0

    async def chat(system, user, **kwargs):
        nonlocal active, peak, calls
        assert kwargs["model"] == "subscription-model"
        active += 1
        peak = max(peak, active)
        calls += 1
        await asyncio.sleep(0.01)
        active -= 1
        return "虚构结果"

    monkeypatch.setattr(bridge, "chat", chat)
    engine = Dehydrator({"buckets_dir": str(tmp_path), "dehydration": {
        "api_format": "codex", "model": "api-model-kept", "codex_model": "subscription-model"}})
    assert engine.api_available and engine.client is None
    assert await asyncio.gather(*(engine._chat("虚构", "虚构") for _ in range(3))) == ["虚构结果"] * 3
    assert peak == 1 and calls == 3

    async def fail(*_, **__):
        nonlocal calls
        calls += 1
        raise RuntimeError("虚构额度不足")

    monkeypatch.setattr(bridge, "chat", fail)
    with pytest.raises(RuntimeError, match="额度不足"):
        await engine._chat("虚构", "虚构")
    assert calls == 4
    engine.close()


def test_resolves_npm_cmd_without_shell(tmp_path, monkeypatch):
    shim = tmp_path / "codex.cmd"
    entry = tmp_path / "node_modules/@openai/codex/bin/codex.js"
    entry.parent.mkdir(parents=True)
    entry.write_text("// 虚构官方入口结构")
    monkeypatch.setattr(bridge.shutil, "which", lambda name: "node.exe" if name == "node" else str(shim))
    assert bridge.resolve_command("codex") == ["node.exe", str(entry)]


def test_real_api_mode_unchanged_without_key(tmp_path):
    engine = Dehydrator({"buckets_dir": str(tmp_path)})
    assert not engine.api_available and engine.client is None
    engine.close()


def test_codex_cache_changes_when_subscription_model_changes(tmp_path):
    engine = Dehydrator({"buckets_dir": str(tmp_path), "dehydration": {"api_format": "codex"}})
    first = engine._content_key("虚构原话")
    engine.codex_model = "another-subscription-model"
    assert engine._content_key("虚构原话") != first
    engine.close()


@pytest.mark.asyncio
async def test_dashboard_switches_to_codex_without_key_and_tests_selected_backend(tmp_path, monkeypatch):
    import web.config_api as api

    engine = Dehydrator({"buckets_dir": str(tmp_path)})
    runtime = {"dehydration": {}}
    monkeypatch.setattr(api.sh, "_require_auth", lambda _: None)
    monkeypatch.setattr(api.sh, "config", runtime)
    monkeypatch.setattr(api.sh, "dehydrator", engine)

    class MCP:
        routes = {}

        def custom_route(self, path, methods):
            def decorate(fn):
                for method in methods:
                    self.routes[(method, path)] = fn
                return fn
            return decorate

    class Request:
        def __init__(self, body):
            self.body = body

        async def json(self):
            return self.body

    mcp = MCP()
    api.register(mcp)
    response = await mcp.routes[("POST", "/api/config")](Request({"dehydration": {
        "api_format": "codex", "codex_model": "subscription-model"}}))
    assert response.status_code == 200
    assert engine.api_available and engine.client is None
    assert engine.codex_model == "subscription-model"

    async def chat(*_, **__):
        return "OK"

    monkeypatch.setattr(bridge, "chat", chat)
    tested = await mcp.routes[("POST", "/api/test/dehydration")](Request({}))
    assert json.loads(tested.body)["ok"]
    runtime["dehydration"]["api_format"] = "openai_compat"
    rejected = await mcp.routes[("POST", "/api/test/dehydration")](Request({"expected_format": "codex"}))
    assert rejected.status_code == 400
    assert "尚未保存" in json.loads(rejected.body)["error"]
    engine.close()
