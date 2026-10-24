# WorkBuddy 开发过程记录（平台审计日志导出）

> 本文件由 `tools/export_workbuddy_trace.py` 从 WorkBuddy 本地审计日志生成，**不是聊天记录**。
> 它证明的是「这些命令确实在本机 WorkBuddy 会话里执行过、这些文件确实被写过」，
> 不包含对话文本，也不包含 MCP 工具调用的请求体。

## 1. 会话信息

| 项 | 值 |
|---|---|
| 会话 ID | `34cbab85-5e8a-4b37-8549-996db17e27f9` |
| 记录条数 | 211 |
| 时间范围 | 2026-10-24 18:38:52 — 2026-10-24 20:29:55（本机时区） |
| 日志来源 | `~/.workbuddy/audit-log/<日期>.jsonl`（WorkBuddy 本地审计日志） |

事件构成：

| 事件类型 | 条数 | 含义 |
|---|---:|---|
| `file-safety.allow-listed` | 129 | 文件写入命中允许规则，平台放行并留痕 |
| `command-safety.sandbox-executed` | 82 | 命令在沙箱内执行并留痕 |

## 2. 完整性

日志采用 `prevHash → hash` 链式结构。本次记录中，相邻记录哈希连续匹配 **206** 处，不连续 **4** 处。

不连续处出现在多进程写入合并（spool 汇入）的接缝上，属日志写入顺序问题，不代表记录被改动；如需向审核方说明，可直接提供原始 `.jsonl` 文件。

> 原始日志在 `~/.workbuddy/audit-log/`，**不在本仓库、不随提交包分发**；它包含本机绝对路径，公开前需先脱敏。

## 3. 执行过的命令（按时间顺序）

凭据已由平台替换为 `[REDACTED]`；本机用户名路径已替换为 `~`；超长命令尾部截断。

