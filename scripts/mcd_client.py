"""Fixed-endpoint, read-only McDonald's MCP client. Python standard library only."""
from __future__ import annotations

import ctypes
import json
import os
import urllib.error
import urllib.request
from pathlib import Path

ENDPOINT = "https://mcp.mcd.cn"
ALLOWED_TOOLS = frozenset({"now-time-info", "query-my-account", "query-my-coupons", "mall-points-products", "mall-product-detail"})
ARGUMENTS = {
    "now-time-info": set(), "query-my-account": set(),
    "query-my-coupons": {"page", "pageSize"},
    "mall-points-products": {"catRuleIds"}, "mall-product-detail": {"spuId"},
}


class McdError(Exception):
    """A message safe to display without credentials or raw server responses."""


def validate_call(name, arguments):
    if name not in ALLOWED_TOOLS:
        raise McdError("该工具不在只读白名单中，已阻止请求。")
    if not isinstance(arguments, dict) or set(arguments) - ARGUMENTS[name]:
        raise McdError("查询参数不符合第一版支持范围。")
    if name == "query-my-coupons":
        for key, limit in [("page", 5), ("pageSize", 200)]:
            value = arguments.get(key, "1" if key == "page" else "200")
            if not isinstance(value, str) or not value.isascii() or not value.isdigit() or not 1 <= int(value) <= limit:
                raise McdError("券分页参数超出允许范围。")
    if name == "mall-points-products" and arguments.get("catRuleIds") != "1>4":
        raise McdError("第一版只查询商品券类目1>4。")
    if name == "mall-product-detail" and (type(arguments.get("spuId")) is not int or arguments["spuId"] <= 0):
        raise McdError("请从真实商品列表中选择有效商品ID。")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class McdClient:
    def __init__(self, token, timeout=25):
        if not isinstance(token, str) or not token.strip() or any(c.isspace() for c in token):
            raise McdError("MCP Token为空或格式不正确。")
        self._headers = {"Authorization": "Bearer " + token, "Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
        self._opener = urllib.request.build_opener(NoRedirect)
        self._timeout = timeout
        self._sequence = 0
        self.calls = []

    def _rpc(self, method, params=None, notification=False):
        self._sequence += 1
        payload = {"jsonrpc": "2.0", "method": method}
        if not notification:
            payload["id"] = self._sequence
        if params is not None:
            payload["params"] = params
        request = urllib.request.Request(ENDPOINT, data=json.dumps(payload).encode(), headers=self._headers, method="POST")
        try:
            with self._opener.open(request, timeout=self._timeout) as response:
                session = response.headers.get("Mcp-Session-Id")
                if session:
                    self._headers["Mcp-Session-Id"] = session
                if notification:
                    return {}
                if "text/event-stream" in response.headers.get("Content-Type", ""):
                    size, data = 0, []
                    for raw in response:
                        size += len(raw)
                        if size > 4 * 1024 * 1024:
                            raise McdError("响应过大，请缩小查询范围。")
                        line = raw.decode("utf-8").rstrip("\r\n")
                        if line.startswith("data:"):
                            data.append(line[5:].lstrip(" "))
                        elif not line and data:
                            event = "\n".join(data)
                            data.clear()
                            if event == "[DONE]":
                                continue
                            message = json.loads(event)
                            if not isinstance(message, dict):
                                raise McdError("MCP事件响应结构不完整。")
                            if message.get("id") == payload["id"]:
                                return self._checked(message)
                    raise McdError("未收到完整MCP响应。")
                raw = response.read(4 * 1024 * 1024 + 1)
                if len(raw) > 4 * 1024 * 1024:
                    raise McdError("响应过大，请缩小查询范围。")
                message = json.loads(raw)
                if not isinstance(message, dict) or message.get("id") != payload["id"]:
                    raise McdError("MCP响应编号不匹配。")
                return self._checked(message)
        except urllib.error.HTTPError as error:
            raise McdError(f"服务返回HTTP {error.code}；401请检查Token，429请稍后再查。") from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise McdError("连接失败或超时，本次未自动重试。") from None
        except (ValueError, UnicodeError):
            raise McdError("服务响应无法解析，本次未使用旧数据替代。") from None

    @staticmethod
    def _checked(message):
        if not isinstance(message, dict) or "error" in message or "result" not in message:
            raise McdError("MCP服务返回错误或无效响应。")
        return message["result"]

    def initialize(self):
        result = self._rpc("initialize", {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "mcd-rights-readonly", "version": "0.2.0"}})
        if not isinstance(result, dict):
            raise McdError("MCP初始化响应结构不完整。")
        self._headers["MCP-Protocol-Version"] = result.get("protocolVersion", "2024-11-05")
        self._rpc("notifications/initialized", notification=True)

    def call(self, name, arguments=None):
        arguments = {} if arguments is None else arguments
        validate_call(name, arguments)  # BEFORE any network operation
        result = self._rpc("tools/call", {"name": name, "arguments": arguments})
        self.calls.append(name)
        if not isinstance(result, dict):
            raise McdError("查询响应结构不完整。")
        if result.get("isError"):
            raise McdError("查询工具执行失败。")
        content = result.get("structuredContent")
        if not isinstance(content, dict):
            blocks = result.get("content", [])
            for block in blocks if isinstance(blocks, list) else []:
                if not isinstance(block, dict) or not isinstance(block.get("text"), str):
                    continue
                text = block.get("text", "")
                try:
                    candidate = json.loads(text.split("## Original Response", 1)[-1].strip())
                    if isinstance(candidate, dict):
                        content = candidate
                        break
                except ValueError:
                    continue
        if (not isinstance(content, dict) or "data" not in content
                or ("code" in content and str(content["code"]) != "200")
                or ("success" in content and content["success"] is not True)
                or ("code" not in content and content.get("success") is not True)):
            raise McdError("查询未返回可验证的成功数据。")
        return content["data"]


