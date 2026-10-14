import csv
from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from django.core import mail
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from loyalty.models import (AuditLog, CustomerProfile, NotificationLog, NotificationSettings,
    NotificationExpiryLot, PointsLot, PointsTransaction, Redemption, Tier, TierHistory)
from loyalty.services import notifications as notices
from loyalty.services.reports import report, safe_cell
from shops.tests.test_http_auth import AUTH_SETTINGS, session_token
from .lifecycle_fixtures import PointsSetup

LOCMEM = {"default": {"BACKEND": "django.core.mail.backends.locmem.EmailBackend"}}


@override_settings(**AUTH_SETTINGS)
class ReportTests(PointsSetup, APITestCase):
    def setUp(self):
        super().setUp()
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {session_token()}")
        self.params = {"from": self.now.date().isoformat(), "to": self.now.date().isoformat()}
        self.lot(100, expires=self.now+timedelta(days=5))
        self.lot(30, "PENDING", expires=self.now+timedelta(days=25))
        for kind, delta in [("REVOKE", -10), ("REDEEM", -20), ("REDEEM_REFUND", 5), ("EXPIRE", -3), ("ADJUST", 7)]:
            PointsTransaction.objects.create(profile=self.profile, type=kind, delta=delta, balance_after=0, idempotency_key=f"report:{kind}")

    def get_report(self, kind, **params):
        return self.client.get(f"/api/admin/reports/{kind}", self.params | params)

    def test_seeded_ledger_numbers_and_grouping(self):
        expected = {"earned":130,"revoked":10,"redeemed":15,"expired":3,"adjusted":7,"net":109}
        for group in ("day", "week", "month"):
            result = self.get_report("points-summary", group_by=group)
            self.assertEqual(result.status_code, 200)
            self.assertEqual(result.data["totals"], expected)
            self.assertEqual({k: v for k, v in result.data["rows"][0].items() if k != "period"}, expected)
        with self.assertNumQueries(2):
            report(self.shop, "points-summary", self.params)

    def test_current_liability_and_tiers(self):
        self.lot(40,expires=self.now+timedelta(days=45))
        self.lot(50,expires=self.now+timedelta(days=85))
        CustomerProfile.objects.create(shop=self.shop, shopify_customer_id=302, available_balance=-9)
        result = self.get_report("liability").data
        self.assertEqual(result, {"available":190,"pending":30,"outstanding":220,"debt":9,"expiring_7":100,"expiring_30":130,"expiring_60":170})
        tier = Tier.objects.create(shop=self.shop, name="Silver", rank=1, min_qualifying_points=10)
        self.profile.tier = tier
        self.profile.save(update_fields=["tier"])
        rows = self.get_report("tier-distribution").data
        self.assertTrue(rows["exists"])
        self.assertEqual(sum(row["count"] for row in rows["rows"]), 2)

    def test_redemptions_and_top_customers(self):
        for i, status in enumerate(["ISSUED", "USED", "EXPIRED", "FAILED"]):
            Redemption.objects.create(profile=self.profile, reward=self.reward, points_spent=10, status=status,
                shopify_discount_id=f"gid:{i}" if status != "FAILED" else "", idempotency_key=f"report-red:{i}")
        result = self.get_report("redemptions").data
        self.assertEqual((result["issued"], result["used"], result["conversion_rate"]), (3, 1, 1/3))
        self.assertEqual(sum(row["count"] for row in result["rows"]), 4)
        for period in ("lifetime", "period"):
            self.assertEqual(self.get_report("top-customers").data[period][0]["points"], 130)

    def test_week_month_boundaries_and_lifetime_vs_period(self):
        from datetime import datetime, timezone as utc
        for day,points in [("2026-09-28",2),("2026-10-04",4),("2026-10-05",3)]:
            with patch("django.utils.timezone.now",return_value=datetime.fromisoformat(day).replace(tzinfo=utc.utc)):
                PointsTransaction.objects.create(profile=self.profile,type="EARN",delta=points,
                    balance_after=0,idempotency_key=f"boundary:{day}")
        params={"from":"2026-09-28","to":"2026-10-05"}
        weekly=self.get_report("points-summary",**params,group_by="week").data["rows"]
        monthly=self.get_report("points-summary",**params,group_by="month").data["rows"]
        self.assertEqual([row["earned"] for row in weekly],[6,3])
        self.assertEqual([row["earned"] for row in monthly],[2,7])
        top=self.get_report("top-customers",**params).data
        self.assertEqual(top["lifetime"][0]["points"],139)
        self.assertEqual(top["period"][0]["points"],9)
        for kind,queries in [("liability",2),("redemptions",2),("top-customers",2),("tier-distribution",2)]:
            with self.assertNumQueries(queries):
                report(self.shop,kind,params)

    def test_ranges_invalid_and_exclusive_end(self):
        for params in [{"from":"bad"}, {"from":"2026-11-01","to":"2026-10-01"}, {"from":"2020-01-01","to":"2026-10-01"}, {"group_by":"year"}]:
            self.assertEqual(self.get_report("points-summary", **params).status_code, 422)
        yesterday=(self.now-timedelta(days=1)).date().isoformat()
        self.assertEqual(self.get_report("points-summary", **{"from":yesterday,"to":yesterday}).data["totals"]["net"], 0)

    def export(self, kind, **params):
        return self.client.get(f"/api/admin/reports/export/{kind}", self.params | params)

    def test_streaming_bom_default_pii_exclusion_and_audit(self):
        self.profile.email="private@example.com"
        self.profile.first_name="=HYPERLINK(1)"
        self.profile.save()
        for kind in ("customers", "ledger", "lots", "redemptions"):
            response=self.export(kind)
            self.assertTrue(response.streaming)
            body=b"".join(response.streaming_content)
            self.assertTrue(body.startswith(b"\xef\xbb\xbf"))
            text=body.decode("utf-8-sig")
            self.assertNotIn("email", next(csv.reader(StringIO(text)) ))
            self.assertNotIn("private@example.com",text)
            audit=AuditLog.objects.filter(action="report.export",target_id=kind).latest("pk")
            self.assertFalse(audit.after["include_pii"])
            self.assertEqual(audit.after["row_count"],len(list(csv.reader(StringIO(text))))-1)

    def test_pii_inclusion_formula_protection_and_row_cap(self):
        self.profile.email="private@example.com"
        self.profile.first_name="=HYPERLINK(1)"
        self.profile.save()
        response=self.export("customers",include_pii="1")
        text=b"".join(response.streaming_content).decode("utf-8-sig")
        self.assertIn("private@example.com",text)
        self.assertIn("'=HYPERLINK(1)",text)
        self.assertTrue(AuditLog.objects.latest("pk").after["include_pii"])
        for value in ["=1", "+1", "-1", "@SUM(1)", "\t1", "\r1"]:
            self.assertEqual(safe_cell(value), "'"+value)
        with override_settings(LOYALTY_EXPORT_MAX_ROWS=1):
            result=self.export("ledger")
        self.assertEqual((result.status_code,result.data["code"]),(422,"EXPORT_TOO_LARGE"))

    def test_pii_projection_for_each_related_export(self):
        self.profile.email="private@example.com";self.profile.first_name="Nguyễn Hùng";self.profile.save()
        Redemption.objects.create(profile=self.profile,reward=self.reward,points_spent=10,idempotency_key="csv-related")
        for kind in ("ledger","lots","redemptions"):
            text=b"".join(self.export(kind,include_pii="1").streaming_content).decode("utf-8-sig")
            self.assertIn("private@example.com",text)
            self.assertIn("Nguyễn Hùng",text)
            self.assertTrue(AuditLog.objects.filter(target_id=kind).latest("pk").after["include_pii"])

    def test_export_file_remains_consistent_after_profile_changes(self):
        self.profile.first_name="Before"
        self.profile.save()
        response=self.export("customers",include_pii="1")
        self.profile.first_name="After"
        self.profile.save()
        text=b"".join(response.streaming_content).decode("utf-8-sig")
        self.assertIn("Before",text)
        self.assertNotIn("After",text)
        self.assertEqual(AuditLog.objects.latest("pk").after["row_count"],1)
        self.assertTrue(response.closed)

    def test_cap_is_rechecked_before_headers_if_filters_change(self):
        from django.db.models.query import QuerySet
        original=QuerySet.iterator
        def change_then_iterate(query, *args, **kwargs):
            if query.model is PointsLot:
                PointsLot.objects.filter(status="AVAILABLE").update(status="PENDING")
            return original(query, *args, **kwargs)
        with override_settings(LOYALTY_EXPORT_MAX_ROWS=1), patch.object(QuerySet,"iterator",change_then_iterate):
            result=self.export("lots",status="PENDING")
        self.assertEqual((result.status_code,result.data["code"]),(422,"EXPORT_TOO_LARGE"))
        self.assertFalse(AuditLog.objects.filter(action="report.export").exists())

    def test_export_filters_tenant_isolation_and_auth(self):
        from shops.models import Shop
        foreign=Shop.objects.create(domain="other.myshopify.com")
        CustomerProfile.objects.create(shop=foreign,shopify_customer_id=999,email="foreign@example.com")
        text=b"".join(self.export("customers",include_pii="1").streaming_content).decode("utf-8-sig")
        self.assertNotIn("foreign@example.com",text)
        self.assertEqual(self.export("lots",status="INVALID").status_code,422)
        self.assertEqual(self.export("lots",profile_id="invalid").status_code,422)
        self.assertEqual(self.export("lots",include_pii="yes").status_code,422)
        self.client.credentials()
        self.assertEqual(self.export("customers").status_code,401)


