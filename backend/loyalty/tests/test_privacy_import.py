import hashlib
import hmac
import base64
import json
from copy import deepcopy
from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command, CommandError
from django.db import connection, DatabaseError, transaction
from django.test import TestCase, TransactionTestCase, override_settings
from rest_framework.test import APITestCase

from loyalty.models import (AuditLog, CustomerProfile, CustomerDataExport, PointsTransaction,
    PointsLot, Redemption, ImportRun, Tier, TierHistory, EarnRule, Campaign, NotificationLog, NotificationSettings)
from loyalty.services import privacy
from loyalty.services.adjustments import adjust_points
from loyalty.services.earning import earn_points_for_order
from loyalty.services.errors import LoyaltyError
from loyalty.services.importing import import_orders
from shops.models import Shop
from shops import bulk_orders
from shops.shopify import ShopifyError, map_order_snapshot
from shops.tests.fixtures import raw_order, raw_refund
from shops.tests.test_http_auth import AUTH_SETTINGS, session_token
from webhooks.models import WebhookEvent
from webhooks.handlers import HANDLERS
from .lifecycle_fixtures import PointsSetup


class PrivacyTests(PointsSetup, TestCase):
    def setUp(self):
        super().setUp()
        self.profile.email='customer@example.invalid';self.profile.first_name='Alice';self.profile.last_name='Smith'
        self.profile.blocked_reason='Call customer@example.invalid';self.profile.birthday_month=2;self.profile.birthday_day=29
        self.profile.birthday_set_at=self.now;self.profile.email_marketing_opt_in=True;self.profile.save()

    def event(self, topic, payload, **values):
        return WebhookEvent.objects.create(shop=self.shop, shop_domain=self.shop.domain,
            webhook_id=f'privacy-{WebhookEvent.objects.count()}', topic=topic, payload=payload, **values)

    def test_complete_anonymization_preserves_financial_history_and_is_idempotent(self):
        lot=self.lot(20, order=1001)
        adjust_points(self.profile, delta=5, reason_code='GIFT', note='Email customer@example.invalid',client_key='pii-note',actor='system')
        entry=self.profile.points_transactions.get(type='ADJUST')
        audit=AuditLog.objects.create(shop=self.shop,actor='system',action='customer.update',target_type='CustomerProfile',target_id=str(self.profile.pk),
            before={'email':self.profile.email,'nested':[{'first_name':'Alice','birthday_month':2}]},after={'note':'Call Alice'})
        unrelated=CustomerProfile.objects.create(shop=self.shop,shopify_customer_id=302,email='other@example.invalid')
        other_audit=AuditLog.objects.create(shop=self.shop,actor='system',action='customer.update',target_type='CustomerProfile',target_id=str(unrelated.pk),after={'email':unrelated.email})
        evt=self.event('orders/paid',{'id':1001,'customer':{'id':301,'email':self.profile.email},'shipping_address':{'name':'Alice'}})
        refund=self.event('refunds/create',{'id':501,'order_id':1001,'note':'Alice'})
        other_event=self.event('customers/update',{'id':302,'email':unrelated.email})
        from loyalty.services.redemption import redeem_reward
        with patch('loyalty.services.redemption.issue_redemption'):
            redeem_reward(self.shop,301,self.reward.pk,'privacy-code',now=self.now)
        redemption=Redemption.objects.get();redemption.status='ISSUED';redemption.discount_code='PRIVACY-TEST'
        redemption.shopify_discount_id='gid://shopify/DiscountCodeNode/1';redemption.last_error='Alice email error';redemption.save()
        saved=privacy.customer_export(self.profile,'system')
        result=privacy.anonymize_customer(self.profile,'system','merchant_request')
        self.assertEqual((result.available_balance,result.pending_balance,result.lifetime_earned),(15,0,25))
        for field in ['email','first_name','last_name','blocked_reason','locale']:
            self.assertEqual(getattr(result,field),'')
        self.assertIsNone(result.birthday_month);self.assertIsNone(result.birthday_set_at)
        self.assertTrue(result.is_anonymized);self.assertTrue(result.email_opt_out);self.assertFalse(result.email_marketing_opt_in)
        entry.refresh_from_db();self.assertEqual(entry.note,'[redacted]');self.assertEqual(entry.delta,5)
        audit.refresh_from_db();self.assertNotIn('Alice',json.dumps([audit.before,audit.after]))
        other_audit.refresh_from_db();self.assertEqual(other_audit.after['email'],unrelated.email)
        for event in [evt,refund]:
            event.refresh_from_db();self.assertTrue(event.payload['redacted']);self.assertNotIn('Alice',json.dumps(event.payload))
        other_event.refresh_from_db();self.assertEqual(other_event.payload['email'],unrelated.email)
        redemption.refresh_from_db();self.assertEqual(redemption.last_error,'')
        self.assertTrue(CustomerDataExport.objects.get().payload['redacted'])
        count=AuditLog.objects.count();privacy.anonymize_customer(result,'system');self.assertEqual(AuditLog.objects.count(),count)
        self.clean_ledger()

    def test_trigger_blocks_regular_update_and_raw_redaction_without_guard(self):
        self.lot(10)
        entry=PointsTransaction.objects.get()
        for sql in ['UPDATE loyalty_pointstransaction SET note=\'[redacted]\' WHERE id=%s',
                    'UPDATE loyalty_pointstransaction SET delta=11 WHERE id=%s',
                    'DELETE FROM loyalty_pointstransaction WHERE id=%s']:
            with self.assertRaises(DatabaseError),transaction.atomic(),connection.cursor() as c:
                c.execute(sql,[entry.pk])
        with transaction.atomic(),privacy.local_guard('loyalty.redact_profile',self.profile.pk) as c:
            for sql in ["UPDATE loyalty_pointstransaction SET note='other' WHERE id=%s",
                        "UPDATE loyalty_pointstransaction SET note='[redacted]',delta=11 WHERE id=%s"]:
                with self.assertRaises(DatabaseError),transaction.atomic():c.execute(sql,[entry.pk])
        privacy.anonymize_customer(self.profile,'system')
        with self.assertRaises(DatabaseError),transaction.atomic(),connection.cursor() as c:
            c.execute("UPDATE loyalty_pointstransaction SET note='[redacted]' WHERE id=%s",[entry.pk])

    def test_guard_does_not_redact_another_profile(self):
        other=CustomerProfile.objects.create(shop=self.shop,shopify_customer_id=302)
        lot=self.lot(5,profile=other);entry=lot.transactions.get()
        with transaction.atomic(),privacy.local_guard('loyalty.redact_profile',self.profile.pk) as c:
            with self.assertRaises(DatabaseError),transaction.atomic():
                c.execute("UPDATE loyalty_pointstransaction SET note='[redacted]' WHERE id=%s",[entry.pk])

    def test_pending_code_blocks_anonymization(self):
        Redemption.objects.create(profile=self.profile,reward=self.reward,points_spent=10,idempotency_key='pending')
        with self.assertRaises(LoyaltyError) as error:privacy.anonymize_customer(self.profile,'system')
        self.assertEqual(error.exception.code,'PENDING_REDEMPTIONS');self.profile.refresh_from_db();self.assertFalse(self.profile.is_anonymized)

    def test_customer_export_and_compliance_request_are_stored_once_not_emailed(self):
        self.lot(20)
        event=self.event('customers/data_request',{'shop_domain':self.shop.domain,'customer':{'id':301},'data_request':{'id':10}})
        with patch('loyalty.services.notifications.notify') as notify:
            HANDLERS[event.topic](event);HANDLERS[event.topic](event)
        export=CustomerDataExport.objects.get();self.assertEqual(export.payload['profile']['email'],self.profile.email)
        self.assertEqual(export.payload['ledger'][0]['delta'],20)
        self.assertEqual(set(export.payload),{'profile','lots','ledger','redemptions','tier_history','audit_entries'})
        self.assertEqual(AuditLog.objects.filter(action='customer.data_export').count(),1);notify.assert_not_called()

    def test_compliance_scope_and_later_anonymized_earn(self):
        event=self.event('customers/redact',{'shop_domain':'other.myshopify.com','customer':{'id':301}})
        with self.assertRaises(ValueError):HANDLERS[event.topic](event)
        event.payload['shop_domain']=self.shop.domain;HANDLERS[event.topic](event)
        raw=historical_order(self.now)
        with patch('loyalty.services.earning.fetch_order',return_value=raw):
            outcome=earn_points_for_order(self.shop,1001)
        self.assertEqual(outcome.code,'anonymized');self.assertFalse(PointsTransaction.objects.exists())

    def test_retention_scrubs_old_terminal_only_and_preserves_recent_failed(self):
        old=self.event('customers/update',{'id':301,'email':self.profile.email},status='PROCESSED')
        recent=self.event('customers/update',{'id':301,'email':self.profile.email},status='FAILED')
        processing=self.event('customers/update',{'id':301,'email':self.profile.email},status='PROCESSING')
        WebhookEvent.objects.filter(pk__in=[old.pk,processing.pk]).update(received_at=self.now-timedelta(days=31))
        result=privacy.scrub_webhook_payloads(30,self.now)
        old.refresh_from_db();recent.refresh_from_db();processing.refresh_from_db()
        self.assertEqual(result['processed'],1);self.assertEqual(old.payload,{'id':301,'customer':{'id':301},'redacted':True})
        self.assertIn('email',recent.payload);self.assertIn('email',processing.payload)

    def test_purge_exact_confirmation_and_other_shop_untouched(self):
        self.lot(15)
        tier=Tier.objects.create(shop=self.shop,name='Silver',rank=1,min_qualifying_points=10)
        TierHistory.objects.create(profile=self.profile,to_tier=tier,reason='upgrade',qualifying_points=15)
        EarnRule.objects.create(shop=self.shop,name='Rule',rule_type='PRODUCT',target_ids=[201],multiplier=2,starts_at=self.now)
        Campaign.objects.create(shop=self.shop,name='Campaign',multiplier=2,starts_at=self.now)
        privacy.customer_export(self.profile,'system')
        self.event('customers/create',{'id':301})
        other=Shop.objects.create(domain='other.myshopify.com',currency='USD')
        other_profile=CustomerProfile.objects.create(shop=other,shopify_customer_id=301,email='other@example.invalid')
        other_lot=self.lot(25,profile=other_profile)
        with self.assertRaises(LoyaltyError):privacy.purge_shop_data(self.shop.domain,'wrong')
        self.assertTrue(Shop.objects.filter(pk=self.shop.pk).exists())
        with transaction.atomic(),privacy.local_guard('loyalty.purge_shop',self.shop.pk) as c:
            with self.assertRaises(DatabaseError),transaction.atomic():
                c.execute('DELETE FROM loyalty_pointstransaction WHERE profile_id=%s',[other_profile.pk])
        self.assertGreater(privacy.purge_shop_data(self.shop.domain,self.shop.domain,batch_size=1),0)
        self.assertFalse(Shop.objects.filter(pk=self.shop.pk).exists());other_profile.refresh_from_db()
        self.assertEqual(other_profile.email,'other@example.invalid');self.assertTrue(other_lot.transactions.exists())
        self.assertEqual(privacy.purge_shop_data(self.shop.domain,self.shop.domain),0)
        self.clean_ledger()

    def test_commands_require_confirmation_and_scrub_accepts_retention(self):
        with self.assertRaises(CommandError):call_command('purge_shop_data',shop_domain=self.shop.domain,stdout=StringIO())
        with self.assertRaises(CommandError):call_command('scrub_webhook_payloads',days=0,stdout=StringIO())
        call_command('scrub_webhook_payloads',days=30,stdout=StringIO())

    def test_bounded_purge_commits_progress_and_can_finish_in_later_tasks(self):
        for index in range(3):self.lot(5,order=1000+index)
        self.event('orders/paid',{'id':1000,'customer':{'id':301}})
        total=privacy.purge_shop_data(self.shop.domain,self.shop.domain,batch_size=1,max_batches=1)
        self.assertGreater(total,0);self.shop.refresh_from_db();self.assertFalse(self.shop.is_active)
        for _ in range(30):
            total+=privacy.purge_shop_data(self.shop.domain,self.shop.domain,batch_size=1,max_batches=1)
            if not Shop.objects.filter(pk=self.shop.pk).exists():break
        self.assertFalse(Shop.objects.filter(pk=self.shop.pk).exists());self.assertGreater(total,3)

    def test_email_only_compliance_matches_one_customer_and_redaction_can_replay(self):
        event=self.event('customers/redact',{'shop_domain':self.shop.domain,'customer':{'email':self.profile.email}})
        HANDLERS[event.topic](event)
        self.profile.refresh_from_db();self.assertTrue(self.profile.is_anonymized)
        # ID-backed redactions are replayable even after the original payload is scrubbed.
        event=self.event('customers/redact',{'shop_domain':self.shop.domain,'customer':{'id':301,'email':'old@example.invalid'}})
        HANDLERS[event.topic](event);event.refresh_from_db();HANDLERS[event.topic](event)
        self.assertTrue(event.payload['redacted']);self.assertEqual(event.payload['shop_domain'],self.shop.domain)

    def test_retention_keeps_retryable_failed_and_scrubs_exhausted_old_failed(self):
        old=self.event('customers/update',{'id':301,'email':self.profile.email},status='FAILED',attempts=5)
        retryable=self.event('customers/update',{'id':301,'email':self.profile.email},status='FAILED',attempts=1)
        WebhookEvent.objects.filter(pk__in=[old.pk,retryable.pk]).update(received_at=self.now-timedelta(days=40))
        privacy.scrub_webhook_payloads(30,self.now)
        old.refresh_from_db();retryable.refresh_from_db()
        self.assertTrue(old.payload['redacted']);self.assertIn('email',retryable.payload)

    @override_settings(SHOPIFY_SHOP_DOMAIN='test.myshopify.com',SHOPIFY_CLIENT_SECRET='privacy-secret')
    def test_signed_compliance_intake_accepts_inactive_shop_and_rejects_bad_hmac(self):
        self.shop.is_active=False;self.shop.save(update_fields=['is_active'])
        payload=json.dumps({'shop_domain':self.shop.domain,'customer':{'id':301,'email':self.profile.email}}).encode()
        signature=base64.b64encode(hmac.new(b'privacy-secret',payload,hashlib.sha256).digest()).decode()
        headers={'HTTP_X_SHOPIFY_SHOP_DOMAIN':self.shop.domain,'HTTP_X_SHOPIFY_TOPIC':'customers/redact','HTTP_X_SHOPIFY_WEBHOOK_ID':'signed-privacy'}
        response=self.client.post('/webhooks/shopify/',payload,content_type='application/json',**headers,HTTP_X_SHOPIFY_HMAC_SHA256=signature)
        self.assertEqual(response.status_code,200)
        response=self.client.post('/webhooks/shopify/',payload,content_type='application/json',**headers,HTTP_X_SHOPIFY_HMAC_SHA256='bad')
        self.assertEqual(response.status_code,401)

    def test_shop_redact_queues_only_authenticated_shop(self):
        event=self.event('shop/redact',{'shop_domain':self.shop.domain})
        with patch('django_q.tasks.async_task') as task,self.captureOnCommitCallbacks(execute=True):
            HANDLERS[event.topic](event)
        task.assert_called_once_with('loyalty.tasks.purge_shop',self.shop.domain)
        event.payload={'shop_domain':'other.myshopify.com'}
        with self.assertRaises(ValueError):HANDLERS[event.topic](event)


