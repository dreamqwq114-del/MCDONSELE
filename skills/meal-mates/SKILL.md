---
name: meal-mates
description: 多人一起点麦当劳到店自取、每人预算不同、需要把账单按人分摊时使用。触发词：一起点麦当劳、拼单、分摊账单、AA、各付各的、预算不够、多人点餐、到店自取、麦当劳配餐。
---

# 麦单合伙人（meal-mates）

## 1. 这个 Skill 干什么

给 2 人（有余力可 3–4 人）配一单**到店自取**的麦当劳：用麦当劳官方 MCP 取门店、菜单与官方核价，生成符合 `docs/RESULT_SCHEMA.md` 的方案 JSON，再由 `scripts/check_plan.py` 算出每人应付与剩余预算，精确到分，最后渲染一张点单卡。

## 2. 职责边界

| 角色 | 负责 | 不负责 |
|---|---|---|
| 大模型 | 理解口味与预算偏好、提候选组合、解释取舍、组织回复 | 不算钱、不编价格、不猜套餐组成 |
| 麦当劳 MCP | 门店列表、菜单、商品详情、官方报价 | 不理解偏好、不做分账 |
| `scripts/check_plan.py` | 分账、预算判定、violations、可行性 | 不取数、不调价 |
| `scripts/render_card.py` | 把已算好的结果渲染成 HTML | 不重算任何金额 |

铁律：**禁止模型自己算钱，禁止模型自己编价格。** 任何出现在回复里的金额，都必须来自 MCP 返回或 `check_plan.py` 的输出。

## 3. 工作流

### 步骤 0 · 确认 MCP 已就绪

- 输入：当前会话的工具列表。
- 动作：检查是否存在 `query-nearby-stores`、`query-meals`、`calculate-price`。**没有就停止**，告知用户在 WorkBuddy 连接器里配置麦当劳 MCP：
  - 地址 `https://mcp.mcd.cn`，类型 Streamable HTTP
  - Header：`Authorization: Bearer <token>`
  - 仓库里有 `mcp-config.example.json` 作格式参考（占位符需手动替换）。
- 产物：一句"已就绪"或一条配置指引。
- 约束：**不要编造报价，不要向用户索要 Token。**

### 步骤 1 · 一次问齐需求

- 输入：用户原始描述。
- 动作：缺项一次问完，已知的不重复问：城市、商圈、人数、每人预算上限、主食是否必需、饮料（必选/可选/不要）、排除项（如不吃辣、不要咖啡）、是否愿意共享小食。
- 产物：需求清单，映射为 `members[]`（`id` 用 A/B/C… 匿名，禁止真实姓名）。
- 约束：成员数组最后一项确认后再进步骤 2。

### 步骤 2 · 选门店

- 输入：城市、商圈。
- 动作：调 `query-nearby-stores`（`searchType=2`、`beType=1`），列出门店名称/地址/距离给用户选。
- 产物：用户确认的 `store_code`（真实编码）。
- 约束：**不自动选店**，不替用户拍板。

### 步骤 3 · 取菜单并分类

- 输入：`store_code`。
- 动作：调 `query-meals`（`orderType=1`、`beType=1`、真实 `storeCode`），把商品按 `main` / `drink` / `side` 分类；套餐组成不明时先调 `query-meal-detail` 确认。
- 产物：候选商品池（`code`、`name`、`kind`、`currentPrice`、`originalPrice`）。
- 约束：商品 `code` 必须来自真实返回；套餐未确认组成不得拆价。

### 步骤 4 · 确认分账规则（只问一次）

- 输入：成员情况。
- 动作：一次性确认四条：① 个人行自付；② 共享行只在参与者间均分；③ 整单优惠按行金额比例分摊；④ 额外费用全员均分或指定承担者。
- 产物：`sharing_rule.status = "confirmed"`，否则 `"pending"`。
- 约束：确认后不再重复问；未确认就输出结果时，必须在回复里标"待确认"且不给出确定应付。

