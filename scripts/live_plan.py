# -*- coding: utf-8 -*-
"""把真实麦当劳 MCP 的门店菜单与官方报价，转成 RESULT_SCHEMA 的输入 JSON。

这一步补的是「MCP 能告诉你多少钱」和「check_plan 能算每人多少」之间的缺口：
不手抄价格、不估算金额，所有金额都来自 query-meals 的菜单价与 calculate-price 的报价。

用法：
    python scripts/live_plan.py \
        --city 上海 --area 人民广场 --store-code 1450713 \
        --budget A=3000 --budget B=3500 \
        --plan "P1|主食齐全 + 共享脆薯饼|A:main:1406,B:main:1450,B:drink:9900008751,S:A+B:side:521741" \
        --plan "P2|不含共享小食|A:main:1406,B:main:1450,B:drink:9900008751" \
        --out runtime/live-input.json

选品语法：逗号分隔，每项 `归属:类别:商品编码`
    归属 = 成员 id（个人行）或 `S:成员1+成员2`（共享行）
    类别 = main | drink | side | combo | other
    `S:A+B:side:521741` 表示 A 与 B 共享一份 521741

只做到店自取（beType=1 / orderType=1），不传 beCode。
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mcd_client import (  # noqa: E402
    AuthError,
    HttpError,
    McdClient,
    MissingTokenError,
    NetworkError,
    RateLimitError,
    RpcError,
    ToolCallError,
    redact,
)

CST = timezone(timedelta(hours=8))
BE_TYPE_PICKUP = 1
ORDER_TYPE_PICKUP = 1


def now_iso() -> str:
    return datetime.now(CST).replace(microsecond=0).isoformat()


def yuan_to_cents(text) -> int | None:
    """官方菜单价是元字符串（如 "33.5"），转整数分；有小数残差就不认。"""
    if text is None:
        return None
    try:
        value = Decimal(str(text)) * 100
    except (InvalidOperation, ValueError):
        return None
    if value != value.to_integral_value():
        return None
    return int(value)


def _split_amount(amount: int, weights: list[int]) -> list[int]:
    """把 amount 按 weights 拆成整数份，最大余数法，合计严格等于 amount。"""
    total_weight = sum(weights)
    if total_weight <= 0:
        raise ValueError("拆分权重必须为正")
    shares = [amount * weight // total_weight for weight in weights]
    remainders = [amount * weight % total_weight for weight in weights]
    left = amount - sum(shares)
    order = sorted(range(len(weights)), key=lambda i: (-remainders[i], i))
    for index in order[:left]:
        shares[index] += 1
    return shares


def parse_selection(spec: str):
    """`A:main:1406,B:drink:9900008751,S:A+B:side:521741` → lines[]"""
    lines = []
    for index, chunk in enumerate(spec.split(","), 1):
        chunk = chunk.strip()
        if not chunk:
            continue
        parts = chunk.split(":")
        if len(parts) == 3:
            owner, kind, code = parts
            shared_by = []
        elif len(parts) == 4 and parts[0].upper() == "S":
            owner = None
            kind, code = parts[2], parts[3]
            shared_by = [m.strip() for m in parts[1].split("+") if m.strip()]
            if not shared_by:
                raise ValueError(f"共享行缺少参与成员：{chunk}")
            if len(shared_by) != len(set(shared_by)):
                raise ValueError(f"共享名单里有重复成员：{parts[1]}。重复成员会变成隐含权重，必须去掉重复项")
        else:
            raise ValueError(f"看不懂的选品写法：{chunk}（应为 归属:类别:编码 或 S:成员1+成员2:类别:编码）")
        lines.append(
            {
                "line_id": f"L{index}",
                "code": str(code).strip(),
                "kind": kind.strip(),
                "qty": 1,
                "owner": owner,
                "shared_by": shared_by,
            }
        )
    return lines


def parse_budgets(items) -> dict[str, int]:
    budgets = {}
    for raw in items:
        if "=" not in raw:
            raise ValueError(f"预算写法应为 A=3000（单位：分），收到 {raw}")
        member, value = raw.split("=", 1)
        budgets[member.strip()] = int(value.strip())
    return budgets


def extract_data(result):
    """统一取出业务数据：优先 structuredContent.data，退回 content[0].text 的 JSON。"""
    if isinstance(result, dict):
        structured = result.get("structuredContent")
        if isinstance(structured, dict) and isinstance(structured.get("data"), (dict, list)):
            return structured["data"], structured
        for part in result.get("content") or []:
            if isinstance(part, dict) and part.get("type") == "text":
                try:
                    payload = json.loads(part.get("text") or "")
                except json.JSONDecodeError:
                    continue
                if isinstance(payload, dict) and isinstance(payload.get("data"), (dict, list)):
                    return payload["data"], payload
    return None, None


class LivePlanner:
    def __init__(self, client: McdClient):
        self.client = client
        self.raw: list[dict] = []

    def _call(self, tool: str, args: dict, keep: bool = True):
        started = datetime.now(CST)
        try:
            result = self.client.call_tool(tool, args)
        except (MissingTokenError, AuthError, RateLimitError, NetworkError, HttpError, RpcError, ToolCallError) as exc:
            raise RuntimeError(f"{tool} 调用失败：{exc}") from exc
        elapsed = (datetime.now(CST) - started).total_seconds()
        if keep and isinstance(result, dict) and result.get("isError"):
            raise RuntimeError(f"{tool} 返回 isError：{str(result)[:300]}")
        data, envelope = extract_data(result)
        if keep:
            self.raw.append(
                {
                    "tool": tool,
                    "request": redact(args),
                    "status": (envelope or {}).get("message"),
                    "datetime": (envelope or {}).get("datetime"),
                    "elapsed_s": round(elapsed, 2),
                    "data": redact(data),
                }
            )
        return data, envelope

    def find_store(self, city: str, area: str, store_code: str) -> dict:
        data, _ = self._call(
            "query-nearby-stores",
            {"beType": BE_TYPE_PICKUP, "searchType": 2, "city": city, "keyword": area},
        )
        stores = []
        if isinstance(data, dict):
            for key in ("stores", "storeList", "list", "shopList"):
                if isinstance(data.get(key), list):
                    stores = data[key]
                    break
        elif isinstance(data, list):
            stores = data
        for store in stores:
            if str(store.get("storeCode")) == str(store_code):
                return store
        if not stores:
            raise RuntimeError(f"{city}/{area} 没查到门店，换个商圈或城市再试")
        raise RuntimeError(
            f"门店 {store_code} 不在 {city}/{area} 的返回结果里；返回的门店有："
            + "、".join(f"{s.get('storeCode')}={s.get('storeName')}" for s in stores[:8])
        )

    def menu(self, store_code: str) -> dict[str, dict]:
        """返回 {商品编码: {name, current_cents, original_cents, category}}"""
        data, _ = self._call(
            "query-meals",
            {"storeCode": str(store_code), "orderType": ORDER_TYPE_PICKUP, "beType": BE_TYPE_PICKUP},
        )
        meals = {}
        categories = []
        if isinstance(data, dict):
            raw_meals = data.get("meals")
            if isinstance(raw_meals, dict):
                meals = raw_meals
            elif isinstance(raw_meals, list):
                meals = {str(m.get("code")): m for m in raw_meals}
            categories = data.get("categories") or []

        # 分类名里可能有换行（官方返回如此），统一清掉再匹配
        category_of = {}
        for category in categories:
            name = str(category.get("name") or "").replace("\n", "").replace("\r", "")
            for item in category.get("meals") or []:
                category_of[str(item.get("code"))] = name

        index = {}
        for code, info in meals.items():
            index[str(code)] = {
                "code": str(code),
                "name": info.get("name") or "",
                "current_cents": yuan_to_cents(info.get("currentPrice")),
                "original_cents": yuan_to_cents(info.get("originalPrice")),
                "category": category_of.get(str(code), ""),
            }
        if not index:
            raise RuntimeError("query-meals 没有返回商品，无法配餐")
        return index

    def coupon_products(self, store_code: str) -> dict[str, str]:
        """从 query-store-coupons 取「券可用商品」的编码与名称。

        存在券专属商品：它不在 query-meals 的菜单里，但能用券核价。
        名称只能来自真实返回，不能自己编。
        """
        try:
            data, _ = self._call(
                "query-store-coupons",
                {
                    "storeCode": str(store_code),
                    "orderType": ORDER_TYPE_PICKUP,
                    "beType": BE_TYPE_PICKUP,
                },
                keep=False,
            )
        except RuntimeError:
            return {}
        mapping = {}
        for coupon in data if isinstance(data, list) else []:
            for product in coupon.get("products") or []:
                code = product.get("productCode")
                name = product.get("productName")
                if code and name:
                    mapping[str(code)] = str(name)
        return mapping

    def combo_components(self, store_code: str, code: str) -> dict:
        """用 query-meal-detail 的默认选项确认套餐真实组成。

        规则：每一轮（round）必须有且只有一个 isDefault=1 的选项，否则视为「组成未确认」，
        整份不能作为一行计价；类别按官方名称推断，不凭想象拆价。
        """
        data = self.detail(store_code, code)
        rounds = (data or {}).get("rounds") or []
        if not rounds:
            raise RuntimeError(f"套餐 {code} 的详情里没有选配轮次，组成无法确认，不能按套餐计价")
        components = []
        for rnd in rounds:
            defaults = [c for c in (rnd.get("choices") or []) if c.get("isDefault") == 1]
            if len(defaults) != 1:
                raise RuntimeError(
                    f"套餐 {code} 的「{rnd.get('name')}」默认选项不唯一（{len(defaults)} 个），组成未确认"
                )
            choice = defaults[0]
            name = str(choice.get("name") or "")
            components.append(
                {
                    "code": str(choice.get("code") or ""),
                    "name": name,
                    "kind": _guess_kind(name),
                    "round": str(rnd.get("name") or ""),
                }
            )
        return {"confirmed": True, "components": components}

    def detail(self, store_code: str, code: str):
        data, _ = self._call(
            "query-meal-detail",
            {
                "storeCode": str(store_code),
                "orderType": ORDER_TYPE_PICKUP,
                "beType": BE_TYPE_PICKUP,
                "code": str(code),
            },
        )
        return data

    def quote(self, store_code: str, selection: list[dict], coupons: dict[str, dict] | None = None):
        """调官方核价，返回 (quote 字段, {line_id: 券前小计})。

        几个容易算错的地方（都踩过真实数据）：
        1. 官方 `productList[].subtotal` 是**券后**金额，`originalSubtotal` 才是券前。
           分账口径是「券前商品额 - 整单优惠 + 明确费用 = 应付」，所以逐行取 originalSubtotal。
        2. `price=0` 且 `productList=[]` 是「这个组合核不了价」，不是免费。
        3. **费用只认官方返回的明确字段**（配送费/打包费/餐具费）。绝不把
           `总额 - 商品额 + 优惠` 的残差当成费用——那样任何无法解释的差额都会自我合理化。
        4. 同一编码的多条选品行，必须把聚合后的小计按数量拆回去，
           不能把整份小计同时记到每一行（否则两个人点同款就会翻倍）。
        """
        coupons = coupons or {}
        line_ids_by_code: dict[str, list[tuple[str, int]]] = {}
        for item in selection:
            line_ids_by_code.setdefault(str(item["code"]), []).append(
                (str(item["line_id"]), int(item.get("qty") or 1))
            )

        items = []
        for code, entries in line_ids_by_code.items():
            row = {"productCode": code, "quantity": sum(qty for _, qty in entries)}
            if code in coupons:
                row["couponId"] = coupons[code]["couponId"]
                row["couponCode"] = coupons[code]["couponCode"]
            items.append(row)

        data, envelope = self._call(
            "calculate-price",
            {
                "storeCode": str(store_code),
                "orderType": ORDER_TYPE_PICKUP,
                "beType": BE_TYPE_PICKUP,
                "items": items,
            },
        )
        if not isinstance(data, dict):
            raise RuntimeError("calculate-price 没有返回可解析的报价数据")

        total = data.get("price")
        product_net = data.get("productPrice")
        product_original = data.get("productOriginalPrice")
        discount = int(data.get("discount") or 0)
        product_list = data.get("productList") or []

        if total is None or product_net is None:
            raise RuntimeError("calculate-price 缺少 price / productPrice 字段，不能当 0 处理")
        if int(total) == 0 or not product_list:
            raise RuntimeError(
                "calculate-price 返回 price=0 或商品列表为空：这不是「免费」，是这个组合核不了价"
                "（常见原因：商品在该店不可售、券与商品不匹配、缺少必选套餐子项）。拒绝输出方案。"
            )

        basis = int(product_original) if product_original is not None else int(product_net) + discount
        if basis - discount != int(product_net):
            raise RuntimeError(
                f"商品金额口径对不上：券前 {basis} - 优惠 {discount} ≠ 券后 {product_net}，"
                "拒绝生成方案，需核对接口返回"
            )

        # 费用只来自明确字段，不反推、不补差
        fee_items = []
        for label, key in (("配送费", "deliveryPrice"), ("打包费", "packingPrice"), ("餐具费", "tablewarePrice")):
            raw = data.get(key)
            if raw is None:
                continue
            if int(raw) != 0:
                fee_items.append({"label": label, "cents": int(raw)})
        fee = sum(item["cents"] for item in fee_items)

        expected = basis - discount + fee
        if expected != int(total):
            raise RuntimeError(
                f"金额口径待核实：券前商品 {basis} - 优惠 {discount} + 明确费用 {fee} = {expected} 分，"
                f"与官方总额 {total} 分相差 {int(total) - expected} 分。"
                "差额来源不明，不能塞进打包费，方案不予确认。"
            )

        # 聚合小计按行拆回，保证每行只承担自己那份
        per_line: dict[str, int] = {}
        for row in product_list:
            code = str(row.get("productCode"))
            entries = line_ids_by_code.get(code)
            if not entries:
                continue
            value = row.get("originalSubtotal")
            if value is None:
                value = row.get("subtotal")
            if value is None:
                continue
            chunks = _split_amount(int(value), [qty for _, qty in entries])
            for (line_id, _), chunk in zip(entries, chunks):
                per_line[line_id] = chunk

        missing = [
            line_id
            for entries in line_ids_by_code.values()
            for line_id, _ in entries
            if line_id not in per_line
        ]
        if missing:
            raise RuntimeError(f"报价里找不到这些选品行的金额：{missing}，拒绝按 0 元补缺")

        quote = {
            "status": "verified",
            "quoted_at": _iso_from_service(envelope),
            "total_cents": int(total),
            "product_cents": basis,
            "fee_cents": fee,
            "discount_cents": discount,
            "fee_items": fee_items,
            "discount_items": ([{"label": "整单优惠", "cents": discount}] if discount else []),
            "field_map": {
                "total": "data.price",
                "product": "data.productOriginalPrice",
                "discount": "data.discount",
                "fee": "data.deliveryPrice + data.packingPrice + data.tablewarePrice",
            },
            "explain": (
                f"总额 {_yuan(int(total))} 元 = 商品 {_yuan(basis)} 元"
                + (f" - 优惠 {_yuan(discount)} 元" if discount else "")
                + (" + " + "、".join(f"{item['label']} {_yuan(item['cents'])} 元" for item in fee_items) if fee_items else "")
            ),
        }
        return quote, per_line


def _iso_from_service(envelope) -> str:
    stamp = (envelope or {}).get("datetime")
    if isinstance(stamp, str) and stamp:
        try:
            parsed = datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S").replace(tzinfo=CST)
            return parsed.isoformat()
        except ValueError:
            pass
    return now_iso()


DRINK_HINTS = ("可乐", "雪碧", "芬达", "饮", "茶", "咖啡", "果汁", "水", "奶")
SIDE_HINTS = ("薯条", "薯饼", "鸡块", "鸡翅", "玉米", "沙拉", "苹果片", "圆筒", "派", "麦旋风")
MAIN_HINTS = ("堡", "卷", "鸡", "鱼", "饭")


def _guess_kind(name: str) -> str:
    """按官方名称判断套餐组成项的类别。名称来自接口，类别是推断，故单列一个函数。"""
    for hint in SIDE_HINTS:
        if hint in name:
            return "side"
    for hint in DRINK_HINTS:
        if hint in name:
            return "drink"
    for hint in MAIN_HINTS:
        if hint in name:
            return "main"
    return "other"


def _yuan(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    cents = abs(int(cents))
    return f"{sign}{cents // 100}.{cents % 100:02d}"


def build_lines(selection, menu, line_subtotals, members_by_id):
    """把选品拼成计费行；金额按 line_id 取，不按商品编码取（同款多人不会串账）。"""
    lines = []
    for item in selection:
        code = item["code"]
        info = menu.get(code)
        if info is None:
            raise RuntimeError(f"商品编码 {code} 不在该门店菜单里，不能编造")
        unit = info["current_cents"]
        line_total = line_subtotals.get(item["line_id"])
        if line_total is None:
            line_total = unit * int(item.get("qty") or 1) if unit is not None else None
        if line_total is None:
            raise RuntimeError(f"商品 {info['name']}({code}) 没有价格，拒绝按 0 元计入")
        if item["shared_by"]:
            unknown = [m for m in item["shared_by"] if m not in members_by_id]
            if unknown:
                raise RuntimeError(f"共享名单里的成员 {unknown} 不存在")
        elif item["owner"] not in members_by_id:
            raise RuntimeError(f"归属成员 {item['owner']} 不存在")
        lines.append(
            {
                "line_id": item["line_id"],
                "code": code,
                "name": info["name"],
                "kind": item["kind"],
                "qty": item["qty"],
                "unit_price_cents": unit,
                "line_total_cents": int(line_total),
                "price_state": "ok",
                "owner": item["owner"],
                "shared_by": item["shared_by"],
                "combo": {"confirmed": False, "components": []},
                "note": info["category"],
            }
        )
    return lines


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="真实 MCP 菜单 + 官方核价 → RESULT_SCHEMA 输入")
    parser.add_argument("--city", required=True)
    parser.add_argument("--area", required=True)
    parser.add_argument("--store-code", required=True)
    parser.add_argument("--budget", action="append", default=[], metavar="A=3000", help="成员预算，单位分")
    parser.add_argument("--plan", action="append", default=[], metavar="P1|说明|选品", help="方案，可重复")
    parser.add_argument(
        "--coupon",
        action="append",
        default=[],
        metavar="商品编码=券ID:券码",
        help="把一张真实券绑到某个商品上（券码只在请求里用，不会写进结果文件）",
    )
    parser.add_argument("--status", action="append", default=[], metavar="A=3000", help=argparse.SUPPRESS)
    parser.add_argument("--drink", action="append", default=[], metavar="A=required", help=argparse.SUPPRESS)
    parser.add_argument("--out", required=True)
    parser.add_argument("--raw-out", default=None, help="原始返回留档（已脱敏）")
    args = parser.parse_args()

    try:
        budgets = parse_budgets(args.budget)
    except ValueError as exc:
        print(f"[错误] {exc}", file=sys.stderr)
        return 2
    if not budgets:
        print("[错误] 至少给一个 --budget，例如 --budget A=3000（单位：分）", file=sys.stderr)
        return 2
    if not args.plan:
        print("[错误] 至少给一个 --plan", file=sys.stderr)
        return 2

    members = []
    for member_id, budget in budgets.items():
        members.append(
            {
                "id": member_id,
                "budget_cents": int(budget),
                "main_required": True,
                "drink": "optional",
                "excludes": [],
                "share_willing": True,
            }
        )
    members_by_id = {m["id"]: m for m in members}

    coupons = {}
    for raw in args.coupon:
        try:
            product, rest = raw.split("=", 1)
            coupon_id, coupon_code = rest.split(":", 1)
        except ValueError:
            print(f"[错误] --coupon 需写成 商品编码=券ID:券码，收到 {raw[:12]}…", file=sys.stderr)
            return 2
        coupons[product.strip()] = {"couponId": coupon_id.strip(), "couponCode": coupon_code.strip()}
    if coupons:
        print(f"[券] 已绑定 {len(coupons)} 张券到对应商品（券码仅用于本次请求，不落盘）")

    try:
        client = McdClient()
        client.initialize()
    except MissingTokenError as exc:
        print(f"[未配置] {exc}", file=sys.stderr)
        return 1
    except AuthError as exc:
        print(f"[认证失败] {exc}", file=sys.stderr)
        return 2
    except (NetworkError, HttpError) as exc:
        print(f"[连接失败] {exc}", file=sys.stderr)
        return 3

    planner = LivePlanner(client)
    try:
        store = planner.find_store(args.city, args.area, args.store_code)
        menu = planner.menu(args.store_code)
        print(f"[门店] {store.get('storeName')}（{args.store_code}）")
        print(f"[菜单] {len(menu)} 个在售商品，价格单位已确认：菜单 currentPrice 为「元」字符串，报价为「分」")

        # 券专属商品：不在菜单里，但绑了券就能核价。名称取自 query-store-coupons 的真实返回。
        wanted = set()
        for raw in args.plan:
            pieces = raw.split("|")
            if len(pieces) == 3:
                wanted.update(item["code"] for item in parse_selection(pieces[2]))
        missing = [code for code in wanted if code not in menu and code in coupons]
        if missing:
            names = planner.coupon_products(args.store_code)
            for code in missing:
                name = names.get(code)
                if not name:
                    raise RuntimeError(f"券绑定商品 {code} 既不在菜单里，也不在券可用商品里，无法确认名称")
                menu[code] = {
                    "code": code,
                    "name": name,
                    "current_cents": None,
                    "original_cents": None,
                    "category": "券可用商品（不在菜单列表）",
                }
            print(f"[券] {len(missing)} 个券专属商品不在菜单里，名称取自券列表真实返回")

        plans = []
        for raw in args.plan:
            pieces = raw.split("|")
            if len(pieces) != 3:
                raise RuntimeError(f"--plan 需写成 `P1|说明|选品`，收到：{raw[:60]}")
            plan_id, label, spec = (p.strip() for p in pieces)
            selection = parse_selection(spec)
            quote, line_subtotals = planner.quote(args.store_code, selection, coupons)
            lines = build_lines(selection, menu, line_subtotals, members_by_id)
            for line in lines:
                if line["kind"] == "combo":
                    combo = planner.combo_components(args.store_code, line["code"])
                    line["combo"] = combo
                    detail_text = " + ".join(c["name"] for c in combo["components"])
                    print(f"[套餐] {line['name']} 组成已确认：{detail_text}（整份归一人，不拆内部价格）")
            plans.append(
                {
                    "plan_id": plan_id,
                    "label": label,
                    "lines": lines,
                    "quote": quote,
                    "unmet": [],
                    "tradeoffs": [],
                }
            )
            print(
                f"[核价] {plan_id} {label} → 官方总额 {_yuan(quote['total_cents'])} 元"
                f"（商品 {_yuan(quote['product_cents'])}，优惠 {_yuan(quote['discount_cents'])}，费用 {_yuan(quote['fee_cents'])}）"
            )
    except RuntimeError as exc:
        print(f"[失败] {exc}", file=sys.stderr)
        return 4

    payload = {
        "schema_version": "1.0",
        "data_mode": "live",
        "generated_at": now_iso(),
        "store": {
            "store_code": str(args.store_code),
            "store_name": store.get("storeName") or "",
            "city": args.city,
            "area": args.area,
            "queried_at": now_iso(),
            "address": store.get("address") or "",
        },
        "order": {
            "mode": "pickup",
            "be_type": BE_TYPE_PICKUP,
            "order_type_code": ORDER_TYPE_PICKUP,
            "be_code": None,
        },
        "members": members,
        "sharing_rule": {
            "status": "confirmed",
            "personal_lines": "self_pay",
            "shared_lines": "equal_split_among_participants",
            "order_discount": "proportional_to_line_amount",
            "extra_fee": {"mode": "equal_all", "bearer": None},
        },
        "plans": plans,
        "best_plan_id": None,
        "notes": [
            f"数据来源：麦当劳官方 MCP，门店 {args.store_code}，核价时间 {(plans[0]['quote'] or {}).get('quoted_at') if plans else ''}",
            "价格为官方 calculate-price 返回，单位分；菜单 currentPrice 为元字符串，已用 Decimal 精确转换。",
        ],
        "sources": {
            "mcp_endpoint": "https://mcp.mcd.cn",
            "tools_used": ["query-nearby-stores", "query-meals", "calculate-price"],
        },
    }

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    raw_path = args.raw_out or str(out.with_name(out.stem + "-raw.json"))
    Path(raw_path).write_text(
        json.dumps({"generated_at": now_iso(), "calls": planner.raw}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"[输出] {out}")
    print(f"[留档] {raw_path}（已脱敏，可放进 runtime/ 不入库）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
