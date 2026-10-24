# -*- coding: utf-8 -*-
"""麦单合伙人 · 金额核算核心。

读取符合 docs/RESULT_SCHEMA.md 的输入 JSON，为每套 plan 计算分账
（allocation）、可行性（feasible）与违规项（violations），并写出
best_plan_id。全程只用整数分，任何缺失价格都不会被当作 0 元。
"""

import argparse
import json
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path

SCHEMA_VERSION = "1.0"
DATA_MODES = ("live", "replay", "fictional")

# §5 编码表中全部为阻断项。
BLOCKING_CODES = frozenset(
    {
        "QUOTE_NOT_VERIFIED",
        "SHARING_RULE_PENDING",
        "MEMBER_OVER_BUDGET",
        "BUDGET_MISSING",
        "PRICE_MISSING",
        "UNBALANCED_TOTAL",
        "UNCLEAR_PRICE_BASIS",
        "FEE_SPLIT_PENDING",
        "MAIN_MISSING",
        "DRINK_MISSING",
        "EXCLUDED_ITEM",
        "COMBO_UNCONFIRMED",
        "SHARE_INELIGIBLE",
        "SHARE_DUPLICATE_MEMBER",
        "DUPLICATE_DRINK",
    }
)

# 出现这些编码时，数字本身不可信（缺价/口径不明/规则未定/无预算/共享名单不合法），
# 分账统一退化为 blocked 形态：payable 一律为 null，绝不输出 0 元。
BLOCKED_ALLOCATION_CODES = frozenset(
    {
        "QUOTE_NOT_VERIFIED",
        "SHARING_RULE_PENDING",
        "PRICE_MISSING",
        "UNCLEAR_PRICE_BASIS",
        "FEE_SPLIT_PENDING",
        "BUDGET_MISSING",
        "SHARE_DUPLICATE_MEMBER",
    }
)

DRINK_NAME_HINTS = ("可乐", "雪碧", "芬达", "饮", "茶", "咖啡", "果汁", "水", "奶")
MAIN_NAME_HINTS = ("堡", "卷", "鸡", "鱼", "饭", "派")
NO_FEASIBLE_NOTE = "当前已检查候选中没有满足全部条件的方案，需放宽预算/共享/必需品条件之一"


