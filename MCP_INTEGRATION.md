# MCP 接入说明

本文记录「麦单合伙人」与麦当劳中国官方 MCP 的实际接入方式、参数来源、金额口径与失败处理。
**凡是未经真实返回验证的，都会在文中明确标注「未验证」**，不要当成已跑通的结论。

## 1. 服务与认证

| 项 | 值 | 来源 |
|---|---|---|
| 服务地址 | `https://mcp.mcd.cn` | 官方 README「2.1 接入地址」 |
| 传输协议 | Streamable HTTP | 官方 README「2.2 传输协议与安全」 |
| 认证 | `Authorization: Bearer <MCP_TOKEN>` | 同上 |
| 限流 | 600 次/分钟，超出返回 429 | 官方 README「2.4 注意事项」「2.7 错误码」 |
| Token 申请 | <https://open.mcd.cn/mcp>（手机号登录 → 控制台 → 激活） | 官方 README「1. 申请 MCP Token」 |
| 官方仓库 | <https://github.com/M-China/mcd-mcp-server> | — |

Token 只存在于环境变量 `MCD_MCP_TOKEN` 或 WorkBuddy 连接器配置中。`scripts/mcd_client.py` 不落盘、不打印、异常信息里出现的疑似凭据会被 `redact()` 抹成 `***`。

## 2. 本项目用到的工具

官方 README 只给出了工具名与描述，**没有给出入参 schema**。因此本项目不硬编码参数结构，而是运行时先 `tools/list` 读取 `inputSchema`，再按 schema 组装参数。名称匹配同时接受连字符与下划线（`query-meals` ≡ `query_meals`），不因客户端命名差异误判工具不存在。

| 工具 | 用途 | 本项目调用约束 |
|---|---|---|
| `query-nearby-stores` | 按城市/商圈搜门店 | `searchType=2`、`beType=1`；用户从结果中选店，程序不自动选 |
| `query-meals` | 取门店当前菜单 | `orderType=1`、`beType=1`、真实 `storeCode` |
| `query-meal-detail` | 确认套餐组成与可选项 | 真实商品 `code`；详情缺失的套餐先跳过 |
| `calculate-price` | 官方订单试算 | 真实编码 + 数量，`orderType=1`、`beType=1`，**不传 `beCode`** |
| `query-store-coupons` | 可选：本账号此门店可用券 | 只取券标识与适用商品，不在任何输出里暴露券码 |

不调用：`create-order`、`query-order`、`cancel-order`、`mall-*`、`draw-lottery`、`auto-bind-coupons`、`delivery-*`、`query-promotions`（**该工具当前用于企业团餐，不能用于普通到店优惠**）。

## 3. 调用顺序

```text
initialize (JSON-RPC 2.0, 协商协议版本, 取 Mcp-Session-Id)
  → notifications/initialized
  → tools/list                                   # 读 inputSchema，决定参数形状
  → tools/call query-nearby-stores   (城市/商圈)
  → [用户选定 storeCode]
  → tools/call query-meals           (storeCode, orderType=1, beType=1)
  → tools/call query-meal-detail     (商品 code)     # 仅套餐/需确认组成时
  → tools/call calculate-price       (商品列表, 不传 beCode)
  → 结果写入 docs/RESULT_SCHEMA.md 定义的 JSON → check_plan.py → render_card.py
```

`scripts/mcd_client.py` 实现了前三步与通用 `tools/call`；`scripts/probe_mcp.py` 实现了上面四步业务链路，并在缺少必填字段时**停止并打印缺哪个字段**，不猜参数、不生成假编码；`scripts/live_plan.py` 把菜单与报价直接转成 `RESULT_SCHEMA` 的输入，避免手抄价格。

协议版本按 `2025-06-18` → `2025-03-26` → `2024-11-05` 依次尝试。**实测：服务端接受 `2025-06-18`，首次尝试即通过**；且未返回 `Mcp-Session-Id`，属无状态模式，后续请求不带该头。

## 3.1 实测握手与工具发现

```text
连接状态：ok
协议版本：2025-06-18
服务端：mcd-mcp 1.0.0
会话：未返回 Mcp-Session-Id
工具数量：35
握手耗时：0.38s
```

工具命名与本文假设一致（连字符形式）。`query-meals` 等工具的 `inputSchema` 已逐个用 `--schema` 核对，参数如下：