def historical_order(now, number=1001):
    raw=json.loads(json.dumps(raw_order(number)).replace('"VND"','"USD"'))
    raw['createdAt']=(now-timedelta(days=2)).isoformat()
    raw['lineItems']['nodes'][0]['originalTotalSet']['shopMoney']['amount']='100.00'
    return raw


def jsonl_orders(orders,stage):
    for raw in orders:
        order=deepcopy(raw);lines=order.pop('lineItems')['nodes'];ships=order.pop('shippingLines')['nodes']
        refunds=order.pop('refunds');order['refunds']=[{'id':r['id']} for r in refunds]
        order['__typename']='Order'
        yield json.dumps(order)
        if stage=='core':
            for line in lines:
                if line.get('product'):
                    collections=line['product'].pop('collections')['nodes']
                    for collection in collections:
                        yield json.dumps({**collection,'__parentId':line['product']['id'],'__typename':'Collection'})
                yield json.dumps({**line,'__parentId':order['id'],'__typename':'LineItem'})
            field,kind='refundLineItems','RefundLineItem'
        else:
            for index,line in enumerate(ships):
                yield json.dumps({**line,'id':f'gid://shopify/ShippingLine/{numeric_tail(order["id"])*10+index}','__parentId':order['id'],'__typename':'ShippingLine'})
            field,kind='refundShippingLines','RefundShippingLine'
        for refund in refunds:
            for index,line in enumerate(refund[field]['nodes']):
                yield json.dumps({**line,'id':f'gid://shopify/{kind}/{numeric_tail(refund["id"])*10+index}','__parentId':refund['id'],'__typename':kind})


