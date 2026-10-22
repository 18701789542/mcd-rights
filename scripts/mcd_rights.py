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
from rights import analyze, collect, markdown, number


def main(argv=None):
    parser = argparse.ArgumentParser(description="麦当劳权益只读助手；默认不写入报告或调用任何交易工具。")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--demo", action="store_true", help="离线合成数据演示，无需Token")
    mode.add_argument("--live", action="store_true", help="查询本人实时权益")
    mode.add_argument("--set-token", action="store_true", help="从隐藏输入或stdin设置当前Windows用户的DPAPI加密Token")
    parser.add_argument("--focus", choices=["points", "coupons", "both"], default="both")
    parser.add_argument("--plan", choices=["yes", "no", "unknown"], default="unknown", help="近期是否计划消费")
    parser.add_argument("--visit-date", help="计划消费日期，YYYY-MM-DD；不填则不制造紧迫倒计时")
    parser.add_argument("--catalog", action="store_true", help="按你的要求查询商品券类兑换候选")
    parser.add_argument("--product-id", type=int, help="从真实列表选定的商品ID；会先重查列表再取详情")
    parser.add_argument("--cash-budget", help="兑换与使用合计的总现金预算，非负十进制数")
    parser.add_argument("--format", choices=["markdown", "json"], default="markdown")
    args = parser.parse_args(argv)
    if args.visit_date:
        try:
            date.fromisoformat(args.visit_date)
        except ValueError:
            parser.error("visit-date应为YYYY-MM-DD。")
    if args.cash_budget is not None and number(args.cash_budget) is None:
        parser.error("cash-budget应为有限、非负的十进制数。")
    if args.product_id is not None and args.product_id <= 0:
        parser.error("product-id应为正整数。")
    try:
        if args.set_token:
            token = getpass.getpass("MCP Token（不回显）：") if sys.stdin.isatty() else sys.stdin.read().strip()
            save_token(token)
            print("已保存至当前Windows用户的本机加密凭证文件，未写入技能目录。")
            return 0
        if args.demo:
            snapshot = json.loads((Path(__file__).resolve().parent.parent / "examples" / "synthetic.json").read_text(encoding="utf-8"))
            for name, wanted in [("points", args.focus in ("both", "points")), ("coupons", args.focus in ("both", "coupons")), ("catalog", args.catalog or args.product_id is not None), ("detail", args.product_id is not None)]:
                if not wanted:
                    snapshot["sections"][name] = {"status": "not_requested"}
            if args.product_id is not None and args.product_id != 103:
                snapshot["sections"]["detail"] = {"status": "error", "error": "合成演示仅提供商品103的详情。"}
        else:
            client = McdClient(load_token())
            client.initialize()
            snapshot = collect(client, args.focus, args.catalog, args.product_id)
        report = analyze(snapshot, args.plan, args.visit_date, args.cash_budget)
        report["mode"] = "synthetic_demo" if args.demo else "live"
        if args.format == "json":
            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            print(markdown(report, args.demo), end="")
        return 2 if any(s["status"] in ("error", "partial") for s in report["sections"].values()) else 0
    except McdError as error:
        print(json.dumps({"status": "error", "message": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