def _token_path():
    if os.name != "nt":
        raise McdError("此平台请通过MCD_MCP_TOKEN环境变量提供Token。")
    return Path(os.environ["LOCALAPPDATA"]) / "McdRights" / "token.dpapi"


def _dpapi(raw, protect):
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]

    buffer = ctypes.create_string_buffer(raw)
    source = Blob(len(raw), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    target = Blob()
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    pointer = ctypes.POINTER(Blob)
    crypt.CryptProtectData.argtypes = [pointer, wintypes.LPCWSTR, pointer, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, pointer]
    crypt.CryptProtectData.restype = wintypes.BOOL
    crypt.CryptUnprotectData.argtypes = [pointer, ctypes.c_void_p, pointer, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, pointer]
    crypt.CryptUnprotectData.restype = wintypes.BOOL
    if protect:
        ok = crypt.CryptProtectData(ctypes.byref(source), "McdRights MCP token", None, None, None, 1, ctypes.byref(target))
    else:
        ok = crypt.CryptUnprotectData(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target))
    if not ok:
        raise McdError("无法访问当前Windows用户的加密凭证。")
    try:
        return ctypes.string_at(target.data, target.size)
    finally:
        kernel.LocalFree.argtypes = [ctypes.c_void_p]
        kernel.LocalFree(ctypes.cast(target.data, ctypes.c_void_p))


def save_token(token):
    if not token.strip() or any(c.isspace() for c in token):
        raise McdError("Token为空或格式不正确。")
    path = _token_path()
    encrypted = _dpapi(token.encode(), True)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(encrypted)


def load_token():
    if os.environ.get("MCD_MCP_TOKEN"):
        return os.environ["MCD_MCP_TOKEN"]
    path = _token_path()
    if not path.exists():
        raise McdError("尚未配置Token：请先运行--set-token，或设置MCD_MCP_TOKEN。")
    return _dpapi(path.read_bytes(), False).decode()
