# 麦单合伙人（mcd-meal-mates）

**合点一单，各自有数。**

给一起吃饭的人配一单麦当劳：官方 MCP 取门店、菜单与报价，程序算清每个人该付多少，精确到分。

> 参赛作品，非麦当劳官方产品。餐品信息、价格与供应状态以麦当劳官方渠道的实时结果为准。

## 1. 解决的痛点

同桌吃饭，预算不同。直接 AA 有三个坑：

1. **总价没超，但某人超支**。总额 60 元两人各摊 30 元，看起来没问题，可 A 只带了 25 元。
2. **不吃共享小食的人被一起均摊**。薯条 12 元两人各付 6 元，可 B 一口没吃。
3. **套餐里已经有一杯可乐，又被单点了一杯**。钱多花了，还没人发现。

目标用户：一起点单的同学、室友、同事。首版固定 **2 人**，同一家门店、立即到店自取。

## 2. 它做什么 / 不做什么

做：

- 每人单独设预算、主食需求、饮料是否必选、排除项、是否愿意共享小食。
- 从真实门店菜单提候选，最多 2 套经官方 `calculate-price` 核价的可行方案。
- 整数分核算，每人均分用最大余数法，**每人应付之和必须等于官方总额**。
- 生成可双击打开、可打印、可复制纯文本清单的 HTML 点单卡。
- 条件改了（换店/换主食/改共享成员）旧报价自动失效，必须重新核价。

不做：下单、支付、领券、积分、外送、预约、团餐、账号系统、网页后端、再写一个 MCP Server。也不宣称"全网最低"。

## 3. 结构

```text
mcd-meal-mates/
  README.md                     # 本文件
  MCP_INTEGRATION.md            # MCP 接入细节、工具顺序、金额单位、失败处理
  CONTEST_DECLARATION.md        # 麦当劳大赛参赛声明（官方原文，双报时才需要）
  mcp-config.example.json       # 仅占位符的配置示例
  workbuddy.md                  # 本次开发的真实记录（操作日志形式，非聊天原文导出）
  .gitignore
  skills/meal-mates/SKILL.md    # WorkBuddy Skill：对话流程怎么走
  scripts/check_plan.py         # 分账与预算判定（纯标准库，确定性）
  scripts/render_card.py        # 结果 JSON → 单文件 HTML
  scripts/mcd_client.py         # 麦当劳 MCP 客户端（Streamable HTTP + Bearer）
  scripts/probe_mcp.py          # 第一阶段四步探针
  scripts/live_plan.py          # 真实菜单 + 官方核价 → RESULT_SCHEMA 输入
  templates/result.html         # 点单卡模板
  tests/test_check_plan.py      # 核算与分账测试
  tests/test_live_plan.py       # 报价对账、同款拆分、共享名单测试
  examples/fictional-input.json # 虚构输入
  examples/fictional-result.json# 虚构输入跑出的结果（可作回归基准）
  examples/fictional-card.html  # 用虚构结果渲染的点单卡
  docs/RESULT_SCHEMA.md         # 模块间数据契约（唯一事实来源）
  docs/DEMO.md                  # 演示脚本与话术
  docs/workbuddy-audit-record.md# 平台审计日志导出的执行记录（不是聊天原文）
  docs/screenshots/             # 两张真实运行截图
  tools/export_workbuddy_trace.py # 从本机审计日志重新导出开发记录
  docs/IMPLEMENTATION_STATUS.md # 已实现 / 已测试 / 仅模拟 / 未完成
  docs/DEVELOPMENT_PROVENANCE.md# 开发来源说明
  runtime/                      # 本地私有产物（整目录忽略）
```

## 4. 启用步骤

### 4.1 配置麦当劳 MCP（已在本机配好，换机器时按此重做）

1. 手机号登录 <https://open.mcd.cn/mcp>，申请 MCP Token。
2. 写入 `~/.workbuddy/mcp.json`（本机已完成）：

```json
{
  "mcpServers": {
    "mcd-mcp": {
      "type": "streamablehttp",
      "url": "https://mcp.mcd.cn",
      "headers": { "Authorization": "Bearer <你的 Token>" }
    }
  }
}
```

3. 在 **专家·技能·连接器 → 自定义连接器** 里对该 Server 点 **信任 / 启用**（配置文件改了不会自动生效）。
4. 命令行验证：

```bash
export MCD_MCP_TOKEN=<你的 Token>     # 仅本机 shell，不要提交
python scripts/mcd_client.py --ping
python scripts/mcd_client.py --list-tools
```

期望输出：`协议版本：2025-06-18`、`工具数量：35`。
仓库里的 `mcp-config.example.json` 只是格式参考，`${MCD_MCP_TOKEN}` 占位符**不保证**所有客户端会自动展开。