def numeric_tail(value):return int(value.rsplit('/',1)[-1])


@override_settings(SHOPIFY_SHOP_DOMAIN='test.myshopify.com')
class ImportTests(PointsSetup, TestCase):
    def setUp(self):
        super().setUp();self.orders=[historical_order(self.now)]
        self.start=patch('shops.bulk_orders.start_operation',side_effect=lambda query:'core' if 'lineItems' in query else 'shipping').start()
        patch('shops.bulk_orders.wait_operation',side_effect=lambda operation:operation).start()
        patch('shops.bulk_orders.download_lines',side_effect=lambda stage:jsonl_orders(self.orders,stage)).start()
        patch('shops.bulk_orders.require_order_scopes').start()
        self.kwargs={'since':(self.now-timedelta(days=10)).date().isoformat(),'until':self.now.date().isoformat(),'now':self.now}

    def run_import(self,**extra):return import_orders(self.shop,**{**self.kwargs,**extra})

    def test_dry_run_writes_nothing_and_uses_streamed_graph(self):
        before=[model.objects.count() for model in [CustomerProfile,PointsLot,PointsTransaction,ImportRun,AuditLog]]
        result=self.run_import()
        self.assertEqual((result['scanned'],result['eligible'],result['points'],result['customers']),(1,1,10,1))
        self.assertEqual(before,[model.objects.count() for model in [CustomerProfile,PointsLot,PointsTransaction,ImportRun,AuditLog]])
        self.assertTrue(result['dry_run']);self.assertEqual(self.start.call_count,2)

    def test_import_available_snapshot_expiry_and_no_per_order_api_or_emails(self):
        with patch('loyalty.services.earning.fetch_order') as fetch,patch('loyalty.services.earning.enqueue_tier_evaluation') as tiers:
            result=self.run_import(dry_run=False,yes=True)
        fetch.assert_not_called();tiers.assert_not_called()
        lot=PointsLot.objects.get();entry=PointsTransaction.objects.get()
        self.assertEqual(lot.status,'AVAILABLE');self.assertEqual(lot.available_at,self.now)
        self.assertEqual(lot.expires_at,self.now+timedelta(days=30));self.assertTrue(entry.rule_snapshot['historical'])
        self.assertEqual(entry.idempotency_key,'earn:1001');self.assertEqual(ImportRun.objects.get().status,'COMPLETED')
        self.assertEqual(result['points'],10);self.assertFalse(NotificationLog.objects.exists());self.clean_ledger()

    def test_pending_and_no_expiry_modes(self):
        self.run_import(dry_run=False,yes=True,activate_now=False,expiry_mode='none')
        lot=PointsLot.objects.get();self.assertEqual(lot.status,'PENDING');self.assertIsNone(lot.expires_at)
        self.assertEqual(lot.available_at,self.now+timedelta(days=7));self.clean_ledger()

    def test_webhook_earn_duplicate_and_skip_matrix(self):
        with patch('loyalty.services.earning.fetch_order',return_value=self.orders[0]):earn_points_for_order(self.shop,1001)
        for number,changes in [(1002,{'test':True}),(1003,{'cancelledAt':self.now.isoformat()}),(1004,{'customer':None}),(1005,{'displayFinancialStatus':'PENDING'})]:
            raw=historical_order(self.now,number);raw.update(changes);self.orders.append(raw)
        result=self.run_import(dry_run=False,yes=True)
        self.assertEqual(result['duplicates'],1);self.assertEqual(result['eligible'],0)
        self.assertEqual(result['skipped'],{'test_order':1,'cancelled_order':1,'guest':1,'financial_status_ineligible':1})
        self.assertEqual(PointsTransaction.objects.count(),1)

    def test_refund_netting_and_shipping_connections_are_kept(self):
        self.config_version(include_shipping=True)
        self.orders[0]['shippingLines']['nodes'][0]['discountedPriceSet']['shopMoney']['amount']='20.00'
        self.orders[0]['refunds']=[json.loads(json.dumps(raw_refund(subtotal='30.00',shipping='10.00')).replace('"VND"','"USD"'))]
        self.orders[0]['displayFinancialStatus']='PARTIALLY_REFUNDED'
        result=self.run_import(dry_run=False,yes=True)
        self.assertEqual(result['points'],8);self.clean_ledger()

    def test_resume_after_failure_is_atomic_with_checkpoint(self):
        self.orders.append(historical_order(self.now,1002))
        original=__import__('loyalty.services.earning',fromlist=['earn_points_from_snapshot']).earn_points_from_snapshot
        def fail(shop,order,**kwargs):
            if order.order_id==1002:raise ShopifyError('safe failure')
            return original(shop,order,**kwargs)
        with patch('loyalty.services.importing.earn_points_from_snapshot',side_effect=fail):
            with self.assertRaises(ShopifyError):self.run_import(dry_run=False,yes=True)
        run=ImportRun.objects.get();self.assertEqual((run.status,run.last_order_id),('FAILED',1001))
        self.assertEqual(PointsTransaction.objects.count(),1)
        result=self.run_import(dry_run=False,yes=True,resume=run.pk)
        self.assertEqual((result['scanned'],result['points']),(2,20));self.assertEqual(PointsTransaction.objects.count(),2)
        self.clean_ledger()

    def test_limit_can_resume_and_changes_to_original_options_are_rejected(self):
        self.orders.append(historical_order(self.now,1002));self.run_import(dry_run=False,yes=True,limit=1)
        run=ImportRun.objects.get();self.assertEqual(run.status,'LIMITED')
        with self.assertRaises(LoyaltyError):self.run_import(dry_run=False,yes=True,resume=run.pk,expiry_mode='none')
        self.run_import(dry_run=False,yes=True,resume=run.pk,limit=1)
        run.refresh_from_db();self.assertEqual(run.status,'COMPLETED');self.assertEqual(run.counts['scanned'],2)

    def test_expired_result_restarts_queries_without_regranting(self):
        with patch('shops.bulk_orders.wait_operation',side_effect=[bulk_orders.ResultExpired('expired'),'core','shipping']):
            result=self.run_import(dry_run=False,yes=True)
        self.assertEqual(result['eligible'],1);self.assertEqual(self.start.call_count,3)

    def test_historical_tier_evaluated_at_end_without_notification(self):
        self.config_version(tiers_enabled=True,welcome_points=50)
        Tier.objects.create(shop=self.shop,name='Silver',rank=1,min_qualifying_points=10)
        self.run_import(dry_run=False,yes=True)
        self.profile.refresh_from_db();self.assertEqual(self.profile.tier.name,'Silver')
        self.assertEqual(TierHistory.objects.count(),1);self.assertFalse(NotificationLog.objects.exists())
        self.assertEqual(PointsTransaction.objects.count(),1)

    def test_huge_window_requires_yes_and_command_defaults_to_dry_run(self):
        with self.assertRaises(LoyaltyError):self.run_import(since='2000-01-01')
        out=StringIO();call_command('import_orders',since=self.kwargs['since'],until=self.kwargs['until'],stdout=out)
        self.assertTrue(json.loads(out.getvalue().strip())['dry_run']);self.assertFalse(ImportRun.objects.exists())

    def test_graph_reassembles_collections_even_when_children_precede_parents(self):
        self.orders[0]['lineItems']['nodes'][0]['product']['collections']['nodes']=[{'id':'gid://shopify/Collection/99'}]
        with bulk_orders.graph() as graph:
            graph.ingest(reversed(list(jsonl_orders(self.orders,'core'))),'core')
            graph.ingest(jsonl_orders(self.orders,'shipping'),'shipping')
            order=next(graph.orders());snapshot=map_order_snapshot(order)
            self.assertEqual(snapshot.lines[0].collection_ids,[99])

    def test_large_jsonl_is_consumed_as_iterator_and_uses_disk(self):
        with bulk_orders.graph() as graph:
            graph.ingest((json.dumps({'id':f'gid://shopify/Order/{i}','__typename':'Order'}) for i in range(1,5001)),'core')
            self.assertEqual(graph.db.execute('SELECT count(*) FROM nodes').fetchone()[0],5000)
            self.assertTrue(graph.db.execute('PRAGMA database_list').fetchone()[2])

    def test_failed_bulk_can_resume_with_fresh_operation_ids(self):
        with patch('shops.bulk_orders.wait_operation',side_effect=bulk_orders.OperationFailed('BULK_FAILED')):
            with self.assertRaises(ShopifyError):self.run_import(dry_run=False,yes=True)
        run=ImportRun.objects.get();self.assertEqual(run.bulk_operation_ids,{})
        result=self.run_import(dry_run=False,yes=True,resume=run.pk)
        self.assertEqual(result['eligible'],1)

    def test_historical_pending_activation_does_not_create_email(self):
        self.run_import(dry_run=False,yes=True,activate_now=False)
        from loyalty.services.lifecycle import activate_due_lots
        activate_due_lots(self.now+timedelta(days=8))
        self.assertFalse(NotificationLog.objects.exists());self.clean_ledger()

    def test_resume_freezes_program_configuration(self):
        self.orders.append(historical_order(self.now,1002))
        self.run_import(dry_run=False,yes=True,limit=1);run=ImportRun.objects.get()
        self.config_version(spend_per_point=1)
        result=self.run_import(dry_run=False,yes=True,resume=run.pk)
        self.assertEqual(result['points'],20)
        self.assertEqual(result['customers'],1)

    def test_mismatched_bulk_snapshots_fail_before_grant(self):
        def lines(stage):
            orders=deepcopy(self.orders)
            orders[0]['updatedAt']='2026-10-01T00:00:00Z' if stage=='core' else '2026-10-02T00:00:00Z'
            return jsonl_orders(orders,stage)
        with patch('shops.bulk_orders.download_lines',side_effect=lines):
            with self.assertRaisesMessage(ShopifyError,'BULK_STAGES_INCONSISTENT'):self.run_import(dry_run=False,yes=True)
        self.assertFalse(PointsTransaction.objects.exists());self.assertEqual(ImportRun.objects.get().bulk_operation_ids,{})