| # | 时间 | 命令 |
|---:|---|---|
| 1 | 2026-10-24 18:38:52 | `ls -la "~/Documents/Codex/2026-10-24/new-chat/outputs" 2>/dev/null \|\| echo "NOT_FOUND"` |
| 2 | 2026-10-24 18:39:02 | `cd "~/Documents/Codex/2026-10-24/new-chat/outputs" && mkdir -p /tmp/mcdzip && unzip -o -q mcd-assistant-0.1.0.zip -d /tmp/mcdzip && find /tmp/mcdzip -type f \| head -50` |
| 3 | 2026-10-24 18:39:07 | `cat /tmp/mcdzip/mcd-assistant/mcp.json; echo "=== plugin.json ==="; cat /tmp/mcdzip/mcd-assistant/plugin.json; echo "=== SKILL.md ==="; head -80 /tmp/mcdzip/mcd-assistant …` |
| 4 | 2026-10-24 18:39:37 | `cd "~/WorkBuddy/MCdon" && mkdir -p mcd-meal-mates/{skills/meal-mates,scripts,templates,tests,examples,docs,runtime} && ls -la mcd-meal-mates` |
| 5 | 2026-10-24 19:01:51 | `cd "~/WorkBuddy/MCdon/mcd-meal-mates" && echo "=== FILES ===" && find . -type f -not -path "./runtime/*" \| sort && echo "" && echo "=== PY ===" && python -m unittest disc …` |
| 6 | 2026-10-24 19:01:56 | `cd "~/WorkBuddy/MCdon/mcd-meal-mates" && python scripts/render_card.py --in runtime/fictional-result.json --out runtime/card.html && echo "exit=$?" && ls -la runtime/*.ht …` |
| 7 | 2026-10-24 19:02:01 | `ls "/c/Program Files/Google/Chrome/Application/chrome.exe" "/c/Program Files (x86)/Microsoft/Edge/Application/msedge.exe" 2>/dev/null` |
| 8 | 2026-10-24 19:02:07 | `cd "~/WorkBuddy/MCdon/mcd-meal-mates" && "/c/Program Files/Google/Chrome/Application/chrome.exe" --headless=new --disable-gpu --hide-scrollbars --window-size=1100,2400 -- …` |
| 9 | 2026-10-24 19:02:17 | `cd "~/WorkBuddy/MCdon/mcd-meal-mates" && "/c/Program Files/Google/Chrome/Application/chrome.exe" --headless=new --disable-gpu --hide-scrollbars --window-size=1100,560 --s …` |
| 10 | 2026-10-24 19:02:53 | `cd "~/WorkBuddy/MCdon/mcd-meal-mates" && cp runtime/fictional-result.json examples/fictional-result.json && cp runtime/card.html examples/fictional-card.html && ls runtim …` |
| 11 | 2026-10-24 19:04:16 | `cd "~/WorkBuddy/MCdon/mcd-meal-mates" && $PY -c "import json;d=json.load(open('mcp-config.example.json',encoding='utf-8'));print('config ok:',list(d['mcpServers']))" && $ …` |
| 12 | 2026-10-24 19:04:23 | `ls "~/WorkBuddy/MCdon/.workbuddy/memory/" 2>/dev/null \|\| echo "NO_DIR"` |
| 13 | 2026-10-24 19:04:27 | `mkdir -p "~/WorkBuddy/MCdon/.workbuddy/memory"` |
| 14 | 2026-10-24 19:10:03 | `ls -la "~/.workbuddy/mcp.json" 2>/dev/null && echo "--- EXISTS ---" \|\| echo "NOT_EXISTS"` |
| 15 | 2026-10-24 19:10:05 | `cd "~/WorkBuddy/MCdon/mcd-meal-mates" && python scripts/mcd_client.py --ping; echo "exit=$?"` |
| 16 | 2026-10-24 19:10:15 | `cd "~/WorkBuddy/MCdon/mcd-meal-mates" && $PY scripts/mcd_client.py --list-tools 2>&1 \| head -45` |
| 17 | 2026-10-24 19:10:21 | `cd "~/WorkBuddy/MCdon/mcd-meal-mates" && for t in query-nearby-stores query-meals query-meal-detail calculate-price; do echo "===== $t ====="; $PY scripts/mcd_client.py - …` |
| 18 | 2026-10-24 19:10:29 | `cd "~/WorkBuddy/MCdon/mcd-meal-mates" && python scripts/probe_mcp.py --city 上海 --area 人民广场 2>&1 \| head -40; echo "exit=$?"` |
| 19 | 2026-10-24 19:10:37 | `cd "~/WorkBuddy/MCdon/mcd-meal-mates" && grep -n "required\\|classify\\|city\\|keyword" scripts/probe_mcp.py \| head -40` |
| 20 | 2026-10-24 19:11:01 | `cd "~/WorkBuddy/MCdon/mcd-meal-mates" && python scripts/probe_mcp.py --city 上海 --area 人民广场 2>&1 \| head -30` |
| 21 | 2026-10-24 19:11:08 | `cd "~/WorkBuddy/MCdon/mcd-meal-mates" && python scripts/probe_mcp.py --city 上海 --area 人民广场 --store-code 1450713 2>&1 \| head -40` |
| 22 | 2026-10-24 19:11:16 | `cd "~/WorkBuddy/MCdon/mcd-meal-mates" && python -c " import sys, json sys.path.insert(0,'scripts') from mcd_client import McdClient c=McdClient(); c.initialize() r=c.call …` |
| 23 | 2026-10-24 19:11:24 | `cd "~/WorkBuddy/MCdon/mcd-meal-mates" && python -c " import sys, json sys.path.insert(0,'scripts') from mcd_client import McdClient c=McdClient(); c.initialize() r=c.call …` |
| 24 | 2026-10-24 19:11:34 | `cd "~/WorkBuddy/MCdon/mcd-meal-mates" && python -c " import sys, json sys.path.insert(0,'scripts') from mcd_client import McdClient c=McdClient(); c.initialize() r=c.call …` |
| 25 | 2026-10-24 19:11:42 | `cd "~/WorkBuddy/MCdon/mcd-meal-mates" && python -c " import sys, json sys.path.insert(0,'scripts') from mcd_client import McdClient c=McdClient(); c.initialize() r=c.call …` |
| 26 | 2026-10-24 19:11:50 | `cd "~/WorkBuddy/MCdon/mcd-meal-mates" && python -c " import sys, json sys.path.insert(0,'scripts') from mcd_client import McdClient c=McdClient(); c.initialize() r=c.call …` |
| 27 | 2026-10-24 19:12:05 | `cd "~/WorkBuddy/MCdon/mcd-meal-mates" && python -c " import sys, json sys.path.insert(0,'scripts') from mcd_client import McdClient c=McdClient(); c.initialize() t=[x for …` |
| 28 | 2026-10-24 19:12:13 | `cd "~/WorkBuddy/MCdon/mcd-meal-mates" && python -c " import sys, json sys.path.insert(0,'scripts') from mcd_client import McdClient c=McdClient(); c.initialize() items=[{ …` |
| 29 | 2026-10-24 19:12:47 | `cd "~/WorkBuddy/MCdon/mcd-meal-mates" && python scripts/live_plan.py --city 上海 --area 人民广场 --store-code 1450713 --budget A=3000 --budget B=3500 --plan "P1\|主食齐全 + 共享脆薯饼\|A: …` |
| 30 | 2026-10-24 19:12:54 | `cd "~/WorkBuddy/MCdon/mcd-meal-mates" && $PY scripts/check_plan.py --in runtime/live-input.json --out runtime/live-result.json && echo "---" && $PY scripts/render_card.py …` |
| 31 | 2026-10-24 19:13:04 | `cd "~/WorkBuddy/MCdon/mcd-meal-mates" && "/c/Program Files/Google/Chrome/Application/chrome.exe" --headless=new --disable-gpu --hide-scrollbars --window-size=1100,1900 -- …` |
| 32 | 2026-10-24 19:13:31 | `cd "~/WorkBuddy/MCdon/mcd-meal-mates" && head -25 scripts/probe_mcp.py \| grep -n "^import\\|^from"` |
| 33 | 2026-10-24 19:13:42 | `cd "~/WorkBuddy/MCdon/mcd-meal-mates" && python scripts/probe_mcp.py --city 上海 --area 人民广场 --store-code 1450713 --item-code 1406 2>&1 \| tail -30` |
| 34 | 2026-10-24 19:13:57 | `cd "~/WorkBuddy/MCdon/mcd-meal-mates" && python scripts/probe_mcp.py --city 上海 --area 人民广场 --store-code 1450713 --item-code 1406 2>&1 \| tail -12` |
| 35 | 2026-10-24 19:15:17 | `cd "~/WorkBuddy/MCdon/mcd-meal-mates" && grep -n "4.3\\|有 Token 后\\|探针会按" README.md \| head` |
| 36 | 2026-10-24 19:16:09 | `cd "~/WorkBuddy/MCdon/mcd-meal-mates" && tail -4 docs/DEVELOPMENT_PROVENANCE.md` |
| 37 | 2026-10-24 19:16:24 | `cd "~/WorkBuddy/MCdon/mcd-meal-mates" && echo "=== TOKEN LEAK CHECK ===" && grep -rl "[REDACTED]" . 2>/dev/null \| head; echo "(空=无泄漏)"; echo "=== TESTS ===" && $PY -m uni …` |
| 38 | 2026-10-24 19:16:38 | `cd "~/WorkBuddy/MCdon/mcd-meal-mates" && wc -l skills/meal-mates/SKILL.md && $PY -m unittest discover -s tests 2>&1 \| tail -2` |
| 39 | 2026-10-24 19:22:05 | `cd "~/WorkBuddy/MCdon/mcd-meal-mates" && $PY scripts/live_plan.py --city 上海 --area 人民广场 --store-code 1450713 --budget A=3500 --budget B=3500 --coupon "9900016523=[REDACTE …` |
| 40 | 2026-10-24 19:22:30 | `cd "~/WorkBuddy/MCdon/mcd-meal-mates" && $PY scripts/live_plan.py --city 上海 --area 人民广场 --store-code 1450713 --budget A=3500 --budget B=3500 --coupon "9900016523=[REDACTE …` |
| 41 | 2026-10-24 19:22:40 | `cd "~/WorkBuddy/MCdon/mcd-meal-mates" && python scripts/check_plan.py --in runtime/live-coupon-input.json --out runtime/live-coupon-result.json` |
| 42 | 2026-10-24 19:22:56 | `cd "~/WorkBuddy/MCdon/mcd-meal-mates" && grep -n "MAIN_MISSING\\|DRINK_MISSING\\|def .*satisf\\|combo" scripts/check_plan.py \| head -30` |
| 43 | 2026-10-24 19:23:05 | `cd "~/WorkBuddy/MCdon/mcd-meal-mates" && grep -n "_is_drink_component" -A 12 scripts/check_plan.py \| head -25` |
| 44 | 2026-10-24 19:23:26 | `cd "~/WorkBuddy/MCdon/mcd-meal-mates" && grep -n "DRINK_NAME_HINTS\s*=" -A 3 scripts/check_plan.py` |
| 45 | 2026-10-24 19:24:03 | `cd "~/WorkBuddy/MCdon/mcd-meal-mates" && $PY scripts/live_plan.py --city 上海 --area 人民广场 --store-code 1450713 --budget A=3500 --budget B=3500 --coupon "9900016523=[REDACTE …` |
| 46 | 2026-10-24 19:24:12 | `cd "~/WorkBuddy/MCdon/mcd-meal-mates" && python scripts/check_plan.py --in runtime/live-coupon-input.json --out runtime/live-coupon-result.json 2>&1` |
| 47 | 2026-10-24 19:24:21 | `cd "~/WorkBuddy/MCdon/mcd-meal-mates" && $PY -m unittest discover -s tests 2>&1 \| tail -4 && $PY -c " import json d=json.load(open('runtime/live-coupon-result.json',encod …` |
| 48 | 2026-10-24 19:24:31 | `cd "~/WorkBuddy/MCdon/mcd-meal-mates" && python scripts/render_card.py --in runtime/live-coupon-result.json --out runtime/live-coupon-card.html --title "麦单合伙人 · 麦当劳上海黄浦华旭 …` |
| 49 | 2026-10-24 19:24:43 | `cd "~/WorkBuddy/MCdon/mcd-meal-mates" && grep -n "^def \\|^class \\|def _load\\|def setUp\\|base_plan\\|def _base" tests/test_check_plan.py \| head -25` |
| 50 | 2026-10-24 19:25:02 | `cd "~/WorkBuddy/MCdon/mcd-meal-mates" && python -m unittest discover -s tests 2>&1 \| tail -8` |
| 51 | 2026-10-24 19:25:27 | `cd "~/WorkBuddy/MCdon/mcd-meal-mates" && grep -n "^## \\|^### " MCP_INTEGRATION.md` |
| 52 | 2026-10-24 19:26:36 | `cd "~/WorkBuddy/MCdon/mcd-meal-mates" && $PY scripts/live_plan.py --city 上海 --area 人民广场 --store-code 1450713 --budget A=3000 --budget B=3500 --plan "P1\|主食齐全 + 共享脆薯饼\|A:mai …` |
| 53 | 2026-10-24 19:34:14 | `ls -lat "~/Downloads" 2>/dev/null \| head -20` |
| 54 | 2026-10-24 19:34:21 | `find "~/Downloads" "~/Desktop" "~/Documents/Codex" "~/WorkBuddy/MCdon" -maxdepth 3 -type f \( -iname "*验收*" -o -iname "*修正*" -o -iname "*清单*" -o -iname "*checklist*" -o - …` |
| 55 | 2026-10-24 19:34:27 | `ls -lat "~/Documents/Codex/2026-10-24/new-chat/outputs" && echo "--- 上级 ---" && ls -lat "~/Documents/Codex/2026-10-24/new-chat"` |
| 56 | 2026-10-24 19:36:34 | `cd "~/WorkBuddy/MCdon/mcd-meal-mates" && python -m unittest discover -s tests 2>&1 \| tail -25` |
| 57 | 2026-10-24 19:36:50 | `cd "~/WorkBuddy/MCdon/mcd-meal-mates" && $PY scripts/live_plan.py --city 上海 --area 人民广场 --store-code 1450713 --budget A=3000 --budget B=3500 --plan "P1\|主食齐全 + 共享脆薯饼\|A:mai …` |
| 58 | 2026-10-24 19:37:03 | `cd "~/WorkBuddy/MCdon/mcd-meal-mates" && $PY scripts/live_plan.py --city 上海 --area 人民广场 --store-code 1450713 --budget A=3000 --budget B=3000 --plan "P1\|两人各一份麦香鸡\|A:main:14 …` |
| 59 | 2026-10-24 19:37:09 | `cd "~/WorkBuddy/MCdon/mcd-meal-mates" && grep -n "5\.2 的数字\\|31 个\\|31 个用例\\|优惠券 \| 未接入\\|未接入" README.md docs/DEVELOPMENT_PROVENANCE.md docs/IMPLEMENTATION_STATUS.md \| head -1 …` |
| 60 | 2026-10-24 19:37:15 | `cd "~/WorkBuddy/MCdon/mcd-meal-mates" && sed -n '160,166p' README.md` |
| 61 | 2026-10-24 19:37:51 | `cd "~/WorkBuddy/MCdon/mcd-meal-mates" && tail -6 CONTEST_DECLARATION.md && echo "=== 行数 ===" && wc -l CONTEST_DECLARATION.md` |
| 62 | 2026-10-24 19:38:09 | `cd "~/WorkBuddy/MCdon/mcd-meal-mates" && mkdir -p docs/screenshots && cp runtime/live-shot.png docs/screenshots/live-card-real.png && cp runtime/coupon-shot.png docs/scre …` |
| 63 | 2026-10-24 19:38:57 | `cd "~/WorkBuddy/MCdon/mcd-meal-mates" && python - <<'EOF' import zipfile, os from pathlib import Path root = Path('.') out = Path('dist/mcd-meal-mates-submission-20261024 …` |
| 64 | 2026-10-24 19:39:05 | `cd "~/WorkBuddy/MCdon/mcd-meal-mates" && python - <<'EOF' import zipfile, re zip_path = 'dist/mcd-meal-mates-submission-20261024.zip' patterns = { 'MCP Token': re.compile …` |
| 65 | 2026-10-24 19:39:13 | `cd "~/WorkBuddy/MCdon/mcd-meal-mates" && grep -n "Bearer" scripts/mcd_client.py \| head -8` |
| 66 | 2026-10-24 19:39:34 | `cd /tmp && rm -rf pkgtest && mkdir pkgtest && cd pkgtest && python -c "import zipfile;zipfile.ZipFile('~/WorkBuddy/MCdon/mcd-meal-mates/dist/mcd-meal-mates-submission-202 …` |
| 67 | 2026-10-24 19:39:41 | `cd "~/WorkBuddy/MCdon/mcd-meal-mates" && grep -n "^## " README.md \| tail -5 && printf '\n# 提交包（构建产物）\ndist/\n' >> .gitignore && tail -4 .gitignore` |
| 68 | 2026-10-24 19:39:48 | `cd "~/WorkBuddy/MCdon/mcd-meal-mates" && cat >> README.md <<'EOF' ## 8. 提交包 `dist/mcd-meal-mates-submission-20261024.zip` 是给活动方看的那一份，已排除： - `runtime/`（探针与核价的原始返回，含本账号券相关数 …` |
| 69 | 2026-10-24 19:39:54 | `cd "~/WorkBuddy/MCdon/mcd-meal-mates" && python - <<'EOF' import zipfile from pathlib import Path skip_dirs = {'runtime', '.git', '__pycache__', 'dist', '.idea', '.vscode …` |
| 70 | 2026-10-24 20:27:35 | `ls -la "~/.workbuddy" 2>/dev/null \| head -30; echo "=== 可能的会话目录 ==="; find "~/.workbuddy" -maxdepth 2 -type d \( -iname "*session*" -o -iname "*history*" -o -iname "*conv …` |
| 71 | 2026-10-24 20:27:42 | `for d in "~/.workbuddy/sessions" "~/.workbuddy/workspace/sessions" "~/.workbuddy/app/session"; do echo "=== $d ==="; ls -lat "$d" 2>/dev/null \| head -8; done` |
| 72 | 2026-10-24 20:27:48 | `cat "~/.workbuddy/sessions/58384.json" 2>/dev/null; echo; echo "=== workspace session dir ==="; ls -lat "~/.workbuddy/workspace/sessions/34cbab85-5e8a-4b37-8549-996db17e2 …` |
| 73 | 2026-10-24 20:27:56 | `cd "~/.workbuddy" && ls -la "bab80088-88e3-4a59-82b8-f44b9fe87aaf" 2>/dev/null \| head -15; echo "=== 找 db ==="; find . -maxdepth 3 -type f \( -name "*.db" -o -name "*.sql …` |
| 74 | 2026-10-24 20:28:02 | `cd "~/.workbuddy/audit-log" && wc -l 2026-10-24.jsonl && python -c " import json rows=[json.loads(l) for l in open('2026-10-24.jsonl',encoding='utf-8') if l.strip()] prin …` |
| 75 | 2026-10-24 20:28:11 | `ls -lat "~/.workbuddy/cache/conversation-product-spill" 2>/dev/null \| head -10; echo "=== 找含对话文本的路径 ==="; ls -lat "~/.workbuddy/changes-detail" 2>/dev/null \| head -5` |
| 76 | 2026-10-24 20:28:21 | `cd "~/.workbuddy/audit-log" && python -c " import json from collections import Counter rows=[json.loads(l) for l in open('2026-10-24.jsonl',encoding='utf-8') if l.strip() …` |
| 77 | 2026-10-24 20:28:31 | `cd "~/.workbuddy/audit-log" && python -c " import json from collections import Counter rows=[json.loads(l) for l in open('2026-10-24.jsonl',encoding='utf-8') if l.strip() …` |
| 78 | 2026-10-24 20:28:53 | `cd "~/WorkBuddy/MCdon/mcd-meal-mates" && python tools/export_workbuddy_trace.py --out docs/workbuddy-audit-record.md 2>&1 \| tail -5 && echo "=== 预览 ===" && head -45 docs/ …` |
| 79 | 2026-10-24 20:28:59 | `cd "~/.workbuddy/audit-log/spool" && ls -la && for f in *.jsonl; do echo "--- $f: $(wc -l < "$f") 行"; done` |
| 80 | 2026-10-24 20:29:17 | `cd "~/WorkBuddy/MCdon/mcd-meal-mates" && python tools/export_workbuddy_trace.py --out docs/workbuddy-audit-record.md 2>&1 \| tail -3 && sed -n '9,14p;37,42p' docs/workbudd …` |
| 81 | 2026-10-24 20:29:23 | `cd "~/WorkBuddy/MCdon/mcd-meal-mates" && echo "=== 敏感信息扫描 ===" && grep -n "1TKHMDD\\|MCD6YK\\|BF9D73D8\\|1[3-9][0-9]\{9\}" docs/workbuddy-audit-record.md \| head -5; echo "(空 …` |
| 82 | 2026-10-24 20:29:43 | `cd "~/WorkBuddy/MCdon/mcd-meal-mates" && python tools/export_workbuddy_trace.py --out docs/workbuddy-audit-record.md 2>&1 \| tail -2 && echo "=== 敏感扫描 ===" && grep -n "1TK …` |