### 4.2 跑一遍核算（无需网络）

```bash
python scripts/check_plan.py --in examples/fictional-input.json --out runtime/result.json
python scripts/render_card.py --in runtime/result.json --out runtime/card.html
```

打开 `runtime/card.html` 即可。页面顶部会明确标注 **"虚构数据，仅用于功能演示"**。

### 4.3 真实链路（已实测通过）

```bash
python scripts/probe_mcp.py --city 上海 --area 人民广场
# 从输出里选一个门店编码，再跑完整四步
python scripts/probe_mcp.py --city 上海 --area 人民广场 --store-code 1450713 --item-code 1406
```

探针会按 `query-nearby-stores → query-meals → query-meal-detail → calculate-price` 依次调用，结果写到 `runtime/probe-<时间戳>.json`。**任何一步失败都会如实记录失败阶段，不会生成看似成功的摘要。**

配好菜单与报价之后，用 `live_plan.py` 一步生成结果 JSON，见 §5.1。

## 5. 一条可复现示例

### 5.1 真实链路（2026-10-24 19:12 实测）

```bash
python scripts/live_plan.py --city 上海 --area 人民广场 --store-code 1450713 \
  --budget A=3000 --budget B=3500 \
  --plan "P1|主食齐全 + 共享脆薯饼|A:main:1406,B:main:1450,B:drink:9900008751,S:A+B:side:521741" \
  --plan "P2|不含共享小食|A:main:1406,B:main:1450,B:drink:9900008751" \
  --out runtime/live-input.json

python scripts/check_plan.py  --in runtime/live-input.json --out runtime/live-result.json
python scripts/render_card.py --in runtime/live-result.json --out runtime/live-card.html
```

门店：麦当劳上海黄浦华旭国际大厦餐厅（`1450713`），核价时间 2026-10-24 19:12 (+08:00)。

| 方案 | 内容 | 官方总额 | A 应付 | B 应付 |
|---|---|---:|---:|---:|
| P1 | 板烧鸡腿堡 + 麦香鸡 + 可乐 + 共享脆薯饼 | **¥58.50** | ¥27.75 | ¥30.75 |
| P2 | 去掉共享小食 | **¥50.00** | ¥23.50 | ¥26.50 |

P1：脆薯饼 ¥8.50 两人各 ¥4.25；2775 + 3075 = 5850 分，与官方 `data.price` 完全一致。

### 5.2 含真实优惠券与套餐（2026-10-24 19:23 实测）

```bash
python scripts/live_plan.py --city 上海 --area 人民广场 --store-code 1450713 \
  --budget A=3500 --budget B=3500 \
  --coupon "9900016523=<券ID>:<券码>" \
  --plan "P1|用券的龙焰两件套 + 共享脆薯饼|A:combo:9900016523,B:main:1450,B:drink:9900008751,S:A+B:side:521741" \
  --plan "P2|不用券的板烧单品组合|A:main:1406,B:main:1450,B:drink:9900008751,S:A+B:side:521741" \
  --out runtime/live-coupon-input.json
```

| 方案 | 商品券前 | 优惠 | 官方总额 | A | B |
|---|---:|---:|---:|---:|---:|
| P1（用券 + 套餐） | ¥69.00 | −¥13.52 | **¥55.48** | ¥30.76 | ¥24.72 |
| P2（无券 + 单品） | ¥58.50 | ¥0 | **¥58.50** | ¥27.75 | ¥30.75 |

套餐由 `query-meal-detail` 确认为「龙焰鸡腿堡 + 可乐中杯」，整份归 A 不拆价；优惠按行金额比例拆为 A −7.50 / B −6.02。

### 5.3 虚构样例（离线回归用，不需要 Token）

`examples/fictional-input.json`：A 预算 30.00 元、B 预算 35.00 元，两人都要主食，B 必须有饮料。

| 方案 | 内容 | 官方总额 | A 应付 | B 应付 |
|---|---|---:|---:|---:|
| P1 | 巨无霸 + 麦辣鸡腿堡 + 中杯可乐 + 共享大薯条（含 3.00 元整单优惠） | ¥61.00 | ¥28.59 | ¥32.41 |
| P2 | 双层吉士汉堡 + 板烧鸡腿堡 + 中杯可乐（无共享小食） | ¥47.00 | ¥16.00 | ¥31.00 |

P1 的 3.00 元优惠按行金额比例分摊为 A −1.41 / B −1.59。

**5.3 的数字是虚构的**，不代表任何真实报价；页面上会强制显示「虚构数据，仅用于功能演示」。

## 6. 限制（写在前面）