def split_amount_largest_remainder(amount, weights):
    """按最大余数法把整数分金额拆成若干份，合计严格等于 amount。

    余数相同则按 weights 的原始顺序优先，保证同一输入永远得到同一结果。
    负数先取绝对值分配再统一取负号。
    """
    if not weights:
        return []
    amount = int(amount)
    if amount == 0:
        return [0 for _ in weights]

    sign = -1 if amount < 0 else 1
    total = abs(amount)
    abs_weights = [abs(int(w)) for w in weights]
    weight_sum = sum(abs_weights)
    if weight_sum <= 0:
        raise ValueError("权重合计为 0，无法按比例分摊")

    bases = []
    remainders = []
    for weight in abs_weights:
        bases.append((total * weight) // weight_sum)
        remainders.append((total * weight) % weight_sum)

    left = total - sum(bases)
    order = sorted(range(len(abs_weights)), key=lambda i: (-remainders[i], i))
    for index in order[:left]:
        bases[index] += 1
    return [sign * base for base in bases]


def _cents_from_value(value, where, errors):
    """把单个 _cents 字段规整为整数分；非法时记录结构错误并返回 None。"""
    if value is None:
        return None
    if isinstance(value, bool):
        errors.append("%s 不能用布尔值当金额" % where)
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        errors.append("%s 出现浮点金额 %r，禁止浮点，请用整数分或元字符串" % (where, value))
        return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            errors.append("%s 为空字符串，无法作为金额" % where)
            return None
        try:
            scaled = Decimal(text) * 100
        except (InvalidOperation, ValueError):
            errors.append("%s 无法解析为金额：%r" % (where, value))
            return None
        if scaled != scaled.to_integral_value():
            errors.append("%s 换算成分后仍有小数残差：%r" % (where, value))
            return None
        return int(scaled)
    errors.append("%s 类型不支持：%r" % (where, type(value).__name__))
    return None


def _normalize_tree(node, path, errors):
    """递归复制数据，并把所有 *_cents 字段统一转成整数分。"""
    if isinstance(node, dict):
        out = {}
        for key, value in node.items():
            child = "%s.%s" % (path, key) if path else str(key)
            if key.endswith("_cents"):
                out[key] = _cents_from_value(value, child, errors)
            else:
                out[key] = _normalize_tree(value, child, errors)
        return out
    if isinstance(node, list):
        return [_normalize_tree(item, "%s[%d]" % (path, i), errors) for i, item in enumerate(node)]
    return node


def check_input(data):
    """校验顶层结构并就地规整金额字段；返回结构错误描述列表（空表示通过）。"""
    errors = []
    if not isinstance(data, dict):
        return ["根节点必须是 JSON 对象"]

    if data.get("schema_version") != SCHEMA_VERSION:
        errors.append("schema_version 必须为 %r，实际为 %r" % (SCHEMA_VERSION, data.get("schema_version")))
    if data.get("data_mode") not in DATA_MODES:
        errors.append("data_mode 必须显式取 live / replay / fictional，实际为 %r" % (data.get("data_mode"),))

    store = data.get("store")
    if not isinstance(store, dict) or not store.get("store_code"):
        errors.append("store.store_code 缺失")

    order = data.get("order")
    if order is not None and not isinstance(order, dict):
        errors.append("order 必须是对象")

    members = data.get("members")
    if not isinstance(members, list) or not members:
        errors.append("members 必须是非空数组")
        members = []

    seen_ids = set()
    for index, member in enumerate(members):
        where = "members[%d]" % index
        if not isinstance(member, dict):
            errors.append("%s 必须是对象" % where)
            continue
        member_id = member.get("id")
        if not isinstance(member_id, str) or not member_id.strip():
            errors.append("%s.id 缺失或为空" % where)
            continue
        if member_id in seen_ids:
            errors.append("%s.id 重复：%s" % (where, member_id))
        seen_ids.add(member_id)
        if member.get("drink") not in (None, "required", "optional", "none"):
            errors.append("%s.drink 取值非法：%r" % (where, member.get("drink")))
        excludes = member.get("excludes")
        if excludes is not None and not isinstance(excludes, list):
            errors.append("%s.excludes 必须是字符串数组" % where)

    rule = data.get("sharing_rule")
    if not isinstance(rule, dict):
        errors.append("sharing_rule 必须是对象")
        rule = {}

    plans = data.get("plans")
    if not isinstance(plans, list) or not plans:
        errors.append("plans 必须是非空数组")
        plans = []

    seen_plan_ids = set()
    for index, plan in enumerate(plans):
        where = "plans[%d]" % index
        if not isinstance(plan, dict):
            errors.append("%s 必须是对象" % where)
            continue
        plan_id = plan.get("plan_id")
        if not isinstance(plan_id, str) or not plan_id.strip():
            errors.append("%s.plan_id 缺失或为空" % where)
        elif plan_id in seen_plan_ids:
            errors.append("%s.plan_id 重复：%s" % (where, plan_id))
        else:
            seen_plan_ids.add(plan_id)
        if not isinstance(plan.get("lines"), list):
            errors.append("%s.lines 必须是数组" % where)
        if not isinstance(plan.get("quote"), dict):
            errors.append("%s.quote 必须是对象" % where)

    normalized = _normalize_tree(data, "", errors)
    if not errors:
        data.clear()
        data.update(normalized)
    return errors


def _line_participants(line):
    """返回 (参与者 id 列表, 是否共享行)：共享行取 shared_by，个人行取 owner。"""
    shared_by = [str(m) for m in (line.get("shared_by") or [])]
    if shared_by:
        return shared_by, True
    owner = line.get("owner")
    return ([str(owner)] if owner else []), False


def _label(items, fallback):
    """从 fee_items / discount_items 里取第一个可用标签。"""
    if isinstance(items, list):
        for item in items:
            if isinstance(item, dict) and item.get("label"):
                return str(item["label"])
    return fallback


def _component_text(component):
    """套餐组成项的文字描述（兼容字符串与对象两种写法）。"""
    if isinstance(component, str):
        return component, ""
    if isinstance(component, dict):
        return str(component.get("name") or ""), str(component.get("kind") or "")
    return "", ""


def _is_drink_component(component):
    """判断套餐组成项是否为饮料（兼容字符串与对象两种写法）。"""
    text, kind = _component_text(component)
    if kind == "drink":
        return True
    return any(hint in text for hint in DRINK_NAME_HINTS)


def _is_main_component(component):
    """判断套餐组成项是否为主食。

    只认「已确认的套餐组成」里的真实名称；名称按官方返回，不做主观猜测。
    推断命中即视为满足，但结果里会说明这是按名称判定。
    """
    text, kind = _component_text(component)
    if kind == "main":
        return True
    if kind in ("drink", "side"):
        return False
    return any(hint in text for hint in MAIN_NAME_HINTS)


def _line_satisfies(line, mid, wanted):
    """该行是否能满足成员 mid 的 wanted（main / drink）需求。

    个人行看 owner；套餐按已确认的组成判断——一个「汉堡 + 可乐」的两件套
    确实同时满足主食和饮料，不能因为它是套餐就判不满足。
    """
    if line.get("owner") != mid:
        return False
    kind = line.get("kind")
    if kind == wanted:
        return True
    if kind != "combo":
        return False
    combo = line.get("combo") or {}
    if combo.get("confirmed") is not True:
        return False
    checker = _is_main_component if wanted == "main" else _is_drink_component
    return any(checker(item) for item in (combo.get("components") or []))


def _blocked_members(members):
    """blocked 形态下的成员列表：应付与剩余一律 null。"""
    rows = []
    for member in members:
        budget = member.get("budget_cents")
        rows.append(
            {
                "id": member.get("id"),
                "payable_cents": None,
                "budget_cents": budget,
                "remaining_cents": None,
                "over_budget": None,
            }
        )
    return rows


def _build_allocation(lines, members, rule, quote, add_violation):
    """正常路径：按行分摊 + 最大余数法分折扣与费用，返回 allocation。"""
    member_ids = [m["id"] for m in members]
    budgets = {m["id"]: m.get("budget_cents") for m in members}
    total_cents = quote.get("total_cents")
    discount_total = quote.get("discount_cents") or 0
    fee_total = quote.get("fee_cents") or 0

    # 1) 整单优惠按行金额权重分摊
    line_totals = [line.get("line_total_cents") or 0 for line in lines]
    if discount_total:
        line_discounts = split_amount_largest_remainder(discount_total, line_totals)
    else:
        line_discounts = [0 for _ in lines]

    allocated = [total - disc for total, disc in zip(line_totals, line_discounts)]

    # 2) 行内分摊：个人行归 owner，共享行只在参与者内均分
    line_rows = []
    discount_by_member = dict((mid, 0) for mid in member_ids)
    for line, discount, net in zip(lines, line_discounts, allocated):
        participants, _shared = _line_participants(line)
        if not participants:
            raise ValueError("行 %s 既无 owner 也无 shared_by，金额无处归属" % line.get("line_id"))
        shares_cents = split_amount_largest_remainder(net, [1] * len(participants))
        shares = [{"member": mid, "cents": value} for mid, value in zip(participants, shares_cents)]

        if discount:
            discount_parts = split_amount_largest_remainder(discount, [1] * len(participants))
            for mid, value in zip(participants, discount_parts):
                if mid in discount_by_member:
                    discount_by_member[mid] += value

        line_rows.append(
            {
                "line_id": line.get("line_id"),
                "allocated_cents": net,
                "shares": shares,
            }
        )

    # 3) 额外费用分摊：全员均分 / 指定承担人全额
    fees = []
    fee_by_member = dict((mid, 0) for mid in member_ids)
    if fee_total:
        mode = ((rule.get("extra_fee") or {}).get("mode"))
        label = _label(quote.get("fee_items"), "额外费用")
        if mode == "designated":
            bearer = (rule.get("extra_fee") or {}).get("bearer")
            split_cents = [fee_total if mid == bearer else 0 for mid in member_ids]
        else:
            split_cents = split_amount_largest_remainder(fee_total, [1] * len(member_ids))
        for mid, value in zip(member_ids, split_cents):
            fee_by_member[mid] += value
        fees.append(
            {
                "label": label,
                "cents": fee_total,
                "split": [{"member": mid, "cents": value} for mid, value in zip(member_ids, split_cents)],
            }
        )

    # 4) 汇总到成员：payable = 各行净分摊 + 费用
    member_rows = []
    sum_payable = 0
    for member in members:
        mid = member["id"]
        payable = sum(
            item["cents"] for row in line_rows for item in row["shares"] if item["member"] == mid
        ) + fee_by_member.get(mid, 0)
        budget = budgets.get(mid)
        over = payable > budget if isinstance(budget, int) else False
        member_rows.append(
            {
                "id": mid,
                "payable_cents": payable,
                "budget_cents": budget,
                "remaining_cents": (budget - payable) if isinstance(budget, int) else None,
                "over_budget": over,
            }
        )
        sum_payable += payable
        if over:
            add_violation("MEMBER_OVER_BUDGET", "成员 %s 应付 %d 分，超出本人预算 %d 分" % (mid, payable, budget))

    discounts = []
    if discount_total:
        discounts.append(
            {
                "label": _label(quote.get("discount_items"), "整单优惠"),
                "cents": -discount_total,
                "split": [{"member": mid, "cents": -discount_by_member.get(mid, 0)} for mid in member_ids],
            }
        )

    balanced = sum_payable == total_cents
    if not balanced:
        add_violation(
            "UNBALANCED_TOTAL",
            "成员应付合计 %d 分不等于官方总额 %r 分" % (sum_payable, total_cents),
        )

    return {
        "method": "proportional_on_discount" if discount_total else "line_direct",
        "notes": (
            "含整单优惠，按行金额比例分摊；行内 shares 已扣除优惠，discounts 仅作展示不重复相加"
            if discount_total
            else "无整单优惠，按行金额直接归属"
        ),
        "lines": line_rows,
        "fees": fees,
        "discounts": discounts,
        "members": member_rows,
        "total_check": {
            "sum_payable_cents": sum_payable,
            "quote_total_cents": total_cents,
            "balanced": balanced,
        },
    }


def allocate_plan(plan, members, rule):
    """核算单套方案，返回补齐 allocation / feasible / violations 的 plan 副本。"""
    lines = plan.get("lines") or []
    quote = plan.get("quote") or {}
    member_ids = [m["id"] for m in members]
    budgets = dict((m["id"], m.get("budget_cents")) for m in members)

    violations = []

    def add_violation(code, message):
        """追加违规项，同一 (code, message) 只记一次。"""
        for existing in violations:
            if existing["code"] == code and existing["message"] == message:
                return
        violations.append({"code": code, "level": "blocking", "message": message})

    # —— 状态类 ——
    if quote.get("status") != "verified":
        add_violation(
            "QUOTE_NOT_VERIFIED",
            "报价状态为 %r，不是 verified（换店/超时后旧报价即为 stale）" % (quote.get("status"),),
        )
    if rule.get("status") != "confirmed":
        add_violation(
            "SHARING_RULE_PENDING",
            "分账规则状态为 %r，未确认前不得给出确定应付" % (rule.get("status"),),
        )

    # —— 价格完整性：缺失绝不当 0 ——
    missing_lines = [
        line for line in lines if line.get("price_state") == "missing" or line.get("line_total_cents") is None
    ]
    for line in missing_lines:
        add_violation(
            "PRICE_MISSING",
            "行 %s（%s）价格为缺失，不得按 0 元处理" % (line.get("line_id"), line.get("name")),
        )

    # —— 金额口径 ——
    product_cents = quote.get("product_cents")
    total_cents = quote.get("total_cents")
    discount_cents = quote.get("discount_cents") or 0
    fee_cents = quote.get("fee_cents") or 0

    if product_cents is None or total_cents is None:
        add_violation("UNCLEAR_PRICE_BASIS", "quote 缺少 product_cents 或 total_cents，无法核对金额口径")
    else:
        if not missing_lines:
            line_sum = sum(line.get("line_total_cents") or 0 for line in lines)
            if line_sum != product_cents:
                add_violation(
                    "UNCLEAR_PRICE_BASIS",
                    "各行金额合计 %d 分不等于引用商品部分 %d 分" % (line_sum, product_cents),
                )
        if product_cents - discount_cents + fee_cents != total_cents:
            add_violation(
                "UNCLEAR_PRICE_BASIS",
                "商品 %d - 优惠 %d + 费用 %d 不等于总额 %d，差额不得塞进打包费"
                % (product_cents, discount_cents, fee_cents, total_cents),
            )

    # —— 额外费用分摊方式 ——
    extra_fee = rule.get("extra_fee") or {}
    fee_mode = extra_fee.get("mode")
    if fee_cents > 0:
        if fee_mode == "pending" or fee_mode not in ("equal_all", "designated"):
            add_violation(
                "FEE_SPLIT_PENDING",
                "存在 %d 分额外费用但分摊方式为 %r，未定不得输出确定应付" % (fee_cents, fee_mode),
            )
        elif fee_mode == "designated" and extra_fee.get("bearer") not in member_ids:
            add_violation(
                "FEE_SPLIT_PENDING",
                "额外费用指定承担人 %r 不在成员列表中" % (extra_fee.get("bearer"),),
            )

    # —— 共享资格 ——
    willingness = dict((m["id"], m.get("share_willing", True)) for m in members)
    for line in lines:
        raw_shared = [str(x).strip() for x in (line.get("shared_by") or [])]
        # 重复成员会变成隐含分摊权重（A+A+B 等于 A 摊两份），必须在入口就拒绝
        duplicates = sorted({mid for mid in raw_shared if raw_shared.count(mid) > 1})
        if duplicates:
            add_violation(
                "SHARE_DUPLICATE_MEMBER",
                "行 %s 的共享名单含重复成员 %s（%s），重复成员不能当加权"
                % (line.get("line_id"), "、".join(duplicates), "+".join(raw_shared)),
            )
            continue
        shared_by, is_shared = _line_participants(line)
        if not is_shared:
            continue
        for mid in shared_by:
            if mid not in willingness:
                add_violation("SHARE_INELIGIBLE", "行 %s 的共享参与者 %s 不在成员列表中" % (line.get("line_id"), mid))
            elif willingness[mid] is False:
                add_violation(
                    "SHARE_INELIGIBLE",
                    "行 %s 的共享名单包含不愿共享的成员 %s" % (line.get("line_id"), mid),
                )

    # —— 行结构与归属 ——
    for line in lines:
        participants, _shared = _line_participants(line)
        if not participants:
            add_violation(
                "UNCLEAR_PRICE_BASIS",
                "行 %s 既无 owner 也无 shared_by，金额无处归属" % line.get("line_id"),
            )

    # —— 必需项 ——
    for member in members:
        mid = member["id"]
        if member.get("main_required"):
            if not any(_line_satisfies(line, mid, "main") for line in lines):
                add_violation("MAIN_MISSING", "成员 %s 要求主食，但没有归属该成员的主食行" % mid)
        if member.get("drink") == "required":
            if not any(_line_satisfies(line, mid, "drink") for line in lines):
                add_violation("DRINK_MISSING", "成员 %s 必需饮料，但没有归属该成员的饮料行" % mid)

        budget = budgets.get(mid)
        if not isinstance(budget, int) or budget <= 0:
            add_violation("BUDGET_MISSING", "成员 %s 未设有效预算（%r 分）" % (mid, budget))

    # —— 排除项 ——
    for line in lines:
        name = str(line.get("name") or "")
        for member in members:
            for keyword in member.get("excludes") or []:
                if keyword and str(keyword) in name:
                    add_violation(
                        "EXCLUDED_ITEM",
                        "行 %s（%s）命中成员 %s 的排除项 %r" % (line.get("line_id"), name, member["id"], keyword),
                    )

    # —— 套餐：未确认组成却拆成多行计价 ——
    codes = {}
    for line in lines:
        code = line.get("code")
        combo = line.get("combo") or {}
        if line.get("kind") == "combo" or combo.get("components"):
            codes.setdefault(code, []).append(line)
    for code, group in codes.items():
        if len(group) > 1 and any((line.get("combo") or {}).get("confirmed") is not True for line in group):
            add_violation(
                "COMBO_UNCONFIRMED",
                "套餐 %r 未确认组成却被拆成 %d 行计价" % (code, len(group)),
            )

    # —— 套餐已含饮料又重复计入 ——
    for combo_line in lines:
        combo = combo_line.get("combo") or {}
        if combo.get("confirmed") is not True:
            continue
        owner = combo_line.get("owner")
        if not any(_is_drink_component(item) for item in (combo.get("components") or [])):
            continue
        for line in lines:
            if line is combo_line or line.get("kind") != "drink":
                continue
            if line.get("owner") == owner:
                add_violation(
                    "DUPLICATE_DRINK",
                    "套餐 %s 已含饮料，成员 %s 又单点 %s" % (combo_line.get("line_id"), owner, line.get("name")),
                )

    # —— 分账 ——
    codes_now = set(item["code"] for item in violations)
    if codes_now & BLOCKED_ALLOCATION_CODES:
        reasons = "；".join("%s：%s" % (item["code"], item["message"]) for item in violations)
        allocation = {
            "method": "blocked",
            "notes": reasons,
            "lines": [],
            "fees": [],
            "discounts": [],
            "members": _blocked_members(members),
            "total_check": {
                "sum_payable_cents": None,
                "quote_total_cents": total_cents,
                "balanced": False,
            },
        }
    else:
        try:
            allocation = _build_allocation(lines, members, rule, quote, add_violation)
        except ValueError as exc:
            add_violation("UNCLEAR_PRICE_BASIS", str(exc))
            reasons = "；".join("%s：%s" % (item["code"], item["message"]) for item in violations)
            allocation = {
                "method": "blocked",
                "notes": reasons,
                "lines": [],
                "fees": [],
                "discounts": [],
                "members": _blocked_members(members),
                "total_check": {
                    "sum_payable_cents": None,
                    "quote_total_cents": total_cents,
                    "balanced": False,
                },
            }

    result = dict(plan)
    result["allocation"] = allocation
    result["violations"] = violations
    result["feasible"] = not any(item["code"] in BLOCKING_CODES for item in violations)
    return result


def run_check(data):
    """逐套核算方案，写回 allocation/feasible/violations 与 best_plan_id。"""
    members = data.get("members") or []
    rule = data.get("sharing_rule") or {}
    plans = [allocate_plan(plan, members, rule) for plan in data.get("plans") or []]

    feasible_ids = [plan["plan_id"] for plan in plans if plan.get("feasible")]
    best_plan_id = feasible_ids[0] if feasible_ids else None

    result = dict(data)
    result["plans"] = plans
    result["best_plan_id"] = best_plan_id

    notes = list(data.get("notes") or [])
    if best_plan_id is None and NO_FEASIBLE_NOTE not in notes:
        notes.append(NO_FEASIBLE_NOTE)
    result["notes"] = notes
    return result


def _fail(message):
    """输出结构性错误并返回退出码 2。"""
    print(message, file=sys.stderr)
    return 2


def main(argv=None):
    """CLI 入口：0 = 至少一套可行，1 = 全不可行，2 = 输入结构性错误。"""
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

    parser = argparse.ArgumentParser(description="麦单合伙人：核算每套方案的分账与可行性")
    parser.add_argument("--in", dest="in_path", required=True, help="输入 JSON 路径")
    parser.add_argument("--out", dest="out_path", required=True, help="结果 JSON 输出路径")
    parser.add_argument("--quiet", action="store_true", help="不输出人类可读摘要到 stderr")
    args = parser.parse_args(argv)

    in_path = Path(args.in_path)
    out_path = Path(args.out_path)

    try:
        with open(in_path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except OSError as exc:
        return _fail("无法读取输入文件 %s：%s" % (in_path, exc))
    except json.JSONDecodeError as exc:
        return _fail("输入不是合法 JSON（%s）：%s" % (in_path, exc))

    errors = check_input(data)
    if errors:
        for item in errors:
            print("输入结构错误：%s" % item, file=sys.stderr)
        return 2

    result = run_check(data)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
        handle.write("\n")

    feasible_ids = [plan["plan_id"] for plan in result["plans"] if plan.get("feasible")]
    if not args.quiet:
        print("数据模式：%s" % result.get("data_mode"), file=sys.stderr)
        for plan in result["plans"]:
            if plan["allocation"]["method"] == "blocked":
                codes = "、".join(item["code"] for item in plan["violations"]) or "无"
                print("  %s：不可计算（%s）" % (plan["plan_id"], codes), file=sys.stderr)
                continue
            detail = "，".join(
                "%s %d 分" % (row["id"], row["payable_cents"]) for row in plan["allocation"]["members"]
            )
            flag = "可行" if plan["feasible"] else "不可行"
            print("  %s：%s；%s" % (plan["plan_id"], flag, detail), file=sys.stderr)
        print("best_plan_id：%s" % result["best_plan_id"], file=sys.stderr)

    print(str(out_path))
    return 0 if feasible_ids else 1


if __name__ == "__main__":
    sys.exit(main())
