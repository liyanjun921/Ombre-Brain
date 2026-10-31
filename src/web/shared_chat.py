"""对话桥 HTTP API；独立 Bearer 鉴权，不接受 Cookie 或 URL 密钥。"""

import asyncio
import json
import os

from starlette.responses import JSONResponse

from ombrebrain.storage.shared_chat import MAX_BATCH_BYTES, MessageConflict
from tools import breath
from tools.shared_chat import enabled, get_store
from . import _shared as sh
from .hooks import _admit_hook_request

_CONTEXT_SLOTS = asyncio.Semaphore(2)
_CONTEXT_TIMEOUT = 12


def _authorize(request):
    if not enabled(sh.config):
        return JSONResponse({"error": "跨端对话桥未启用"}, status_code=404)
    token = os.environ.get("OMBRE_SHARED_CHAT_TOKEN", "")
    auth = request.headers.get("authorization", "").split(" ", 1)
    supplied = auth[1] if len(auth) == 2 and auth[0].lower() == "bearer" else ""
    if len(token) < 32 or not supplied or not sh._constant_time_text_equal(token, supplied):
        return JSONResponse({"error": "需要有效的对话桥 Bearer Token"}, status_code=401)
    return None


async def _body(request):
    data = bytearray()
    async for part in request.stream():
        data.extend(part)
        if len(data) > MAX_BATCH_BYTES:
            raise ValueError("请求体超过 1 MiB")
    body = json.loads(data)
    if not isinstance(body, dict):
        raise ValueError("请求体必须是 JSON 对象")
    return body


def register(mcp):
    @mcp.custom_route("/api/shared-chat/append", methods=["POST"])
    async def append(request):
        denied = _authorize(request)
        if denied is not None:
            return denied
        try:
            body = await _body(request)
            if set(body) != {"platform", "conversation_id", "messages"}:
                raise ValueError("必须且仅提供 platform、conversation_id、messages")
            result = await asyncio.to_thread(get_store(sh.config).append, **body)
            return JSONResponse(result)
        except MessageConflict as exc:
            return JSONResponse({"error": str(exc)}, status_code=409)
        except (ValueError, TypeError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    @mcp.custom_route("/api/shared-chat/search", methods=["POST"])
    async def search(request):
        denied = _authorize(request)
        if denied is not None:
            return denied
        try:
            body = await _body(request)
            allowed = {"query", "platform", "conversation_id", "since", "until", "limit", "before_id"}
            if set(body) - allowed:
                raise ValueError("查询含未知字段")
            return JSONResponse(await asyncio.to_thread(get_store(sh.config).search, **body))
        except (ValueError, TypeError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    @mcp.custom_route("/api/shared-chat/status", methods=["GET"])
    async def status(request):
        denied = _authorize(request)
        if denied is not None:
            return denied
        return JSONResponse(await asyncio.to_thread(get_store(sh.config).status))

    @mcp.custom_route("/api/shared-chat/context", methods=["POST"])
    async def context(request):
        denied = _authorize(request)
        if denied is not None:
            return denied
        try:
            body = await _body(request)
            if set(body) - {"query", "conversation_id"}:
                raise ValueError("上下文请求含未知字段")
            query = body.get("query", "")
            conversation_id = body.get("conversation_id", "")
            if not isinstance(query, str) or len(query) > 1000:
                raise ValueError("query 最长 1000 字符")
            if not isinstance(conversation_id, str) or len(conversation_id) > 200:
                raise ValueError("conversation_id 最长 200 字符")
            if not _admit_hook_request(request):
                return JSONResponse({"error": "请求过于频繁"}, status_code=429)
            try:
                await asyncio.wait_for(_CONTEXT_SLOTS.acquire(), timeout=0.01)
            except asyncio.TimeoutError:
                return JSONResponse({"error": "检索繁忙，请稍后重试"}, status_code=429)
            try:
                memory, memory_status = "", "ok"
                try:
                    memory = await asyncio.wait_for(breath.dispatch(
                        query=query, max_tokens=1200, max_results=3
                    ), timeout=_CONTEXT_TIMEOUT)
                except Exception:
                    memory_status = "unavailable"
                recent = await asyncio.to_thread(
                    get_store(sh.config).search, limit=4, exclude_conversation_id=conversation_id
                )
                # 分区返回，宿主不能把历史文字冒充成本轮用户的输入。
                return JSONResponse({"ok": True, "memory": memory, "memory_status": memory_status,
                                     "recent": recent["messages"], "injected": False,
                                     "coverage": recent["note"]})
            finally:
                _CONTEXT_SLOTS.release()
        except (ValueError, TypeError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
