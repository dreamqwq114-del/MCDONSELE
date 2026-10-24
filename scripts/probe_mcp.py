#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""麦当劳官方 MCP 第一阶段探针。

只做真实调用，不做任何模拟：每一步的真实状态、耗时、错误摘要都会写进
`runtime/probe-<YYYYMMDD-HHMMSS>.json`，供 docs/IMPLEMENTATION_STATUS.md 引用。

用法：
    python scripts/probe_mcp.py --city 上海 --area 人民广场
    python scripts/probe_mcp.py --city 上海 --area 人民广场 --store-code <编码> --item-code <编码>

退出码：
    0  全部走通
    1  未配置 MCD_MCP_TOKEN
    2  认证失败（401）
    3  网络 / 服务端 / 协议错误
    4  inputSchema 的必填字段无法由 CLI 参数满足（不猜）
    5  已跑通但需要人工补参数（缺 --store-code 或 --item-code），非错误
"""

import argparse
import json
import os
import re
import sys
from itertools import chain
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mcd_client import (  # noqa: E402
    AuthError,
    HttpError,
    McdClient,
    McdError,
    MissingTokenError,
    NetworkError,
    RateLimitError,
    RpcError,
    ToolCallError,
    redact,
    scrub_text,
    tool_payload,
)

RUNTIME_DIR = Path(__file__).resolve().parent.parent / "runtime"

TARGET_TOOLS = ("query-nearby-stores", "query-meals", "query-meal-detail", "calculate-price")

TOKEN_GUIDE = (
    "未检测到环境变量 MCD_MCP_TOKEN，无法访问麦当劳官方 MCP。\n"
    "  1) 申请 Token：https://open.mcd.cn/mcp\n"
    "  2) 或在 WorkBuddy 中配置连接器：聊天窗口左下角「+」→ 连接器 → 新增自定义 MCP，\n"
    "     地址 https://mcp.mcd.cn ，类型 Streamable HTTP，\n"
    "     请求头 Authorization: Bearer <你的 Token>\n"
    "  3) 命令行临时试用：export MCD_MCP_TOKEN=<你的 Token> 后重跑本脚本。\n"
    "已写出 status=blocked 的探针记录，便于后续替换为真实结果。"
)

# ------------------------------------------------------------ schema 自适应

CITY_KEYS = {"city", "cityname", "citycode", "cityid"}
AREA_KEYS = {
    "area", "areaname", "district", "districtname", "businessarea", "businessdistrict",
    "region", "location", "address", "addr", "addressname", "keyword", "keywords",
    "key", "query", "searchkey", "searchkeyword", "searchtext", "shopname", "storename", "name",
}
STORE_KEYS = {"storecode", "storeid", "shopcode", "storeno", "store"}
ITEM_KEYS = {"code", "mealcode", "itemcode", "productcode", "skucode", "spucode"}
PRODUCT_KEYS = {"products", "productlist", "items", "itemlist", "goods", "meals", "cart", "skus"}
QTY_KEYS = {"qty", "quantity", "num", "count", "number"}
SEARCH_TYPE_KEYS = {"searchtype"}
BE_TYPE_KEYS = {"betype", "businesscode", "betypeid"}
ORDER_TYPE_KEYS = {"ordertype", "ordertypeid", "ordertype_code"}


def norm(name):
    return str(name).strip().lower().replace("_", "").replace("-", "")


def find_tool(tools, target):
    """连字符/下划线都认：query-meals == query_meals。"""
    wanted = norm(target)
    for tool in tools:
        if norm(tool.get("name", "")) == wanted:
            return tool
    return None


def tool_schema(tool):
    schema = tool.get("inputSchema") or tool.get("input_schema") or {}
    return schema if isinstance(schema, dict) else {}


def schema_props(tool):
    props = tool_schema(tool).get("properties") or {}
    return props if isinstance(props, dict) else {}


def required_fields(tool):
    req = tool_schema(tool).get("required") or []
    props = schema_props(tool)
    return [(str(name), props.get(name) or {}) for name in req]


def classify(field, prop=None):
    key = norm(field)
    if key in CITY_KEYS:
        return "city"
    if key in AREA_KEYS:
        return "area"
    if key in SEARCH_TYPE_KEYS:
        return "search_type"
    if key in BE_TYPE_KEYS:
        return "be_type"
    if key in ORDER_TYPE_KEYS:
        return "order_type"
    if key in STORE_KEYS:
        return "store_code"
    if key in PRODUCT_KEYS:
        return "products"
    if key in QTY_KEYS:
        return "qty"
    if key in ITEM_KEYS:
        return "item_code"
    for token in ("keyword", "search", "business", "area", "district", "region"):
        if token in key:
            return "area"
    for token in ("city",):
        if token in key:
            return "city"
    for token in ("store",):
        if token in key:
            return "store_code"
    ptype = (prop or {}).get("type")
    if ptype == "array":
        return "products"
    return None


def coerce(prop, value):
    ptype = (prop or {}).get("type")
    if ptype == "integer" and not isinstance(value, int):
        try:
            return int(str(value).strip())
        except ValueError:
            return value
    if ptype == "number" and not isinstance(value, (int, float)):
        try:
            return float(str(value).strip())
        except ValueError:
            return value
    if ptype == "boolean" and not isinstance(value, bool):
        return str(value).lower() in ("1", "true", "yes")
    if ptype == "array" and not isinstance(value, list):
        return [value]
    if ptype == "string" and not isinstance(value, str):
        return str(value)
    return value


def detect_product_field(tool):
    """找到「商品列表」数组字段名及其 item 内的编码/数量字段名。"""
    props = schema_props(tool)
    for key, prop in props.items():
        if isinstance(prop, dict) and prop.get("type") == "array":
            items = prop.get("items") or {}
            item_props = items.get("properties") or {}
            code_key, qty_key = "code", "qty"
            for sub in item_props:
                kind = classify(sub)
                if kind == "item_code":
                    code_key = sub
                elif kind == "qty":
                    qty_key = sub
            return key, code_key, qty_key
    for key in props:
        if classify(key) == "products":
            return key, "code", "qty"
    return None, "code", "qty"


def missing_message(field, prop):
    ptype = (prop or {}).get("type") or "未声明类型"
    desc = (prop or {}).get("description") or "无描述"
    return f"缺少必填字段 {field}（{ptype} / {desc}）"


def build_params(tool, ctx, extras=None):
    """按真实 inputSchema 组装参数；无法满足的必填字段返回错误而不是猜。"""
    props = schema_props(tool)
    params, missing = {}, []
    product_field, code_key, qty_key = (None, "code", "qty")
    if ctx.need_products:
        product_field, code_key, qty_key = detect_product_field(tool)

    def value_for(kind, field, prop):
        if kind == "city":
            return ctx.city
        if kind == "area":
            return ctx.area
        if kind == "search_type":
            return 2
        if kind == "be_type":
            return 1
        if kind == "order_type":
            return 1
        if kind == "store_code":
            return ctx.store_code
        if kind == "item_code":
            return ctx.item_code
        if kind == "qty":
            return 1
        if kind == "products":
            if not ctx.item_code:
                return None
            return [{code_key: ctx.item_code, qty_key: 1}]
        if prop.get("default") is not None:
            return prop["default"]
        if prop.get("enum"):
            return prop["enum"][0] if len(prop["enum"]) == 1 else None
        return None

    req = required_fields(tool)
    for field, prop in req:
        kind = classify(field, prop)
        value = value_for(kind, field, prop)
        if value is None:
            missing.append(missing_message(field, prop))
            continue
        params[field] = coerce(prop, value)

    # 可选但业务必需的字段（如 query-nearby-stores 的 city / keyword）：
    # 它们在 schema 里不是 required，但不填就搜不出目标商圈的门店，所以有值就补上。
    for field, prop in props.items():
        if field in params:
            continue
        kind = classify(field, prop)
        if kind == "products" and ctx.need_products and ctx.item_code:
            product_field, code_key, qty_key = detect_product_field(tool)
            if norm(field) == norm(product_field or ""):
                params[field] = [{code_key: ctx.item_code, qty_key: 1}]
            continue
        if kind in ("city", "area", "store_code", "item_code"):
            value = value_for(kind, field, prop)
            if value is not None:
                params[field] = coerce(prop, value)

    # 业务参数只在 schema 里确实存在时才补，避免服务端因未知字段报错
    for name, wanted in (extras or {}).items():
        for field, prop in props.items():
            if norm(field) == norm(name) and field not in params:
                params[field] = coerce(prop, wanted)
                break

    # 到店自取不得传 beCode
    for field in list(params):
        if norm(field) in ("becode", "becodeid", "businesscode"):
            params.pop(field, None)

    return params, missing


# ------------------------------------------------------------ 结果探查

STORE_CODE_KEYS = ["storeCode", "store_code", "shopCode", "storeId", "storeNo", "code", "id"]
STORE_NAME_KEYS = ["storeName", "store_name", "shopName", "name", "title"]
STORE_ADDR_KEYS = ["address", "addr", "storeAddress", "fullAddress", "addressName", "location"]
MEAL_CODE_KEYS = ["code", "mealCode", "productCode", "itemCode", "spuCode", "skuCode", "id"]
MEAL_NAME_KEYS = ["name", "mealName", "productName", "title", "label"]
MEAL_PRICE_KEYS = ["price", "currentPrice", "salePrice", "amount", "priceText", "originalPrice", "minPrice"]
PRICE_HINT_RE = re.compile(r"(price|amount|total|fee|discount|cents|pay|cost|yuan)", re.IGNORECASE)


def flat_scalars(obj, prefix="", depth=2):
    """把 dict 摊平成 {归一化键名: (原始键名, 值)}，只保留标量。"""
    out = {}

    def walk(node, path, level):
        if level > depth:
            return
        if isinstance(node, dict):
            for key, value in node.items():
                child = f"{path}.{key}" if path else str(key)
                if isinstance(value, (dict, list)):
                    walk(value, child, level + 1)
                else:
                    out.setdefault(norm(key), (child, value))
        elif isinstance(node, list):
            for index, value in enumerate(node[:3]):
                if isinstance(value, (dict, list)):
                    walk(value, f"{path}[{index}]", level + 1)
                else:
                    out.setdefault(f"{norm(path)}[{index}]", (f"{path}[{index}]", value))

    walk(obj, prefix, 0)
    return out


def pick_field(item, candidates):
    if not isinstance(item, dict):
        return None, None
    flat = flat_scalars(item, depth=1)
    for cand in candidates:
        hit = flat.get(norm(cand))
        if hit and hit[1] not in (None, ""):
            return hit[0], hit[1]
    return None, None


def iter_lists(obj, path="", level=0):
    if level > 6:
        return
    if isinstance(obj, dict):
        for key, value in obj.items():
            yield from iter_lists(value, f"{path}.{key}" if path else str(key), level + 1)
    elif isinstance(obj, list):
        yield path, obj
        for index, value in enumerate(obj[:3]):
            yield from iter_lists(value, f"{path}[{index}]", level + 1)


def iter_maps(obj, path="", level=0):
    """有些工具把列表塞进「编码 → 对象」的字典里（例如 query-meals 的 data.meals
    返回的是 112 个编码到商品的映射），这里把它摊回数组，并用键名补上编码字段。"""
    if level > 6:
        return
    if isinstance(obj, dict):
        if len(obj) >= 2 and all(isinstance(value, dict) for value in obj.values()):
            items = []
            for key, value in obj.items():
                merged = dict(value)
                merged.setdefault("code", key)
                items.append(merged)
            yield path, items
        for key, value in obj.items():
            yield from iter_maps(value, f"{path}.{key}" if path else str(key), level + 1)
    elif isinstance(obj, list):
        for index, value in enumerate(obj[:3]):
            yield from iter_maps(value, f"{path}[{index}]", level + 1)


def pick_record_list(payload, code_keys, name_keys):
    """在任意嵌套里挑最像「门店/商品列表」的数组（含字典型列表）。"""
    best, best_score = None, 0.0
    sources = chain(iter_lists(payload), iter_maps(payload))
    for path, items in sources:
        dicts = [item for item in items if isinstance(item, dict)]
        if not dicts:
            continue
        hit = 0
        for item in dicts:
            _, code = pick_field(item, code_keys)
            _, name = pick_field(item, name_keys)
            if code not in (None, "") and name not in (None, ""):
                hit += 1
        score = hit / len(dicts)
        if hit and score > best_score:
            best, best_score = (path, dicts), score
    return best if best else (None, [])


def collect_prices(payload, limit=12):
    """只收集金额相关字段，供价格摘要使用。"""
    found = []

    def walk(node, path, level):
        if len(found) >= limit or level > 5:
            return
        if isinstance(node, dict):
            for key, value in node.items():
                child = f"{path}.{key}" if path else str(key)
                if isinstance(value, (dict, list)):
                    walk(value, child, level + 1)
                elif PRICE_HINT_RE.search(str(key)) and value not in (None, ""):
                    found.append(f"{child}={value}")
        elif isinstance(node, list):
            for index, value in enumerate(node[:5]):
                walk(value, f"{path}[{index}]", level + 1)

    walk(payload, "", 0)
    return found[:limit]


# ------------------------------------------------------------ 探针主体


class Context:
    def __init__(self, city, area, store_code, item_code, need_products=False):
        self.city = city
        self.area = area
        self.store_code = store_code
        self.item_code = item_code
        self.need_products = need_products


class Probe:
    def __init__(self, client, city, area):
        self.client = client
        self.city = city
        self.area = area
        self.steps = []
        self.tools = []
        self.exit_code = 0
        self.status = "running"

    # -- 记录
    def _step(self, name, status, duration_ms, error=None, price_summary=None):
        self.steps.append(
            {
                "name": name,
                "status": status,
                "duration_ms": int(duration_ms),
                "error_summary": scrub_text(error)[:300] if error else None,
                "price_summary": price_summary or [],
            }
        )

    def _fail(self, name, started, error, code=3):
        duration = (time.perf_counter() - started) * 1000
        print(f"[{name}] 状态=fail 耗时={duration / 1000:.2f}s\n  错误：{scrub_text(str(error))[:300]}")
        self._step(name, "fail", duration, error=str(error))
        self.exit_code = code

    def run(self, store_code=None, item_code=None):
        started = time.perf_counter()
        try:
            info = self.client.initialize()
        except AuthError as exc:
            self._fail("initialize", started, exc, code=2)
            self.status = "auth_failed"
            return
        except (NetworkError, RateLimitError, HttpError, RpcError) as exc:
            self._fail("initialize", started, exc, code=3)
            self.status = "failed"
            return

        duration = (time.perf_counter() - started) * 1000
        proto = info.get("protocol_version") or "未知"
        print(f"[initialize] 状态=ok 耗时={duration / 1000:.2f}s 协议={proto} 会话={'有' if info.get('session') else '无'}")
        self._step("initialize", "ok", duration)

        started = time.perf_counter()
        try:
            self.tools = self.client.list_tools()
        except McdError as exc:
            self._fail("tools/list", started, exc, code=2 if isinstance(exc, AuthError) else 3)
            self.status = "failed"
            return
        duration = (time.perf_counter() - started) * 1000
        names = [str(t.get("name")) for t in self.tools]
        present = {target: find_tool(self.tools, target) is not None for target in TARGET_TOOLS}
        print(f"[tools/list] 状态=ok 耗时={duration / 1000:.2f}s 工具数={len(names)}")
        for target, ok in present.items():
            print(f"  - {target}: {'已发现' if ok else '未发现'}")
        self._step("tools/list", "ok", duration)

        if not all(present.values()):
            missing = [t for t, ok in present.items() if not ok]
            print(f"  注意：以下目标工具未在 tools/list 中出现：{', '.join(missing)}")
            print(f"  服务端实际工具：{', '.join(names) if names else '（空）'}")

        # 步骤 1：门店
        started = time.perf_counter()
        store_tool = find_tool(self.tools, "query-nearby-stores")
        if store_tool is None:
            self._fail("query-nearby-stores", started, RpcError("tools/list 中没有该工具"), code=3)
            self.status = "failed"
            return
        ctx = Context(self.city, self.area, store_code, item_code)
        params, missing = build_params(
            store_tool, ctx, extras={"searchType": 2, "beType": 1}
        )
        if missing:
            duration = (time.perf_counter() - started) * 1000
            for msg in missing:
                print(f"[query-nearby-stores] 状态=fail 耗时={duration / 1000:.2f}s\n  {msg}")
            self._step("query-nearby-stores", "fail", duration, error="; ".join(missing))
            print("  该必填字段无法从 --city/--area 推导，已停止（不猜参数）。请补充 CLI 参数后重试。")
            self.exit_code = 4
            self.status = "schema_blocked"
            return

        try:
            result = self.client.call_tool(str(store_tool.get("name")), params)
        except (AuthError, RateLimitError, NetworkError, HttpError, RpcError, ToolCallError) as exc:
            self._fail("query-nearby-stores", started, exc, code=2 if isinstance(exc, AuthError) else 3)
            self.status = "failed"
            return

        duration = (time.perf_counter() - started) * 1000
        payload = tool_payload(result)
        _, stores = pick_record_list(payload, STORE_CODE_KEYS, STORE_NAME_KEYS)
        print(f"[query-nearby-stores] 状态=ok 耗时={duration / 1000:.2f}s 门店数={len(stores)}")
        print(f"  请求参数：{json.dumps(redact(params), ensure_ascii=False)}")
        for index, store in enumerate(stores[:10], 1):
            code_key, code = pick_field(store, STORE_CODE_KEYS)
            name_key, name = pick_field(store, STORE_NAME_KEYS)
            addr_key, addr = pick_field(store, STORE_ADDR_KEYS)
            print(
                f"  {index:>2}) 编码={code} | 名称={name} | 地址={addr}"
                f"   （字段：{code_key} / {name_key} / {addr_key}）"
            )
        if len(stores) > 10:
            print(f"  …另有 {len(stores) - 10} 家门店未显示")
        self._step("query-nearby-stores", "ok", duration)

        if not store_code:
            self.status = "awaiting_store_code"
            self.exit_code = 5
            print("\n[停在门店列表] 请确认一家门店的编码后带上 --store-code 重跑，例如：")
            example = stores[0] if stores else {}
            _, example_code = pick_field(example, STORE_CODE_KEYS)
            print(
                f"  python scripts/probe_mcp.py --city {self.city} --area {self.area} "
                f"--store-code {example_code if example_code else '<门店编码>'}"
            )
            print("  不会自动替你选门店。")
            return

        # 步骤 2：菜单
        started = time.perf_counter()
        meals_tool = find_tool(self.tools, "query-meals")
        if meals_tool is None:
            self._fail("query-meals", started, RpcError("tools/list 中没有该工具"), code=3)
            self.status = "failed"
            return
        ctx = Context(self.city, self.area, store_code, item_code)
        params, missing = build_params(meals_tool, ctx, extras={"orderType": 1, "beType": 1, "storeCode": store_code})
        if missing:
            duration = (time.perf_counter() - started) * 1000
            for msg in missing:
                print(f"[query-meals] 状态=fail 耗时={duration / 1000:.2f}s\n  {msg}")
            self._step("query-meals", "fail", duration, error="; ".join(missing))
            self.exit_code = 4
            self.status = "schema_blocked"
            return
        try:
            result = self.client.call_tool(str(meals_tool.get("name")), params)
        except (AuthError, RateLimitError, NetworkError, HttpError, RpcError, ToolCallError) as exc:
            self._fail("query-meals", started, exc, code=2 if isinstance(exc, AuthError) else 3)
            self.status = "failed"
            return
        duration = (time.perf_counter() - started) * 1000
        payload = tool_payload(result)
        _, meals = pick_record_list(payload, MEAL_CODE_KEYS, MEAL_NAME_KEYS)
        prices = collect_prices(payload)
        print(f"[query-meals] 状态=ok 耗时={duration / 1000:.2f}s 商品数={len(meals)}")
        print(f"  请求参数：{json.dumps(redact(params), ensure_ascii=False)}")
        for index, meal in enumerate(meals[:20], 1):
            code_key, code = pick_field(meal, MEAL_CODE_KEYS)
            name_key, name = pick_field(meal, MEAL_NAME_KEYS)
            price_key, price = pick_field(meal, MEAL_PRICE_KEYS)
            print(
                f"  {index:>2}) {code_key}={code} {name_key}={name} {price_key}={price}"
                f"   （金额字段：{price_key}）"
            )
        if len(meals) > 20:
            print(f"  …另有 {len(meals) - 20} 个商品未显示")
        print("  提醒：金额单位待确认（分或元以官方字段语义为准），未做任何换算。")
        self._step("query-meals", "ok", duration, price_summary=prices)

        if not item_code:
            self.status = "awaiting_item_code"
            self.exit_code = 5
            print("\n[停在商品列表] 请确认一个商品编码后带上 --item-code 重跑，例如：")
            example = meals[0] if meals else {}
            _, example_code = pick_field(example, MEAL_CODE_KEYS)
            print(
                f"  python scripts/probe_mcp.py --city {self.city} --area {self.area} "
                f"--store-code {store_code} --item-code {example_code if example_code else '<商品编码>'}"
            )
            return

        # 步骤 3：商品详情
        started = time.perf_counter()
        detail_tool = find_tool(self.tools, "query-meal-detail")
        if detail_tool is None:
            self._fail("query-meal-detail", started, RpcError("tools/list 中没有该工具"), code=3)
            self.status = "failed"
            return
        ctx = Context(self.city, self.area, store_code, item_code)
        params, missing = build_params(detail_tool, ctx, extras={"storeCode": store_code})
        if missing:
            duration = (time.perf_counter() - started) * 1000
            for msg in missing:
                print(f"[query-meal-detail] 状态=fail 耗时={duration / 1000:.2f}s\n  {msg}")
            self._step("query-meal-detail", "fail", duration, error="; ".join(missing))
            self.exit_code = 4
            self.status = "schema_blocked"
            return
        try:
            result = self.client.call_tool(str(detail_tool.get("name")), params)
        except (AuthError, RateLimitError, NetworkError, HttpError, RpcError, ToolCallError) as exc:
            self._fail("query-meal-detail", started, exc, code=2 if isinstance(exc, AuthError) else 3)
            self.status = "failed"
            return
        duration = (time.perf_counter() - started) * 1000
        payload = tool_payload(result)
        prices = collect_prices(payload)
        print(f"[query-meal-detail] 状态=ok 耗时={duration / 1000:.2f}s")
        print(f"  请求参数：{json.dumps(redact(params), ensure_ascii=False)}")
        if prices:
            print("  金额字段：")
            for item in prices:
                print(f"    {item}")
        self._step("query-meal-detail", "ok", duration, price_summary=prices)

        # 步骤 4：计价（不传 beCode）
        started = time.perf_counter()
        price_tool = find_tool(self.tools, "calculate-price")
        if price_tool is None:
            self._fail("calculate-price", started, RpcError("tools/list 中没有该工具"), code=3)
            self.status = "failed"
            return
        ctx = Context(self.city, self.area, store_code, item_code, need_products=True)
        params, missing = build_params(
            price_tool, ctx, extras={"orderType": 1, "beType": 1, "storeCode": store_code}
        )
        if missing:
            duration = (time.perf_counter() - started) * 1000
            for msg in missing:
                print(f"[calculate-price] 状态=fail 耗时={duration / 1000:.2f}s\n  {msg}")
            self._step("calculate-price", "fail", duration, error="; ".join(missing))
            self.exit_code = 4
            self.status = "schema_blocked"
            return
        try:
            result = self.client.call_tool(str(price_tool.get("name")), params)
        except (AuthError, RateLimitError, NetworkError, HttpError, RpcError, ToolCallError) as exc:
            self._fail("calculate-price", started, exc, code=2 if isinstance(exc, AuthError) else 3)
            self.status = "failed"
            return
        duration = (time.perf_counter() - started) * 1000
        payload = tool_payload(result)
        prices = collect_prices(payload)
        print(f"[calculate-price] 状态=ok 耗时={duration / 1000:.2f}s")
        print(f"  请求参数：{json.dumps(redact(params), ensure_ascii=False)}")
        if prices:
            print("  金额字段：")
            for item in prices:
                print(f"    {item}")
        else:
            print("  未在响应中找到金额字段，请人工核对返回结构。")
        self._step("calculate-price", "ok", duration, price_summary=prices)
        self.status = "ok"
        self.exit_code = 0


def write_probe_json(report):
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = RUNTIME_DIR / f"probe-{stamp}.json"
    with path.open("w", encoding="utf-8") as handle:
        json.dump(redact(report), handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    return path


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass

    parser = argparse.ArgumentParser(
        prog="probe_mcp.py",
        description="麦当劳官方 MCP 探针：真实调用并记录走到哪一步（不模拟、不猜参数）。",
        epilog=(
            "退出码：0 全通 / 1 未配置 Token / 2 认证失败 / 3 网络或服务端错误 / "
            "4 必填字段无法满足 / 5 需要人工补 --store-code 或 --item-code（非错误）"
        ),
    )
    parser.add_argument("--city", required=True, help="城市，例如 上海")
    parser.add_argument("--area", required=True, help="商圈/地址，例如 人民广场")
    parser.add_argument("--store-code", default=None, help="真实门店编码（不填则停在门店列表）")
    parser.add_argument("--item-code", default=None, help="真实商品编码（不填则停在商品列表）")
    parser.add_argument("--endpoint", default=None, help="覆盖默认 MCP 地址")
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args(argv)

    started_at = datetime.now().astimezone().isoformat(timespec="seconds")
    report = {
        "probe_version": "0.1.0",
        "generated_at": started_at,
        "status": "running",
        "endpoint": args.endpoint or "https://mcp.mcd.cn",
        "query": {"city": args.city, "area": args.area},
        "store_code": args.store_code,
        "item_code": args.item_code,
        "tools": [],
        "target_tools": {},
        "steps": [],
        "notes": [],
    }

    try:
        client = McdClient(
            token=None,
            endpoint=args.endpoint or "https://mcp.mcd.cn",
            timeout=args.timeout,
        )
    except MissingTokenError:
        print(TOKEN_GUIDE, file=sys.stderr)
        report["status"] = "blocked"
        report["notes"].append("缺少 MCD_MCP_TOKEN，未发起任何网络请求。")
        path = write_probe_json(report)
        print(f"[blocked] 探针记录已写出：{path.relative_to(RUNTIME_DIR.parent)}", file=sys.stderr)
        return 1

    probe = Probe(client, args.city, args.area)
    probe.run(store_code=args.store_code, item_code=args.item_code)

    report["status"] = probe.status
    report["tools"] = [str(t.get("name")) for t in probe.tools]
    if probe.tools:
        report["target_tools"] = {target: find_tool(probe.tools, target) is not None for target in TARGET_TOOLS}
    else:
        # 没拿到 tools/list 时不能断言工具不存在
        report["target_tools"] = {}
        report["notes"].append("未取得 tools/list，目标工具是否存在仍未知。")
    report["steps"] = probe.steps
    report["exit_code"] = probe.exit_code
    if probe.status == "awaiting_store_code":
        report["notes"].append("停在门店列表：等待用户指定 --store-code，未自动选择门店。")
    elif probe.status == "awaiting_item_code":
        report["notes"].append("停在商品列表：等待用户指定 --item-code，未自动选择商品。")
    elif probe.status == "blocked":
        report["notes"].append("缺少 Token。")
    if probe.status == "ok":
        report["notes"].append("四步全部走通；金额单位仍需人工确认。")

    path = write_probe_json(report)
    print(f"\n探针记录：{path}")
    print(f"最终状态：{probe.status}（退出码 {probe.exit_code}）")
    return probe.exit_code


if __name__ == "__main__":
    sys.exit(main())
