from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone as UTC
from decimal import Decimal as D
from io import StringIO
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import DatabaseError, close_old_connections, connection, transaction
from django.test import SimpleTestCase, TestCase, TransactionTestCase, override_settings
from rest_framework.test import APITestCase

from loyalty.models import AuditLog, Campaign, CustomerProfile, EarnRule, EarningRevision, PointsTransaction, ProgramConfig, Tier, TierHistory
from loyalty.services.calculation import EarnRules, OrderLine, OrderSnapshot, compute_earn
from loyalty.services.earning import earn_points_for_order
from loyalty.services.earning_admin import save_definition
from loyalty.services.revoking import revoke_points_for_refund, rules_from_earn
from loyalty.services.tiers import evaluate_all, evaluate_profile_tier, qualifying_points, window_start
from shops.models import Shop
from shops.tests.fixtures import raw_order, raw_refund
from shops.tests.test_http_auth import AUTH_SETTINGS, session_token

NOW = datetime(2026, 10, 13, 12, tzinfo=UTC.utc)


class FlexibleCalculationTests(SimpleTestCase):
    def setUp(self):
        self.order = OrderSnapshot(1, False, False, "PAID", False, 1, "web", [
            OrderLine(1, 101, [501], False, 1, D("15")), OrderLine(2, 102, [], False, 1, D("10"))], created_at=NOW)
        self.rule = dict(id=1, rule_type="PRODUCT", target_ids=[101], multiplier="2", starts_at=NOW, ends_at=NOW+timedelta(days=1), is_active=True)
        self.campaign = dict(id=1, multiplier="1.50", audience_tier_ids=[], min_eligible_spend=None, starts_at=NOW, ends_at=NOW+timedelta(days=1), is_active=True)

    def test_hand_calculated_stacking_table(self):
        collection = {**self.rule, "id": 2, "rule_type": "COLLECTION", "target_ids": [501], "multiplier": "3"}
        second = {**self.rule, "id": 3, "target_ids": [102], "multiplier": "3"}
        for line_rules, campaigns, tier, expected in [
            ([], [], "1", 2),                         # floor(25/10)
            ([self.rule], [], "1", 4),               # (15*2+10)/10
            ([self.rule, collection], [], "1", 5),  # highest line factor: (15*3+10)/10
            ([self.rule, second], [self.campaign], "1.25", 11), # floor(60/10*1.5*1.25)
            ([self.rule], [self.campaign], "1.25", 7), # floor(40/10*1.5*1.25)
            ([], [self.campaign, {**self.campaign, "id": 2, "multiplier": "2"}], "1.25", 6), # floor(25/10*2*1.25)
            ([], [], "1.25", 3),                      # floor(2.5*1.25), not floor(2.5)*1.25
        ]:
            with self.subTest(line_rules=line_rules, tier=tier, expected=expected):
                rules = EarnRules(D("10"), tier_multiplier=D(tier), candidate_rules=line_rules, candidate_campaigns=campaigns)
                self.assertEqual(compute_earn(self.order, rules).points, expected)

    def test_window_boundaries_audience_and_minimum(self):
        for at, audience, tier_id, minimum, expected in [
            (NOW, [], None, None, 3),
            (NOW-timedelta(microseconds=1), [], None, None, 2),
            (NOW+timedelta(days=1), [], None, None, 2),
            (NOW, [7], 7, "25", 3),
            (NOW, [7], 8, None, 2),
            (NOW, [], None, "25.01", 2),
        ]:
            with self.subTest(at=at, audience=audience):
                rules = EarnRules(D("10"), tier_id=tier_id, candidate_campaigns=[{**self.campaign, "audience_tier_ids": audience, "min_eligible_spend": minimum}])
                self.assertEqual(compute_earn(replace(self.order, created_at=at), rules).points, expected)

    def test_gift_card_proportional_and_shipping_neutral(self):
        rules = EarnRules(D("10"), candidate_rules=[self.rule], include_shipping=True)
        # Products weighted 40, shipping 5 -> 45; gift card 15 of 30 -> 22.5 effective -> 2 points.
        result = compute_earn(replace(self.order, shipping_after_discounts=D("5"), gift_card_paid=D("15")), rules)
        self.assertEqual(result.points, 2)
        self.assertEqual(D(result.breakdown["effective_spend"]), D("22.5"))

    def test_line_window_and_priority_never_override_higher_multiplier(self):
        rules = EarnRules(D("10"), candidate_rules=[self.rule, {**self.rule, "id":2, "multiplier":"1.50", "priority":100}])
        for at, expected in [(NOW-timedelta(microseconds=1),2),(NOW,4),(NOW+timedelta(days=1),2)]:
            with self.subTest(at=at):
                self.assertEqual(compute_earn(replace(self.order,created_at=at),rules).points,expected)

    def test_order_mapping_normalizes_created_time_to_utc(self):
        from shops.shopify import map_order_snapshot
        raw=raw_order()
        raw["createdAt"]="2026-10-13T19:00:00+07:00"
        self.assertEqual(map_order_snapshot(raw).created_at,NOW)

    def test_campaign_minimum_uses_unweighted_eligible_spend(self):
        rules=EarnRules(D("10"),candidate_rules=[self.rule],candidate_campaigns=[{**self.campaign,"min_eligible_spend":"35"}])
        self.assertEqual(compute_earn(self.order,rules).points,4) # eligible 25 < 35 although weighted spend is 40

    def test_proportional_thirds_do_not_lose_a_point_before_final_floor(self):
        order=replace(self.order,lines=[replace(self.order.lines[0],total_after_discounts=D("1")),replace(self.order.lines[1],total_after_discounts=D("2"))],gift_card_paid=D("2"))
        rules=EarnRules(D("1"),candidate_rules=[self.rule,{**self.rule,"id":2,"target_ids":[102],"multiplier":"4"}],candidate_campaigns=[{**self.campaign,"multiplier":"3"}])
        # Weighted spend 1*2+2*4=10; paid by eligible cash = 1/3; campaign 3 -> exactly 10.
        self.assertEqual(compute_earn(order,rules).points,10)

    def test_decimal_validation_and_old_neutral_snapshot(self):
        for value in [1.25, True, D("0"), D("100.01"), D("1.001"), D("NaN")]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                compute_earn(self.order, EarnRules(D("10"), tier_multiplier=value))
        old = type("Entry", (), {"rule_snapshot": {"rules": {"spend_per_point": "10", "include_tax": False,
            "include_shipping": False, "excluded_product_ids": [], "excluded_collection_ids": [], "excluded_channels": []}}})()
        self.assertEqual(compute_earn(self.order, rules_from_earn(old)).points, 2)


