#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""麦单合伙人 · 点单卡渲染器。

把 check_plan.py 产出的结果 JSON 注入单文件 HTML 模板，输出可 file:// 直接打开的
点单卡。渲染器不做任何金额重算：页面上的数字全部来自结果 JSON。

用法：
    python scripts/render_card.py --in runtime/fictional-result.json --out runtime/card.html
    python scripts/render_card.py --in result.json --out card.html --title "我的点单卡"
"""

from __future__ import annotations

import argparse
import html
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TEMPLATE = ROOT / "templates" / "result.html"

DATA_MARKER = "__MEAL_MATES_DATA__"
TITLE_MARKER = "__MEAL_MATES_TITLE__"
DEFAULT_TITLE = "麦单合伙人 · 点单卡"


class InputError(Exception):
    """结果 JSON 不满足契约。"""


def fail(message: str, code: int = 2) -> "NoReturn":  # type: ignore[valid-type]
    print("[render_card] " + message, file=sys.stderr)
    raise SystemExit(code)


def load_result(path: Path) -> dict:
    if not path.exists():
        fail("找不到输入文件：{}".format(path), 2)
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        fail("读取输入文件失败：{}（{}）".format(path, exc), 2)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        fail(
            "结果 JSON 解析失败：第 {} 行第 {} 列，{}".format(exc.lineno, exc.colno, exc.msg),
            2,
        )
    if not isinstance(data, dict):
        fail("结果 JSON 顶层必须是对象，实际是 {}".format(type(data).__name__), 2)
    return data


def validate(data: dict) -> None:
    """只做契约级校验，不做业务判断（业务判断属于 check_plan.py）。"""
    version = data.get("schema_version")
    if version is None or str(version).strip() == "":
        fail("结果缺少 schema_version 字段，无法确认数据契约版本。", 2)

    plans = data.get("plans")
    if not isinstance(plans, list) or not plans:
        fail("结果中的 plans 为空，没有可渲染的方案。", 2)

    for index, plan in enumerate(plans):
        if not isinstance(plan, dict):
            fail("plans[{}] 不是对象，无法渲染。".format(index), 2)
        if plan.get("plan_id") in (None, ""):
            fail("plans[{}] 缺少 plan_id，无法用于方案切换。".format(index), 2)

    members = data.get("members")
    if members is not None and not isinstance(members, list):
        fail("members 必须是数组。", 2)


def encode_payload(data: dict) -> str:
    """序列化为可安全放进 <script type="application/json"> 的文本。

    除 JSON 本身的转义外，额外把 < > & 转成 \\uXXXX，避免出现 </script> 提前闭合
    或 <!-- 注释起始，从而杜绝内嵌数据逃逸成脚本。
    """
    raw = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return (
        raw.replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )


def pick_title(data: dict, title: str | None) -> str:
    if title and title.strip():
        return html.escape(title.strip(), quote=True)
    store = data.get("store") if isinstance(data.get("store"), dict) else {}
    store_name = store.get("store_name")
    if isinstance(store_name, str) and store_name.strip():
        return html.escape("麦单合伙人 · {} 点单卡".format(store_name.strip()), quote=True)
    return html.escape(DEFAULT_TITLE, quote=True)


def render(template_path: Path, data: dict, title: str | None) -> str:
    if not template_path.exists():
        fail("找不到模板文件：{}".format(template_path), 2)
    try:
        template = template_path.read_text(encoding="utf-8")
    except OSError as exc:
        fail("读取模板失败：{}（{}）".format(template_path, exc), 2)

    if DATA_MARKER not in template:
        fail("模板缺少数据占位符 {}。".format(DATA_MARKER), 2)
    if TITLE_MARKER not in template:
        fail("模板缺少标题占位符 {}。".format(TITLE_MARKER), 2)

    payload = encode_payload(data)
    page = template.replace(DATA_MARKER, payload, 1).replace(
        TITLE_MARKER, pick_title(data, title), 1
    )
    return page


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except (OSError, ValueError):
            pass
    if hasattr(sys.stderr, "reconfigure"):
        try:
            sys.stderr.reconfigure(encoding="utf-8")
        except (OSError, ValueError):
            pass

    parser = argparse.ArgumentParser(
        prog="render_card.py",
        description="把麦单合伙人结果 JSON 渲染为单文件 HTML 点单卡（只展示，不重算金额）。",
    )
    parser.add_argument("--in", dest="input_path", required=True, help="结果 JSON 路径")
    parser.add_argument("--out", dest="output_path", required=True, help="输出 HTML 路径")
    parser.add_argument("--title", dest="title", default=None, help="页面标题（可选）")
    parser.add_argument(
        "--template",
        dest="template_path",
        default=str(DEFAULT_TEMPLATE),
        help="模板路径（默认 templates/result.html）",
    )
    args = parser.parse_args(argv)

    in_path = Path(args.input_path)
    out_path = Path(args.output_path)

    data = load_result(in_path)
    validate(data)

    page = render(Path(args.template_path), data, args.title)

    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        # newline="" 保留模板里的原始换行；文件头已由 <meta charset="utf-8"> 声明。
        with out_path.open("w", encoding="utf-8", newline="") as handle:
            handle.write(page)
    except OSError as exc:
        print("[render_card] 写入输出文件失败：{}（{}）".format(out_path, exc), file=sys.stderr)
        return 1

    plans = data.get("plans") or []
    mode = data.get("data_mode") or "unknown"
    sizes = len(page.encode("utf-8"))
    print(
        "已生成点单卡：{}（{} 套方案，data_mode={}，{:.1f} KB）".format(
            out_path, len(plans), mode, sizes / 1024.0
        )
    )
    for plan in plans:
        if not isinstance(plan, dict):
            continue
        quote = plan.get("quote") if isinstance(plan.get("quote"), dict) else {}
        alloc = plan.get("allocation") if isinstance(plan.get("allocation"), dict) else {}
        print(
            "  - {} {} | 报价 {} | 分账 {} | 可行 {}".format(
                plan.get("plan_id"),
                plan.get("label") or "(未命名)",
                quote.get("status") or "未知",
                alloc.get("method") or "未知",
                "是" if plan.get("feasible") is True else "否",
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