@override_settings(**AUTH_SETTINGS)
class PrivacyAPITests(PointsSetup, APITestCase):
    def setUp(self):
        super().setUp();self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {session_token()}')

    def test_export_and_anonymize_are_shop_scoped_and_audited(self):
        self.lot(10)
        response=self.client.get(f'/api/admin/customers/{self.profile.pk}/data-export')
        self.assertEqual(response.status_code,200);self.assertIn('attachment',response['Content-Disposition'])
        self.assertEqual(response.json()['profile']['shopify_customer_id'],301)
        other=Shop.objects.create(domain='other.myshopify.com',currency='USD')
        profile=CustomerProfile.objects.create(shop=other,shopify_customer_id=301)
        self.assertEqual(self.client.get(f'/api/admin/customers/{profile.pk}/data-export').status_code,404)
        self.assertEqual(self.client.post(f'/api/admin/customers/{self.profile.pk}/anonymize',{'confirm':False},format='json').status_code,400)
        response=self.client.post(f'/api/admin/customers/{self.profile.pk}/anonymize',{'confirm':True},format='json')
        self.assertEqual(response.status_code,200);self.assertTrue(response.data['is_anonymized'])
        self.assertEqual(set(AuditLog.objects.values_list('action',flat=True)),{'customer.anonymize','customer.data_export'})

    def test_import_history_is_read_only_and_excludes_other_shop(self):
        ImportRun.objects.create(shop=self.shop,options={'since':'2026-01-01'},counts={'scanned':2})
        other=Shop.objects.create(domain='other.myshopify.com',currency='USD')
        ImportRun.objects.create(shop=other)
        response=self.client.get('/api/admin/import-runs')
        self.assertEqual(response.status_code,200);self.assertEqual(response.data['count'],1)
        self.assertEqual(self.client.post('/api/admin/import-runs',{},format='json').status_code,405)


