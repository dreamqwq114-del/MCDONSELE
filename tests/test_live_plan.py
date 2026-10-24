# -*- coding: utf-8 -*-
"""live_plan.py 的离线单元测试。

用桩客户端喂进构造好的 calculate-price 返回，不联网、不读凭据。
覆盖两个只在真实/异常报价下才暴露的缺陷：
  1. 不得把无法解释的金额差额反推成"额外费用"；
  2. 同一商品编码被多个成员分别点单时，聚合小计不能整份记到每一行。
"""

import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"


def _load_live_plan():
    """按文件路径加载 scripts/live_plan.py；它 import mcd_client，所以先补 sys.path。"""
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location("live_plan_under_test", SCRIPTS / "live_plan.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


live_plan = _load_live_plan()


class StubClient:
    """替身客户端：把预设的 data 连同一层 MCP 信封返回。"""

    def __init__(self, data):
        self.data = data
        self.calls = []

    def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        return {
            "isError": False,
            "structuredContent": {"success": True, "code": 200, "message": "请求成功",
                                  "datetime": "2026-10-24 19:00:00", "data": self.data},
        }


def priced(price, product_price, product_original, product_list, discount=0, **fees):
    data = {
        "price": price,
        "productPrice": product_price,
        "productOriginalPrice": product_original,
        "discount": discount,
        "productList": product_list,
    }
    data.update(fees)
    return data


def row(code, qty, original_subtotal, subtotal=None):
    return {
        "productCode": code,
        "productName": code,
        "quantity": qty,
        "originalSubtotal": original_subtotal,
        "subtotal": original_subtotal if subtotal is None else subtotal,
    }


class TestQuoteReconciliation(unittest.TestCase):
    """费用只能来自官方明确字段，差额不得被自我合理化。"""

    def test_unexplained_residual_is_rejected(self):
        # 复现：商品 2000、总额 2300、优惠 0、明确费用字段全为 0。
        # 旧实现用 fee = total - product + discount 反推出 300 分并标 verified。
        client = StubClient(
            priced(2300, 2000, 2000, [row("X1", 1, 2000)],
                   deliveryPrice=0, packingPrice=0, tablewarePrice=0)
        )
        planner = live_plan.LivePlanner(client)
        selection = [{"line_id": "L1", "code": "X1", "qty": 1}]
        with self.assertRaises(RuntimeError) as ctx:
            planner.quote("1450713", selection)
        message = str(ctx.exception)
        self.assertIn("金额口径待核实", message)
        self.assertIn("300", message)  # 差额要报出来，不能吞掉

    def test_explicit_fee_still_allocates(self):
        # 明确打包费 100 分时，总额 2100 = 2000 - 0 + 100，应当通过并计入费用项。
        client = StubClient(
            priced(2100, 2000, 2000, [row("X1", 1, 2000)],
                   deliveryPrice=0, packingPrice=100, tablewarePrice=0)
        )
        planner = live_plan.LivePlanner(client)
        quote, per_line = planner.quote("1450713", [{"line_id": "L1", "code": "X1", "qty": 1}])
        self.assertEqual(quote["fee_cents"], 100)
        self.assertEqual(quote["fee_items"], [{"label": "打包费", "cents": 100}])
        self.assertEqual(quote["total_cents"], 2100)
        self.assertEqual(per_line, {"L1": 2000})

    def test_coupon_basis_is_pre_discount(self):
        # 券前 3400、优惠 1352、券后 2048：分账基数取券前，逐行也取 originalSubtotal。
        client = StubClient(
            priced(2048, 2048, 3400, [row("C1", 1, 3400, subtotal=2048)], discount=1352,
                   deliveryPrice=0, packingPrice=0, tablewarePrice=0)
        )
        planner = live_plan.LivePlanner(client)
        quote, per_line = planner.quote("1450713", [{"line_id": "L1", "code": "C1", "qty": 1}])
        self.assertEqual(quote["product_cents"], 3400)
        self.assertEqual(quote["discount_cents"], 1352)
        self.assertEqual(quote["total_cents"], 2048)
        self.assertEqual(per_line, {"L1": 3400})

    def test_zero_price_is_not_free(self):
        client = StubClient(priced(0, 0, 0, []))
        planner = live_plan.LivePlanner(client)
        with self.assertRaises(RuntimeError) as ctx:
            planner.quote("1450713", [{"line_id": "L1", "code": "C1", "qty": 1}])
        self.assertIn("核不了价", str(ctx.exception))


class TestDuplicateCodeSplit(unittest.TestCase):
    """两个成员点同一款商品时，聚合小计必须拆回，而不是每行都记全额。"""

    def test_same_item_for_two_members_splits_evenly(self):
        # 复现：A、B 各点一份 20 元同款，官方聚合返回一份 4000 分的小计。
        client = StubClient(
            priced(4000, 4000, 4000, [row("X1", 2, 4000)],
                   deliveryPrice=0, packingPrice=0, tablewarePrice=0)
        )
        planner = live_plan.LivePlanner(client)
        selection = [
            {"line_id": "L1", "code": "X1", "qty": 1},
            {"line_id": "L2", "code": "X1", "qty": 1},
        ]
        _quote, per_line = planner.quote("1450713", selection)
        self.assertEqual(per_line["L1"] + per_line["L2"], 4000)
        self.assertEqual(per_line, {"L1": 2000, "L2": 2000})
        # 请求仍按编码聚合成一条，数量为 2
        self.assertEqual(client.calls[0][1]["items"], [{"productCode": "X1", "quantity": 2}])

    def test_uneven_prices_keep_total_exact(self):
        # 奇数金额拆两行：1001 分 → 501 / 500，合计仍为 1001，不丢不多。
        client = StubClient(
            priced(1001, 1001, 1001, [row("X1", 2, 1001)],
                   deliveryPrice=0, packingPrice=0, tablewarePrice=0)
        )
        planner = live_plan.LivePlanner(client)
        selection = [
            {"line_id": "L1", "code": "X1", "qty": 1},
            {"line_id": "L2", "code": "X1", "qty": 1},
        ]
        _quote, per_line = planner.quote("1450713", selection)
        self.assertEqual(sum(per_line.values()), 1001)
        self.assertEqual(sorted(per_line.values()), [500, 501])

    def test_line_missing_from_quote_is_rejected(self):
        # 官方只回了一行，选品里却有两行 → 不能按 0 元补缺。
        client = StubClient(
            priced(2000, 2000, 2000, [row("X1", 1, 2000)],
                   deliveryPrice=0, packingPrice=0, tablewarePrice=0)
        )
        planner = live_plan.LivePlanner(client)
        selection = [
            {"line_id": "L1", "code": "X1", "qty": 1},
            {"line_id": "L2", "code": "Y2", "qty": 1},
        ]
        with self.assertRaises(RuntimeError) as ctx:
            planner.quote("1450713", selection)
        self.assertIn("找不到", str(ctx.exception))


class TestDuplicateShareMembers(unittest.TestCase):
    """共享名单里的重复成员必须在解析阶段就被拒绝。"""

    def test_duplicate_members_rejected_by_parser(self):
        with self.assertRaises(ValueError) as ctx:
            live_plan.parse_selection("S:A+A+B:side:521741")
        self.assertIn("重复成员", str(ctx.exception))

    def test_distinct_members_still_parse(self):
        parsed = live_plan.parse_selection("S:A+B:side:521741,A:main:1406")
        self.assertEqual(parsed[0]["shared_by"], ["A", "B"])
        self.assertEqual(parsed[0]["owner"], None)
        self.assertEqual(parsed[1]["owner"], "A")


if __name__ == "__main__":
    unittest.main()