### 步骤 5 · 配餐与核价

- 输入：商品池、成员需求、分账规则。
- 动作：先出最多 5 个候选；对最有希望的 2–3 个调 `calculate-price` 核价（`orderType=1`、`beType=1`、真实 `storeCode`，**不传 `beCode`**）。核价失败再做有限替换（同价位换单品），不做无限搜索。
- 产物：每个候选的官方 `data.price` / `data.productPrice` 与 `quote` 字段。
- 约束：不传 `beCode`；不改金额凑预算。
- **套餐**：用 `--plan` 里的 `combo` 类别，`live_plan.py` 会调 `query-meal-detail` 确认每轮唯一默认选项；确认不了就不出方案，不要自己拆价。
- **优惠券**：用户明确要用券时才加 `--coupon 商品编码=券ID:券码`（来自 `query-store-coupons`）。**不自动领券、不自动挑券**，券码不进结果文件与卡片。
- **0 元不是免费**：`price=0` 或 `productList=[]` 表示核不了价，必须换组合，不能当 0 元成单。

### 步骤 6 · 核算并出卡

- 输入：候选方案。
- 动作（**首选**，`storeCode` 与商品编码都来自真实返回）：

```bash
python scripts/live_plan.py --city <城市> --area <商圈> --store-code <真实编码> \
  --budget A=3000 --budget B=3500 \
  --plan "P1|<一句话说明>|A:main:<编码>,B:main:<编码>,B:drink:<编码>,S:A+B:side:<编码>" \
  --plan "P2|<一句话说明>|..." \
  --out runtime/input.json
python scripts/check_plan.py  --in runtime/input.json --out runtime/result.json
python scripts/render_card.py --in runtime/result.json --out runtime/card.html --title "麦单合伙人 · <门店名>"
```

- 选品语法：`归属:类别:编码`；共享行写 `S:成员1+成员2:类别:编码`。类别用 `main`/`drink`/`side`/`combo`/`other`。预算单位是**分**（30 元 = 3000）。
- `live_plan.py` 会自己拒绝：编码不在该店菜单里、商品没有价格、共享名单里有不存在的成员。退出码 4 表示真实链路失败，把 stderr 原文转给用户，不要自己补数据。
- 只有在没有 MCP 的情况下才手工按 `docs/RESULT_SCHEMA.md` 写输入 JSON，并必须把 `data_mode` 标成 `fictional` 或 `replay`。
- 产物：结果 JSON、HTML 卡片、回复。
- 退出码：`0` 至少一套可行；`1` 全部不可行；`2` 输入结构或字段不合法（按 stderr 修 JSON 后重跑，不要手改结果里的金额）。

### 步骤 7 · 展示与收敛

- 输入：`runtime/result.json`。
- 动作：最多展示 2 套 `feasible=true` 的方案。全部不可行时，报告已检查范围 + 具体冲突（谁的哪项超了多少钱/缺什么），再问可放宽哪一项：预算、共享、必需品。
- 产物：方案对比或"放宽哪一项"的提问。
- 约束：**不私自加预算**，不擅自删掉必需项。

### 步骤 8 · 条件变更即重核

- 输入：用户改动（换店、换主食、改共享成员、改用餐方式）。
- 动作：把旧 `quote.status` 置为 `"stale"`，重新走步骤 3–6 核价。
- 产物：新结果 JSON + 新卡片。
- 约束：**只要改动了商品或门店，就不能沿用旧核价。** 只改分账规则时不必重新核价，但必须重跑 `check_plan.py`。

## 4. 金额与单位铁律

