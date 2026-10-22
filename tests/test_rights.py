import copy
import io
import json
import sys
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from rights import analyze, collect, fetch_coupons, markdown, number, temporal
from mcd_client import McdClient, McdError, validate_call
from mcd_rights import main


class RightsTests(unittest.TestCase):
    def setUp(self):
        self.snapshot = json.loads((ROOT / "examples/synthetic.json").read_text(encoding="utf-8"))

    def test_month_buckets_keep_actual_field_names_and_no_daily_countdown(self):
        r = analyze(self.snapshot)
        self.assertEqual(r["sections"]["points"]["values"]["currentMouthExpirePoint"], "500")
        self.assertNotIn("2026-10-31", json.dumps(r["sections"]["points"]))

    def test_missing_numbers_not_zero_and_decimal_precision(self):
        for bad in [None, "", "NaN", "Infinity", "-1", True, "abc"]:
            self.assertIsNone(number(bad))
        self.assertEqual(str(number("0.10") + number("0.20")), "0.30")
        self.snapshot["sections"]["points"]["data"]["currentMouthExpirePoint"] = None
        self.assertIsNone(analyze(self.snapshot)["sections"]["points"]["values"]["currentMouthExpirePoint"])

    def test_no_consumption_means_no_action_recommendations(self):
        r = analyze(self.snapshot, plan="no")
        self.assertEqual(r["attention"], [])
        self.assertEqual(r["sections"]["points"]["values"]["currentMouthExpirePoint"], "500")

    def test_expired_product_status_conflict_excluded(self):
        r = analyze(self.snapshot)
        self.assertEqual(r["sections"]["catalog"]["candidate_count"], 1)
        self.assertEqual(r["sections"]["catalog"]["items"][0]["id"], 103)

    def test_missing_or_exact_end_timestamp_not_confirmed(self):
        for end in [None, self.snapshot["queried_at"]]:
            s = copy.deepcopy(self.snapshot)
            s["sections"]["catalog"]["data"][1]["downTime"] = end
            self.assertEqual(analyze(s)["sections"]["catalog"]["candidate_count"], 0)

    def test_date_only_coupon_keeps_date_granularity(self):
        now = datetime.fromisoformat(self.snapshot["queried_at"])
        self.assertEqual(temporal("2026-10-22", now, True), "today")
        self.assertEqual(temporal("not-a-date", now), "unknown")

    def test_two_stage_cash_and_unknown_cash_are_not_zero(self):
        r = analyze(self.snapshot, budget="0")["sections"]["detail"]
        self.assertEqual(r["skus"][0]["exchange_cash"], "0")
        self.assertEqual(r["skus"][0]["use_cash"], "7")
        self.assertIn("超过", r["skus"][0]["budget_status"])
        self.assertIn("待核实", r["skus"][1]["budget_status"])

    def test_coupon_enable_is_not_order_eligibility(self):
        r = analyze(self.snapshot)
        self.assertTrue(all(c["order_usable"] == "未核验" for c in r["sections"]["coupons"]["items"]))

    def test_disabled_coupon_is_not_attention_candidate(self):
        self.snapshot["sections"]["coupons"]["data"][0]["enable"] = 0
        r = analyze(self.snapshot)
        self.assertFalse(any(a["kind"] == "coupon" for a in r["attention"]))

    def test_coupon_deadline_uses_beijing_date_for_visit(self):
        c = self.snapshot["sections"]["coupons"]["data"][0]
        c["tradeEndDate"] = "2026-10-24T20:00:00Z"
        r = analyze(self.snapshot, visit_date="2026-10-24")
        a = next(a for a in r["attention"] if a["kind"] == "coupon")
        self.assertEqual(a["priority"], "可以稍后看")
        self.assertTrue(a["sort_date"].startswith("2026-10-25"))

    def test_coupon_display_orders_real_times_across_timezones(self):
        original = self.snapshot["sections"]["coupons"]["data"][0]
        first, second = copy.deepcopy(original), copy.deepcopy(original)
        first.update(title="实际较晚", tradeEndDate="2026-10-24T20:00:00Z")
        second.update(title="实际较早", tradeEndDate="2026-10-25T01:00:00+08:00")
        self.snapshot["sections"]["coupons"]["data"] = [first, second]
        items = analyze(self.snapshot)["sections"]["coupons"]["items"]
        self.assertEqual([c["title"] for c in items], ["实际较早", "实际较晚"])

    def test_partial_success_is_not_empty_coupon(self):
        self.snapshot["sections"]["coupons"] = {"status": "error", "error": "券查询失败"}
        r = analyze(self.snapshot)
        self.assertEqual(r["sections"]["points"]["status"], "ok")
        self.assertEqual(r["sections"]["coupons"]["status"], "error")
        self.assertNotIn("0张", markdown(r))

    def test_rendered_remote_titles_cannot_become_html_or_markdown_links(self):
        self.snapshot["sections"]["coupons"]["data"][0]["title"] = '[执行](https://bad.example) <script>x</script>\n# 指令'
        out = markdown(analyze(self.snapshot), True)
        self.assertNotIn("<script>", out)
        self.assertNotIn("[执行](", out)
        self.assertIn("合成数据", out)

    def test_attention_has_at_most_three_and_provenance(self):
        r = analyze(self.snapshot)
        self.assertLessEqual(len(r["attention"]), 3)
        self.assertTrue(all(a["evidence"]["tool"] for a in r["attention"]))


