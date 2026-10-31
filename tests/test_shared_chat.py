"""虚构消息覆盖跨端档案、鉴权、回环、重启和上下文交付边界。"""

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from mcp.server.fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.routing import Route

from ombrebrain.storage.shared_chat import MessageConflict, SharedChatStore
from tools import shared_chat as tools
from web import shared_chat as web


def message(message_id="m1", **extra):
    return {"message_id": message_id, "role": "user", "text": "虚构测试：明天讨论蓝色车票。\n原话第二行",
            "created_at": "2026-10-31T12:00:00+08:00", **extra}


def test_roundtrip_restart_scopes_and_literal_search(tmp_path):
    store = SharedChatStore(tmp_path)
    receipt = store.append("wechat", "wx-1", [message()])
    assert receipt["inserted"] == 1
    assert store.append("wechat", "wx-1", [message()])["duplicates"] == 1
    reopened = SharedChatStore(tmp_path)
    found = reopened.search(query="蓝色车票", since="2026-10-31T00:00:00+08:00")
    assert found["messages"][0]["platform"] == "wechat"
    assert reopened.read(receipt["ids"][0])["message"]["text"] == message()["text"]
    assert reopened.search(query="%_ OR 1=1")["messages"] == []
    assert SharedChatStore(tmp_path, "other").status()["messages"] == 0
    assert SharedChatStore(tmp_path, "other").read(receipt["ids"][0])["message"] is None


def test_conflict_rolls_back_entire_batch(tmp_path):
    store = SharedChatStore(tmp_path)
    store.append("wechat", "wx-1", [message()])
    with pytest.raises(MessageConflict):
        store.append("wechat", "wx-1", [message("m2"), message(text="另一句")])
    assert store.status()["messages"] == 1


@pytest.mark.parametrize("change", [
    {"role": "system"}, {"role": "tool"}, {"created_at": "2026-10-31"},
    {"text": "字" * 30000}, {"text": ""}, {"unknown": True},
])
def test_rejects_non_dialogue_invalid_and_oversized_messages(tmp_path, change):
    store = SharedChatStore(tmp_path)
    with pytest.raises(ValueError):
        store.append("chatgpt", "gpt-1", [message(**change)])
    assert store.status()["messages"] == 0


def test_concurrent_retries_and_page_boundaries(tmp_path):
    store = SharedChatStore(tmp_path)
    with ThreadPoolExecutor(max_workers=12) as pool:
        list(pool.map(lambda _: store.append("wechat", "wx", [message()]), range(40)))
    assert store.status()["messages"] == 1
    store.append("chatgpt", "gpt", [message("m2"), message("m3")])
    page = store.search(limit=2)
    following = store.search(limit=2, before_id=page["next_before_id"])
    assert len(page["messages"]) == 2 and len(following["messages"]) == 1
    assert store.search(exclude_conversation_id="gpt")["messages"][0]["conversation_id"] == "wx"


class Routes:
    def __init__(self):
        self.routes = []

    def custom_route(self, path, methods):
        def register(handler):
            self.routes.append(Route(path, handler, methods=methods))
            return handler
        return register


@pytest.fixture
def bridge(monkeypatch, tmp_path):
    monkeypatch.setenv("OMBRE_SHARED_CHAT_ENABLED", "true")
    monkeypatch.setenv("OMBRE_SHARED_CHAT_TOKEN", "x" * 32)
    monkeypatch.setattr(web.sh, "config", {"buckets_dir": str(tmp_path)})
    monkeypatch.setattr(web, "_admit_hook_request", lambda _: True)
    routes = Routes()
    web.register(routes)
    return Starlette(routes=routes.routes)


