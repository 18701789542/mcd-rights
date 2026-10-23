"""Deterministic interpretation; no HTTP, credentials, file writes or model calls."""
from __future__ import annotations

from datetime import date, datetime, timezone, timedelta
from decimal import Decimal, InvalidOperation
import hashlib

TZ = timezone(timedelta(hours=8))


def number(value):
    if value is None or isinstance(value, bool) or not str(value).strip():
        return None
    try:
        result = Decimal(str(value))
        return result if result.is_finite() and result >= 0 else None
    except InvalidOperation:
        return None


def numeric_text(value):
    n = number(value)
    return format(n, "f") if n is not None else None


def temporal(value, now, end=False):
    """Return factual timing label; date-only values never become hourly countdowns."""
    if not isinstance(value, str) or not value.strip():
        return "unknown"
    try:
        if len(value) == 10:
            d = date.fromisoformat(value)
            return "past" if d < now.date() else "today" if d == now.date() else "future"
        d = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=TZ)  # Official server fields use GMT+08:00.
        if d == now:
            return "boundary"
        return "past" if d < now else "future"
    except (ValueError, TypeError):
        return "unknown"


def coupon_identity(coupon):
    key = coupon.get("id") or coupon.get("code")
    if not key:
        return None
    return hashlib.sha256(str(key).encode()).hexdigest()


def coupon_deadline_key(coupon):
    value = coupon.get("end")
    if not isinstance(value, str):
        return (1, "", "")
    try:
        if len(value) == 10:
            return (0, date.fromisoformat(value).isoformat(), "")
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        parsed = parsed.replace(tzinfo=TZ) if parsed.tzinfo is None else parsed.astimezone(TZ)
        return (0, parsed.date().isoformat(), parsed.time().isoformat())
    except ValueError:
        return (1, "", "")


def assess_sku(sku, available, budget, window_valid, plan):
    """Compare returned balances/costs; this is never a transaction eligibility check."""
    points, exchange, use = (number(sku.get(k)) for k in ("points", "price", "extTradePrice"))
    total = exchange + use if exchange is not None and use is not None else None
    enough = None if points is None or available is None else available >= points
    gap = max(points - available, Decimal(0)) if enough is not None else None
    valid_id = type(sku.get("skuId")) is int and sku["skuId"] > 0
    budget_status = "未设置预算" if budget is None else "成本待核实" if total is None else "在总现金预算内（仍需官方核验）" if total <= budget else "超过总现金预算"
    points_status = "积分信息不足" if enough is None else "当前积分足够" if enough else f"当前积分不足，还差{numeric_text(gap)}积分"
    can_consider = valid_id and enough is True and total is not None and budget is not None and total <= budget and window_valid and plan == "yes"
    if plan == "no":
        decision = "近期不消费，无需为权益兑换。"
    elif enough is False:
        decision = points_status + "，本次不列为候选。"
    elif budget is not None and total is not None and total > budget:
        decision = "超过总现金预算，本次不列为候选。"
    elif enough is None or total is None:
        decision = "积分或完整现金成本不足，暂不能判断。"
    elif not valid_id:
        decision = "SKU标识缺失或异常，无法列为可选择的候选。"
    elif not window_valid:
        decision = "商品状态或展示时间未确认，暂不能列为候选。"
    elif plan != "yes":
        decision = "先确认是否计划消费，再决定是否考虑兑换。"
    elif budget is None:
        decision = "当前积分足够；请先确定总现金预算。"
    else:
        decision = "积分足够且在总现金预算内；库存、资格与券适用性仍需官方核验。"
    return {"sku_id": sku.get("skuId"), "points": numeric_text(points), "exchange_cash": numeric_text(exchange), "use_cash": numeric_text(use), "total_cash": numeric_text(total), "points_gap": numeric_text(gap), "points_status": points_status, "budget_status": budget_status, "can_consider": can_consider, "decision": decision}
