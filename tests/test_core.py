import copy
import io
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from rights import analyze, collect
from mcd_rights import main


class CoreDecisionTests(unittest.TestCase):
    def setUp(self):
        self.snapshot = json.loads((ROOT / "examples/synthetic.json").read_text(encoding="utf-8"))

    def sku(self, budget="7", plan="yes"):
        return analyze(self.snapshot, plan=plan, budget=budget)["sections"]["detail"]["skus"][0]

    def test_complete_points_and_cash_comparison(self):
        sku = self.sku()
        self.assertTrue(sku["can_consider"])
        self.assertEqual(sku["total_cash"], "7")
        self.assertEqual(sku["points_gap"], "0")
        self.assertIn("官方核验", sku["decision"])

    def test_enough_cash_does_not_override_insufficient_points(self):
        self.snapshot["sections"]["points"]["data"]["availablePoint"] = "400"
        sku = self.sku(budget="50")
        self.assertFalse(sku["can_consider"])
        self.assertEqual(sku["points_gap"], "100")
        self.assertIn("积分不足", sku["decision"])

    def test_both_cash_stages_count_towards_budget(self):
        self.snapshot["sections"]["detail"]["data"]["skuList"][0]["price"] = "2"
        sku = self.sku()
        self.assertEqual(sku["total_cash"], "9")
        self.assertFalse(sku["can_consider"])
        self.assertIn("超过", sku["decision"])

    def test_unknown_cost_or_account_balance_never_means_affordable(self):
        for field in ("points", "price", "extTradePrice"):
            with self.subTest(field=field):
                saved = copy.deepcopy(self.snapshot)
                self.snapshot["sections"]["detail"]["data"]["skuList"][0][field] = None
                self.assertFalse(self.sku()["can_consider"])
                self.snapshot = saved
        self.snapshot["sections"]["points"] = {"status": "error", "error": "积分查询失败"}
        self.assertIn("信息不足", self.sku()["points_status"])

    def test_no_plan_or_missing_budget_cannot_be_a_recommendation(self):
        self.assertFalse(self.sku(plan="no")["can_consider"])
        self.assertFalse(self.sku(plan="unknown")["can_consider"])
        self.assertFalse(self.sku(budget=None)["can_consider"])

    def test_missing_sku_id_cannot_be_selected(self):
        self.snapshot["sections"]["detail"]["data"]["skuList"][0]["skuId"] = None
        self.assertFalse(self.sku()["can_consider"])
        self.assertIn("标识缺失", self.sku()["decision"])

    def test_detail_and_listing_must_both_support_window(self):
        self.snapshot["sections"]["catalog"]["data"][1]["status"] = 3
        self.assertFalse(self.sku()["can_consider"])
        self.snapshot["sections"]["catalog"]["data"][1]["status"] = 2
        self.snapshot["sections"]["detail"]["data"]["downTime"] = None
        self.assertFalse(self.sku()["can_consider"])

    def test_coupon_ends_before_planned_visit_is_not_attention(self):
        report = analyze(self.snapshot, plan="yes", visit_date="2026-10-26")
        coupon = next(c for c in report["sections"]["coupons"]["items"] if c["title"] == "合成小食券")
        self.assertEqual(coupon["visit_fit"], "计划日期已过展示截止")
        self.assertFalse(any(a["kind"] == "coupon" for a in report["attention"]))

    def test_same_day_visit_does_not_claim_exact_time_eligibility(self):
        report = analyze(self.snapshot, plan="yes", visit_date="2026-10-25")
        coupon = report["sections"]["coupons"]["items"][0]
        self.assertEqual(coupon["visit_fit"], "计划当日时段需核实")
        self.assertEqual(coupon["order_usable"], "未核验")


class InteractiveTests(unittest.TestCase):
    def run_demo(self, answers):
        with patch("builtins.input", side_effect=answers), patch("sys.stdout", new_callable=io.StringIO) as out, patch("mcd_rights.McdClient") as client:
            result = main(["--demo", "--interactive"])
            client.assert_not_called()
            return result, out.getvalue()

    def test_demo_guided_flow_reaches_selected_sku_decision(self):
        result, text = self.run_demo(["3", "1", "2026-10-25", "y", "0", "103"])
        self.assertEqual(result, 0)
        self.assertIn("合成数据", text)
        self.assertIn("总现金7元", text)
        self.assertIn("超过总现金预算", text)

    def test_no_plan_ends_after_facts_without_extra_prompts(self):
        result, text = self.run_demo(["1", "2"])
        self.assertEqual(result, 0)
        self.assertIn("无需为了权益额外花钱", text)
        self.assertNotIn("请选择想核对的商品", text)

    def test_excluded_id_cannot_be_selected_in_guided_flow(self):
        result, text = self.run_demo(["3", "1", "", "y", "7", "101", "103"])
        self.assertEqual(result, 0)
        self.assertIn("请选择上面显示的商品ID", text)
        self.assertIn("积分足够且在总现金预算内", text)

    def test_user_cancel_has_no_network_and_no_stacktrace(self):
        with patch("builtins.input", side_effect=EOFError), patch("sys.stdout", new_callable=io.StringIO), patch("sys.stderr", new_callable=io.StringIO) as err, patch("mcd_rights.McdClient") as client:
            self.assertEqual(main(["--live", "--interactive"]), 130)
            client.assert_not_called()
            self.assertNotIn("Traceback", err.getvalue())

    def test_live_guided_flow_calls_detail_only_after_selection(self):
        fixture = json.loads((ROOT / "examples/synthetic.json").read_text(encoding="utf-8"))
        events = []

        class FakeClient:
            def __init__(self, token): pass
            def initialize(self): events.append("initialize")
            def call(self, name, arguments=None):
                events.append((name, arguments))
                if name == "now-time-info": return {"datetime": fixture["queried_at"]}
                if name == "query-my-account": return fixture["sections"]["points"]["data"]
                if name == "query-my-coupons": return {"coupons": [], "totalCount": 0, "totalPages": 0, "currentPage": 1}
                if name == "mall-points-products": return fixture["sections"]["catalog"]["data"]
                if name == "mall-product-detail": return fixture["sections"]["detail"]["data"]
                raise AssertionError(name)

        answers = iter(["3", "1", "", "y", "7", "103"])
        def answer(prompt):
            if "商品ID" in prompt:
                self.assertFalse(any(isinstance(e, tuple) and e[0] == "mall-product-detail" for e in events))
            return next(answers)

        with patch("mcd_rights.McdClient", FakeClient), patch("mcd_rights.load_token", return_value="synthetic-token"), patch("builtins.input", side_effect=answer), patch("sys.stdout", new_callable=io.StringIO) as out:
            self.assertEqual(main(["--live", "--interactive"]), 0)
        details = [e for e in events if isinstance(e, tuple) and e[0] == "mall-product-detail"]
        self.assertEqual(details, [("mall-product-detail", {"spuId": 103})])
        self.assertIn("积分足够且在总现金预算内", out.getvalue())

    def test_wrong_product_detail_is_rejected(self):
        class FakeClient:
            def call(self, name, arguments=None):
                if name == "now-time-info": return {"datetime": "2026-10-23T00:30:00+08:00"}
                if name == "query-my-account": return {"availablePoint": "1000"}
                if name == "mall-points-products": return [{"spuId": 103}]
                if name == "mall-product-detail": return {"spuId": 999, "skuList": []}
                raise AssertionError(name)
        self.assertEqual(collect(FakeClient(), focus="points", product_id=103)["sections"]["detail"]["status"], "error")


if __name__ == "__main__":
    unittest.main()
