# -*- coding: utf-8 -*-
"""check_plan.py 的单元测试。

只使用 examples/fictional-input.json 的虚构数据（每次从文件重新读取，
改动全部在内存副本上完成，绝不写回该文件），全程不联网。
"""

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "check_plan.py"
FIXTURE_PATH = ROOT / "examples" / "fictional-input.json"


def _load_module():
    """按文件路径加载 scripts/check_plan.py（不新增 scripts 包文件）。"""
    spec = importlib.util.spec_from_file_location("check_plan_under_test", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


check_plan = _load_module()


def fresh_input():
    """从虚构样例重新读取一份全新的输入对象（深拷贝，互不共享引用）。"""
    with open(FIXTURE_PATH, "r", encoding="utf-8") as handle:
        return json.load(handle)


def plan_of(result, plan_id):
    """按 plan_id 取核算后的方案。"""
    for plan in result["plans"]:
        if plan["plan_id"] == plan_id:
            return plan
    raise AssertionError("找不到方案 %s" % plan_id)


def codes_of(plan):
    """取方案的违规编码集合。"""
    return {item["code"] for item in plan["violations"]}


def payable_of(plan):
    """取 {成员 id: 应付分数}。"""
    return {row["id"]: row["payable_cents"] for row in plan["allocation"]["members"]}


def run_result(data):
    """跑一遍结构校验 + 核算，结构错误视为测试失败。"""
    errors = check_plan.check_input(data)
    if errors:
        raise AssertionError("输入结构不应报错：%s" % errors)
    return check_plan.run_check(data)


class TestLargestRemainder(unittest.TestCase):
    """最大余数法：一分钱都不能丢、不能重。"""

    def test_hundred_cents_split_into_three(self):
        parts = check_plan.split_amount_largest_remainder(100, [1, 1, 1])
        self.assertEqual(parts, [34, 33, 33])
        self.assertEqual(sum(parts), 100)

    def test_single_cent_goes_to_first_participant(self):
        parts = check_plan.split_amount_largest_remainder(1, [1, 1, 1])
        self.assertEqual(parts, [1, 0, 0])
        self.assertEqual(sum(parts), 1)

    def test_remainder_tie_follows_original_order(self):
        # 7/3：基数各 2，余 1 分按原始顺序给第 0 位，结果必须稳定可复现。
        self.assertEqual(check_plan.split_amount_largest_remainder(7, [1, 1, 1]), [3, 2, 2])
        self.assertEqual(check_plan.split_amount_largest_remainder(7, [1, 1, 1]), [3, 2, 2])

    def test_weighted_split_keeps_total(self):
        parts = check_plan.split_amount_largest_remainder(300, [2400, 1900, 900, 1200])
        self.assertEqual(parts, [113, 89, 42, 56])
        self.assertEqual(sum(parts), 300)

    def test_negative_amount_keeps_total(self):
        parts = check_plan.split_amount_largest_remainder(-100, [1, 1, 1])
        self.assertEqual(parts, [-34, -33, -33])
        self.assertEqual(sum(parts), -100)

    def test_zero_weight_sum_is_rejected(self):
        # 权重全 0 且金额 > 0 无法分摊，必须抛错而不是随意塞给某人。
        with self.assertRaises(ValueError):
            check_plan.split_amount_largest_remainder(100, [0, 0])

    def test_line_shares_sum_back_to_allocated(self):
        # 端到端：每行 shares 之和必须等于 allocated_cents，且成员合计等于官方总额。
        result = run_result(fresh_input())
        plan = plan_of(result, "P1")
        for row in plan["allocation"]["lines"]:
            self.assertEqual(sum(item["cents"] for item in row["shares"]), row["allocated_cents"])
        self.assertEqual(sum(payable_of(plan).values()), 6100)


class TestMemberBudget(unittest.TestCase):
    """个人预算逐人拦截：总预算够不能替代每人检查。"""

    def test_member_over_budget_blocks_even_if_total_is_enough(self):
        data = fresh_input()
        for member in data["members"]:
            member["budget_cents"] = 1000 if member["id"] == "A" else 8000
        result = run_result(data)
        plan = plan_of(result, "P2")
        # 总预算 9000 分 > 总额 4700 分，但 A 应付 1600 分超过自己的 1000 分。
        self.assertLessEqual(sum(m["budget_cents"] for m in data["members"]), 9000)
        self.assertFalse(plan["feasible"])
        self.assertIn("MEMBER_OVER_BUDGET", codes_of(plan))
        self.assertEqual(payable_of(plan)["A"], 1600)

    def test_budget_missing_blocks(self):
        data = fresh_input()
        del data["members"][0]["budget_cents"]
        result = run_result(data)
        plan = plan_of(result, "P2")
        self.assertFalse(plan["feasible"])
        self.assertIn("BUDGET_MISSING", codes_of(plan))
        self.assertEqual(plan["allocation"]["method"], "blocked")
        self.assertIsNone(payable_of(plan)["A"])


class TestShareEligibility(unittest.TestCase):
    """共享资格：未参与共享者在该行分摊为 0。"""

    def test_non_participant_pays_nothing_for_shared_line(self):
        data = fresh_input()
        for member in data["members"]:
            member["budget_cents"] = 4000
        # 大薯条改为 A 单独承担，B 不在 shared_by 中。
        data["plans"][0]["lines"][3]["shared_by"] = ["A"]
        result = run_result(data)
        plan = plan_of(result, "P1")
        side_row = [row for row in plan["allocation"]["lines"] if row["line_id"] == "L4"][0]
        shares = {item["member"]: item["cents"] for item in side_row["shares"]}
        self.assertEqual(shares, {"A": 1144})
        self.assertNotIn("B", shares)
        # B 只承担自己的主食与饮料：1811 + 858。
        self.assertEqual(payable_of(plan)["B"], 2669)
        self.assertEqual(sum(payable_of(plan).values()), 6100)

    def test_unwilling_member_in_shared_by_is_blocked(self):
        data = fresh_input()
        data["members"][1]["share_willing"] = False
        result = run_result(data)
        plan = plan_of(result, "P1")
        self.assertFalse(plan["feasible"])
        self.assertIn("SHARE_INELIGIBLE", codes_of(plan))

    def test_duplicate_shared_member_is_blocked(self):
        # 复现：共享名单写成 A+A+B 会被当成三个分摊槽位，
        # 结果 A 摊两份、B 摊一份，却仍然 feasible=true 且无提示。
        # 修复后必须阻断，且不得给出确定应付。
        data = fresh_input()
        for member in data["members"]:
            member["budget_cents"] = 5000
        data["plans"][0]["lines"][3]["shared_by"] = ["A", "A", "B"]
        plan = plan_of(run_result(data), "P1")
        self.assertFalse(plan["feasible"])
        self.assertIn("SHARE_DUPLICATE_MEMBER", codes_of(plan))
        self.assertEqual(plan["allocation"]["method"], "blocked")
        self.assertTrue(all(row["payable_cents"] is None for row in plan["allocation"]["members"]))


class TestComboChecks(unittest.TestCase):
    """套餐相关：重复计入饮料、未确认组成却拆价。"""

    def test_confirmed_combo_with_drink_plus_separate_drink_is_duplicate(self):
        # 判定依据：套餐 combo.confirmed=True 且组成里已含饮料，
        # 同一 owner 又有一行 kind="drink"，属于重复计入 → DUPLICATE_DRINK。
        data = fresh_input()
        plan = data["plans"][1]
        plan["lines"].append(
            {
                "line_id": "L4",
                "code": "TEST-COMBO-001",
                "name": "可乐套餐",
                "kind": "combo",
                "qty": 1,
                "unit_price_cents": 2500,
                "line_total_cents": 2500,
                "price_state": "ok",
                "owner": "B",
                "shared_by": [],
                "combo": {"confirmed": True, "components": [{"kind": "drink", "name": "中杯可口可乐"}]},
                "note": "",
            }
        )
        plan["quote"]["product_cents"] = 7200
        plan["quote"]["total_cents"] = 7200

        result = run_result(data)
        checked = plan_of(result, "P2")
        self.assertFalse(checked["feasible"])
        self.assertIn("DUPLICATE_DRINK", codes_of(checked))

    def test_confirmed_combo_counts_as_member_main(self):
        # 判定依据：真实两件套「汉堡 + 可乐」经 query-meal-detail 确认组成后，
        # 对成员 A 来说既是主食也是饮料。不能因为它是套餐就判 MAIN_MISSING。
        data = fresh_input()
        plan = data["plans"][1]
        plan["lines"][0] = {
            "line_id": "L1",
            "code": "TEST-COMBO-010",
            "name": "龙焰鸡腿堡两件套",
            "kind": "combo",
            "qty": 1,
            "unit_price_cents": 1600,
            "line_total_cents": 1600,
            "price_state": "ok",
            "owner": "A",
            "shared_by": [],
            "combo": {
                "confirmed": True,
                "components": [
                    {"code": "TEST-521952", "name": "龙焰鸡腿堡", "kind": "main", "round": "龙焰鸡腿堡"},
                    {"code": "TEST-3050", "name": "可乐中杯", "kind": "drink", "round": "选择饮料"},
                ],
            },
            "note": "",
        }
        checked = plan_of(run_result(data), "P2")
        self.assertNotIn("MAIN_MISSING", codes_of(checked))
        self.assertTrue(checked["feasible"])

    def test_unconfirmed_combo_does_not_count_as_main(self):
        # 判定依据：组成未确认（confirmed=False）时，不能凭套餐名就认定含主食。
        data = fresh_input()
        plan = data["plans"][1]
        plan["lines"][0].update(
            {
                "code": "TEST-COMBO-011",
                "name": "龙焰鸡腿堡两件套",
                "kind": "combo",
                "combo": {
                    "confirmed": False,
                    "components": [{"name": "龙焰鸡腿堡", "kind": "main"}],
                },
            }
        )
        checked = plan_of(run_result(data), "P2")
        self.assertFalse(checked["feasible"])
        self.assertIn("MAIN_MISSING", codes_of(checked))

    def test_unconfirmed_combo_split_across_lines_is_blocked(self):
        # 判定依据：同一 code 出现在多行（按组成拆价），但 combo.confirmed 不为 True
        # → COMBO_UNCONFIRMED（schema §5）。
        data = fresh_input()
        plan = data["plans"][1]
        for line in plan["lines"][:2]:
            line["code"] = "TEST-COMBO-009"
            line["kind"] = "combo"
            line["combo"] = {"confirmed": False, "components": []}
        result = run_result(data)
        checked = plan_of(result, "P2")
        self.assertFalse(checked["feasible"])
        self.assertIn("COMBO_UNCONFIRMED", codes_of(checked))


class TestDiscountAndFee(unittest.TestCase):
    """整单优惠与额外费用：可解释、不重复加、口径不明即阻断。"""

    def test_p1_discount_is_proportional_and_matches_official_total(self):
        result = run_result(fresh_input())
        plan = plan_of(result, "P1")
        allocation = plan["allocation"]
        self.assertEqual(allocation["method"], "proportional_on_discount")
        self.assertIn("按行金额比例分摊", allocation["notes"])
        self.assertEqual(len(allocation["discounts"]), 1)
        self.assertEqual(allocation["discounts"][0]["cents"], -300)
        self.assertEqual(sum(item["cents"] for item in allocation["discounts"][0]["split"]), -300)
        self.assertTrue(plan["feasible"])
        # A：2287（巨无霸折后）+ 572（薯条折后）= 2859；B：1811 + 858 + 572 = 3241。
        self.assertEqual(payable_of(plan), {"A": 2859, "B": 3241})
        self.assertEqual(sum(payable_of(plan).values()), 6100)
        self.assertEqual(allocation["total_check"]["quote_total_cents"], 6100)
        self.assertTrue(allocation["total_check"]["balanced"])

    def test_p2_without_discount_uses_line_direct(self):
        result = run_result(fresh_input())
        plan = plan_of(result, "P2")
        self.assertEqual(plan["allocation"]["method"], "line_direct")
        self.assertEqual(plan["allocation"]["discounts"], [])
        self.assertEqual(plan["allocation"]["fees"], [])
        self.assertEqual(payable_of(plan), {"A": 1600, "B": 3100})
        self.assertEqual(sum(payable_of(plan).values()), 4700)

    def test_fee_already_inside_total_is_not_added_twice(self):
        # 6400（商品）- 300（优惠）+ 300（费用）= 6400（总额），费用已包含在总额内。
        data = fresh_input()
        for member in data["members"]:
            member["budget_cents"] = 3800
        quote = data["plans"][0]["quote"]
        quote["fee_cents"] = 300
        quote["total_cents"] = 6400
        quote["fee_items"] = [{"label": "打包费", "cents": 300}]

        result = run_result(data)
        plan = plan_of(result, "P1")
        self.assertEqual(plan["allocation"]["fees"][0]["cents"], 300)
        self.assertEqual(sum(item["cents"] for item in plan["allocation"]["fees"][0]["split"]), 300)
        self.assertEqual(sum(payable_of(plan).values()), 6400)
        self.assertEqual(plan["allocation"]["total_check"]["sum_payable_cents"], 6400)
        self.assertTrue(plan["allocation"]["total_check"]["balanced"])
        self.assertLess(sum(payable_of(plan).values()), 6700)  # 没有重复加费用

    def test_designated_fee_bearer_pays_all(self):
        data = fresh_input()
        for member in data["members"]:
            member["budget_cents"] = 3800
        quote = data["plans"][0]["quote"]
        quote["fee_cents"] = 300
        quote["total_cents"] = 6400
        data["sharing_rule"]["extra_fee"] = {"mode": "designated", "bearer": "B"}

        result = run_result(data)
        plan = plan_of(result, "P1")
        self.assertEqual(payable_of(plan), {"A": 2859, "B": 3541})
        self.assertEqual(sum(payable_of(plan).values()), 6400)

    def test_unexplainable_price_basis_blocks(self):
        # 商品 6400 - 优惠 300 + 费用 300 = 6400，但总额仍是 6100，差额无法解释。
        data = fresh_input()
        quote = data["plans"][0]["quote"]
        quote["fee_cents"] = 300
        quote["fee_items"] = [{"label": "打包费", "cents": 300}]
        result = run_result(data)
        plan = plan_of(result, "P1")
        self.assertFalse(plan["feasible"])
        self.assertIn("UNCLEAR_PRICE_BASIS", codes_of(plan))
        self.assertEqual(plan["allocation"]["method"], "blocked")
        self.assertEqual(payable_of(plan), {"A": None, "B": None})

    def test_fee_split_pending_blocks(self):
        data = fresh_input()
        quote = data["plans"][0]["quote"]
        quote["fee_cents"] = 300
        quote["total_cents"] = 6400
        data["sharing_rule"]["extra_fee"] = {"mode": "pending", "bearer": None}
        result = run_result(data)
        plan = plan_of(result, "P1")
        self.assertFalse(plan["feasible"])
        self.assertIn("FEE_SPLIT_PENDING", codes_of(plan))
        self.assertEqual(plan["allocation"]["method"], "blocked")

    def test_zero_fee_needs_no_extra_question(self):
        # fee_cents = 0 时即便 extra_fee.mode 是 pending，也不应报 FEE_SPLIT_PENDING。
        data = fresh_input()
        data["sharing_rule"]["extra_fee"] = {"mode": "pending", "bearer": None}
        result = run_result(data)
        plan = plan_of(result, "P2")
        self.assertNotIn("FEE_SPLIT_PENDING", codes_of(plan))
        self.assertTrue(plan["feasible"])


class TestQuoteStatus(unittest.TestCase):
    """报价状态：stale（如换店导致旧报价失效）不得给出确定应付。"""

    def test_stale_quote_blocks_allocation(self):
        data = fresh_input()
        data["plans"][0]["quote"]["status"] = "stale"
        result = run_result(data)
        plan = plan_of(result, "P1")
        self.assertFalse(plan["feasible"])
        self.assertIn("QUOTE_NOT_VERIFIED", codes_of(plan))
        self.assertEqual(plan["allocation"]["method"], "blocked")
        self.assertEqual(payable_of(plan), {"A": None, "B": None})
        self.assertFalse(plan["allocation"]["total_check"]["balanced"])

    def test_sharing_rule_pending_blocks(self):
        data = fresh_input()
        data["sharing_rule"]["status"] = "pending"
        result = run_result(data)
        plan = plan_of(result, "P2")
        self.assertFalse(plan["feasible"])
        self.assertIn("SHARING_RULE_PENDING", codes_of(plan))
        self.assertIsNone(payable_of(plan)["B"])


class TestMissingData(unittest.TestCase):
    """不完整数据：价格为 null 必须阻断，绝不当 0 元。"""

    def test_null_price_blocks_and_never_becomes_zero(self):
        data = fresh_input()
        line = data["plans"][1]["lines"][2]
        line["line_total_cents"] = None
        line["price_state"] = "missing"

        result = run_result(data)
        plan = plan_of(result, "P2")
        self.assertFalse(plan["feasible"])
        self.assertIn("PRICE_MISSING", codes_of(plan))
        self.assertEqual(plan["allocation"]["method"], "blocked")
        self.assertEqual(plan["allocation"]["lines"], [])
        for row in plan["allocation"]["members"]:
            self.assertIsNone(row["payable_cents"])
            self.assertIsNone(row["remaining_cents"])
            self.assertIsNotNone(row["budget_cents"])

    def test_missing_main_and_drink_and_excluded_item(self):
        data = fresh_input()
        plan = data["plans"][1]
        plan["lines"] = [line for line in plan["lines"] if line["kind"] != "drink"]
        plan["quote"]["product_cents"] = 3800
        plan["quote"]["total_cents"] = 3800
        data["members"][1]["excludes"] = ["板烧"]
        result = run_result(data)
        checked = plan_of(result, "P2")
        self.assertIn("DRINK_MISSING", codes_of(checked))
        self.assertIn("EXCLUDED_ITEM", codes_of(checked))
        self.assertFalse(checked["feasible"])


class TestAmountParsing(unittest.TestCase):
    """金额解析：元字符串转分，残留小数或浮点一律结构性错误。"""

    def test_yuan_string_is_converted_to_cents(self):
        data = fresh_input()
        data["members"][0]["budget_cents"] = "30.00"
        data["plans"][1]["quote"]["total_cents"] = "47.00"
        self.assertEqual(check_plan.check_input(data), [])
        self.assertEqual(data["members"][0]["budget_cents"], 3000)
        self.assertEqual(data["plans"][1]["quote"]["total_cents"], 4700)

    def test_yuan_string_with_residual_decimals_is_rejected(self):
        data = fresh_input()
        data["members"][0]["budget_cents"] = "30.005"
        errors = check_plan.check_input(data)
        self.assertTrue(errors)
        self.assertIn("残差", errors[0])

    def test_float_amount_is_rejected(self):
        data = fresh_input()
        data["plans"][0]["quote"]["total_cents"] = 61.0
        errors = check_plan.check_input(data)
        self.assertTrue(errors)
        self.assertIn("浮点", errors[0])

    def test_missing_store_code_is_structural_error(self):
        data = fresh_input()
        del data["store"]["store_code"]
        self.assertTrue(check_plan.check_input(data))


class TestCli(unittest.TestCase):
    """CLI 行为：退出码、最优方案、notes 追加、端到端跑通。"""

    def _run_cli(self, data, extra_args=()):
        with tempfile.TemporaryDirectory() as tmp:
            in_path = Path(tmp) / "input.json"
            out_path = Path(tmp) / "out" / "result.json"
            with open(in_path, "w", encoding="utf-8") as handle:
                json.dump(data, handle, ensure_ascii=False)
            completed = subprocess.run(
                [sys.executable, str(SCRIPT_PATH), "--in", str(in_path), "--out", str(out_path), *extra_args],
                cwd=str(ROOT),
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            payload = None
            if out_path.exists():
                with open(out_path, "r", encoding="utf-8") as handle:
                    payload = json.load(handle)
            return completed, payload

    def test_fictional_example_end_to_end(self):
        # 直接用 examples/fictional-input.json 跑 CLI，退出码必须为 0。
        with tempfile.TemporaryDirectory() as tmp:
            out_path = Path(tmp) / "fictional-result.json"
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT_PATH),
                    "--in",
                    str(FIXTURE_PATH),
                    "--out",
                    str(out_path),
                ],
                cwd=str(ROOT),
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            with open(out_path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        self.assertEqual(payload["best_plan_id"], "P1")
        self.assertEqual(payload["data_mode"], "fictional")
        self.assertEqual([p["feasible"] for p in payload["plans"]], [True, True])
        self.assertEqual(sum(payable_of(plan_of(payload, "P1")).values()), 6100)
        self.assertEqual(sum(payable_of(plan_of(payload, "P2")).values()), 4700)

    def test_all_plans_blocked_returns_exit_code_one_and_note(self):
        data = fresh_input()
        for plan in data["plans"]:
            plan["quote"]["status"] = "stale"
        completed, payload = self._run_cli(data, extra_args=("--quiet",))
        self.assertEqual(completed.returncode, 1)
        self.assertIsNone(payload["best_plan_id"])
        self.assertFalse(any(plan["feasible"] for plan in payload["plans"]))
        self.assertIn(
            "当前已检查候选中没有满足全部条件的方案，需放宽预算/共享/必需品条件之一",
            payload["notes"],
        )
        # --quiet 不向 stderr 输出人类摘要，stdout 仍给出结果路径。
        self.assertEqual(completed.stderr.strip(), "")
        self.assertIn("result.json", completed.stdout)

    def test_structural_error_returns_exit_code_two(self):
        data = fresh_input()
        data["plans"][0]["quote"]["total_cents"] = "61.005"
        completed, payload = self._run_cli(data)
        self.assertEqual(completed.returncode, 2)
        self.assertIsNone(payload)
        self.assertIn("残差", completed.stderr)


if __name__ == "__main__":
    unittest.main()