class BulkTransportTests(TestCase):
    def test_busy_and_failed_partial_results_are_not_imported(self):
        with patch('shops.bulk_orders.graphql',return_value={'currentBulkOperation':{'id':'op','status':'RUNNING'}}):
            with self.assertRaises(ShopifyError):bulk_orders.start_operation('{}')
        with patch('shops.bulk_orders.graphql',return_value={'currentBulkOperation':{'id':'op','status':'FAILED','partialDataUrl':'private-url'}}):
            with self.assertRaisesMessage(ShopifyError,'BULK_FAILED'):bulk_orders.wait_operation('op')

    def test_poll_uses_specific_id_when_another_operation_is_current(self):
        with patch('shops.bulk_orders.graphql',side_effect=[{'currentBulkOperation':{'id':'other','status':'COMPLETED'}},{'node':{'id':'wanted','status':'COMPLETED','url':'result'}}]) as api:
            self.assertEqual(bulk_orders.wait_operation('wanted'),'result')
            self.assertEqual(api.call_args.args[1],{'id':'wanted'})

    def test_old_orders_need_read_all_orders(self):
        from django.utils import timezone
        with patch('shops.bulk_orders.graphql',return_value={'currentAppInstallation':{'accessScopes':[{'handle':'read_orders'}]}}):
            with self.assertRaisesMessage(ShopifyError,'READ_ALL_ORDERS_REQUIRED'):bulk_orders.require_order_scopes('2000-01-01',timezone.now())

    def test_download_rejects_arbitrary_host_and_suppresses_signed_url_errors(self):
        for url in ['http://storage.googleapis.com/file','https://evil.example/file','https://user:secret@cdn.shopify.com/file']:
            with self.assertRaisesMessage(ShopifyError,'BULK_RESULT_HOST_REJECTED'):list(bulk_orders.download_lines(url))

    def test_bulk_queries_stay_under_five_connections(self):
        for stage in ['core','shipping']:
            query=bulk_orders.bulk_query('2026-01-01','2026-02-01',stage)
            self.assertLessEqual(query.count('edges {'),5)
            self.assertIn('sortKey: ID',query)

    def test_older_active_bulk_is_detected_even_when_current_is_completed(self):
        with patch('shops.bulk_orders.graphql',side_effect=[{'currentBulkOperation':{'id':'newer','status':'COMPLETED'}},
            {'bulkOperations':{'nodes':[{'id':'older','status':'RUNNING'}]}}]):
            with self.assertRaisesMessage(ShopifyError,'BULK_BUSY'):bulk_orders.start_operation('{}')

    def test_start_bulk_submits_query_only_when_no_operation_is_active(self):
        with patch('shops.bulk_orders.graphql',side_effect=[{'currentBulkOperation':None},{'bulkOperations':{'nodes':[]}},
            {'bulkOperationRunQuery':{'bulkOperation':{'id':'new','status':'CREATED'},'userErrors':[]}}]) as call:
            self.assertEqual(bulk_orders.start_operation('{ orders { edges { node { id } } } }'),'new')
            self.assertIn('orders',call.call_args.args[1]['query'])

    def test_expired_download_uses_stream_and_never_exposes_signed_url(self):
        from unittest.mock import MagicMock
        response=MagicMock();response.status_code=403
        manager=MagicMock();manager.__enter__.return_value=response
        with patch('shops.bulk_orders.httpx.stream',return_value=manager):
            with self.assertRaisesMessage(bulk_orders.ResultExpired,'BULK_RESULT_EXPIRED'):
                list(bulk_orders.download_lines('https://storage.googleapis.com/result?secret=do-not-log'))

    def test_invalid_jsonl_and_missing_stage_are_rejected(self):
        with bulk_orders.graph() as graph:
            with self.assertRaisesMessage(ShopifyError,'INVALID_BULK_JSONL'):graph.ingest(['not-json'],'core')
            graph.ingest([json.dumps({'id':'gid://shopify/Order/1','__typename':'Order'})],'core')
            with self.assertRaisesMessage(ShopifyError,'BULK_STAGES_INCONSISTENT'):list(graph.orders())