| 工具 | required | 关键可选字段 |
|---|---|---|
| `query-nearby-stores` | `beType`, `searchType` | `city`, `keyword` ← **必填语义上需要，但 schema 未标 required** |
| `query-meals` | `storeCode`, `orderType`, `beType` | `beCode`（到店自取不传）、`reservationDate` |
| `query-meal-detail` | `storeCode`, `orderType`, `beType`, `code` | `beCode`（到店自取不传） |
| `calculate-price` | `storeCode`, `orderType`, `beType` | `items[]{productCode, quantity, roundList, modification, couponCode, couponId}`、`withOrder`、`needTableware` |

**踩过的坑**：`city` / `keyword` / `items` 都不是 `required`，只按 required 组装会得到「0 家门店」「漏传商品列表」这种静默错误——服务端照样返回 200，但内容是空。这就是为什么必须校验返回内容的语义，而不只看 HTTP 状态。

## 4. 金额单位（已实测确认）

| 来源 | 字段 | 实测值 | 单位 | 处理 |
|---|---|---|---|---|
| `query-meals` | `currentPrice` / `originalPrice` | `"23.5"` / `"23.5"` | **元**（字符串） | `Decimal(text) * 100` → 2350 分；有小数残差就报错，不四舍五入 |
| `calculate-price` | `price` / `productPrice` / `originalPrice` | `5850` | **分**（整数） | 直接按整数分使用 |
| `calculate-price` | `productList[].subtotal` | `2350` | **分**（整数） | 与 `productPrice` 交叉核对，逐行金额之和必须等于 `productPrice` |
| `calculate-price` | `enjoyable.realDiscount` | `5.0` | **元**（浮点） | 同一响应里混着两种单位，必须按字段各自语义解析 |
| 内部数据 | 所有 `*_cents` | — | 分 | 一律整数，禁止浮点 |

总额口径：

```text
total_cents == product_cents - discount_cents + fee_cents
```

- `product_cents` 只用于核对，**不在总额上再加一次**。
- `fee_cents` 已包含在 `total_cents` 内，**不得重复相加**。
- 上式不成立 → `UNCLEAR_PRICE_BASIS`，阻断"核价完成"的输出。**差额绝不塞进"打包费"**。

实测样本（`1450713`，四件商品）：`price=5850`、`productPrice=5850`、`discount=0`，
`productList` 逐行 2350 + 1700 + 950 + 850 = 5850，与 `productPrice` 完全一致，`fee = 5850 - 5850 + 0 = 0`。

## 4.1 返回结构里的三个陷阱

1. **菜单是字典不是数组**：`data.meals` 形如 `{"1406": {...}, "1450": {...}}`，键就是商品编码，共 112 个；配套的 `data.categories[].meals` 只是 `{code, tags}` 索引。按数组解析会得到「0 个商品」。
2. **分类名含换行**：`"鸡肉汉堡\n/卷"`、`"小食甜品\n/其他"`、`"小食拼盘\n/多人餐"`，做分类匹配前必须去掉 `\n`。
3. `canWithOrder` 在 112 个商品上全为 `false`，语义未确认，**不拿它做可售性过滤**，可售性以 `calculate-price` 成功为准。

## 4.2 优惠券与「0 元」陷阱（实测）

用真实券核价（`9900016523` 龙焰鸡腿堡两件套 + 券）：

| 字段 | 不带券 | 带券 |
|---|---|---|
| `productOriginalPrice` | 0 | 3400 |
| `productPrice` | 0 | 2048 |
| `discount` | 0 | 1352 |
| `price` | **0** | 2048 |
| `productList` | **[]** | 1 行，`originalSubtotal=3400`、`subtotal=2048` |

**`price=0` + `productList=[]` 不是免费，是「这个组合核不了价」。** 服务端照样返回 `code=200`。
`live_plan.py` 现在遇到这种情况直接拒绝出方案，不生成 0 元单。

券相关要点：

- 券来源：`query-store-coupons`（`storeCode` + `orderType=1` + `beType=1`），返回 `couponId`、`couponCode`、`title`、`tradeDateTime`、`products[]{productCode, productName}`。
- 券码只在 `calculate-price` 的 `items[].couponCode` / `couponId` 里出现，**不进结果文件、不进卡片、不进日志**。
- **券专属商品不在菜单里**：该商品是 `query-store-coupons` 的 `products` 里有、`query-meals` 的 112 个商品里没有。名称只能从券列表取，不能自己编。
- 券是商品级优惠，但接口只返回整单 `discount`。本项目按行金额比例分摊，卡片上明确写"含整单优惠，按行金额比例分摊"，不称其为官方逐行成交价。

## 4.3 套餐组成