@override_settings(MAILERS=LOCMEM, **AUTH_SETTINGS)
class NotificationTests(PointsSetup, APITestCase):
    def setUp(self):
        super().setUp()
        self.profile.email="private@example.com"
        self.profile.email_marketing_opt_in=True
        self.profile.save()
        self.settings=NotificationSettings.objects.create(shop=self.shop,enabled_types=list(NotificationLog.Type.values))
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {session_token()}")
        self.enqueue=patch("loyalty.services.notifications.async_task").start()

    def entry(self, kind="POINTS_EARNED", suffix=None, **refs):
        if kind in ("POINTS_EARNED","POINTS_AVAILABLE") and not refs:
            refs={"lot":self.lot(10)}
        return notices.notify(self.profile,kind,suffix or str(NotificationLog.objects.count()),**refs)

    def test_event_deduplication_and_on_commit_rollback(self):
        lot=self.lot(10)
        with self.captureOnCommitCallbacks(execute=True):
            one=notices.notify(self.profile,"POINTS_EARNED",lot.pk,lot=lot)
            two=notices.notify(self.profile,"POINTS_EARNED",lot.pk,lot=lot)
        self.assertEqual(one.pk,two.pk)
        self.enqueue.assert_called_once_with("loyalty.tasks.send_notification",one.pk)
        self.assertEqual(notices.send_notification(one.pk),"SENT")
        self.assertEqual(notices.send_notification(one.pk),"NOOP")
        self.assertEqual(len(mail.outbox),1)
        from django.db import transaction
        with self.captureOnCommitCallbacks(execute=True):
            try:
                with transaction.atomic():
                    notices.notify(self.profile,"POINTS_EARNED","rolled-back",lot=lot)
                    raise ValueError()
            except ValueError:
                pass
        self.assertEqual(NotificationLog.objects.count(),1)

    def test_consent_matrix_all_types(self):
        for kind in NotificationLog.Type.values:
            for opt_in,opt_out in [(False,False),(True,False),(True,True)]:
                self.profile.email_marketing_opt_in=opt_in
                self.profile.email_opt_out=opt_out
                expected="" if kind in notices.TRANSACTIONAL or (opt_in and not opt_out) else "OPTED_OUT" if opt_out else "NO_MARKETING_CONSENT"
                self.assertEqual(notices.skip_reason(self.profile,kind,self.settings),expected)

    def test_skip_blocked_anonymized_missing_email_disabled(self):
        for field,value,reason in [("is_blocked",True,"BLOCKED"),("is_anonymized",True,"ANONYMIZED"),("email","","NO_EMAIL")]:
            original=getattr(self.profile,field)
            setattr(self.profile,field,value);self.profile.save()
            entry=self.entry()
            self.assertEqual(notices.send_notification(entry.pk),"SKIPPED")
            entry.refresh_from_db();self.assertEqual(entry.skip_reason,reason)
            setattr(self.profile,field,original);self.profile.save()
        self.settings.enabled_types=[];self.settings.save()
        entry=self.entry();notices.send_notification(entry.pk);entry.refresh_from_db()
        self.assertEqual(entry.skip_reason,"DISABLED")

    def test_retry_backoff_max_attempts_and_no_private_logs(self):
        entry=self.entry()
        secret="private@example.com SECRET-CODE"
        with patch("loyalty.services.notifications.EmailMultiAlternatives.send",side_effect=RuntimeError(secret)), self.assertLogs("loyalty.services.notifications",level="WARNING") as logs:
            notices.send_notification(entry.pk)
        self.assertNotIn(secret,str(logs.output))
        entry.refresh_from_db()
        self.assertEqual((entry.status,entry.attempts,entry.last_error),("FAILED",1,"RuntimeError"))
        self.assertGreater(entry.next_attempt_at,timezone.now())
        self.assertEqual(notices.send_notification(entry.pk),"NOOP")
        NotificationLog.objects.filter(pk=entry.pk).update(next_attempt_at=self.now-timedelta(seconds=1))
        notices.sweep_notifications()
        self.enqueue.assert_called_once()
        self.assertEqual(notices.send_notification(entry.pk),"SENT")
        NotificationLog.objects.filter(pk=entry.pk).update(status="FAILED",attempts=5)
        self.assertEqual(notices.sweep_notifications(),0)
        self.assertEqual(notices.send_notification(entry.pk),"NOOP")

    def test_queue_failure_recovered(self):
        entry=self.entry()
        self.enqueue.side_effect=RuntimeError("private@example.com SECRET")
        with self.assertLogs("loyalty.services.notifications",level="WARNING") as logs:
            notices.queue_task(entry.pk)
        self.assertNotIn("private@example.com",str(logs.output))
        entry.refresh_from_db();self.assertEqual(entry.status,"QUEUED")
        self.enqueue.side_effect=None
        notices.sweep_notifications();self.assertEqual(notices.send_notification(entry.pk),"SENT")

    def test_unsubscribe_signature_tampering_idempotency_and_no_pii(self):
        token=notices.unsubscribe_token(self.profile.pk)
        self.assertNotIn(self.profile.email,token)
        self.assertEqual(self.client.get(f"/unsubscribe/{token}/").status_code,200)
        self.assertEqual(self.client.post(f"/unsubscribe/{token}/").status_code,200)
        self.assertEqual(self.client.get(f"/unsubscribe/{token}bad/").status_code,400)
        self.profile.refresh_from_db();self.assertTrue(self.profile.email_opt_out)

    def test_expiring_summary_dedupe_per_lot_threshold_and_unsubscribe_headers(self):
        first=self.lot(10,expires=self.now+timedelta(days=2))
        second=self.lot(20,"PENDING",expires=self.now+timedelta(days=3))
        self.lot(90,expires=self.now+timedelta(days=8))
        self.assertEqual(notices.expiring_notifications(self.now),1)
        self.assertEqual(notices.expiring_notifications(self.now),0)
        entry=NotificationLog.objects.get(type="POINTS_EXPIRING")
        self.assertEqual(NotificationExpiryLot.objects.filter(notification=entry).count(),2)
        self.assertEqual(notices.send_notification(entry.pk),"SENT")
        self.assertIn("30 points",mail.outbox[0].body)
        self.assertIn("List-Unsubscribe",mail.outbox[0].extra_headers)
        self.config_version(expiry_reminder_days=4)
        self.assertEqual(notices.expiring_notifications(self.now),1)

    def test_daily_reminder_batches_continue_with_a_cursor(self):
        from loyalty.tasks import expiring_notifications
        for customer_id in (302,303):
            profile=CustomerProfile.objects.create(shop=self.shop,shopify_customer_id=customer_id)
            self.lot(2,profile=profile,expires=self.now+timedelta(days=2))
        self.lot(2,expires=self.now+timedelta(days=2))
        with patch("django_q.tasks.async_task") as enqueue:
            first=expiring_notifications(self.now.isoformat(),batch_size=2)
            self.assertEqual(first,{"profiles":2,"queued":2,"has_more":True})
            arguments=enqueue.call_args.args
            self.assertEqual(arguments[0],"loyalty.tasks.expiring_notifications")
            last=expiring_notifications(*arguments[1:])
            self.assertEqual(last,{"profiles":1,"queued":1,"has_more":False})
        self.assertEqual(NotificationLog.objects.filter(type="POINTS_EXPIRING").count(),3)
        self.assertEqual(notices.expiring_notifications(self.now),0)

    def test_templates_en_vi_each_type_escaped_and_no_missing_variables(self):
        from django.template import engines
        engine=engines["django"].engine
        previous=engine.string_if_invalid
        engine.string_if_invalid="MISSING:%s"
        try:
            for language in ("en","vi"):
                self.profile.locale=language
                for kind in NotificationLog.Type.values:
                    context={"points":10,"code":"<CODE>","reward":"<script>unsafe</script>","tier":"Gold","lots":[self.lot(3,expires=self.now+timedelta(days=2))],"expires_at":self.now,"available_at":self.now,"grace_deadline":self.now+timedelta(days=30)}
                    message=notices.message(kind,self.settings,self.profile,context,self.profile.email)
                    html=message.alternatives[0].content
                    self.assertNotIn("MISSING",html+message.body+message.subject)
                    self.assertNotIn("<script>",html)
                    self.assertEqual("List-Unsubscribe" in message.extra_headers,kind not in notices.TRANSACTIONAL)
        finally:
            engine.string_if_invalid=previous

    def test_sync_consent_preserves_absent_and_local_unsubscribe(self):
        from webhooks.handlers import sync_customer
        from types import SimpleNamespace
        self.profile.email_opt_out=True;self.profile.save()
        for payload,expected in [({"email_marketing_consent":{"state":"unsubscribed"}},False),({},False),({"email_marketing_consent":{"state":"subscribed"},"locale":"vi"},True)]:
            sync_customer(SimpleNamespace(shop=self.shop,shop_id=self.shop.pk,payload={"id":301,**payload}))
            self.profile.refresh_from_db()
            self.assertEqual(self.profile.email_marketing_opt_in,expected)
            self.assertTrue(self.profile.email_opt_out)
        self.assertEqual(self.profile.locale,"vi")

    def test_consent_is_checked_again_at_delivery(self):
        entry=self.entry("POINTS_AVAILABLE")
        CustomerProfile.objects.filter(pk=self.profile.pk).update(email_marketing_opt_in=False)
        self.assertEqual(notices.send_notification(entry.pk),"SKIPPED")
        entry.refresh_from_db();self.assertEqual(entry.skip_reason,"NO_MARKETING_CONSENT")

    def test_redacted_console_never_outputs_address_or_reward_code(self):
        from loyalty.email_backend import RedactedConsoleBackend
        output=StringIO()
        message=mail.EmailMessage(subject="SECRET-CODE",body="SECRET-CODE",to=["private@example.com"])
        self.assertEqual(RedactedConsoleBackend(stream=output).send_messages([message]),1)
        self.assertNotIn("SECRET-CODE",output.getvalue())
        self.assertNotIn("private@example.com",output.getvalue())

    def test_activation_trigger_once(self):
        from loyalty.services.lifecycle import activate_due_lots
        lot=self.lot(10,"PENDING",available=self.now-timedelta(days=1))
        activate_due_lots(self.now);activate_due_lots(self.now)
        self.assertEqual(NotificationLog.objects.filter(type="POINTS_AVAILABLE",lot=lot).count(),1)

    def test_code_issued_trigger_once_and_private_logs(self):
        from loyalty.services.redemption import accept_discount
        redemption=Redemption.objects.create(profile=self.profile,reward=self.reward,points_spent=10,
            discount_code="SECRET-CODE",idempotency_key="notice-code")
        accept_discount(redemption.pk,"SECRET-CODE","gid:1",self.now)
        accept_discount(redemption.pk,"SECRET-CODE","gid:1",self.now)
        entry=NotificationLog.objects.get(type="REWARD_CODE_ISSUED")
        with self.assertLogs("loyalty.services.notifications",level="INFO") as logs:
            self.assertEqual(notices.send_notification(entry.pk),"SENT")
        self.assertNotIn("SECRET-CODE",str(logs.output))
        self.assertNotIn(self.profile.email,str(logs.output))
        self.assertIn("SECRET-CODE",mail.outbox[0].body)
        self.assertNotIn("SECRET-CODE",str(entry.__dict__))
        payload=self.client.get("/api/admin/notifications/log").data
        self.assertNotIn("SECRET-CODE",str(payload));self.assertNotIn(self.profile.email,str(payload))

    def test_earn_trigger_duplicate_and_historical_suppression(self):
        from loyalty.services.earning import earn_points_for_order
        from shops.tests.fixtures import raw_order
        raw=raw_order()
        # Existing fixture is VND and customer 301.
        self.shop.currency="VND";self.shop.save()
        with patch("loyalty.services.earning.fetch_order",return_value=raw):
            earn_points_for_order(self.shop,1001)
            earn_points_for_order(self.shop,1001)
        self.assertEqual(NotificationLog.objects.filter(type="POINTS_EARNED").count(),1)
        raw["id"]="gid://shopify/Order/1002"
        with patch("loyalty.services.earning.fetch_order",return_value=raw):
            earn_points_for_order(self.shop,1002,historical=True)
        self.assertEqual(NotificationLog.objects.filter(type="POINTS_EARNED").count(),1)

    def test_tier_upgrade_trigger_once_no_downgrade_email(self):
        from loyalty.services.tiers import evaluate_profile_tier
        self.config_version(tiers_enabled=True)
        Tier.objects.create(shop=self.shop,name="Silver",rank=1,min_qualifying_points=10)
        self.lot(20,order=1001)
        now=timezone.now()+timedelta(seconds=1)
        evaluate_profile_tier(self.profile,now)
        evaluate_profile_tier(self.profile,now)
        self.assertEqual(NotificationLog.objects.filter(type="TIER_UPGRADED").count(),1)
        self.assertEqual(TierHistory.objects.count(),1)
        self.config_version(tiers_enabled=True,tier_downgrade_grace_days=0)
        PointsTransaction.objects.create(profile=self.profile,type="REVOKE",delta=-20,order_id=1001,
            balance_after=0,idempotency_key="notice-downgrade")
        evaluate_profile_tier(self.profile,now+timedelta(seconds=1),allow_downgrade=True)
        self.assertEqual(TierHistory.objects.count(),2)
        self.assertEqual(NotificationLog.objects.filter(type="TIER_UPGRADED").count(),1)

    @patch("loyalty.api.notifications.async_task")
    def test_settings_audit_test_email_and_rate_limit(self, enqueue):
        result=self.client.patch("/api/admin/notifications/settings",{"enabled_types":["POINTS_EARNED"],"reply_to":"reply@example.com"},format="json")
        self.assertEqual(result.status_code,200)
        audit=AuditLog.objects.get(action="notifications.settings")
        self.assertNotIn("reply@example.com",str(audit.after))
        self.assertEqual(self.client.patch("/api/admin/notifications/settings",{"enabled_types":["BOGUS"]},format="json").status_code,400)
        self.assertEqual(self.client.post("/api/admin/notifications/test",{},format="json").status_code,422)
        with self.captureOnCommitCallbacks(execute=True):
            result=self.client.post("/api/admin/notifications/test",{"email":"admin@example.com"},format="json")
        self.assertEqual(result.status_code,202)
        enqueue.assert_called_once()
        self.assertEqual(self.client.post("/api/admin/notifications/test",{"email":"admin@example.com"},format="json").status_code,429)
        self.assertNotIn("admin@example.com",str(AuditLog.objects.get(action="notifications.test").after))

    @patch("loyalty.api.notifications.async_task")
    def test_test_recipient_cannot_override_signed_admin(self, enqueue):
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {session_token(email='trusted@example.com')}")
        with self.captureOnCommitCallbacks(execute=True):
            result=self.client.post("/api/admin/notifications/test",{"email":"different@example.com"},format="json")
        self.assertEqual(result.status_code,202)
        self.assertEqual(enqueue.call_args.args,("loyalty.tasks.send_test_email",self.shop.pk,"trusted@example.com"))