class RuleSnapshotTests(TestCase):
    def setUp(self):
        self.shop = Shop.objects.create(domain="test.myshopify.com", currency="VND")
        ProgramConfig.objects.create(shop=self.shop, version=1, effective_from=NOW-timedelta(days=1), tiers_enabled=True)
        self.tier = Tier.objects.create(shop=self.shop, name="Silver", rank=1, min_qualifying_points=0, multiplier=D("1.25"))
        self.profile = CustomerProfile.objects.create(shop=self.shop, shopify_customer_id=301, tier=self.tier)
        with patch("django.utils.timezone.now", return_value=NOW):
            self.rule = EarnRule.objects.create(shop=self.shop, name="Product", rule_type="PRODUCT", target_ids=[201], multiplier=2, starts_at=NOW-timedelta(days=1))
            self.campaign = Campaign.objects.create(shop=self.shop, name="Campaign", multiplier=D("1.50"), min_eligible_spend=D("100000"), starts_at=NOW-timedelta(days=1))
        self.raw = raw_order()
        self.raw["createdAt"] = (NOW+timedelta(minutes=1)).isoformat()

    def earn(self):
        with patch("loyalty.services.earning.fetch_order", return_value=deepcopy(self.raw)), patch("django.utils.timezone.now", return_value=NOW+timedelta(hours=1)):
            return earn_points_for_order(self.shop, 1001)

    def test_edit_and_deactivate_never_cause_phantom_revokes(self):
        self.assertEqual(self.earn().points, 37)
        with patch("django.utils.timezone.now", return_value=NOW+timedelta(days=1)):
            self.rule.multiplier = 100
            self.rule.is_active = False
            self.rule.save()
            self.campaign.multiplier = 100
            self.campaign.is_active = False
            self.campaign.save()
        self.tier.multiplier = 100
        self.tier.save()
        self.raw["displayFinancialStatus"] = "PARTIALLY_REFUNDED"
        self.raw["refunds"] = [raw_refund(501, subtotal="20000")]
        with patch("loyalty.services.revoking.fetch_order", return_value=deepcopy(self.raw)):
            result = revoke_points_for_refund(self.shop, 1001, 501)
        self.assertEqual(result.points, 7)  # 37 original - floor(80000/10000*2*1.5*1.25)=30
        self.assertEqual(PointsTransaction.objects.get(type="REVOKE").rule_snapshot["target"], 30)

    def test_delayed_order_uses_revision_from_created_time_and_on_commit_task(self):
        with patch("django.utils.timezone.now", return_value=NOW+timedelta(minutes=2)):
            self.rule.multiplier = 3
            self.rule.save()
        with patch("django_q.tasks.async_task") as queued, self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(self.earn().points, 37)
            queued.assert_not_called()
        queued.assert_called_once_with("loyalty.tasks.evaluate_profile_tier", self.profile.pk)
        saved = PointsTransaction.objects.get(type="EARN").rule_snapshot["rules"]
        self.assertEqual(saved["line_multipliers"]["101"]["rule_id"], self.rule.pk)
        self.assertEqual(saved["campaign_id"], self.campaign.pk)
        self.assertEqual(saved["tier_id"], self.tier.pk)

    def test_history_and_definitions_cannot_be_deleted(self):
        with self.assertRaises(ValidationError):
            self.rule.delete()
        with self.assertRaises(ValidationError):
            EarningRevision.objects.all().delete()
        with self.assertRaises(DatabaseError), transaction.atomic(), connection.cursor() as cursor:
            cursor.execute("DELETE FROM loyalty_earnrule WHERE id=%s", [self.rule.pk])

    def test_deactivation_changes_future_orders_only(self):
        with patch("django.utils.timezone.now",return_value=NOW+timedelta(days=1)):
            self.rule.is_active=False
            self.rule.save()
            self.campaign.is_active=False
            self.campaign.save()
        self.assertEqual(self.earn().points,37) # old order created before deactivation
        raw=raw_order(1002)
        raw["createdAt"]=(NOW+timedelta(days=1,minutes=1)).isoformat()
        with patch("loyalty.services.earning.fetch_order",return_value=raw),patch("django.utils.timezone.now",return_value=NOW+timedelta(days=2)):
            self.assertEqual(earn_points_for_order(self.shop,1002).points,12) # floor(10*1.25), no rule/campaign