def fetch_coupons(call):
    coupons, identities, reasons, total = [], set(), [], None
    pages, expected_last = 0, None
    for page in range(1, 6):
        try:
            data = call("query-my-coupons", {"page": str(page), "pageSize": "200"})
        except Exception:
            if not pages:
                return {"status": "error", "error": "账户券查询失败；不能据此判断没有券。"}
            reasons.append("分页中途失败")
            break
        if not isinstance(data, dict) or not isinstance(data.get("coupons"), list):
            reasons.append("分页结构不完整")
            break
        count, last, current = data.get("totalCount"), data.get("totalPages"), data.get("currentPage")
        if any(type(x) is not int or x < 0 for x in (count, last)) or type(current) is not int or current != page:
            reasons.append("分页计数或页码异常")
            break
        if total is None:
            total = count
            expected_last = last
        if total != count or expected_last != last or (count > 0 and last == 0) or len(data['coupons']) > 200:
            reasons.append("分页总数变化或矛盾")
            break
        pages += 1
        for c in data["coupons"]:
            if not isinstance(c, dict):
                reasons.append("券条目结构异常")
                continue
            key = coupon_identity(c)
            if key is None:
                reasons.append("券缺少去重标识，覆盖无法确认")
            elif key in identities:
                reasons.append("分页出现重复券")
                continue
            else:
                identities.add(key)
            # IDs, codes, images and raw descriptions are not exposed in output.
            coupons.append({k: c.get(k) for k in ("title", "subtitle", "tradeStartDate", "tradeEndDate", "datetimeText", "enable")})
        if page >= last:
            if len(coupons) != total:
                reasons.append("返回数量与总数不一致")
            break
        if not data["coupons"]:
            reasons.append("中间页为空")
            break
        if page == 5:
            reasons.append("超过最多5页的读取范围")
    state = "partial" if reasons else "empty" if total == 0 else "ok"
    return {"status": state, "data": coupons, "total": total, "read_count": len(coupons), "pages": pages, "limitations": list(dict.fromkeys(reasons))}


def collect(client, focus="both", catalog=False, product_id=None):
    sections = {name: {"status": "not_requested"} for name in ("points", "coupons", "catalog", "detail")}
    try:
        data = client.call("now-time-info")
        now = datetime.fromisoformat(data["datetime"].replace("Z", "+00:00"))
        now = now.replace(tzinfo=TZ) if now.tzinfo is None else now.astimezone(TZ)
        time_source = "MCP服务时间"
    except Exception:
        now = datetime.now(TZ)
        time_source = "本机时间（服务时间查询失败，需核对时钟）"
    for key, tool in [("points", "query-my-account")]:
        if focus in ("points", "both"):
            try:
                value = client.call(tool)
                if not isinstance(value, dict):
                    raise ValueError()
                fields = ("availablePoint", "currentMouthExpirePoint", "nextMouthExpirePoint")
                sections[key] = {"status": "ok", "data": {k: value.get(k) for k in fields}}
            except Exception:
                sections[key] = {"status": "error", "error": "积分查询失败，不能当作0积分。"}
    if focus in ("coupons", "both"):
        sections["coupons"] = fetch_coupons(client.call)
    if catalog or product_id is not None:
        try:
            products = client.call("mall-points-products", {"catRuleIds": "1>4"})
            if not isinstance(products, list) or any(not isinstance(p, dict) for p in products):
                raise ValueError()
            fields = ("spuId", "spuName", "point", "price", "status", "upTime", "downTime")
            sections["catalog"] = {"status": "ok" if products else "empty", "data": [{k: p.get(k) for k in fields} for p in products]}
            if product_id is not None:
                if not any(p.get("spuId") == product_id for p in products):
                    sections["detail"] = {"status": "error", "error": "该商品不在本轮商品券列表中；未请求详情。"}
                else:
                    detail = client.call("mall-product-detail", {"spuId": product_id})
                    if not isinstance(detail, dict) or detail.get("spuId") != product_id or not isinstance(detail.get("skuList"), list):
                        raise ValueError()
                    sections["detail"] = {"status": "ok", "data": {k: detail.get(k) for k in ("spuId", "spuName", "note", "upTime", "downTime", "skuList")}}
        except Exception:
            key = "detail" if sections["catalog"]["status"] in ("ok", "empty") and product_id is not None else "catalog"
            sections[key] = {"status": "error", "error": "商品查询失败，现金成本及可兑换状态未确认。"}
    return {"queried_at": now.isoformat(), "time_source": time_source, "sections": sections}


