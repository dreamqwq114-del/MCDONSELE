# 数据契约（v1.0）

本文件是 `check_plan.py`、`render_card.py`、Skill 三者之间唯一的接口契约。
任何模块改动字段名，必须同步改本文件、`examples/fictional-input.json` 的字段语义和测试。

## 1. 金额规则

- 一切金额字段以 `_cents` 结尾，值为 **整数分**。禁止浮点。
- 元字符串转分：`Decimal(text) * 100`，再 `int(...)`，仅当结果无小数残差时通过；有残差则报 `UNPARSABLE_AMOUNT`，不得四舍五入糊过去。
- 禁止把缺失价格当 0：价格为 `null` 或缺失 → 该行 `price_state="missing"`，整单不得标记 `verified`。

## 2. 顶层结构

```jsonc
{
  "schema_version": "1.0",
  "data_mode": "live",          // live | replay | fictional —— 必须显式
  "generated_at": "2026-10-24T19:00:00+08:00",   // 带时区
  "store": {
    "store_code": "string",     // 官方门店编码
    "store_name": "string",
    "city": "string",
    "area": "string",           // 商圈
    "queried_at": "2026-10-24T18:59:00+08:00"
  },
  "order": {
    "mode": "pickup",           // 首版仅 pickup（到店自取）
    "be_type": 1,
    "order_type_code": 1,
    "be_code": null             // 到店自取必须为 null，不得传
  },
  "members": [
    {
      "id": "A",                // 匿名 ID，禁止真实姓名
      "budget_cents": 3000,
      "main_required": true,    // 必须有主食
      "drink": "optional",      // required | optional | none
      "excludes": ["咖啡"],      // 明确排除项，可为空
      "share_willing": true     // 是否愿意分摊共享品
    }
  ],
  "sharing_rule": {
    "status": "confirmed",      // confirmed | pending（pending 时不得输出确定应付）
    "personal_lines": "self_pay",
    "shared_lines": "equal_split_among_participants",
    "order_discount": "proportional_to_line_amount",
    "extra_fee": {
      "mode": "equal_all",      // equal_all | designated | pending
      "bearer": null            // designated 时填成员 id
    }
  },
  "plans": [ /* 见 §3 */ ],
  "best_plan_id": "P1",         // 取 plans 数组中第一个 feasible=true 的方案；全不可行时为 null
                                // 注意：不对候选做择优/比价排序，顺序 = 人的判断顺序
  "notes": ["给人看的取舍/局限说明"],
  "sources": {
    "mcp_endpoint": "https://mcp.mcd.cn",
    "tools_used": ["query-nearby-stores", "query-meals", "query-meal-detail", "calculate-price"]
  }
}
```

## 2.1 价格口径（关键）

`quote` 里三个金额的关系是：

```text
total_cents = product_cents - discount_cents + fee_cents
```

- `product_cents` 是**券前商品额**，对应官方 `data.productOriginalPrice`。
- `line_total_cents` 是**券前**行金额，对应 `productList[].originalSubtotal`。
- 官方 `productList[].subtotal` 是**券后**金额，**不能**拿来当 `line_total_cents`，
  否则 `discount_cents` 会被扣第二次。

## 2.2 套餐组成

```jsonc
"combo": {
  "confirmed": true,              // 每一轮都有唯一默认选项时为 true
  "components": [
    {"code": "521952", "name": "龙焰鸡腿堡", "kind": "main",  "round": "龙焰鸡腿堡"},
    {"code": "3050",   "name": "可乐中杯",   "kind": "drink", "round": "选择饮料"}
  ]
}
```

- `confirmed=false` 时：不得把套餐拆成多行计价，也不得认定它满足主食/饮料。
- `confirmed=true` 时：整份归一个 owner，内部不拆价；且**组成里含主食即满足该成员的「必须有主食」，含饮料即满足「饮料必选」**。
  `kind` 按官方名称推断（先判 side，再判 drink，最后判 main），名称本身是接口返回的原文。