class TierTests(TestCase):
    def setUp(self):
        self.shop = Shop.objects.create(domain="tier.myshopify.com", currency="USD")
        self.profile = CustomerProfile.objects.create(shop=self.shop, shopify_customer_id=301)
        self.config = ProgramConfig.objects.create(shop=self.shop, version=1, effective_from=NOW-timedelta(days=100),
            tiers_enabled=True, tier_window_months=0, tier_downgrade_grace_days=3)
        self.base = Tier.objects.create(shop=self.shop, name="Base", rank=0, min_qualifying_points=0)
        self.gold = Tier.objects.create(shop=self.shop, name="Gold", rank=1, min_qualifying_points=100, multiplier=D("1.50"))

    def entry(self, delta, at=NOW, kind="EARN", order=1001):
        with patch("django.utils.timezone.now", return_value=at):
            return PointsTransaction.objects.create(profile=self.profile, type=kind, delta=delta, balance_after=0,
                order_id=order, idempotency_key=f"tier-test:{PointsTransaction.objects.count()}")

    def test_net_metric_excludes_nonorders_adjustments_and_future_rows(self):
        self.entry(120)
        self.entry(-30, kind="REVOKE")
        self.entry(500, kind="ADJUST")
        self.entry(500, order=None)
        self.entry(500, NOW+timedelta(days=1))
        self.assertEqual(qualifying_points(self.profile, NOW), 90)
        self.entry(-200, kind="REVOKE")
        self.assertEqual(qualifying_points(self.profile, NOW), 0)

    def test_month_window_exact_boundary_and_calendar_clamp(self):
        edge = window_start(NOW, 1)
        self.entry(20, edge-timedelta(microseconds=1))
        self.entry(30, edge)
        self.assertEqual(qualifying_points(self.profile, NOW, 1), 30)
        self.assertEqual(window_start(datetime(2024,3,31,tzinfo=UTC.utc),1).day,29)

    def test_upgrade_then_daily_downgrade_after_latest_drop_grace(self):
        self.entry(120)
        self.assertTrue(evaluate_profile_tier(self.profile, NOW)["changed"])
        self.entry(-30, NOW+timedelta(days=1), "REVOKE")
        self.assertFalse(evaluate_profile_tier(self.profile, NOW+timedelta(days=1))["changed"])
        self.entry(-10, NOW+timedelta(days=2), "REVOKE")
        evaluate_profile_tier(self.profile, NOW+timedelta(days=2))
        self.assertFalse(evaluate_profile_tier(self.profile, NOW+timedelta(days=4), allow_downgrade=True)["changed"])
        self.assertFalse(evaluate_profile_tier(self.profile, NOW+timedelta(days=5))["changed"])
        self.assertTrue(evaluate_profile_tier(self.profile, NOW+timedelta(days=5), allow_downgrade=True)["changed"])
        self.assertEqual(TierHistory.objects.count(), 2)
        self.assertEqual(AuditLog.objects.filter(action="tier.change").count(), 2)
        self.assertEqual(PointsTransaction.objects.count(), 3)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.tier_id, self.base.pk)

    def test_as_of_dry_run_is_read_only_and_tier_history_database_immutable(self):
        self.entry(120)
        output = StringIO()
        call_command("evaluate_tiers", as_of=NOW.isoformat(), dry_run=True, stdout=output)
        self.assertEqual(TierHistory.objects.count(),0)
        self.profile.refresh_from_db()
        self.assertIsNone(self.profile.tier_evaluated_at)
        call_command("evaluate_tiers", as_of=NOW.isoformat(), stdout=StringIO())
        with self.assertRaises(DatabaseError), transaction.atomic(), connection.cursor() as cursor:
            cursor.execute("UPDATE loyalty_tierhistory SET reason='bad'")

    def test_rolling_expiry_starts_grace_without_new_webhooks_and_daily_paginates(self):
        ProgramConfig.objects.create(shop=self.shop,version=2,effective_from=NOW-timedelta(days=1),tiers_enabled=True,
            tier_window_months=1,tier_downgrade_grace_days=2)
        self.entry(120,NOW-timedelta(days=20))
        evaluate_profile_tier(self.profile,NOW)
        after=NOW+timedelta(days=20)
        self.assertFalse(evaluate_all(after)["results"][0]["changed"])
        self.assertTrue(evaluate_all(after+timedelta(days=2))["results"][0]["changed"])
        CustomerProfile.objects.create(shop=self.shop,shopify_customer_id=302)
        batch=evaluate_all(after+timedelta(days=3),limit=1)
        self.assertTrue(batch["truncated"])
        self.assertEqual(evaluate_all(after+timedelta(days=3),limit=1,after_id=batch["next_after_id"])["scanned"],1)

    def test_tier_proxy_and_integrity_warning_are_read_only(self):
        from loyalty.services.reporting import proxy_me
        from loyalty.services.integrity import check_profile_warnings
        self.entry(120)
        self.assertIn("tier_rank_mismatch",check_profile_warnings(self.profile,NOW))
        evaluate_profile_tier(self.profile,NOW)
        self.profile.refresh_from_db()
        with patch("django.utils.timezone.now",return_value=NOW):
            summary=proxy_me(self.shop,301)
        self.assertEqual(summary["tier"]["name"],"Gold")
        self.assertEqual(summary["tier"]["multiplier"],"1.50")
        self.assertEqual(TierHistory.objects.count(),1)
        self.assertNotIn("tier_rank_mismatch",check_profile_warnings(self.profile,NOW))