- 所有 `*_cents` 字段一律**整数分**，禁止浮点。
- 菜单里 `currentPrice` / `originalPrice` 是**字符串**，单位未确认前**不得"除以 100"**；换算必须走 `Decimal(text) * 100`，有小数残差就报错，不四舍五入。
- `calculate-price` 返回的金额字段单位是**分**。
- 价格为 `null` 或缺失时：该行 `price_state="missing"`，**不得按 0 处理**，整单不得标记 `verified`。
- 总额口径：`total = product - discount + fee`；`product_cents` 只用于核对，不在总额上再加一次；`fee_cents` 已含在总额里。
- `query-promotions` 是企业团餐用，普通到店自取**不用**。

## 5. 失败与降级

| 情况 | 处理 |
|---|---|
| 401 / 鉴权失败 | 停止调用，让用户在连接器里更新 Token，不要重试刷接口 |
| 429 / 限流 | 降频，最多重试一次，仍失败则报告已取到的部分 |
| 工具缺失（无 `query-meals` 等） | 停止并说明配置方法，不编造数据 |
| `calculate-price` 对套餐持续报错 | 降级为单品组合，并在 `notes` / `tradeoffs` 注明"套餐核价不可用，已降级单品" |
| `check_plan.py` 退出码 2 | 读 stderr 修 JSON 结构，不手改结果金额 |

## 6. 不要做的事

- 不下单、不支付、不领券、不查积分、不读配送地址。
- 不展示 Token、券码、手机号、真实姓名。
- 不宣称"全网最低"或任何无法核验的价格比较。
- 不把历史回放或虚构样例标成本次实时核价（`data_mode` 必须如实）。
- 不编造节省金额，不编造营业时间与库存。
- 不把缺价商品按 0 元计入，不用估算价冒充官方报价。

## 7. 输出格式

每次给结论，必须包含这 7 项：

1. 门店（名称 + 商圈）
2. 核价时间（`quote.quoted_at`，带时区）
3. 总额（元，括号内可给分）
4. 每人应付 / 剩余预算（来自 `allocation.members`）
5. 共享品与参与人（`shared_by`）
6. 卡片路径（`runtime/card.html`）
7. 数据来源标签：`live` / `replay` / `fictional`

方案不可行时，追加：命中的 violations code + 一句人话解释 + 建议放宽哪一项。

## 8. violations 编码（与 `docs/RESULT_SCHEMA.md` §5 一致，全部阻断）

`QUOTE_NOT_VERIFIED`（报价未 verified）、`SHARING_RULE_PENDING`（分账规则未确认）、`MEMBER_OVER_BUDGET`（超出本人预算）、`BUDGET_MISSING`（未设预算）、`PRICE_MISSING`（行价格缺失）、`UNBALANCED_TOTAL`（分账合计≠官方总额）、`UNCLEAR_PRICE_BASIS`（商品与总额差无法解释）、`FEE_SPLIT_PENDING`（额外费用分摊未定）、`MAIN_MISSING`（无主食）、`DRINK_MISSING`（必需饮料缺失）、`EXCLUDED_ITEM`（含排除商品）、`COMBO_UNCONFIRMED`（套餐未确认却拆价）、`SHARE_INELIGIBLE`（共享名单含不愿共享者）、`SHARE_DUPLICATE_MEMBER`（共享名单有重复成员）、`DUPLICATE_DRINK`（套餐含饮料又重复计入）。

修复顺序：先修状态类与缺价（`QUOTE_NOT_VERIFIED`、`SHARING_RULE_PENDING`、`PRICE_MISSING`、`BUDGET_MISSING`、`FEE_SPLIT_PENDING`、`UNCLEAR_PRICE_BASIS`）→ 这些存在时 `allocation` 会是 `method="blocked"`，应付为 `null`，属正常，不要填 0 → 再修商品结构类（`MAIN_MISSING`、`DRINK_MISSING`、`EXCLUDED_ITEM`、`COMBO_UNCONFIRMED`、`DUPLICATE_DRINK`）→ 最后看预算（`MEMBER_OVER_BUDGET`、`UNBALANCED_TOTAL`）。