- 已确认套餐含饮料，同一 owner 又单点饮料 → `DUPLICATE_DRINK`。

## 3. 方案（plan）

```jsonc
{
  "plan_id": "P1",
  "label": "主食齐全 + 共享大薯条",
  "lines": [
    {
      "line_id": "L1",
      "code": "官方商品编码",       // 必须来自真实返回；虚构样例用 TEST- 前缀
      "name": "巨无霸",
      "kind": "main",             // main | drink | side | combo | other
      "qty": 1,
      "unit_price_cents": 2400,   // 菜单参考价（可能为 null）
      "line_total_cents": 2400,   // 该行参考金额；null 表示缺价
      "price_state": "ok",        // ok | missing
      "owner": "A",               // 个人计费行归属；共享行为 null
      "shared_by": [],            // 共享行的参与者 id 列表；个人行为 []
      "combo": {"confirmed": false, "components": []},  // 套餐组成；confirmed=true 才可整份计一行
      "note": ""
    }
  ],
  "quote": {
    "status": "verified",         // verified | failed | stale | unverified
    "quoted_at": "2026-10-24T19:01:00+08:00",
    "total_cents": 6100,          // 对应官方 data.price
    "product_cents": 6400,        // 商品部分；用于核对，不在总额上再加
    "fee_cents": 0,               // 总额已含，解释用，不重复相加
    "discount_cents": 300,        // 正数表示优惠额
    "fee_items": [{"label": "打包费", "cents": 0}],
    "discount_items": [{"label": "整单优惠", "cents": 300}],
    "field_map": {"total": "data.price", "product": "data.productPrice"},
    "explain": "总额 = 商品 - 整单优惠；无额外费用"
  },
  "allocation": { /* check_plan.py 计算，见 §4 */ },
  "feasible": true,
  "violations": [],               // 见 §5
  "unmet": [],                    // 未满足的用户需求描述
  "tradeoffs": []                 // 取舍说明，禁止编造节省金额
}
```

## 4. 分账（allocation，由 `check_plan.py` 生成）

```jsonc
{
  "method": "proportional_on_discount",   // line_direct | proportional_on_discount | blocked
  "notes": "含整单优惠，按行金额比例分摊；行内 shares 已扣除优惠，discounts 仅作展示不重复相加",
  "lines": [
    {"line_id": "L1", "allocated_cents": 2287,
     "shares": [{"member": "A", "cents": 2287}]}
  ],
  "fees": [],
  "discounts": [{"label": "整单优惠", "cents": -300,
                 "split": [{"member": "A", "cents": -141}, {"member": "B", "cents": -159}]}],
  "members": [
    {"id": "A", "payable_cents": 2859, "budget_cents": 3000,
     "remaining_cents": 141, "over_budget": false},
    {"id": "B", "payable_cents": 3241, "budget_cents": 3500,
     "remaining_cents": 259, "over_budget": false}
  ],
  "total_check": {"sum_payable_cents": 6100, "quote_total_cents": 6100, "balanced": true}
}
```

以上数字是 `examples/fictional-input.json` 的真实输出，可作回归基准。

两个容易搞错的口径：

1. **`shares` 是折后净额**：`sum(shares) == allocated_cents == line_total_cents - 该行折扣`，所以
   `payable = Σ lines.shares + Σ fees.split`。
2. **`discounts[].split` 只做归因展示**（负数），**不参与 payable 求和**，否则优惠会被扣两次。
   渲染层与任何读结果的下游都按这两条来，不要自己重算。

硬性约束：`sum(members[].payable_cents) == quote.total_cents`，且每个 `shares` / `fees.split` 之和回到对应行/费用金额。

## 5. violations 编码表

所有 code 都会让 `feasible=false`，但分两层处理：

- **金额层未定（`allocation.method="blocked"`，`payable_cents=null`，页面显示"分账待确认"）**：
  `QUOTE_NOT_VERIFIED` `SHARING_RULE_PENDING` `PRICE_MISSING` `UNCLEAR_PRICE_BASIS`
  `FEE_SPLIT_PENDING` `BUDGET_MISSING` `SHARE_DUPLICATE_MEMBER`
  ——这些情况下数字本身不可信，宁可不给数字，也不给一个像真的数。