@override_settings(**AUTH_SETTINGS)
class DefinitionAPITests(APITestCase):
    def setUp(self):
        self.shop = Shop.objects.create(domain="test.myshopify.com")
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {session_token()}")

    def test_crud_audit_scoping_validation_and_no_delete(self):
        for path, body in [("earn-rules", {"name":"Product", "rule_type":"PRODUCT", "target_ids":[201], "multiplier":"2.25", "starts_at":NOW.isoformat()}),
            ("campaigns", {"name":"Campaign", "multiplier":"1.50", "starts_at":NOW.isoformat()}),
            ("tiers", {"name":"Silver", "rank":1, "min_qualifying_points":100, "multiplier":"1.25"})]:
            with self.subTest(path=path):
                response = self.client.post(f"/api/admin/{path}", body, format="json")
                self.assertEqual(response.status_code,201,response.data)
                pk=response.data["id"]
                self.assertEqual(self.client.get(f"/api/admin/{path}").data["count"],1)
                self.assertEqual(self.client.patch(f"/api/admin/{path}/{pk}", {"multiplier":"0"}, format="json").status_code,400)
                self.assertEqual(self.client.delete(f"/api/admin/{path}/{pk}").status_code,405)
                self.assertEqual(self.client.post(f"/api/admin/{path}/{pk}/deactivate", {}, format="json").status_code,200)
        self.assertEqual(AuditLog.objects.count(),6)
        bad=self.client.post("/api/admin/tiers", {"name":"Bad", "rank":2, "min_qualifying_points":50, "multiplier":"1.50"},format="json")
        # The first tier was deactivated; activate it before validating rank order.
        self.assertEqual(bad.status_code,201)
        tier=Tier.objects.get(name="Silver")
        self.assertEqual(self.client.post(f"/api/admin/tiers/{tier.pk}/activate",{},format="json").status_code,422)
        self.assertEqual(self.client.post("/api/admin/campaigns", {"name":"Bad audience", "multiplier":"2", "starts_at":NOW.isoformat(), "audience_tier_ids":[999]},format="json").status_code,422)


