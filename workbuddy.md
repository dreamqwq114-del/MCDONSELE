# workbuddy.md · 本次开发记录

## 形式声明（先读这段）

这不是聊天原文导出。本次无法从 WorkBuddy 导出原始对话，按执行方案 §11 的替代要求，本文件以**操作日志 + 决策记录**的形式如实记录：做了什么、跑了什么命令、结果如何、哪些没做成。时间取北京时间，事件顺序与实际执行一致。

**材料形式**：本文件是开发过程摘要 + 可复核的运行痕迹（命令、退出码、时间戳、截图），不是平台导出的对话原文。是否满足活动方对「开发对话记录」的格式要求，请以活动方人工审核为准；若必须提供原文导出，需要用户在 WorkBuddy 客户端内自行导出。

可核对的痕迹：

- **`docs/workbuddy-audit-record.md`**：由 `tools/export_workbuddy_trace.py` 从 WorkBuddy 本地审计日志导出的真实执行记录（211 条，含每条命令的时间戳、参数与 `prevHash→hash` 链）。这是平台生成的旁证，不是对话文本。
- 文件系统时间戳：`scripts/`、`tests/`、`docs/` 下各文件的创建/修改时间与本日志时间线一致。
- 运行记录：`runtime/probe-*.json`（探针原始返回，已脱敏）、`runtime/live-*-raw.json`（核价原始返回，已脱敏）。**这两个目录不入公开包**。
- 截图：`docs/screenshots/live-card-real.png`（无券单）、`docs/screenshots/live-card-coupon.png`（含券 + 套餐单），均为本次真实运行后渲染的卡片。

---

## 0. 起点

- 时间：2026-10-24 18:38（北京时间）。
- 输入：用户提供《麦单合伙人-WorkBuddy 执行方案.md》《发给 WorkBuddy 的启动指令.md》，以及 WaytoAGI 用餐赛道页面 <https://1024.waytoagi.com/tracks/dine>。
- 工作目录：`C:/Users/12086/WorkBuddy/MCdon`，新建 `mcd-meal-mates/`。
- 已知：WaytoAGI 用餐赛道要求真实调用麦当劳 MCP、作品可运行；麦当劳大赛（可选双报）截止 10/25 23:59，WaytoAGI 硬截止 10/25 16:00。

## 1. 先查连接，再写代码

18:39 先确认 MCP 到底通不通，结论是**不通**：

- 本会话工具列表中没有 `query-nearby-stores` / `query-meals` / `calculate-price` 等麦当劳工具。
- 会话上下文显示已连接连接器只有 Agent Mail。
- `list_installed_plugins` 返回 `No connectors installed`。

按启动指令第 2 条，此时不编造调用成功，改为：**建立不依赖网络的项目骨架与核算模块**，MCP 客户端按官方规范写完整，等 Token 到位即可实测。

同时读了官方 README（`github.com/M-China/mcd-mcp-server`），确认：地址 `https://mcp.mcd.cn`、Streamable HTTP、`Authorization: Bearer`、600 次/分钟限流、401/429 语义、工具清单。**README 没有给每个工具的入参 schema** —— 这决定了实现方式：客户端必须运行时 `tools/list` 读 schema，不能硬编码参数。

## 2. 先定契约，再并行开工

18:41 写 `docs/RESULT_SCHEMA.md`：金额一律整数分、违规码表、分账结构、CLI 与退出码。理由很简单——四个模块要并行开发，没有一份共同契约，后面必定对不上。

18:42 写 `examples/fictional-input.json`：两人、两套方案、虚构门店与商品编码（`TEST-` 前缀），金额设计成"有整单优惠 + 有共享品"，好让分账逻辑真的被压到。

然后并行派 4 个子任务，文件所有权互不重叠：

| 子任务 | 独占文件 |
|---|---|
| 核算核心 | `scripts/check_plan.py`、`tests/test_check_plan.py` |
| 点单卡 | `templates/result.html`、`scripts/render_card.py` |
| MCP 客户端 | `scripts/mcd_client.py`、`scripts/probe_mcp.py` |
| Skill 与配置 | `skills/meal-mates/SKILL.md`、`mcp-config.example.json`、`.gitignore` |

## 3. 实际跑出来的东西

18:55–19:01 逐项验收，命令与真实结果：