def test_http_auth_atomic_conflict_and_context_delivery(bridge, monkeypatch):
    async def recall(**kwargs):
        assert kwargs["query"] == "车票"
        return "虚构长期记忆：她收藏了蓝色车票。"
    monkeypatch.setattr(web.breath, "dispatch", recall)

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=bridge), base_url="http://test") as client:
            assert (await client.get("/api/shared-chat/status")).status_code == 401
            assert (await client.get("/api/shared-chat/status?token=" + "x" * 32)).status_code == 401
            client.headers["Authorization"] = "Bearer " + "x" * 32
            payload = {"platform": "wechat", "conversation_id": "wx", "messages": [message()]}
            assert (await client.post("/api/shared-chat/append", json=payload)).json()["inserted"] == 1
            # GPT 查询微信；GPT 写入再由微信上下文取回，两个方向都是真正读写。
            found = (await client.post("/api/shared-chat/search", json={"query": "车票"})).json()
            assert found["messages"][0]["conversation_id"] == "wx"
            payload.update(platform="chatgpt", conversation_id="gpt")
            assert (await client.post("/api/shared-chat/append", json=payload)).status_code == 200
            context = (await client.post("/api/shared-chat/context", json={"query": "车票", "conversation_id": "wx"})).json()
            assert context["injected"] is False
            assert context["memory_status"] == "ok" and "长期记忆" in context["memory"]
            assert context["recent"][0]["platform"] == "chatgpt"
            payload["messages"] = [message(text="冲突")]
            assert (await client.post("/api/shared-chat/append", json=payload)).status_code == 409
            assert (await client.post("/api/shared-chat/append", json=[])).status_code == 400
            assert (await client.post("/api/shared-chat/search", json={"scope": "other"})).status_code == 400
    asyncio.run(run())


def test_disabled_routes_and_memory_degradation(bridge, monkeypatch):
    async def fail(**kwargs):
        raise RuntimeError("虚构离线")
    monkeypatch.setattr(web.breath, "dispatch", fail)

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=bridge), base_url="http://test",
                                     headers={"Authorization": "Bearer " + "x" * 32}) as client:
            result = (await client.post("/api/shared-chat/context", json={"query": "车票"})).json()
            assert result["memory_status"] == "unavailable" and result["memory"] == ""
            monkeypatch.setenv("OMBRE_SHARED_CHAT_ENABLED", "false")
            assert (await client.get("/api/shared-chat/status")).status_code == 404
    asyncio.run(run())


def test_mcp_optional_tools_real_dispatch(tmp_path, monkeypatch):
    monkeypatch.setattr(tools.rt, "config", {"buckets_dir": str(tmp_path)})
    monkeypatch.setenv("OMBRE_SHARED_CHAT_ENABLED", "false")
    mcp = FastMCP("test")
    tools.register(mcp)
    assert asyncio.run(mcp.list_tools()) == []
    monkeypatch.setenv("OMBRE_SHARED_CHAT_ENABLED", "true")
    tools.register(mcp)
    names = {item.name for item in asyncio.run(mcp.list_tools())}
    assert names == {"chat_append", "chat_search", "chat_read", "chat_status"}

    async def run():
        result = await mcp.call_tool("chat_append", {"platform": "chatgpt", "conversation_id": "gpt",
                                                   "messages": [message()]})
        assert "inserted" in str(result)
        result = await mcp.call_tool("chat_search", {"query": "车票"})
        assert "chatgpt" in str(result)
        result = await mcp.call_tool("chat_read", {"id": 1})
        blocks = result[0] if isinstance(result, tuple) else result
        assert json.loads(blocks[0].text)["message"]["text"] == message()["text"]
    asyncio.run(run())


def test_mcp_refuses_anonymous_archive_access(tmp_path, monkeypatch):
    monkeypatch.setenv("OMBRE_SHARED_CHAT_ENABLED", "true")
    monkeypatch.setattr(tools.rt, "config", {"buckets_dir": str(tmp_path), "mcp_require_auth": False})
    with pytest.raises(ValueError, match="mcp_require_auth"):
        tools.register(FastMCP("test"))
