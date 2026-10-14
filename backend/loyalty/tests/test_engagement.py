from copy import deepcopy
from datetime import datetime, timedelta, timezone as utc
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.db import transaction
from django.test import override_settings, TransactionTestCase
from django.utils import timezone
from rest_framework.test import APITestCase

from loyalty.models import (CustomerProfile, PointsLot, PointsTransaction, Referral, ReferralCode,
    NotificationLog, NotificationSettings, AuditLog, Tier)
from loyalty.services.engagement import (grant_bonus, welcome_on_signup, set_birthday, grant_birthday, shop_zone, birthday_state)
from loyalty.services.earning import earn_points_for_order
from loyalty.services.referrals import (create_code, normalized_email, reward_referral, record_referral,
    referral_summary, manual_reject, reject_for_order)
from loyalty.services.integrity import check_profile_integrity
from loyalty.services.tiers import qualifying_points
from shops.shopify import map_order_snapshot
from shops.models import Shop
from shops.tests.fixtures import raw_order, raw_refund
from shops.tests.test_http_auth import AUTH_SETTINGS, session_token, signed_query
from .lifecycle_fixtures import PointsSetup


@override_settings(**AUTH_SETTINGS)
class EngagementTests(PointsSetup, APITestCase):
    def setUp(self):
        super().setUp()
        self.shop.currency="VND";self.shop.iana_timezone="UTC";self.shop.save()
        self.profile.email="friend@example.com";self.profile.save()
        self.referrer=CustomerProfile.objects.create(shop=self.shop,shopify_customer_id=701,email="owner@gmail.com")
        self.code=ReferralCode.objects.create(profile=self.referrer,code="H7K9M2NP")
        self.config=self.config_version(welcome_points=20,birthday_enabled=True,birthday_points=30,
            referral_enabled=True,referral_referrer_points=50,referral_referee_points=25,
            referral_max_per_referrer_per_month=2)
        patch("loyalty.services.notifications.async_task").start()
        patch("django_q.tasks.async_task").start()
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {session_token()}")

    def raw(self, order_id=1001, customer_id=301, code=None, email="friend@example.com"):
        raw=raw_order(order_id)
        raw["customer"]={"id":f"gid://shopify/Customer/{customer_id}","email":email,
            "createdAt":(self.now-timedelta(days=2)).isoformat()}
        raw["customAttributes"]=[] if code is None else [{"key":"loyalty_ref","value":code}]
        return raw

    def earn(self, raw=None, historical=False):
        raw=raw or self.raw()
        with patch("loyalty.services.earning.fetch_order",return_value=deepcopy(raw)):
            return earn_points_for_order(self.shop,int(raw["id"].rsplit("/",1)[-1]),historical=historical)

    def bonus(self, code):
        return PointsTransaction.objects.filter(rule_snapshot__code=code)

    def pending(self, raw=None):
        raw=raw or self.raw(code=self.code.code)
        self.earn(raw)
        return Referral.objects.get(referee_order_id=int(raw["id"].rsplit("/",1)[-1]))

    def make_available(self,row):
        from loyalty.services.lifecycle import activate_due_lots
        now=timezone.now()+timedelta(days=8)
        activate_due_lots(now)
        return now

    def test_first_order_welcome_once_never_on_skipped_or_historical(self):
        self.assertEqual(self.earn().status,"EARNED")
        self.assertEqual(self.earn().status,"DUPLICATE")
        self.earn(self.raw(1002))
        entry=self.bonus("welcome").get()
        self.assertEqual(entry.delta,20);self.assertIsNone(entry.order_id)
        self.assertEqual(entry.idempotency_key,f"shop:{self.shop.pk}:welcome:301")
        self.assertEqual(entry.lot.status,"AVAILABLE")
        historical=self.raw(1003,302)
        self.earn(historical,historical=True)
        self.earn(self.raw(1004,302))
        skipped=self.raw(1005,303);skipped["cancelledAt"]=self.now.isoformat()
        self.assertEqual(self.earn(skipped).status,"SKIPPED")
        self.assertEqual(self.bonus("welcome").count(),1)
        self.clean_ledger()

    def test_signup_guard_fresh_verified_and_same_key_across_trigger_changes(self):
        self.config_version(welcome_trigger="SIGNUP")
        for payload in [{"created_at":(self.now-timedelta(hours=25)).isoformat(),"verified_email":True},
            {"created_at":self.now.isoformat(),"verified_email":False},
            {"created_at":self.now.isoformat(),"verified_email":"true"},
            {"created_at":(self.now+timedelta(minutes=1)).isoformat(),"verified_email":True},
            {"created_at":"invalid","verified_email":True}]:
            self.assertIsNone(welcome_on_signup(self.profile,payload,self.now))
        good={"created_at":(self.now-timedelta(hours=24)).isoformat(),"verified_email":True}
        welcome_on_signup(self.profile,good,self.now);welcome_on_signup(self.profile,good,self.now)
        self.config_version(welcome_trigger="FIRST_ORDER")
        self.earn()
        self.assertEqual(self.bonus("welcome").count(),1)
        self.clean_ledger()

    def test_signup_only_customer_create_and_never_customer_sync(self):
        from webhooks.handlers import customers_create, customers_update
        from types import SimpleNamespace
        self.config_version(welcome_trigger="SIGNUP")
        event=SimpleNamespace(shop=self.shop,shop_id=self.shop.pk,payload={"id":301,"created_at":self.now.isoformat(),"verified_email":True})
        customers_update(event);self.assertFalse(self.bonus("welcome").exists())
        customers_create(event);customers_create(event);self.assertEqual(self.bonus("welcome").count(),1)
        with patch("loyalty.management.commands.sync_customers.fetch_customer_page",return_value=([{
            "id":"gid://shopify/Customer/304","createdAt":self.now.isoformat(),"email":"new@example.com"}],None)):
            call_command("sync_customers",stdout=StringIO())
        self.assertEqual(self.bonus("welcome").count(),1)

    def test_bonus_debt_offset_expiry_and_no_tier_multiplier_or_qualification(self):
        self.debt(25)
        tier=Tier.objects.create(shop=self.shop,name="Gold",rank=1,min_qualifying_points=1,multiplier=10)
        self.profile.tier=tier;self.profile.save()
        entry=grant_bonus(self.profile,20,"welcome:301","welcome","WELCOME",self.config,self.now)
        self.assertEqual((entry.delta,entry.lot.points_remaining,entry.lot.status),(20,0,"CONSUMED"))
        next_entry=grant_bonus(self.profile,30,"birthday:301:2026","birthday","BIRTHDAY",self.config,self.now)
        self.assertEqual((next_entry.delta,next_entry.lot.points_remaining),(30,25))
        self.assertEqual(next_entry.lot.expires_at,self.now+timedelta(days=30))
        self.assertEqual(qualifying_points(self.profile,timezone.now()+timedelta(seconds=1)),0)
        self.assertEqual(check_profile_integrity(self.profile),[])
        self.assertEqual(NotificationLog.objects.filter(type__in=["WELCOME_POINTS","BIRTHDAY_POINTS"]).count(),2)

    def test_birthday_validation_and_twelve_calendar_month_lock(self):
        for month,day in [(0,1),(13,1),(2,30),(4,31),(1,0),(True,1)]:
            with self.assertRaises(Exception) as raised:
                set_birthday(self.profile,month,day,actor="customer",now=self.now)
            self.assertEqual(raised.exception.code,"INVALID_BIRTHDAY")
        leap=datetime(2028,2,29,12,tzinfo=utc.utc)
        set_birthday(self.profile,2,29,actor="customer",now=leap)
        with self.assertRaises(Exception) as raised:
            set_birthday(self.profile,3,1,actor="customer",now=datetime(2029,2,28,11,59,tzinfo=utc.utc))
        self.assertEqual(raised.exception.code,"BIRTHDAY_LOCKED")
        result=set_birthday(self.profile,3,1,actor="shopify_user:123",admin=True,now=datetime(2029,2,28,12,tzinfo=utc.utc))
        self.assertEqual(result["month"],3)
        self.assertEqual(AuditLog.objects.get(action="customer.birthday").after,{"month":3,"day":1})

    def birthday_profile(self,month,day,customer=301,orders=True):
        profile=self.profile if customer==301 else CustomerProfile.objects.create(shop=self.shop,shopify_customer_id=customer)
        set_birthday(profile,month,day,actor="customer",now=self.now-timedelta(days=1))
        if orders:self.lot(10,order=customer+1000,profile=profile)
        profile.refresh_from_db();return profile

    def test_birthday_shop_timezone_boundary_and_cache_fetch_once(self):
        profile=self.birthday_profile(1,2)
        self.shop.iana_timezone="Pacific/Kiritimati";self.shop.save()
        profile.shop=self.shop
        before=datetime(2027,1,1,9,59,tzinfo=utc.utc)
        self.assertIsNone(grant_birthday(profile,before))
        at=datetime(2027,1,1,10,tzinfo=utc.utc)
        self.assertIsNotNone(grant_birthday(profile,at));grant_birthday(profile,at)
        self.assertEqual(self.bonus("birthday").count(),1)
        self.shop.iana_timezone="";self.shop.save()
        with patch("loyalty.services.engagement.graphql",return_value={"shop":{"ianaTimezone":"Asia/Ho_Chi_Minh"}}) as query:
            shop_zone(self.shop);shop_zone(Shop.objects.get(pk=self.shop.pk))
        self.assertEqual(query.call_count,1)

    def test_birthday_leap_and_non_leap_years(self):
        profile=self.birthday_profile(2,29)
        grant_birthday(profile,datetime(2027,2,28,12,tzinfo=utc.utc))
        self.assertEqual(self.bonus("birthday").count(),1)
        self.assertIsNone(grant_birthday(profile,datetime(2028,2,28,12,tzinfo=utc.utc)))
        grant_birthday(profile,datetime(2028,2,29,12,tzinfo=utc.utc))
        self.assertEqual(self.bonus("birthday").count(),2)
        self.clean_ledger()

    def test_birthday_catchup_inclusive_seven_days_and_year_rollover(self):
        profile=self.birthday_profile(1,1)
        self.assertIsNone(grant_birthday(profile,datetime(2027,1,9,12,tzinfo=utc.utc)))
        grant_birthday(profile,datetime(2027,1,8,12,tzinfo=utc.utc))
        other=self.birthday_profile(12,31,customer=302)
        entry=grant_birthday(other,datetime(2027,1,2,12,tzinfo=utc.utc))
        self.assertTrue(entry.idempotency_key.endswith("birthday:302:2026"))
        self.assertEqual(self.bonus("birthday").count(),2)

    def test_birthday_ineligible_profiles_and_no_backdating_by_new_input(self):
        profile=self.birthday_profile(1,1,orders=False)
        at=datetime(2027,1,1,12,tzinfo=utc.utc)
        self.assertIsNone(grant_birthday(profile,at))
        self.lot(10,order=2001)
        for field in ("is_blocked","is_anonymized"):
            CustomerProfile.objects.filter(pk=profile.pk).update(**{field:True})
            self.assertIsNone(grant_birthday(profile,at))
            CustomerProfile.objects.filter(pk=profile.pk).update(**{field:False})
        CustomerProfile.objects.filter(pk=profile.pk).update(birthday_set_at=datetime(2027,1,2,tzinfo=utc.utc))
        self.assertIsNone(grant_birthday(profile,datetime(2027,1,3,tzinfo=utc.utc)))
        self.assertFalse(self.bonus("birthday").exists())

    def test_referral_code_create_or_return_and_normalized_email(self):
        code=create_code(self.profile)
        self.assertEqual(code.pk,create_code(self.profile).pk)
        self.assertEqual(len(code.code),8)
        self.assertFalse(set(code.code)&set("01ILO"))
        self.assertEqual(normalized_email(" O.w.N.e.r+offer@GoogleMail.com "),"owner@gmail.com")
        self.assertNotEqual(normalized_email("a.b+test@example.com"),normalized_email("ab@example.com"))

    def test_order_mapping_attribution_and_customer_age(self):
        order=map_order_snapshot(self.raw(code=self.code.code))
        self.assertEqual(order.custom_attributes,{"loyalty_ref":self.code.code})
        self.assertEqual(order.customer_email,"friend@example.com")
        self.assertEqual(order.customer_created_at,self.now-timedelta(days=2))

    def test_unknown_inactive_self_alias_email_fraud_matrix(self):
        cases=[("ZZZZZZZZ","friend@example.com","UNKNOWN_CODE"),
            (self.code.code," O.w.n.e.r+deal@googlemail.com ","SELF_REFERRAL")]
        for index,(code,email,reason) in enumerate(cases):
            row=self.pending(self.raw(1001+index,301+index,code,email))
            self.assertEqual(row.reject_reason,reason)
        row=self.pending(self.raw(1004,701,self.code.code,"another@example.com"))
        self.assertEqual(row.reject_reason,"SELF_REFERRAL")
        self.code.is_active=False;self.code.save()
        row=self.pending(self.raw(1005,305,self.code.code))
        self.assertEqual(row.reject_reason,"INACTIVE_CODE")
        self.assertFalse(self.bonus("referral_referrer").exists())

    def test_existing_customer_age_minimum_and_cap_guards(self):
        self.lot(5,order=777)
        self.assertEqual(self.pending().reject_reason,"EXISTING_CUSTOMER")
        CustomerProfile.objects.create(shop=self.shop,shopify_customer_id=302,shopify_created_at=self.now-timedelta(days=31))
        self.assertEqual(self.pending(self.raw(1002,302,self.code.code)).reject_reason,"PROFILE_TOO_OLD")
        self.config_version(referral_min_order_amount=200000)
        self.assertEqual(self.pending(self.raw(1003,303,self.code.code)).reject_reason,"BELOW_MINIMUM")
        self.config_version(referral_min_order_amount=None,referral_max_per_referrer_per_month=1)
        self.assertEqual(self.pending(self.raw(1004,304,self.code.code)).status,"PENDING")
        self.assertEqual(self.pending(self.raw(1005,305,self.code.code)).reject_reason,"MONTHLY_CAP")

    def test_duplicate_referee_order_and_customer_do_not_get_new_referral(self):
        row=self.pending()
        self.earn(self.raw(code=self.code.code))
        self.assertEqual(Referral.objects.count(),1)
        self.assertEqual(self.pending(self.raw(1002,301,self.code.code)).reject_reason,"ALREADY_REFERRED")
        self.assertEqual(row.status,"PENDING")

    def test_historical_orders_never_attribute_or_welcome(self):
        self.earn(self.raw(code=self.code.code),historical=True)
        self.assertFalse(Referral.objects.exists())
        self.assertFalse(self.bonus("welcome").exists())

    def test_reward_uses_frozen_config_both_wallets_and_is_idempotent(self):
        raw=self.raw(code=self.code.code);row=self.pending(raw)
        self.assertEqual(reward_referral(row.pk,self.now),"PENDING")
        now=self.make_available(row)
        self.config_version(referral_referrer_points=999,referral_referee_points=888,expiry_days=60)
        with patch("loyalty.services.referrals.fetch_order",return_value=raw):
            self.assertEqual(reward_referral(row.pk,now),"REWARDED")
            self.assertEqual(reward_referral(row.pk,now),"NOOP")
        self.assertEqual(self.bonus("referral_referrer").get().delta,50)
        self.assertEqual(self.bonus("referral_referee").get().delta,25)
        for entry in self.bonus("referral_referrer")|self.bonus("referral_referee"):
            self.assertIsNone(entry.order_id)
            self.assertEqual(entry.lot.expires_at,now+timedelta(days=30))
        self.assertEqual(referral_summary(self.referrer)["counts"]["rewarded"],1)
        self.assertEqual(referral_summary(self.referrer)["points_earned"],50)
        self.assertEqual(qualifying_points(self.referrer,now),0)
        self.clean_ledger()

    def test_refund_before_grant_cancelled_and_expired_pending(self):
        for i,reason in enumerate(["REFUNDED","CANCELLED"]):
            raw=self.raw(1001+i,301+i,self.code.code);row=self.pending(raw);now=self.make_available(row)
            if reason=="REFUNDED":raw["refunds"]=[raw_refund()]
            else:raw["cancelledAt"]=now.isoformat()
            with patch("loyalty.services.referrals.fetch_order",return_value=raw):
                self.assertEqual(reward_referral(row.pk,now),"REJECTED")
            row.refresh_from_db();self.assertEqual(row.reject_reason,reason)
        row=self.pending(self.raw(1003,303,self.code.code))
        self.assertEqual(reward_referral(row.pk,timezone.now()+timedelta(days=91)),"REJECTED")
        row.refresh_from_db();self.assertEqual(row.reject_reason,"EXPIRED_PENDING")
        self.assertFalse(self.bonus("referral_referrer").exists())

    def test_received_refund_webhook_rejects_even_before_processing(self):
        from webhooks.models import WebhookEvent
        row=self.pending()
        WebhookEvent.objects.create(shop=self.shop,shop_domain=self.shop.domain,webhook_id="refund-received",topic="refunds/create",payload={"id":501,"order_id":1001})
        self.assertEqual(reward_referral(row.pk,timezone.now()),"REJECTED")
        row.refresh_from_db();self.assertEqual(row.reject_reason,"REFUNDED")

    def test_later_refund_never_claws_referral_points_back(self):
        row=self.pending();now=self.make_available(row)
        with patch("loyalty.services.referrals.fetch_order",return_value=self.raw(code=self.code.code)):
            reward_referral(row.pk,now)
        reject_for_order(self.shop,1001,"REFUNDED",now)
        row.refresh_from_db();self.assertEqual(row.status,"REWARDED")
        self.assertEqual(self.bonus("referral_referrer").get().delta,50)

    def test_atomic_two_sided_grant_rolls_back_on_second_failure(self):
        row=self.pending();now=self.make_available(row)
        from loyalty.services import referrals
        original=referrals.grant_bonus
        def fail_second(profile,*args,**kwargs):
            if profile.pk==self.profile.pk:raise RuntimeError("simulated failure")
            return original(profile,*args,**kwargs)
        with patch("loyalty.services.referrals.fetch_order",return_value=self.raw(code=self.code.code)),patch("loyalty.services.referrals.grant_bonus",side_effect=fail_second):
            with self.assertRaises(RuntimeError):reward_referral(row.pk,now)
        self.assertFalse(self.bonus("referral_referrer").exists())
        row.refresh_from_db();self.assertEqual(row.status,"PENDING")
        self.clean_ledger()

    def test_monthly_cap_uses_shop_calendar_and_reserves_pending_slots(self):
        self.shop.iana_timezone="America/Los_Angeles";self.shop.save()
        self.config_version(referral_max_per_referrer_per_month=1)
        # Both are Nov 1 in UTC; the first is still Oct 31 in the shop.
        with patch("loyalty.services.earning.timezone.now",return_value=datetime(2026,11,1,0,30,tzinfo=utc.utc)):
            first=self.pending(self.raw(1001,301,self.code.code))
        with patch("loyalty.services.earning.timezone.now",return_value=datetime(2026,11,1,8,30,tzinfo=utc.utc)):
            second=self.pending(self.raw(1002,302,self.code.code))
            third=self.pending(self.raw(1003,303,self.code.code))
        self.assertEqual((first.status,second.status,third.reject_reason),("PENDING","PENDING","MONTHLY_CAP"))
        self.assertEqual((first.qualifying_month.month,second.qualifying_month.month),(10,11))

    def test_network_retry_does_not_duplicate_referral_grants(self):
        from loyalty.tasks import reward_referral as reward_task
        from shops.shopify import ShopifyTransientError
        row=self.pending();now=self.make_available(row)
        with patch("loyalty.services.referrals.fetch_order",side_effect=[ShopifyTransientError("private data"),self.raw(code=self.code.code)]):
            with self.assertLogs("loyalty.tasks",level="WARNING") as logs:
                self.assertEqual(reward_task(row.pk,now.isoformat()),"FAILED")
            self.assertNotIn("private data",str(logs.output))
            self.assertEqual(reward_task(row.pk,now.isoformat()),"REWARDED")
            self.assertEqual(reward_task(row.pk,now.isoformat()),"NOOP")
        self.assertEqual(self.bonus("referral_referrer").count(),1)
        self.assertEqual(self.bonus("referral_referee").count(),1)
        self.clean_ledger()

    def test_proxy_signed_identity_only_and_admin_audit_reject(self):
        def path(endpoint,customer="301"):
            return f"/proxy/{endpoint}?{signed_query(customer).urlencode()}"
        self.assertEqual(self.client.post("/proxy/profile/birthday",{"month":1,"day":1},format="json").status_code,403)
        self.assertEqual(self.client.post(path("profile/birthday",customer=""),{"month":1,"day":1},format="json").status_code,401)
        response=self.client.post(path("profile/birthday"),{"month":2,"day":29,"customer_id":701},format="json")
        self.assertEqual(response.status_code,400)
        self.assertEqual(self.client.post(path("profile/birthday"),{"month":2,"day":29},format="json").status_code,200)
        self.referrer.refresh_from_db();self.assertIsNone(self.referrer.birthday_month)
        self.assertEqual(self.client.post(path("referral/code"),{},format="json").status_code,200)
        self.assertEqual(self.client.post(path("referral/code"),{"customer_id":701},format="json").status_code,400)
        self.assertEqual(self.client.get(path("referral")).status_code,200)
        row=self.pending()
        self.assertEqual(self.client.get("/api/admin/referrals",{"status":"PENDING","referrer":self.referrer.pk}).data["count"],1)
        self.assertEqual(self.client.post(f"/api/admin/referrals/{row.pk}/reject",{},format="json").status_code,200)
        self.assertEqual(self.client.post(f"/api/admin/referrals/{row.pk}/reject",{},format="json").status_code,409)
        self.assertEqual(AuditLog.objects.filter(action="referral.reject").count(),1)

    def test_commands_as_of_clocks_and_notification_consent(self):
        from loyalty.services.notifications import send_notification
        self.birthday_profile(1,1)
        stdout=StringIO()
        call_command("grant_birthdays",as_of="2027-01-01T12:00:00Z",stdout=stdout)
        call_command("grant_birthdays",as_of="2027-01-01T12:00:00Z",stdout=stdout)
        self.assertEqual(self.bonus("birthday").count(),1)
        NotificationSettings.objects.create(shop=self.shop,enabled_types=["BIRTHDAY_POINTS"])
        entry=NotificationLog.objects.get(type="BIRTHDAY_POINTS")
        self.assertEqual(send_notification(entry.pk),"SKIPPED")
        entry.refresh_from_db();self.assertEqual(entry.skip_reason,"NO_MARKETING_CONSENT")
        call_command("sweep_referrals",as_of="2027-01-01T12:00:00Z",stdout=StringIO())