def coupon_page(page=1, total=1, pages=1, ids=("a",)):
    return {"currentPage": page, "totalCount": total, "totalPages": pages, "pageSize": 200, "coupons": [{"id": k, "code": "PRIVATE-CODE", "title": "样例", "tradeEndDate": "2026-12-01"} for k in ids]}


class PaginationTests(unittest.TestCase):
    def test_empty_is_a_success_not_failure(self):
        r = fetch_coupons(lambda *args: coupon_page(total=0, pages=0, ids=()))
        self.assertEqual(r["status"], "empty")

    def test_five_page_limit_and_no_private_codes_in_output(self):
        calls = []
        def fetch(tool, args):
            calls.append(args)
            return coupon_page(int(args["page"]), total=6, pages=6, ids=(args["page"],))
        r = fetch_coupons(fetch)
        self.assertEqual(r["status"], "partial")
        self.assertEqual(len(calls), 5)
        self.assertNotIn("PRIVATE-CODE", json.dumps(r))

    def test_duplicate_pages_not_claimed_complete(self):
        r = fetch_coupons(lambda tool, args: coupon_page(int(args["page"]), 2, 2))
        self.assertEqual(r["status"], "partial")
        self.assertEqual(r["read_count"], 1)

    def test_midway_failure_preserves_partial_data(self):
        def fetch(tool, args):
            if args["page"] == "2": raise McdError("timeout")
            return coupon_page(1, 2, 2)
        r = fetch_coupons(fetch)
        self.assertEqual(r["status"], "partial")
        self.assertEqual(r["read_count"], 1)

    def test_missing_or_inconsistent_metadata_not_complete(self):
        for value in [{"coupons": []}, coupon_page(total=2, ids=("a",))]:
            self.assertEqual(fetch_coupons(lambda *args: value)["status"], "partial")

    def test_changing_page_total_is_partial(self):
        def fetch(tool, args):
            p = int(args["page"])
            return coupon_page(p, 2, 2 if p == 1 else 3, (str(p),))
        self.assertEqual(fetch_coupons(fetch)["status"], "partial")

    def test_boolean_metadata_is_rejected(self):
        value = coupon_page()
        value["currentPage"] = True
        self.assertEqual(fetch_coupons(lambda *args: value)["status"], "partial")