## 4. 被写入过的文件（去重）

共 25 个路径：

- `~/.workbuddy/mcp.json`
- `~/Documents/Codex/2026-10-24/new-chat/outputs/发给WorkBuddy的启动指令.md`
- `~/Documents/Codex/2026-10-24/new-chat/outputs/麦单合伙人-WorkBuddy执行方案.md`
- `~/Documents/Codex/2026-10-24/new-chat/outputs/麦单合伙人-验收与修正清单.md`
- `~/WorkBuddy/MCdon/.workbuddy/memory/2026-10-24.md`
- `~/WorkBuddy/MCdon/mcd-meal-mates/CONTEST_DECLARATION.md`
- `~/WorkBuddy/MCdon/mcd-meal-mates/MCP_INTEGRATION.md`
- `~/WorkBuddy/MCdon/mcd-meal-mates/README.md`
- `~/WorkBuddy/MCdon/mcd-meal-mates/docs/DEMO.md`
- `~/WorkBuddy/MCdon/mcd-meal-mates/docs/DEVELOPMENT_PROVENANCE.md`
- `~/WorkBuddy/MCdon/mcd-meal-mates/docs/IMPLEMENTATION_STATUS.md`
- `~/WorkBuddy/MCdon/mcd-meal-mates/docs/RESULT_SCHEMA.md`
- `~/WorkBuddy/MCdon/mcd-meal-mates/examples/fictional-input.json`
- `~/WorkBuddy/MCdon/mcd-meal-mates/runtime/coupon-shot.png`
- `~/WorkBuddy/MCdon/mcd-meal-mates/runtime/live-shot.png`
- `~/WorkBuddy/MCdon/mcd-meal-mates/runtime/shot-top.png`
- `~/WorkBuddy/MCdon/mcd-meal-mates/runtime/shot.png`
- `~/WorkBuddy/MCdon/mcd-meal-mates/scripts/check_plan.py`
- `~/WorkBuddy/MCdon/mcd-meal-mates/scripts/live_plan.py`
- `~/WorkBuddy/MCdon/mcd-meal-mates/scripts/probe_mcp.py`
- `~/WorkBuddy/MCdon/mcd-meal-mates/skills/meal-mates/SKILL.md`
- `~/WorkBuddy/MCdon/mcd-meal-mates/tests/test_check_plan.py`
- `~/WorkBuddy/MCdon/mcd-meal-mates/tests/test_live_plan.py`
- `~/WorkBuddy/MCdon/mcd-meal-mates/tools/export_workbuddy_trace.py`
- `~/WorkBuddy/MCdon/mcd-meal-mates/workbuddy.md`

## 5. 这份记录能证明什么、不能证明什么

能证明：

- 开发确实在本机 WorkBuddy 会话中发生，有平台生成的时间戳与哈希链。
- 命令的先后顺序、参数（含门店编码、预算、选品）与文件写入范围可逐条核对。
- 凭据在平台侧即被脱敏，日志里没有明文 Token。

不能证明：

- 不能替代对话原文：不含我的解释、取舍理由与你的指令。
- 不含 MCP 调用明细：那部分记录在麦当劳服务端，本地只能靠 `runtime/probe-*.json` 佐证。

## 6. 如何复现

```bash
python tools/export_workbuddy_trace.py --out docs/workbuddy-audit-record.md
# 指定会话与日期
python tools/export_workbuddy_trace.py --session <sessionId> --since 2026-10-24 --out <路径>
```