@override_settings(**AUTH_SETTINGS)
class EngagementConcurrencyTests(PointsSetup, TransactionTestCase):
    def setUp(self):
        super().setUp()
        self.shop.currency="VND";self.shop.iana_timezone="UTC";self.shop.save()
        self.config=self.config_version(welcome_points=20,birthday_enabled=True,birthday_points=30,referral_enabled=True,
            referral_referrer_points=50,referral_referee_points=25,referral_max_per_referrer_per_month=1)
        self.referrer=CustomerProfile.objects.create(shop=self.shop,shopify_customer_id=701,email="owner@gmail.com")
        self.code=ReferralCode.objects.create(profile=self.referrer,code="H7K9M2NP")
        patch("loyalty.services.notifications.async_task").start()
        patch("django_q.tasks.async_task").start()

    def concurrent(self,action):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        from django.db import connections,close_old_connections
        barrier=Barrier(2)
        def run(index):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                return action(index)
            finally:connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:return list(pool.map(run,range(2)))

    def test_duplicate_signup_grants_once_under_concurrency(self):
        self.config_version(welcome_trigger="SIGNUP")
        payload={"created_at":self.now.isoformat(),"verified_email":True}
        self.concurrent(lambda _:welcome_on_signup(CustomerProfile.objects.get(pk=self.profile.pk),payload,timezone.now()))
        self.assertEqual(PointsTransaction.objects.filter(rule_snapshot__code="welcome").count(),1)
        self.clean_ledger()

    def test_two_first_orders_award_only_one_welcome(self):
        def fetch(order_id):return raw_order(order_id)
        with patch("loyalty.services.earning.fetch_order",side_effect=fetch):
            self.concurrent(lambda i:earn_points_for_order(Shop.objects.get(pk=self.shop.pk),1001+i))
        self.assertEqual(PointsTransaction.objects.filter(rule_snapshot__code="welcome").count(),1)
        self.clean_ledger()

    def test_birthday_duplicate_workers_grant_once(self):
        set_birthday(self.profile,1,1,actor="customer",now=self.now-timedelta(days=1))
        self.lot(10,order=1001)
        self.concurrent(lambda _:grant_birthday(CustomerProfile.objects.get(pk=self.profile.pk),datetime(2027,1,1,12,tzinfo=utc.utc)))
        self.assertEqual(PointsTransaction.objects.filter(rule_snapshot__code="birthday").count(),1)
        self.clean_ledger()

    def test_cap_reservation_two_referees_only_one_pending(self):
        def fetch(order_id):
            raw=raw_order(order_id);raw["customer"]={"id":f"gid://shopify/Customer/{301+order_id-1001}"}
            raw["customAttributes"]=[{"key":"loyalty_ref","value":self.code.code}]
            return raw
        with patch("loyalty.services.earning.fetch_order",side_effect=fetch):
            self.concurrent(lambda i:earn_points_for_order(Shop.objects.get(pk=self.shop.pk),1001+i))
        self.assertEqual(Referral.objects.filter(status="PENDING").count(),1)
        self.assertEqual(Referral.objects.filter(reject_reason="MONTHLY_CAP").count(),1)
        self.clean_ledger()

    def test_referral_duplicate_sweepers_grant_both_sides_once(self):
        raw=raw_order();raw["customAttributes"]=[{"key":"loyalty_ref","value":self.code.code}]
        with patch("loyalty.services.earning.fetch_order",return_value=raw):earn_points_for_order(self.shop,1001)
        from loyalty.services.lifecycle import activate_due_lots
        now=timezone.now()+timedelta(days=8);activate_due_lots(now)
        row=Referral.objects.get()
        with patch("loyalty.services.referrals.fetch_order",return_value=raw):
            result=self.concurrent(lambda _:reward_referral(row.pk,now))
        self.assertCountEqual(result,["REWARDED","NOOP"])
        self.assertEqual(PointsTransaction.objects.filter(rule_snapshot__code__in=["referral_referrer","referral_referee"]).count(),2)
        self.clean_ledger()