- **真实链路已跑通**：门店、菜单、商品详情、官方核价四步都有真实返回（`docs/IMPLEMENTATION_STATUS.md` §1）。
- 首版只支持到店自取、同店、立即餐、个人餐 + 独立共享小食。
- 套餐需 `query-meal-detail` 确认每轮有唯一默认选项后才可整份计一行；确认后它同时满足主食/饮料需求。
- 不做全局最优搜索：只比较已核价的少量候选，不保证找到最便宜的组合。
- **优惠券需要显式指定**（`--coupon 商品编码=券ID:券码`），不做自动挑券、不自动领券；券码不进结果文件与卡片。
- `price=0` 且 `productList=[]` 视为"无法核价"，拒绝出方案，绝不按 0 元成单（真实踩过）。
- 额外费用分摊（`fee>0`）在到店自取实测为 0，该分支仅在虚构数据上验证过。
- 券专属商品可能不在 `query-meals` 菜单里，名称取自 `query-store-coupons` 的真实返回。
- 3–4 人：代码按 `members` 数组通用实现，只用 2 人真实数据验证过。
- 菜单 `canWithOrder` 全为 `false`，语义未确认，不用于可售性过滤（以 `calculate-price` 成功为准）。

## 7. 测试

```bash
python -m unittest discover -s tests -v
```

覆盖：金额守恒（最大余数、1 分余数不丢不重）、个人预算拦截、共享资格与重复成员、套餐重复饮料与组成确认、优惠与费用口径、报价失效、不完整数据（缺价不按 0）、同款商品多人点的行金额拆分、报价残差不得当费用。当前 **43 个用例**全部通过。

真实链路复验（2026-10-24 19:40）：两人各点一份麦香鸡 + 一份可乐，官方总额 ¥43.50，生成的行金额为 1700 / 1700 / 950，合计 4350 分，与官方一致——修复前会变成 3400 + 3400 + 950 被金额校验拦下。

## 8. 提交包

`dist/mcd-meal-mates-submission-20261024.zip` 是给活动方看的那一份，已排除：

- `runtime/`（探针与核价的原始返回，含本账号券相关数据）
- `.env`、`mcp-config.json`（真实凭据配置）
- `__pycache__/`、`.git/`

包含 26 个文件：源码、Skill、模板、测试、虚构回归样例、文档、平台审计记录导出、两张真实运行截图、官方参赛声明原文、记录导出工具。

包内自检（解压后独立运行）：`python -m unittest discover -s tests` → 43 个用例通过；虚构样例可跑出结果卡。

重新打包：

```bash
python - <<'PY'
import zipfile
from pathlib import Path
skip_dirs = {'runtime', '.git', '__pycache__', 'dist', '.idea', '.vscode'}
files = [p for p in Path('.').rglob('*') if p.is_file()
         and not (set(p.parts) & skip_dirs)
         and not p.name.startswith('.env')
         and p.name != 'mcp-config.json'
         and p.suffix not in {'.pyc', '.log'}]
Path('dist').mkdir(exist_ok=True)
with zipfile.ZipFile('dist/mcd-meal-mates-submission-20261024.zip', 'w', zipfile.ZIP_DEFLATED) as z:
    for p in files:
        z.write(p, Path('mcd-meal-mates') / p)
PY
```

**打包前请再确认**：不要把填好 Token 的 `~/.workbuddy/mcp.json` 或任何券码复制进项目目录。

## 9. 开发记录怎么导出

WorkBuddy 的对话区顶栏只有「对话内搜索 / 分享任务 / 详情面板」，**没有导出按钮**。可行的三条路：

### 9.1 平台审计日志（推荐，已落盘）

本机 `~/.workbuddy/audit-log/` 记录了每次文件写入与命令执行，带时间戳和 `prevHash→hash` 链：

```bash
python tools/export_workbuddy_trace.py --out docs/workbuddy-audit-record.md
```

产物已在仓库里（`docs/workbuddy-audit-record.md`）。它**不含对话文本**，只证明命令与文件操作确实在本会话发生过；凭据由平台标成 `[REDACTED]`，导出脚本再加一层脱敏。

### 9.2 手动复制

在对话区全选 → 复制到 Markdown 文件。这是唯一能保留对话原文的方式，但需要人工清理。

### 9.3 分享任务（谨慎）

「分享任务」会生成**公开链接**。本项目的对话里出现过 MCP Token 与真实券码，**分享等于公开凭据**。如确需分享：

1. 先到 <https://open.mcd.cn/mcp> 重新生成 Token，让旧 Token 失效；
2. 确认对话里没有新的凭据与个人信息；
3. 再点分享。

### 9.4 材料边界（如实说明）

提交给活动方的「开发对话记录」目前是：`workbuddy.md`（过程摘要）+ `docs/workbuddy-audit-record.md`（平台执行记录）+ 截图。
这三样都不是聊天原文导出。若活动方要求原文，需要你在客户端里手动复制或分享，并自行完成凭据清理。