class PrivacyImportConcurrencyTests(PointsSetup, TransactionTestCase):
    def test_import_lock_excludes_second_connection_for_same_shop(self):
        from concurrent.futures import ThreadPoolExecutor
        from django.db import close_old_connections
        from loyalty.services.importing import import_lock
        def attempt():
            close_old_connections()
            try:
                with import_lock(self.shop):return 'unexpected'
            except LoyaltyError as error:return error.code
            finally:connection.close()
        with import_lock(self.shop),ThreadPoolExecutor(max_workers=1) as pool:
            self.assertEqual(pool.submit(attempt).result(timeout=10),'IMPORT_BUSY')
        with import_lock(self.shop):pass

    def test_import_and_webhook_wallet_path_cannot_double_earn(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        from django.db import close_old_connections
        from loyalty.services.earning import earn_points_from_snapshot
        order=map_order_snapshot(historical_order(self.now));barrier=Barrier(2)
        def earn(historical):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                return earn_points_from_snapshot(self.shop,order,historical=historical).status
            finally:connection.close()
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures=[pool.submit(earn,historical) for historical in [True,False]]
            statuses=[f.result(timeout=15) for f in futures]
        self.assertEqual(sorted(statuses),['DUPLICATE','EARNED'])
        self.assertEqual(PointsTransaction.objects.count(),1);self.clean_ledger()

    def test_anonymization_and_customer_sync_never_restore_pii(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        from django.db import close_old_connections
        from loyalty.services.customers import upsert_customer_profile
        barrier=Barrier(2)
        def work(anonymize):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                if anonymize:privacy.anonymize_customer(self.profile,'system')
                else:upsert_customer_profile(self.shop,301,email='concurrent@example.invalid',first_name='Alice')
            finally:connection.close()
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures=[pool.submit(work,value) for value in [True,False]]
            for future in futures:future.result(timeout=15)
        self.profile.refresh_from_db();self.assertTrue(self.profile.is_anonymized)
        self.assertEqual(self.profile.email,'');self.assertEqual(self.profile.first_name,'')