def analyze(snapshot, plan="unknown", visit_date=None, budget=None):
    now = datetime.fromisoformat(snapshot["queried_at"])
    if now.tzinfo is None:
        now = now.replace(tzinfo=TZ)
    else:
        now = now.astimezone(TZ)
    visit = date.fromisoformat(visit_date) if visit_date else None
    budget_value = number(budget)
    report = {"version": "0.2.1", "queried_at": now.isoformat(), "time_source": snapshot.get("time_source", "未说明"), "plan": plan, "visit_date": visit_date, "cash_budget": numeric_text(budget), "sections": {}, "attention": [], "uncertainties": []}
    attention = report["attention"]
    for name, section in snapshot["sections"].items():
        if section["status"] in ("error", "not_requested"):
            report["sections"][name] = {k: v for k, v in section.items() if k in ("status", "error")}
            if section["status"] == "error":
                report["uncertainties"].append(section.get("error", name + "查询失败"))
            continue
        if name == "points":
            data = section.get("data", {})
            values = {k: numeric_text(data.get(k)) for k in ("availablePoint", "currentMouthExpirePoint", "nextMouthExpirePoint")}
            report["sections"][name] = {"status": "ok", "values": values, "precision": "月份汇总，没有逐笔准确到期日"}
            if any(v is None for v in values.values()):
                report["uncertainties"].append("部分积分字段缺失或数值异常，未知不等于0。")
            for field, label in [("currentMouthExpirePoint", "本月将过期积分"), ("nextMouthExpirePoint", "下月将过期积分")]:
                amount = number(data.get(field))
                if amount is not None and amount > 0:
                    attention.append({"kind": "points", "title": label, "reason": f"{format(amount, 'f')}积分；仅月份汇总，不提供逐笔到期日。", "evidence": {"tool": "query-my-account", "field": field}, "priority": "先关注" if field.startswith("current") else "可以稍后看"})
        elif name == "coupons":
            items = []
            for c in section.get("data", []):
                timing, start = temporal(c.get("tradeEndDate"), now, True), temporal(c.get("tradeStartDate"), now)
                flag = "已过展示截止" if timing == "past" else "待核实" if timing in ("unknown", "boundary", "today") or start in ("unknown", "future", "boundary", "today") or c.get("enable") != 1 else "仅确认账户持有"
                title = str(c.get("title") or "未命名券")
                visit_fit = "未提供计划日期"
                if visit and start != "unknown" and timing != "unknown":
                    dates = []
                    for field in ("tradeStartDate", "tradeEndDate"):
                        parsed = datetime.fromisoformat(c[field].replace("Z", "+00:00"))
                        parsed = parsed.replace(tzinfo=TZ) if parsed.tzinfo is None else parsed.astimezone(TZ)
                        dates.append(parsed.date())
                    visit_fit = "计划日期尚未生效" if visit < dates[0] else "计划日期已过展示截止" if visit > dates[1] else "计划当日时段需核实" if visit in dates else "计划日期在展示范围内，订单适用性未核验"
                item = {"title": title, "start": c.get("tradeStartDate"), "end": c.get("tradeEndDate"), "timing": timing, "flag": flag, "visit_fit": visit_fit, "order_usable": "未核验"}
                items.append(item)
                if flag == "仅确认账户持有" and visit_fit not in ("计划日期尚未生效", "计划日期已过展示截止"):
                    end_dt = datetime.fromisoformat(c["tradeEndDate"].replace("Z", "+00:00"))
                    end_dt = end_dt.replace(tzinfo=TZ) if end_dt.tzinfo is None else end_dt.astimezone(TZ)
                    deadline = end_dt.date()
                    urgent = visit is not None and deadline <= visit
                    attention.append({"kind": "coupon", "title": title, "reason": f"展示结束时间：{c['tradeEndDate']}；{visit_fit}；门店与订单可用性未核验。", "priority": "先关注" if urgent else "可以稍后看", "sort_date": end_dt.isoformat(), "evidence": {"tool": "query-my-coupons", "field": "tradeEndDate"}})
            items.sort(key=coupon_deadline_key)
            report["sections"][name] = {"status": section["status"], "read_count": section.get("read_count", len(items)), "total": section.get("total"), "pages": section.get("pages"), "items": items, "limitations": section.get("limitations", [])}
            if section["status"] == "partial":
                report["uncertainties"].append("券列表覆盖不全：" + "；".join(section.get("limitations", [])))
            if any(c["flag"] == "待核实" for c in items):
                report["uncertainties"].append("部分券的时间或生效状态需核实，不保证当前可用。")
        elif name == "catalog":
            candidates, conflicts = [], []
            for p in section.get("data", []):
                start, end = temporal(p.get("upTime"), now), temporal(p.get("downTime"), now, True)
                if p.get("status") == 2 and start == "past" and end == "future" and len(str(p.get("upTime"))) > 10 and len(str(p.get("downTime"))) > 10:
                    candidates.append({"id": p.get("spuId"), "title": str(p.get("spuName") or "未命名商品"), "points": numeric_text(p.get("point")), "exchange_cash": numeric_text(p.get("price")), "use_cash": None, "budget_status": "完整现金成本待查详情", "flag": "待进一步核验的候选"})
                else:
                    conflicts.append({"title": str(p.get("spuName") or "未命名商品"), "reason": "状态与时间不一致或信息不足，不作为确定可兑换建议"})
            report["sections"][name] = {"status": section["status"], "candidate_count": len(candidates), "items": candidates, "excluded_count": len(conflicts), "excluded": conflicts}
        elif name == "detail":
            data, skus = section.get("data", {}), []
            account = snapshot["sections"].get("points", {})
            available = number(account.get("data", {}).get("availablePoint")) if account.get("status") == "ok" else None
            listing = next((p for p in snapshot["sections"].get("catalog", {}).get("data", []) if p.get("spuId") == data.get("spuId")), {})
            start, end = temporal(data.get("upTime"), now), temporal(data.get("downTime"), now, True)
            window_valid = (listing.get("status") == 2 and temporal(listing.get("upTime"), now) == "past" and temporal(listing.get("downTime"), now, True) == "future" and start == "past" and end == "future" and all(len(str(v)) > 10 for v in [listing.get("upTime"), listing.get("downTime"), data.get("upTime"), data.get("downTime")]))
            for sku in data.get("skuList", []):
                if not isinstance(sku, dict):
                    continue
                skus.append(assess_sku(sku, available, budget_value, window_valid, plan))
            window = "列表与详情均在展示窗口内，交易资格仍未核验" if window_valid else "状态或时间窗口异常／不足，请核实后再考虑兑换"
            report["sections"][name] = {"status": "ok", "title": str(data.get("spuName") or "未命名商品"), "note": data.get("note"), "skus": skus, "time_window": window, "transaction_validated": False}
            if not skus:
                report["uncertainties"].append("该商品没有返回可判断的SKU，不推荐兑换。")
    order = {"先关注": 0, "可以稍后看": 1}
    attention.sort(key=lambda a: (order.get(a["priority"], 2), a["kind"], a.get("sort_date", "")))
    report["attention"] = [] if plan == "no" else attention[:3]
    report["message"] = "近期不计划消费，无需为了权益额外花钱；临期事实仍在概览中展示。" if plan == "no" else "仅提供权益信息与关注顺序，不保证兑换、库存或门店订单可用性。"
    return report