class ClientTests(unittest.TestCase):
    def test_empty_non_object_arguments_are_rejected_before_network(self):
        client = McdClient("test-only-token")
        with patch.object(client, "_rpc") as request:
            for args in [[], "", False, 0]:
                with self.assertRaises(McdError): client.call("query-my-account", args)
            request.assert_not_called()

    def test_contradictory_business_success_is_rejected(self):
        client = McdClient("test-only-token")
        for content in [{"success": True, "code": 500, "data": []}, {"success": 1, "code": 200, "data": []}]:
            with patch.object(client, "_rpc", return_value={"structuredContent": content}):
                with self.assertRaises(McdError): client.call("query-my-account")

    def test_malformed_text_content_gives_safe_error(self):
        client = McdClient("test-only-token")
        for content in [None, 7, [None, {}, {"text": None}]]:
            with patch.object(client, "_rpc", return_value={"content": content}):
                with self.assertRaises(McdError): client.call("query-my-account")

    def test_sse_can_skip_notification_and_read_multiline_result(self):
        response = io.BytesIO(b'data: {"jsonrpc":"2.0","method":"ping"}\n\ndata: {"jsonrpc":"2.0",\ndata: "id":1,"result":{"ok":true}}\n\n')
        response.headers = {"Content-Type": "text/event-stream", "Mcp-Session-Id": "synthetic-session"}
        client = McdClient("test-only-token")
        with patch.object(client._opener, "open", return_value=response):
            self.assertEqual(client._rpc("initialize"), {"ok": True})
        self.assertEqual(client._headers["Mcp-Session-Id"], "synthetic-session")

    def test_bad_sse_payload_and_wrong_json_id_give_safe_errors(self):
        for body, mime in [(b'data: []\n\n', 'text/event-stream'), (b'{"id":999,"result":{}}', 'application/json')]:
            response = io.BytesIO(body)
            response.headers = {"Content-Type": mime}
            client = McdClient("test-only-token")
            with patch.object(client._opener, "open", return_value=response):
                with self.assertRaises(McdError): client._rpc("initialize")

    def test_all_mutations_denied_before_network(self):
        client = McdClient("test-only-token")
        with patch.object(client, "_rpc") as request:
            for name in ["create-order", "cancel-order", "auto-bind-coupons", "mall-create-order", "draw-lottery", "tools/list"]:
                with self.assertRaises(McdError): client.call(name)
            request.assert_not_called()

    def test_argument_limits_and_unknown_fields_denied(self):
        for name, args in [("query-my-account", {"address": "x"}), ("query-my-coupons", {"page": "6"}), ("mall-points-products", {"catRuleIds": "2"}), ("mall-product-detail", {"spuId": True})]:
            with self.assertRaises(McdError): validate_call(name, args)

    def test_business_errors_are_not_successful_data(self):
        client = McdClient("test-only-token")
        for response in [{"isError": True}, {"structuredContent": {"success": False, "code": 200, "data": []}}, {"structuredContent": {"code": 401, "data": []}}]:
            with patch.object(client, "_rpc", return_value=response):
                with self.assertRaises(McdError): client.call("query-my-account")

    def test_wrong_response_structure_gives_safe_error(self):
        client = McdClient("test-only-token")
        with patch.object(client, "_rpc", return_value=[]):
            with self.assertRaises(McdError): client.initialize()
            with self.assertRaises(McdError): client.call("query-my-account")

    def test_structured_and_text_fallback_response(self):
        client = McdClient("test-only-token")
        payload = {"success": True, "code": 200, "data": {"availablePoint": "0"}}
        for response in [{"structuredContent": payload}, {"content": [{"type": "text", "text": "## Original Response\n\n" + json.dumps(payload)}]}]:
            with patch.object(client, "_rpc", return_value=response):
                self.assertEqual(client.call("query-my-account"), payload["data"])

    def test_details_only_for_selected_id_in_fresh_catalog(self):
        class Fake:
            def __init__(self): self.calls = []
            def call(self, name, args=None):
                self.calls.append(name)
                if name == "now-time-info": return {"datetime": "2026-10-22T20:35:00+08:00"}
                if name == "query-my-account": return {}
                if name == "mall-points-products": return [{"spuId": 100}]
                raise AssertionError("Unexpected call: " + name)
        client = Fake()
        r = collect(client, focus="points", product_id=999)
        self.assertEqual(r["sections"]["detail"]["status"], "error")
        self.assertNotIn("mall-product-detail", client.calls)
        self.assertNotIn("query-my-coupons", client.calls)

    def test_demo_is_offline(self):
        with patch("mcd_rights.McdClient") as client, patch("sys.stdout", new_callable=io.StringIO) as out:
            self.assertEqual(main(["--demo", "--catalog", "--product-id", "103", "--cash-budget", "0"]), 0)
            self.assertIn("合成数据", out.getvalue())
            client.assert_not_called()


if __name__ == "__main__":
    unittest.main()