```
$ python -m unittest discover -s tests
Ran 31 tests in 1.788s
OK

$ python scripts/check_plan.py --in examples/fictional-input.json --out runtime/fictional-result.json
数据模式：fictional
  P1：可行；A 2859 分，B 3241 分
  P2：可行；A 1600 分，B 3100 分
best_plan_id：P1
exit=0

$ python scripts/render_card.py --in runtime/fictional-result.json --out runtime/card.html
已生成点单卡：runtime\card.html（2 套方案，data_mode=fictional，60.6 KB）
exit=0
```

数字核对：P1 中 A = 2287（巨无霸折后）+ 572（薯条一半）= 2859；B = 1811 + 858 + 572 = 3241；合计 6100 = 官方总额。优惠 300 分按行金额权重分成 141/159，用的是最大余数法。

19:02 用 headless Chrome 截图核对渲染效果（`runtime/shot.png`），确认首屏是门店 + 核价时间 + 总额 + 每人应付 + 数据来源徽章，且**没有**出现错误警示条。

## 4. MCP 部分到底做到哪一步

19:00 `scripts/mcd_client.py --ping` 用**故意写错的假 Token** 打真实端点，收到 **401** 并映射为认证失败（exit 2）。这一步的价值：证明 TLS、HTTP、JSON-RPC、错误映射链路是通的，只是没有凭据。**这不是"调用成功"**，业务工具一个都没调到。

19:00 `scripts/probe_mcp.py --city 上海 --area 人民广场` 在缺 Token 处停止，写 `runtime/probe-20261024-190036.json`（`status: blocked`），打印配置指引，exit 1，无 traceback。

`--self-test` 25 条断言通过：redact 脱敏、SSE 多事件取首个 result、401/429/500 映射、请求头组装。

## 5. 中途修正的地方

19:05 子任务反馈了三处"文档与实现对不上"，逐一改的是文档（实现更合理）：

1. `docs/RESULT_SCHEMA.md` §4 的示例数字是拍脑袋写的（A 2887），与最大余数法实算的 2859 不符 → 改成真实输出，并标注为回归基准。
2. 分账口径写歧义：`shares` 到底是折前还是折后 → 明确为**折后净额**，`discounts[].split` **只做归因展示、不参与求和**，否则优惠会被扣两次。
3. 违规码原来一律写"阻断"，实际分两层：金额层未定（`payable_cents=null`，页面显示"分账待确认"）与业务层不通过（金额照算，让人看见差多少） → 文档补上这两层。

另外补了 `render_card.py` 的 `--title/--template` 与退出码表，`best_plan_id` 的语义（plans 里第一个可行，不做择优）。

## 6. 19:10 时的卡点（已被第 7 节接续）

以下是拿到 Token 之前的状态，保留以便对照：

- 没有一次真实的 `calculate-price` 成功调用，所以没有任何 `live` 数据。
- README / DEMO / IMPLEMENTATION_STATUS 里所有金额都来自虚构样例，页面上强制显示"虚构数据，仅用于功能演示"。
- 菜单 `currentPrice` 真实单位未确认（不敢统一除以 100，就写清楚"未确认"）。
- 优惠券、3–4 人验证、套餐真实核价，都没做。

19:09 用户给出 Token 后，上述第 1–3 条在 20 分钟内被推翻，见下一节。

## 7. 第二步：接上真实 MCP（19:09–19:20）

用户在对话里直接给了 MCP Token，要求"帮我接入"。处理方式：Token 只进 `~/.workbuddy/mcp.json` 与本机环境变量，不进仓库、不进日志、不进任何输出。

### 7.1 握手

```
$ python scripts/mcd_client.py --ping
连接状态：ok
协议版本：2025-06-18
服务端：mcd-mcp 1.0.0
会话：未返回 Mcp-Session-Id
工具数量：35
握手耗时：0.38s
```

35 个工具，命名与官方 README 一致。用 `--schema` 逐个看了 4 个核心工具的 `inputSchema`，**发现一个关键事实：`city`、`keyword`、`items` 都不是 `required`**。这直接导致了后面的静默失败。

### 7.2 三个只有真实接口才会暴露的缺陷

1. **门店搜出 0 家**。请求体只有 `{"beType":1,"searchType":2}` —— 探针只填 `required` 字段，城市和商圈被丢了。服务端返回 200，`门店数=0`。修：定位类可选字段，schema 里有就补。
2. **菜单 112 个商品显示 0 个**。`data.meals` 是 `{编码: 商品}` 的字典，不是数组，探针按数组找自然找不到。修：加字典型列表摊平，键名补成 `code`。
3. **核价漏传商品**。`items` 不是 `required`，请求只发了门店和类型。修：按 `inputSchema` 的数组结构推出 `productCode` + `quantity`。

