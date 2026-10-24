#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""麦当劳官方 MCP 客户端（仅标准库）。

用途：给「麦单合伙人」的探针脚本与调试提供统一的 JSON-RPC 2.0 / Streamable HTTP 封装。

安全约定（硬性）：
- Token 只从 `MCD_MCP_TOKEN` 读取，只存在于内存与请求头中；
- 任何打印、日志、异常信息都不得包含 Token；
- 所有对外输出都必须经过 `redact()`。

退出码：0 成功 / 1 未配置 Token / 2 认证失败 / 3 网络、服务端或本地参数错误。
"""

import argparse
import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.request

DEFAULT_ENDPOINT = "https://mcp.mcd.cn"
PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
TOKEN_ENV = "MCD_MCP_TOKEN"
CLIENT_INFO = {"name": "meal-mates-probe", "version": "0.1.0"}

# ---------------------------------------------------------------- 错误类型


class McdError(Exception):
    """所有本模块异常的基类。"""


class MissingTokenError(McdError):
    pass


class AuthError(McdError):
    pass


class RateLimitError(McdError):
    def __init__(self, message, retry_after=None):
        super().__init__(message)
        self.retry_after = retry_after


class NetworkError(McdError):
    pass


class HttpError(McdError):
    def __init__(self, message, status=None, body=""):
        super().__init__(message)
        self.status = status
        self.body = body


class RpcError(McdError):
    def __init__(self, message, code=None):
        super().__init__(message)
        self.code = code


class ToolCallError(McdError):
    pass


# ---------------------------------------------------------------- 脱敏工具

_SECRET_VALUE_RE = re.compile(r"^[A-Za-z0-9._\-+/=]{20,}$")
_SECRET_KEY_RE = re.compile(r"(token|authorization|secret|coupon)", re.IGNORECASE)
_BEARER_RE = re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-+/=]{8,}")
_ENV_ASSIGN_RE = re.compile(r"(?i)\b([A-Z0-9_]*(?:TOKEN|SECRET|AUTHORIZATION)[A-Z0-9_]*\s*[=:]\s*)\S+")
_LONG_ALNUM_RE = re.compile(r"\b[A-Za-z0-9._\-+/=]{32,}\b")


def looks_like_secret(value):
    """长度 >= 20 且整体是字母数字（含少量 base64/token 常见符号）→ 视为疑似凭据。"""
    if not isinstance(value, str):
        return False
    if len(value) < 20:
        return False
    if not _SECRET_VALUE_RE.match(value):
        return False
    has_digit = any(c.isdigit() for c in value)
    return has_digit or len(value) >= 40


def scrub_text(text):
    """对任意文本做凭据擦除，可安全用于异常消息与响应体片段。"""
    if not isinstance(text, str):
        return text
    text = _BEARER_RE.sub(r"\1***", text)
    text = _ENV_ASSIGN_RE.sub(r"\1***", text)
    text = _LONG_ALNUM_RE.sub("***", text)
    return text


def redact(obj, _depth=0):
    """递归脱敏。键名命中 token/authorization/secret/coupon 且值为标量时整体替换为 ***。

    值为 dict/list 时继续递归（保留 JSON Schema 之类的结构，避免把整段 schema 抹掉）。
    """
    if _depth > 40:
        return "***"
    if isinstance(obj, dict):
        out = {}
        for key, value in obj.items():
            if isinstance(value, (dict, list)):
                out[key] = redact(value, _depth + 1)
            elif _SECRET_KEY_RE.search(str(key)):
                out[key] = "***"
            elif isinstance(value, str):
                out[key] = "***" if looks_like_secret(value) else scrub_text(value)
            elif isinstance(value, (bytes, bytearray)):
                out[key] = "***"
            else:
                out[key] = value
        return out
    if isinstance(obj, (list, tuple)):
        return [redact(item, _depth + 1) for item in obj]
    if isinstance(obj, str):
        return "***" if looks_like_secret(obj) else scrub_text(obj)
    return obj


def redact_str(text):
    return scrub_text(text)


# ---------------------------------------------------------------- 响应解析


def parse_sse_events(text):
    """把 SSE 文本拆成若干 data 载荷（同一事件的多个 data: 行用换行拼接）。"""
    payloads, buf = [], []
    for raw in text.splitlines():
        line = raw.rstrip("\r")
        if not line.strip():
            if buf:
                payloads.append("\n".join(buf))
                buf = []
            continue
        if line.startswith(":"):
            continue
        if line.startswith("data:"):
            value = line[5:]
            if value.startswith(" "):
                value = value[1:]
            buf.append(value)
    if buf:
        payloads.append("\n".join(buf))
    return payloads


def sse_json_objects(text):
    """SSE → JSON 对象列表（忽略心跳、[DONE]、非 JSON 载荷）。"""
    objs = []
    for payload in parse_sse_events(text):
        payload = payload.strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            objs.append(json.loads(payload))
        except (ValueError, TypeError):
            continue
    return objs


def _flatten(objs):
    flat = []
    for item in objs:
        if isinstance(item, list):
            flat.extend(item)
        else:
            flat.append(item)
    return flat


def pick_rpc_message(objs):
    """取第一个含 result 或 error 的 JSON-RPC 响应；数组会被摊平。"""
    for item in _flatten(objs):
        if isinstance(item, dict) and ("result" in item or "error" in item):
            return item
    return None


def pick_any_message(objs):
    """兜底：返回第一个 dict（没有 result/error 字段的响应）。"""
    for item in _flatten(objs):
        if isinstance(item, dict):
            return item
    return None


def http_error_from(status, body_text):
    """把 HTTP 状态映射为具体异常（纯函数，便于自测）。"""
    body_text = body_text or ""
    snippet = scrub_text(body_text.strip())[:300]
    if status == 401:
        return AuthError(
            "认证失败（HTTP 401）：Token 无效或缺失，请到 https://open.mcd.cn/mcp "
            "重新申请或在 WorkBuddy 连接器中更新。"
        )
    if status == 429:
        retry_after = None
        match = re.search(r"(?i)retry[-_ ]?after[\"']?\s*[:=]\s*\"?(\d+)", body_text)
        if match:
            retry_after = int(match.group(1))
        message = "触发限流（HTTP 429）：超过 600 次/分钟。请降低调用频率后重试"
        message += f"（建议等待 {retry_after} 秒）。" if retry_after else "（可稍等片刻再试）。"
        return RateLimitError(message, retry_after=retry_after)
    return HttpError(
        f"服务端返回 HTTP {status}。响应片段：{snippet or '（空）'}",
        status=status,
        body=snippet,
    )


# ---------------------------------------------------------------- 客户端


class McdClient:
    """麦当劳 MCP 的 Streamable HTTP 客户端。"""

    def __init__(self, token=None, endpoint=DEFAULT_ENDPOINT, timeout=30):
        token = token or os.environ.get(TOKEN_ENV, "")
        token = (token or "").strip()
        if not token:
            raise MissingTokenError(
                "未找到访问凭据：请在环境变量 MCD_MCP_TOKEN 或 WorkBuddy 连接器中配置后重试。"
                "（Token 可在 https://open.mcd.cn/mcp 申请）"
            )
        if token.lower().startswith("bearer "):
            token = token[7:].strip()
        self._token = token                    # 仅内存，永不打印
        self.endpoint = endpoint.rstrip("/")
        self.timeout = timeout
        self.session_id = None
        self.protocol_version = None
        self.server_info = {}
        self._req_id = 0
        self._ssl_ctx = ssl.create_default_context()
        self.last_http_status = None

    # -- 内部：请求头（含凭据，禁止外泄）
    def _headers(self, with_session=True):
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Authorization": "Bearer " + self._token,
            "User-Agent": "meal-mates-probe/0.1.0",
        }
        if with_session and self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        return headers

    def _next_id(self):
        self._req_id += 1
        return self._req_id

    # -- HTTP 层
    def _post_raw(self, payload, with_session=True):
        """POST 一次，返回 (status, headers_lower, body_bytes)。"""
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            self.endpoint, data=data, method="POST", headers=self._headers(with_session)
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout, context=self._ssl_ctx) as resp:
                status = resp.status
                headers = {str(k).lower(): v for k, v in resp.headers.items()}
                body = resp.read() or b""
                self.last_http_status = status
                return status, headers, body
        except urllib.error.HTTPError as exc:
            raw = b""
            try:
                raw = exc.read() or b""
            except Exception:  # noqa: BLE001 - 读响应体失败不应掩盖原始状态码
                raw = b""
            self.last_http_status = exc.code
            raise http_error_from(exc.code, raw.decode("utf-8", "replace")) from None
        except urllib.error.URLError as exc:
            reason = getattr(exc, "reason", exc)
            if isinstance(reason, TimeoutError):
                raise NetworkError(
                    f"当前环境无法访问 {_host(self.endpoint)}：请求超时（{self.timeout}s）。"
                    "请检查网络/代理后重试。"
                ) from None
            raise NetworkError(
                f"当前环境无法访问 {_host(self.endpoint)}（DNS 解析失败或网络不通）。"
                f"原始错误：{scrub_text(str(reason))[:200]}"
            ) from None
        except TimeoutError:
            raise NetworkError(
                f"当前环境无法访问 {_host(self.endpoint)}：请求超时（{self.timeout}s）。"
            ) from None
        except OSError as exc:
            raise NetworkError(
                f"当前环境无法访问 {_host(self.endpoint)}：{scrub_text(str(exc))[:200]}"
            ) from None

    def _decode(self, status, headers, body):
        """把响应体解析成 JSON-RPC 消息；通知类响应可能为空。"""
        text = body.decode("utf-8", "replace") if body else ""
        ctype = (headers.get("content-type") or "").lower()
        is_sse = "text/event-stream" in ctype or text.lstrip().startswith(("data:", "event:", "id:"))
        if is_sse:
            objs = sse_json_objects(text)
            message = pick_rpc_message(objs) or pick_any_message(objs)
            if message is None:
                if status in (202, 204) or not text.strip():
                    return None
                raise RpcError("服务端返回了无法解析的 SSE 响应。")
            return message
        if not text.strip():
            if status in (202, 204):
                return None
            raise RpcError(f"服务端返回空响应（HTTP {status}）。")
        try:
            parsed = json.loads(text)
        except ValueError:
            raise RpcError(
                f"服务端返回了非 JSON 内容（HTTP {status}）：{scrub_text(text.strip())[:200]}"
            ) from None
        if isinstance(parsed, list):
            message = pick_rpc_message(parsed) or pick_any_message(parsed)
        elif isinstance(parsed, dict) and ("result" in parsed or "error" in parsed):
            message = parsed
        else:
            message = pick_rpc_message([parsed]) or pick_any_message([parsed])
        if message is None:
            raise RpcError("服务端返回的 JSON 中既没有 result 也没有 error。")
        return message

    def _rpc(self, method, params=None, notification=False):
        payload = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = params
        if not notification:
            payload["id"] = self._next_id()
        status, headers, body = self._post_raw(payload)
        if notification and status in (202, 204) and not (body or b"").strip():
            return None
        message = self._decode(status, headers, body)
        if message is None:
            if notification:
                return None
            raise RpcError(f"{method} 未返回结果。")
        if "error" in message and message["error"] is not None:
            err = message["error"] if isinstance(message["error"], dict) else {"message": str(message["error"])}
            code = err.get("code")
            detail = scrub_text(str(err.get("message", "")))[:300]
            raise RpcError(f"{method} 返回 JSON-RPC 错误{'' if code is None else f'（{code}）'}：{detail}", code=code)
        return message.get("result")

    # -- 协议层
    def _capture_session(self, headers):
        for key, value in headers.items():
            if key.lower() == "mcp-session-id" and value:
                self.session_id = value.strip()
                return

    def initialize(self):
        """依次尝试候选协议版本，服务端接受哪个就用哪个；成功后发 initialized 通知。"""
        last_error = None
        payload = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "initialize",
            "params": {"protocolVersion": "", "capabilities": {}, "clientInfo": CLIENT_INFO},
        }
        for version in PROTOCOL_VERSIONS:
            payload["params"]["protocolVersion"] = version
            self.session_id = None
            try:
                # initialize 请求本身不携带 Mcp-Session-Id
                status, headers, body = self._post_raw(payload, with_session=False)
                message = self._decode(status, headers, body)
            except (AuthError, RateLimitError, NetworkError):
                raise
            except McdError as exc:
                last_error = exc
                continue
            if not message or message.get("error"):
                err = (message or {}).get("error") or {}
                detail = err.get("message") if isinstance(err, dict) else err
                last_error = RpcError(f"服务端拒绝 {version}：{scrub_text(str(detail))[:200]}")
                continue
            result = message.get("result")
            if not isinstance(result, dict):
                last_error = RpcError(f"{version} 的 initialize 返回结构异常。")
                continue
            self._capture_session(headers)
            self.protocol_version = result.get("protocolVersion") or version
            self.server_info = result.get("serverInfo") or {}
            break
        else:
            raise RpcError(f"initialize 失败，候选协议版本均未被接受：{last_error}")

        self._notify("notifications/initialized")
        return {
            "protocol_version": self.protocol_version,
            "server_info": self.server_info,
            "session": bool(self.session_id),
        }

    # 语义化别名，方便调用方读代码
    open = initialize

    def _notify(self, method, params=None):
        """发送通知；接受 202/204 无 body。"""
        payload = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = params
        status, headers, body = self._post_raw(payload)
        if status in (202, 204) and not (body or b"").strip():
            return True
        message = self._decode(status, headers, body)
        if message and message.get("error"):
            err = message["error"] if isinstance(message["error"], dict) else {"message": str(message["error"])}
            raise RpcError(f"{method} 返回错误：{scrub_text(str(err.get('message', '')))[:300]}")
        return True

    def list_tools(self):
        result = self._rpc("tools/list", {})
        tools = (result or {}).get("tools") or []
        return [t for t in tools if isinstance(t, dict)]

    def call_tool(self, name, arguments):
        try:
            result = self._rpc("tools/call", {"name": name, "arguments": arguments or {}})
        except RpcError as exc:
            raise ToolCallError(f"工具 {name} 调用失败：{exc}") from None
        if isinstance(result, dict) and result.get("isError"):
            raise ToolCallError(f"工具 {name} 返回错误：{tool_text(result)[:500]}")
        return result


def _host(endpoint):
    return endpoint.split("//", 1)[-1].split("/", 1)[0]


def tool_text(result):
    """把工具结果里的 text 内容拼起来，便于错误摘要。"""
    if not isinstance(result, dict):
        return json.dumps(redact(result), ensure_ascii=False)
    parts = []
    for item in result.get("content") or []:
        if isinstance(item, dict) and isinstance(item.get("text"), str):
            parts.append(item["text"])
    if parts:
        return scrub_text("\n".join(parts))
    return scrub_text(json.dumps(redact(result), ensure_ascii=False))


def tool_payload(result):
    """优先返回结构化内容，其次返回解析后的文本 JSON，最后原样返回。"""
    if not isinstance(result, dict):
        return result
    for key in ("structuredContent", "structured_content"):
        if isinstance(result.get(key), (dict, list)):
            return result[key]
    text = tool_text(result)
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return result


# ---------------------------------------------------------------- 自测


def _self_test():
    failures = []

    def check(label, condition):
        if condition:
            print(f"  ok   {label}")
        else:
            print(f"  FAIL {label}")
            failures.append(label)

    print("[self-test] redact")
    fake_token = "a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6"
    data = {
        "accessToken": fake_token,
        "nested": {"Authorization": "Bearer " + fake_token, "list": [fake_token, "巨无霸"]},
        "couponCode": "ABC123",
        "coupon": {"type": "string", "description": "优惠券"},
        "name": "人民广场店",
        "storeCode": "SH001",
        "price": 2450,
    }
    out = redact(data)
    blob = json.dumps(out, ensure_ascii=False)
    check("token 值被抹除", fake_token not in blob)
    check("Bearer 头被抹除", "Bearer a1b2" not in blob)
    check("coupon 标量被抹除", out["couponCode"] == "***")
    check("coupon 结构保留", isinstance(out["coupon"], dict) and out["coupon"]["type"] == "string")
    check("普通字段未误伤", out["name"] == "人民广场店" and out["storeCode"] == "SH001")
    check("数字原样保留", out["price"] == 2450)
    check("列表内 token 被抹除", out["nested"]["list"][0] == "***")

    print("[self-test] SSE 解析")
    sse = (
        ": keep-alive\n\n"
        "event: message\n"
        'data: {"jsonrpc":"2.0","method":"notifications/progress","params":{"p":1}}\n\n'
        "event: message\n"
        'data: {"jsonrpc":"2.0","id":2,\n'
        'data:  "result":{"tools":[{"name":"query-meals"}]}}\n\n'
        "event: message\n"
        'data: {"jsonrpc":"2.0","id":2,"result":{"duplicate":true}}\n\n'
        "data: [DONE]\n\n"
    )
    message = pick_rpc_message(sse_json_objects(sse))
    check("跳过心跳与通知事件", message is not None and message.get("id") == 2)
    check("多行 data 正确拼接", message is not None and "tools" in message["result"])
    check("取到的是第一个 result 事件", message is not None and "duplicate" not in message["result"])

    err_sse = (
        'data: {"jsonrpc":"2.0","id":3,"error":{"code":-32602,"message":"Invalid params"}}\n\n'
    )
    emsg = pick_rpc_message(sse_json_objects(err_sse))
    check("error 事件可识别", emsg is not None and emsg["error"]["code"] == -32602)
    check("无 result/error 时返回 None", pick_rpc_message([{"jsonrpc": "2.0"}]) is None)

    print("[self-test] HTTP 状态映射")
    e401 = http_error_from(401, '{"error":"unauthorized"}')
    check("401 → AuthError", isinstance(e401, AuthError))
    check("401 提示含重新申请入口", "open.mcd.cn/mcp" in str(e401) and "Token 无效或缺失" in str(e401))
    e429 = http_error_from(429, '{"message":"too many","retryAfter":"7"}')
    check("429 → RateLimitError", isinstance(e429, RateLimitError))
    check("429 解析 retry_after", isinstance(e429, RateLimitError) and e429.retry_after == 7)
    e500 = http_error_from(500, 'upstream failed Bearer ' + fake_token + " " + fake_token)
    check("500 → HttpError", isinstance(e500, HttpError) and e500.status == 500)
    check("响应体中的凭据被过滤", fake_token not in str(e500))
    check("HTTP 错误保留状态码文本", "500" in str(e500))

    print("[self-test] 缺少 Token 的行为")
    saved = os.environ.pop(TOKEN_ENV, None)
    try:
        try:
            McdClient(token="")
            check("缺 Token 抛 MissingTokenError", False)
        except MissingTokenError as exc:
            msg = str(exc)
            check("缺 Token 抛 MissingTokenError", True)
            check("提示含环境变量名", TOKEN_ENV in msg)
            check("提示含连接器路径", "连接器" in msg)
    finally:
        if saved is not None:
            os.environ[TOKEN_ENV] = saved

    print("[self-test] 请求头")
    client = McdClient(token=fake_token, timeout=5)
    headers = client._headers(with_session=False)
    check("Accept 同时声明 json 与 sse", headers["Accept"] == "application/json, text/event-stream")
    check("initialize 请求不带 session", "Mcp-Session-Id" not in headers)
    client.session_id = "sess-123"
    check("有 session 时带上", client._headers()["Mcp-Session-Id"] == "sess-123")

    print()
    if failures:
        print(f"self-test 失败 {len(failures)} 项：" + "; ".join(failures))
        return 3
    print("self-test 全部通过")
    return 0


# ---------------------------------------------------------------- CLI


def _print_tools(tools):
    if not tools:
        print("服务端未返回任何工具。")
        return
    print(f"共 {len(tools)} 个工具：")
    width = max(len(str(t.get("name", ""))) for t in tools)
    for tool in tools:
        name = str(tool.get("name", "?"))
        desc = (tool.get("description") or "").strip().splitlines()
        first = desc[0].strip() if desc else ""
        if len(first) > 90:
            first = first[:90] + "…"
        print(f"  {name.ljust(width)}  {first}")
    print("\n查看参数：--schema <工具名>")


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001 - 老终端不支持时保持原样
        pass

    parser = argparse.ArgumentParser(
        prog="mcd_client.py",
        description="麦当劳官方 MCP（https://mcp.mcd.cn）客户端。Token 从环境变量 MCD_MCP_TOKEN 读取。",
        epilog="退出码：0 成功 / 1 未配置 Token / 2 认证失败 / 3 网络、服务端或参数错误。",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--list-tools", action="store_true", help="列出工具名与描述首行")
    group.add_argument("--schema", metavar="TOOL", help="打印指定工具的 inputSchema")
    group.add_argument("--call", metavar="TOOL", help="调用工具，配合 --args 传 JSON 参数")
    group.add_argument("--ping", action="store_true", help="只做 initialize + tools/list，报告连接状态")
    group.add_argument("--self-test", action="store_true", help="跑内置断言（redact / SSE / 错误码映射）")
    parser.add_argument("--args", metavar="JSON", default="{}", help="--call 的参数，JSON 对象")
    parser.add_argument("--endpoint", default=os.environ.get("MCD_MCP_ENDPOINT", DEFAULT_ENDPOINT))
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args(argv)

    if args.self_test:
        return _self_test()

    if not (args.list_tools or args.schema or args.call or args.ping):
        parser.print_help()
        return 0

    try:
        client = McdClient(token=None, endpoint=args.endpoint, timeout=args.timeout)
    except MissingTokenError as exc:
        print(f"[未配置 Token] {exc}", file=sys.stderr)
        return 1

    try:
        started = time.monotonic()
        info = client.open()
        elapsed = time.monotonic() - started
        if args.ping:
            tools = client.list_tools()
            print("连接状态：ok")
            print(f"地址：{client.endpoint}")
            print(f"协议版本：{info['protocol_version']}")
            server = info.get("server_info") or {}
            if server:
                print(f"服务端：{server.get('name', '?')} {server.get('version', '')}".rstrip())
            print(f"会话：{'已建立' if info['session'] else '未返回 Mcp-Session-Id'}")
            print(f"工具数量：{len(tools)}")
            print(f"握手耗时：{elapsed:.2f}s")
            return 0

        if args.list_tools:
            _print_tools(client.list_tools())
            return 0

        if args.schema:
            tools = {str(t.get("name")): t for t in client.list_tools()}
            tool = tools.get(args.schema)
            if tool is None:
                print(f"未找到工具 {args.schema}。可用工具：{', '.join(tools) or '（无）'}", file=sys.stderr)
                return 3
            schema = tool.get("inputSchema") or {}
            print(f"# {args.schema}")
            desc = (tool.get("description") or "").strip()
            if desc:
                print(f"# {desc.splitlines()[0]}")
            print(json.dumps(redact(schema), ensure_ascii=False, indent=2))
            return 0

        if args.call:
            try:
                call_args = json.loads(args.args or "{}")
            except ValueError as exc:
                print(f"[参数错误] --args 不是合法 JSON：{exc}", file=sys.stderr)
                return 3
            if not isinstance(call_args, dict):
                print("[参数错误] --args 必须是 JSON 对象。", file=sys.stderr)
                return 3
            result = client.call_tool(args.call, call_args)
            text = json.dumps(redact(result), ensure_ascii=False, indent=2)
            if len(text) > 6000:
                text = text[:6000] + "\n…（已截断）"
            print(text)
            return 0

    except MissingTokenError as exc:
        print(f"[未配置 Token] {exc}", file=sys.stderr)
        return 1
    except AuthError as exc:
        print(f"[认证失败] {exc}", file=sys.stderr)
        return 2
    except RateLimitError as exc:
        print(f"[限流] {exc}", file=sys.stderr)
        return 3
    except ToolCallError as exc:
        print(f"[工具错误] {exc}", file=sys.stderr)
        return 3
    except NetworkError as exc:
        print(f"[网络错误] {exc}", file=sys.stderr)
        return 3
    except HttpError as exc:
        print(f"[服务端错误] {exc}", file=sys.stderr)
        return 3
    except McdError as exc:
        print(f"[协议错误] {scrub_text(str(exc))}", file=sys.stderr)
        return 3
    except KeyboardInterrupt:
        print("已中断。", file=sys.stderr)
        return 3

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