@override_settings(MAILERS=LOCMEM)
class NotificationConcurrencyTests(PointsSetup, TransactionTestCase):
    def test_expiring_job_survives_real_orm_broker_connection_cleanup(self):
        from django_q.brokers.orm import ORM
        from django_q.models import OrmQ
        from django_q.tasks import async_task
        self.config_version(expiry_reminder_days=5)
        self.lot(10, expires=self.now + timedelta(days=2))
        broker = ORM("test-expiring-reminders")
        with patch("loyalty.services.notifications.async_task",
                   side_effect=lambda *args, **kwargs: async_task(*args, **kwargs, broker=broker, sync=False)):
            self.assertEqual(notices.expiring_notifications(self.now), 1)
            self.assertEqual(notices.expiring_notifications(self.now), 0)
        self.assertEqual(OrmQ.objects.filter(key="test-expiring-reminders").count(), 1)
        self.clean_ledger()

    def test_duplicate_workers_send_only_once(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        from django.db import connections, close_old_connections
        self.profile.email="private@example.com";self.profile.save()
        NotificationSettings.objects.create(shop=self.shop,enabled_types=["POINTS_EARNED"])
        lot=self.lot(10)
        with patch("loyalty.services.notifications.async_task"):
            entry=notices.notify(self.profile,"POINTS_EARNED",lot.pk,lot=lot)
        barrier=Barrier(2)
        def send():
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                return notices.send_notification(entry.pk)
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses=list(pool.map(lambda _:send(),range(2)))
        self.assertCountEqual(statuses,["SENT","NOOP"])
        self.assertEqual(len(mail.outbox),1)