class TierConcurrencyTests(TransactionTestCase):
    def test_two_earns_then_concurrent_evaluation_produce_one_upgrade(self):
        shop=Shop.objects.create(domain="concurrent.myshopify.com", currency="VND")
        ProgramConfig.objects.create(shop=shop,version=1,effective_from=NOW-timedelta(days=1),tiers_enabled=True)
        profile=CustomerProfile.objects.create(shop=shop,shopify_customer_id=301)
        Tier.objects.create(shop=shop,name="Gold",rank=1,min_qualifying_points=20,multiplier=D("1.25"))
        def earn(order_id):
            close_old_connections()
            try:
                return earn_points_for_order(Shop.objects.get(pk=shop.pk),order_id).points
            finally:close_old_connections()
        def evaluate(_):
            close_old_connections()
            try:return evaluate_profile_tier(CustomerProfile.objects.get(pk=profile.pk),NOW+timedelta(days=1))
            finally:close_old_connections()
        with patch("loyalty.services.earning.fetch_order",side_effect=lambda order_id: raw_order(order_id)), patch("django_q.tasks.async_task") as queued:
            with ThreadPoolExecutor(max_workers=2) as pool:self.assertEqual(sorted(pool.map(earn,[1001,1002])),[10,10])
            self.assertEqual(queued.call_count,2)
        with ThreadPoolExecutor(max_workers=2) as pool:list(pool.map(evaluate,[1,2]))
        self.assertEqual(TierHistory.objects.count(),1)