三个都是"HTTP 200 但内容空"型的错误。这也说明为什么探针必须校验返回语义，而不是只看状态码。

### 7.3 真实数据长什么样

- 门店：上海人民广场搜到 5 家，如 `1450713` 麦当劳上海黄浦华旭国际大厦餐厅。
- 菜单：112 个商品、16 个分类；分类名里带换行（`"鸡肉汉堡\n/卷"`）。
- 菜单价 `currentPrice` 是**元字符串**（`"23.5"`）；报价 `data.price` 是**分**（`5850`）；同一个响应里 `enjoyable.realDiscount=5.0` 又是**元**。三种单位混在一起，"统一除以 100"必错。
- 四件商品核价：`price=5850`、`productPrice=5850`、`discount=0`，`productList` 逐行 2350+1700+950+850=5850。

### 7.4 补上桥接脚本

原来 MCP 和 `check_plan.py` 之间是断的——要么手抄价格，要么让模型自己填数字，两种都不可接受。19:12 写了 `scripts/live_plan.py`：

```bash
$ python scripts/live_plan.py --city 上海 --area 人民广场 --store-code 1450713 \
    --budget A=3000 --budget B=3500 \
    --plan "P1|主食齐全 + 共享脆薯饼|A:main:1406,B:main:1450,B:drink:9900008751,S:A+B:side:521741" \
    --plan "P2|不含共享小食|A:main:1406,B:main:1450,B:drink:9900008751" \
    --out runtime/live-input.json
[门店] 麦当劳上海黄浦华旭国际大厦餐厅（1450713）
[菜单] 112 个在售商品，价格单位已确认
[核价] P1 → 官方总额 58.50 元
[核价] P2 → 官方总额 50.00 元
```

它拒绝三件事：编码不在菜单里、商品没有价格（不按 0 元）、共享名单里的成员不存在。

### 7.5 最终真实结果

```
$ python scripts/check_plan.py --in runtime/live-input.json --out runtime/live-result.json
数据模式：live
  P1：可行；A 2775 分，B 3075 分
  P2：可行；A 2350 分，B 2650 分
best_plan_id：P1
```

A 27.75 + B 30.75 = 58.50 = 官方总额。脆薯饼 ¥8.50 两人各 ¥4.25。
卡片渲染后截图核对：绿色徽章「本次实时核价」，门店名与核价时间都是真实返回。

### 7.6 这一步之后仍未做的

优惠券、套餐 `roundList` 整份计价、3–4 人真实验证、`discount>0` 与 `fee>0` 的真实分摊校验。这些不影响主链路。

## 8. 第三步：连接器生效后重测（19:21–19:28）

用户在连接器里点了信任，MCP 工具直接出现在会话里（`mcp__mcd-mcp__*`），于是换成原生调用再走一遍。

### 8.1 原生调用结果

- `query-nearby-stores`（上海/人民广场）→ 5 家门店，带距离、营业时间、可预约时段。
- `query-store-coupons`（1450713）→ 查到 1 张真实可用券：「1024“码”力翻倍」，适用「龙焰鸡腿堡两件套」，有效期到 10-30。

### 8.2 又抓到两个真实坑

1. **`price=0` 不代表免费**。同一件两件套，不带券时 `calculate-price` 返回 `price=0`、`productPrice=0`、`productList=[]`，HTTP 200，`message` 还是"请求成功"。带券才返回 `3400 → 2048`、`discount=1352`。
   代码原来会用菜单价兜底，等于自己造了一个价格。现在直接拒绝出方案。
2. **券后金额不能当分账基数**。`productPrice` 和 `productList[].subtotal` 都是**券后**的，原来的桥接脚本拿它们当基数，`check_plan` 又减一次 `discount`，算出来 `product - discount ≠ total`，被判口径不明。改成取 `productOriginalPrice` + `originalSubtotal`（券前）后自洽。

顺带发现**券专属商品不在菜单里**：`9900016523` 不在 `query-meals` 的 112 个商品里，只在券列表里。名称只能从 `query-store-coupons` 取，不能编。

### 8.3 一个规则漏洞

真实两件套 = 龙焰鸡腿堡 + 可乐中杯（`query-meal-detail` 的 rounds 确认）。它明明是 A 的主食，但 `check_plan` 只认 `kind="main"`，判了 `MAIN_MISSING`，把方案拦掉。

