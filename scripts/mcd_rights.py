#!/usr/bin/env python3
"""麦麦权益先用谁 — live read-only query or explicitly synthetic demonstration."""
from __future__ import annotations

import argparse
import getpass
import json
import sys
from datetime import date
from pathlib import Path

from mcd_client import McdClient, McdError, load_token, save_token
from rights import analyze, collect, markdown, number, plain


def ask(prompt, default, valid, error):
    while True:
        value = input(prompt).strip() or default
        if valid(value):
            return value
        print(error)


def valid_date(value):
    try:
        date.fromisoformat(value)
        return True
    except ValueError:
        return False


def configure_interactive(args):
    print("麦麦权益先用谁 · 引导查询（只读，不兑换或下单）")
    focuses = {"1": "points", "2": "coupons", "3": "both"}
    focus_default = next(k for k, v in focuses.items() if v == args.focus)
    args.focus = focuses[ask(f"关注什么？1积分 / 2账户券 / 3两者 [默认{focus_default}]：", focus_default, lambda v: v in focuses, "请输入1、2或3。")]
    plans = {"1": "yes", "2": "no", "3": "unknown"}
    plan_default = next(k for k, v in plans.items() if v == args.plan)
    args.plan = plans[ask(f"近期是否计划消费？1是 / 2否 / 3不确定 [默认{plan_default}]：", plan_default, lambda v: v in plans, "请输入1、2或3。")]
    if args.plan == "yes":
        args.visit_date = ask("计划日期 YYYY-MM-DD（可留空）：", args.visit_date or "", lambda v: not v or valid_date(v), "请输入有效日期或留空。") or None
    else:
        args.visit_date = None
    args.catalog = False if args.plan == "no" else ask("是否查看积分兑换商品？y/N：", "n", lambda v: v.lower() in ("y", "n"), "请输入y或n。 ").lower() == "y"
    if args.catalog:
        args.cash_budget = ask("兑换与用券合计现金预算（可留空，0表示不加钱）：", args.cash_budget or "", lambda v: not v or number(v) is not None, "请输入非负金额或留空。") or None


def query_snapshot(args, client):
    if not args.demo:
        return collect(client, args.focus, args.catalog, args.product_id)
    snapshot = json.loads((Path(__file__).resolve().parent.parent / "examples" / "synthetic.json").read_text(encoding="utf-8"))
    for name, wanted in [("points", args.focus in ("both", "points")), ("coupons", args.focus in ("both", "coupons")), ("catalog", args.catalog or args.product_id is not None), ("detail", args.product_id is not None)]:
        if not wanted:
            snapshot["sections"][name] = {"status": "not_requested"}
    if args.product_id is not None and args.product_id != 103:
        snapshot["sections"]["detail"] = {"status": "error", "error": "合成演示仅提供商品103的详情。"}
    return snapshot


def show_report(args, snapshot):
    report = analyze(snapshot, args.plan, args.visit_date, args.cash_budget)
    report["mode"] = "synthetic_demo" if args.demo else "live"
    if args.format == "json":
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(markdown(report, args.demo), end="")
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description="麦当劳权益只读助手；默认不写入报告或调用任何交易工具。")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--demo", action="store_true", help="离线合成数据演示，无需Token")
    mode.add_argument("--live", action="store_true", help="查询本人实时权益")
    mode.add_argument("--set-token", action="store_true", help="从隐藏输入或stdin设置当前Windows用户的DPAPI加密Token")
    parser.add_argument("--token-file", help="Windows DPAPI加密凭证文件的绝对路径；显式指定时优先于MCD_MCP_TOKEN")
    parser.add_argument("--focus", choices=["points", "coupons", "both"], default="both")
    parser.add_argument("--plan", choices=["yes", "no", "unknown"], default="unknown", help="近期是否计划消费")
    parser.add_argument("--visit-date", help="计划消费日期，YYYY-MM-DD；不填则不制造紧迫倒计时")
    parser.add_argument("--catalog", action="store_true", help="按你的要求查询商品券类兑换候选")
    parser.add_argument("--product-id", type=int, help="从真实列表选定的商品ID；会先重查列表再取详情")
    parser.add_argument("--cash-budget", help="兑换与使用合计的总现金预算，非负十进制数")
    parser.add_argument("--format", choices=["markdown", "json"], default="markdown")
    parser.add_argument("--interactive", action="store_true", help="引导填写消费计划、查看权益、选择真实商品并核对完整成本")
    args = parser.parse_args(argv)
    if args.demo and args.token_file is not None:
        parser.error("合成演示无需凭证，请不要同时指定token-file。")
    if args.visit_date:
        try:
            date.fromisoformat(args.visit_date)
        except ValueError:
            parser.error("visit-date应为YYYY-MM-DD。")
    if args.cash_budget is not None and number(args.cash_budget) is None:
        parser.error("cash-budget应为有限、非负的十进制数。")
    if args.product_id is not None and args.product_id <= 0:
        parser.error("product-id应为正整数。")
    if args.interactive and (args.format != "markdown" or args.set_token or args.product_id is not None):
        parser.error("interactive用于人类交互，请使用Markdown输出，并单独选择商品或设置Token。")
    try:
        if args.set_token:
            token = getpass.getpass("MCP Token（不回显）：") if sys.stdin.isatty() else sys.stdin.read().strip()
            save_token(token, args.token_file) if args.token_file is not None else save_token(token)
            print("已保存至当前Windows用户的本机加密凭证文件，未写入技能目录。")
            return 0
        if args.interactive:
            configure_interactive(args)
        client = None
        if not args.demo:
            client = McdClient(load_token(args.token_file) if args.token_file is not None else load_token())
            client.initialize()
        report = show_report(args, query_snapshot(args, client))
        if args.interactive and args.catalog:
            products = report["sections"].get("catalog", {}).get("items", [])
            if products:
                print("\n请选择想核对的商品；只查你选的商品详情：")
                for p in products:
                    print(f"  ID {plain(p['id'])}：{plain(p['title'])}")
                ids = {str(p["id"]) for p in products if type(p.get("id")) is int and p["id"] > 0}
                selected = ask("商品ID（留空结束）：", "", lambda v: not v or v in ids, "请选择上面显示的商品ID，或留空结束。")
                if selected:
                    args.product_id = int(selected)
                    print("\n已选择商品，正在重新查询积分、列表与详情以核对完整成本。")
                    report = show_report(args, query_snapshot(args, client))
        return 2 if any(s["status"] in ("error", "partial") for s in report["sections"].values()) else 0
    except McdError as error:
        print(json.dumps({"status": "error", "message": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 1
    except (EOFError, KeyboardInterrupt):
        print("\n查询已结束；未执行兑换或下单。", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