def plain(value):
    """Render server strings as text, never HTML, links, Markdown instructions or commands."""
    text = str(value if value is not None else "未知").replace("\r", " ").replace("\n", " ")
    return text.replace("<", "＜").replace(">", "＞").replace("[", "［").replace("]", "］").replace("`", "｀").replace("|", "｜")


def markdown(report, demo=False):
    lines = ["# 麦麦权益先用谁", "", "**合成数据演示，不是你的真实权益。**" if demo else "本轮实时查询结果，仅供你本人查看。", "", f"查询时间：{plain(report['queried_at'])}（{plain(report['time_source'])}）", "", report["message"], ""]
    labels = {"points": "积分", "coupons": "账户券", "catalog": "兑换候选", "detail": "所选商品详情"}
    states = {"not_requested": "未查询", "error": "查询失败", "empty": "查询成功，列表为空", "partial": "部分结果，覆盖不全", "ok": "查询成功"}
    for key, s in report["sections"].items():
        lines += [f"## {labels[key]} · {states[s['status']]}", ""]
        if s["status"] in ("error", "not_requested"):
            if s.get("error"):
                lines += [plain(s["error"]), ""]
            continue
        if key == "points":
            for field, label in [("availablePoint", "可用积分"), ("currentMouthExpirePoint", "本月将过期"), ("nextMouthExpirePoint", "下月将过期")]:
                lines.append(f"- {label}：{plain(s['values'][field])}")
            lines += ["- 临期信息按月份汇总，不能换算成逐笔准确倒计时。", ""]
        elif key == "coupons":
            lines += [f"本次读到 {s['read_count']} 张；服务端总数 {plain(s['total'])}。门店／订单适用性未核验。", ""]
            for c in s["items"][:10]:
                lines.append(f"- {plain(c['title'])}：{plain(c['end'])}；{c['flag']}；{c['visit_fit']}")
            if len(s["items"]) > 10:
                lines.append("更多券请使用JSON输出查看，本段只展示前10条。")
            lines.append("")
        elif key == "catalog":
            lines += [f"候选 {s['candidate_count']} 项；排除或待核实 {s['excluded_count']} 项。候选不等于已确认可兑换。", ""]
            for p in s["items"][:5]:
                lines.append(f"- {plain(p['title'])}（ID {plain(p['id'])}）：{plain(p['points'])}积分，兑换现金{plain(p['exchange_cash'])}元，用券现金待核实。")
            if len(s["items"]) > 5:
                lines.append("本段展示前5项；更多商品ID请使用--format json查看。")
            lines += ["选择商品ID后可查询具体SKU成本。", ""]
        elif key == "detail":
            lines += [plain(s["title"]), "", plain(s["time_window"]), ""]
            for sku in s["skus"]:
                lines.append(f"- SKU {plain(sku['sku_id'])}：{plain(sku['points'])}积分；兑换现金{plain(sku['exchange_cash'])}元；用券额外现金{plain(sku['use_cash'])}元；总现金{plain(sku['total_cash'])}元。{plain(sku['points_status'])}，{sku['budget_status']}。{plain(sku['decision'])}")
            lines += ["购买须知（服务原文，按文本展示）：", "", plain(s.get("note")), ""]
    lines += ["## 本次关注项", ""]
    for a in report["attention"]:
        lines += [f"- **{a['priority']}**：{plain(a['title'])}。{plain(a['reason'])}"]
    if not report["attention"]:
        lines.append("没有需要确定推荐的行动。可查看上方概览或进一步核验。")
    if report["uncertainties"]:
        lines += ["", "## 尚需核实", "", *["- " + plain(x) for x in report["uncertainties"]]]
    return "\n".join(lines) + "\n"