改成：`combo.confirmed=true` 时，组成里含主食即满足"必须有主食"，含饮料即满足"饮料必选"；未确认的套餐不享受这个待遇。补了 2 条回归测试（33 个全过）。

### 8.4 最终真实结果

```
[套餐] 龙焰鸡腿堡两件套 组成已确认：龙焰鸡腿堡 + 可乐中杯（整份归一人，不拆内部价格）
[核价] P1 → 官方总额 55.48 元（商品 69.00，优惠 13.52，费用 0.00）
[核价] P2 → 官方总额 58.50 元（商品 58.50，优惠 0.00，费用 0.00）

数据模式：live
  P1：可行；A 3076 分，B 2472 分
  P2：可行；A 2775 分，B 3075 分
```

P1：6900 − 1352 = 5548；3076 + 2472 = 5548。优惠按行金额拆成 A −750 / B −602，脆薯饼 683 分拆成 342 / 341（最大余数法，不多不少一分）。

这一单把此前只有虚构数据支撑的三条逻辑都跑真了：套餐整份计价、套餐满足主食、整单优惠按比例分摊。

## 9. 第四步：按验收清单修三个缺陷（19:34–19:45）

收到一份独立审计清单（`麦单合伙人-验收与修正清单.md`），逐条复现后确认三个都是真问题，不是误报。

### 9.1 费用是反推出来的，等于自证

原代码 `fee = total - basis + discount`，于是 `basis - discount + fee == total` 恒成立——任何无法解释的差额都会被"证明"成正常费用，还能标 `verified`。

审计复现：商品 2000、总额 2300、优惠 0、费用字段全 0 → 输出 `fee=300`、`status=verified`。

改法：费用只从 `deliveryPrice` / `packingPrice` / `tablewarePrice` 三个明确字段取，再拿 `basis - discount + fee` 去核对总额，**对不上就抛错并报出差额**。另外加了一条交叉核对：`券前 - 优惠` 必须等于官方 `productPrice`。

### 9.2 同款商品被多人点时金额翻倍

核价时按编码合并数量（正确），但返回后把聚合小计按编码回填给**每一条**选品行。两个人各点一份 20 元同款 → 官方 4000 分，生成的两行各 4000，合计 8000，方案被金额校验拦掉。

改法：`quote()` 返回 `{line_id: 小计}`，聚合小计按各行的数量用最大余数法拆回去；缺行即报错，不按 0 元补。

真实复验（19:40）：两人各点麦香鸡 + 一份可乐，官方总额 ¥43.50，生成行金额 1700 / 1700 / 950，合计 4350 分，与官方一致。

### 9.3 `A+A+B` 被当成三个分摊槽位

共享名单允许重复成员，`A+A+B` 会把共享品切成三份，A 摊两份、B 摊一份，而且 `feasible=true`、无任何提示。

改法：`parse_selection` 解析阶段就拒绝重复成员；`check_plan` 增加 `SHARE_DUPLICATE_MEMBER`（金额层阻断，`payable=null`），双保险。

### 9.4 测试

新增 `tests/test_live_plan.py`（桩客户端，不联网）覆盖上面 1、2 两类的复现，`test_check_plan.py` 补重复成员一条。测试数 33 → **43，全过**。真实链路两单重跑结果与修改前一致（58.50 / 50.00 与 55.48 / 58.50），说明修复没有破坏主流程。

### 9.5 材料修正

- README：`5.2 的数字是虚构的` 改为 `5.3`（5.2 是真实券案例，写错了会误导审核）；测试数 31 → 43。
- `docs/DEVELOPMENT_PROVENANCE.md`：优惠券状态从"未接入"改为"支持指定券核价，未做自动发现与最优选券"。
- `CONTEST_DECLARATION.md`：删掉之前追加的分隔线与来源说明，恢复官方原文（官方要求不可修改），来源改记在开发来源文档里。
- 新增 `docs/screenshots/` 两张真实运行截图，作为开发记录的可视证据。

## 10. 下一步（需要用户动作）

1. 报名、协议、公开仓库、提交作品由用户完成，程序不代做。
2. 若同步参加麦当劳 GitHub 大赛，仓库需含 `README.md`、`CONTEST_DECLARATION.md`、`MCP_INTEGRATION.md`、源码、`workbuddy.md`——这些都已在目录里。
3. 真实卡片目前在 `runtime/`（已 gitignore），是否放进 `examples/` 供公开仓库展示，由用户决定。