- **业务层不通过（金额照常算出，让用户看到差多少）**：
  `MEMBER_OVER_BUDGET` `UNBALANCED_TOTAL` `MAIN_MISSING` `DRINK_MISSING`
  `EXCLUDED_ITEM` `COMBO_UNCONFIRMED` `SHARE_INELIGIBLE` `DUPLICATE_DRINK`

violation 对象形状：`{"code": "...", "level": "blocking", "message": "人话说明"}`。

| code | 含义 | 阻断 |
|---|---|---|
| `QUOTE_NOT_VERIFIED` | 报价状态不是 verified | 是（金额层） |
| `SHARING_RULE_PENDING` | 分账规则未确认 | 是（金额层） |
| `PRICE_MISSING` | 行价格缺失 | 是（金额层） |
| `UNCLEAR_PRICE_BASIS` | 商品部分与总额差无法解释 / 行既无归属也无共享名单 / 权重全 0 | 是（金额层） |
| `FEE_SPLIT_PENDING` | 存在额外费用且分摊方式未定（含 designated 无有效承担者） | 是（金额层） |
| `BUDGET_MISSING` | 成员未设预算或预算 ≤ 0 | 是（金额层） |
| `MEMBER_OVER_BUDGET` | 某成员应付超本人预算 | 是（业务层） |
| `UNBALANCED_TOTAL` | 分账合计 ≠ 官方总额 | 是（业务层） |
| `MAIN_MISSING` | 成员无主食（按 owner 判定，共享的主食不算） | 是（业务层） |
| `DRINK_MISSING` | 必须饮料的成员没有饮料 | 是（业务层） |
| `EXCLUDED_ITEM` | 含被排除商品 | 是（业务层） |
| `COMBO_UNCONFIRMED` | 套餐未确认组成却拆价 | 是（业务层） |
| `SHARE_INELIGIBLE` | 共享名单含不愿共享者或成员不存在 | 是（业务层） |
| `SHARE_DUPLICATE_MEMBER` | 共享名单含重复成员（`A+A+B` 会被当成三个槽位） | 是（金额层） |
| `DUPLICATE_DRINK` | 套餐已含饮料又重复计入 | 是（业务层） |

## 6. CLI 契约

```bash
python scripts/check_plan.py  --in examples/fictional-input.json --out runtime/fictional-result.json [--quiet]
python scripts/render_card.py --in runtime/fictional-result.json --out runtime/card.html [--title "麦单合伙人 · 门店名"] [--template templates/result.html]
python scripts/mcd_client.py  --ping | --list-tools | --schema <tool> | --call <tool> --args '<json>' | --self-test
python scripts/probe_mcp.py   --city 上海 --area 人民广场 --store-code <code> --item-code <code>
```

退出码：

| 脚本 | 0 | 1 | 2 | 其他 |
|---|---|---|---|---|
| `check_plan.py` | 至少一套可行 | 无可行方案 | 输入结构/字段不合法 | — |
| `render_card.py` | 已生成 | 写文件失败 | 契约错误（缺 `schema_version`、`plans` 为空、坏 JSON） | — |
| `mcd_client.py` | 成功 | 未配置 Token | 认证失败 401 | 3 网络/服务端错误 |
| `probe_mcp.py` | 探针跑完 | 无 Token（写出 blocked 记录） | 认证失败 | 3 网络错误 · 4 缺必填字段无法推导 · 5 等待用户选门店/商品 |

`check_plan.py`：stderr 输出人类可读摘要（`--quiet` 关掉），stdout 输出结果 JSON 路径。结构错误时不写输出文件。
`render_card.py`：只渲染已计算结果，不在页面里重算；对 `data_mode` 与 `quote.status` 显示显著标签；`payable_cents=null` 显示"分账待确认"而非 0。