`query-meal-detail` 返回 `data.rounds[]`，每轮 `choices[]` 里 `isDefault=1` 的是默认选项。
真实案例：`9900016523 龙焰鸡腿堡两件套` = 第 1 轮「龙焰鸡腿堡 `521952`」+ 第 2 轮「可乐中杯 `3050`」。

- 每轮必须有**且只有一个** `isDefault=1`，否则视为组成未确认，不做套餐计价。
- 组成确认后整份归一人、不拆内部价格；同时它也就满足了该成员的"必须有主食 / 饮料必选"。
- 特调（`modification`）首版不主动展开，只用默认选项。

## 5. 失败处理

| 情况 | 客户端行为 | 用户可见结果 |
|---|---|---|
| 无 Token | `MissingTokenError`，退出码 1 | 指引去 open.mcd.cn 申请 / 去连接器配置，不发起请求 |
| 401 | `AuthError`，退出码 2 | "Token 无效或缺失"，不重试刷接口 |
| 429 | `RateLimitError`，最多重试一次 | 报告已取到的部分结果，不静默降级 |
| 4xx/5xx | `HttpError`，含状态码 + 响应体前 300 字符（已脱敏） | 报告失败阶段 |
| 网络不通 | `NetworkError` | "当前环境无法访问 mcp.mcd.cn" |
| 工具缺失 | 停止并列出服务端实际工具 | 配置指引，**不编造报价** |
| `isError: true` | `ToolCallError`，含服务端文本前 500 字符 | 如实转述 |
| 套餐核价持续失败 | 降级为单品组合 | `tradeoffs` 注明"套餐核价不可用，已降级单品" |

## 6. 安全与合规

- 不输出真实 Token、券码、手机号、真实姓名、配送地址。
- 不读取用户已保存的配送地址（`delivery-query-addresses` 不调用）。
- 不代用户下单、支付、领券、消耗积分。
- `mcp-config.example.json` 只含占位符；`.gitignore` 排除 `runtime/`、`.env`、真实配置。
- 官方 README 声明：未经书面授权不得将仓库内容用于商业售卖、付费分发或暗示官方背书。本项目为个人参赛作品，非官方产品。

## 7. 业务价值

麦当劳 MCP 只回答"这一单多少钱"，不回答"这一单该怎么分"。
本项目补上的正是后者：把一份官方报价拆成具名、可核对、每人都在自己预算内的应付金额。
`calculate-price` 提供权威总额，`check_plan.py` 提供可审计的分配过程，两者交叉验证——**任何一分钱对不上就拒绝输出确定应付**。这也是它能被信任的原因。

## 8. 实测记录

时间：2026-10-24 19:10–19:15（北京时间）。Token 从本机环境变量注入，未出现在任何输出、日志或文件中。

```bash
# 1. 握手 + 工具发现
$ python scripts/mcd_client.py --ping
连接状态：ok / 协议版本：2025-06-18 / 服务端：mcd-mcp 1.0.0 / 工具数量：35 / 握手耗时：0.38s

# 2. 四步探针（完整通过）
$ python scripts/probe_mcp.py --city 上海 --area 人民广场 --store-code 1450713 --item-code 1406
[query-nearby-stores] 状态=ok 耗时=0.36s 门店数=5
[query-meals]         状态=ok 耗时=0.46s 商品数=112
[query-meal-detail]   状态=ok 耗时=0.50s
[calculate-price]     状态=ok 耗时=0.15s  data.price=2350
最终状态：ok（退出码 0）

# 3. 真实两人方案
$ python scripts/live_plan.py --city 上海 --area 人民广场 --store-code 1450713 \
    --budget A=3000 --budget B=3500 \
    --plan "P1|主食齐全 + 共享脆薯饼|A:main:1406,B:main:1450,B:drink:9900008751,S:A+B:side:521741" \
    --plan "P2|不含共享小食|A:main:1406,B:main:1450,B:drink:9900008751" \
    --out runtime/live-input.json
[核价] P1 → 官方总额 58.50 元（商品 58.50，优惠 0.00，费用 0.00）
[核价] P2 → 官方总额 50.00 元（商品 50.00，优惠 0.00，费用 0.00）

# 4. 分账
$ python scripts/check_plan.py --in runtime/live-input.json --out runtime/live-result.json
数据模式：live
  P1：可行；A 2775 分，B 3075 分
  P2：可行；A 2350 分，B 2650 分
best_plan_id：P1
```

原始返回留档在 `runtime/live-input-raw.json`（已脱敏，`runtime/` 整目录不入库）。
