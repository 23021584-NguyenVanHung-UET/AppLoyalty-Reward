from django.urls import re_path

from . import admin, proxy, earning, reports, notifications, engagement, privacy
from loyalty.models import EarnRule, Campaign, Tier
from .not_found import AdminNotFound, ProxyNotFound

admin_patterns = [
    re_path(r"^import-runs/?$", privacy.Imports.as_view()),
    re_path(r"^customers/(?P<pk>[0-9]+)/data-export/?$", privacy.CustomerExport.as_view()),
    re_path(r"^customers/(?P<pk>[0-9]+)/anonymize/?$", privacy.Anonymize.as_view()),
    re_path(r"^referrals/?$", engagement.Referrals.as_view()),
    re_path(r"^referrals/(?P<pk>[0-9]+)/reject/?$", engagement.RejectReferral.as_view()),
    re_path(r"^customers/(?P<pk>[0-9]+)/birthday/?$", engagement.AdminBirthday.as_view()),
    re_path(r"^notifications/settings/?$", notifications.Settings.as_view()),
    re_path(r"^notifications/log/?$", notifications.Logs.as_view()),
    re_path(r"^notifications/test/?$", notifications.TestEmail.as_view()),
    re_path(r"^reports/export/(?P<kind>[a-z-]+)/?$", reports.Export.as_view()),
    re_path(r"^reports/(?P<kind>[a-z-]+)/?$", reports.Report.as_view()),
    re_path(r"^customers/(?P<pk>[0-9]+)/tier-history/?$", earning.CustomerTierHistory.as_view()),
    re_path(r"^me/?$", admin.Me.as_view()),
    re_path(r"^dashboard/?$", admin.Dashboard.as_view()),
    re_path(r"^customers/?$", admin.Customers.as_view()),
    re_path(r"^customers/(?P<pk>[0-9]+)/?$", admin.CustomerDetail.as_view()),
    re_path(r"^customers/(?P<pk>[0-9]+)/lots/?$", admin.CustomerLots.as_view()),
    re_path(r"^customers/(?P<pk>[0-9]+)/ledger/?$", admin.CustomerLedger.as_view()),
    re_path(r"^customers/(?P<pk>[0-9]+)/block/?$", admin.CustomerBlock.as_view(), {"blocked": True}),
    re_path(r"^customers/(?P<pk>[0-9]+)/unblock/?$", admin.CustomerBlock.as_view(), {"blocked": False}),
    re_path(r"^customers/(?P<pk>[0-9]+)/adjust/?$", admin.CustomerAdjust.as_view()),
    re_path(r"^rewards/?$", admin.Rewards.as_view()),
    re_path(r"^rewards/(?P<pk>[0-9]+)/?$", admin.RewardDetail.as_view()),
    re_path(r"^rewards/(?P<pk>[0-9]+)/activate/?$", admin.RewardActive.as_view(), {"active": True}),
    re_path(r"^rewards/(?P<pk>[0-9]+)/deactivate/?$", admin.RewardActive.as_view(), {"active": False}),
    re_path(r"^program-config/?$", admin.Config.as_view()),
    re_path(r"^program-config/history/?$", admin.ConfigHistory.as_view()),
    re_path(r"^program-config/(?P<pk>[0-9]+)/?$", admin.ConfigDelete.as_view()),
    re_path(r"^redemptions/?$", admin.Redemptions.as_view()),
    re_path(r"^redemptions/(?P<pk>[0-9]+)/?$", admin.RedemptionDetail.as_view()),
    re_path(r"^redemptions/(?P<pk>[0-9]+)/cancel/?$", admin.RedemptionCancel.as_view()),
    re_path(r"^webhook-events/?$", admin.WebhookEvents.as_view()),
    re_path(r"^webhook-events/(?P<pk>[0-9]+)/?$", admin.WebhookDetail.as_view()),
    re_path(r"^webhook-events/(?P<pk>[0-9]+)/retry/?$", admin.WebhookRetry.as_view()),
    re_path(r"^integrity/?$", admin.Integrity.as_view()),
    re_path(r"^audit-log/?$", admin.Audit.as_view()),
    re_path(r"^.*$", AdminNotFound.as_view()),
]
for prefix, model, input_type in [("earn-rules", EarnRule, earning.RuleInput), ("campaigns", Campaign, earning.CampaignInput), ("tiers", Tier, earning.TierInput)]:
    listing, detail, active = earning.definition_views(model, input_type)
    admin_patterns[0:0] = [re_path(rf"^{prefix}/?$", listing),
        re_path(rf"^{prefix}/(?P<pk>[0-9]+)/?$", detail),
        re_path(rf"^{prefix}/(?P<pk>[0-9]+)/activate/?$", active, {"active": True}),
        re_path(rf"^{prefix}/(?P<pk>[0-9]+)/deactivate/?$", active, {"active": False})]
proxy_patterns = [
    re_path(r"^profile/birthday/?$", engagement.Birthday.as_view()),
    re_path(r"^referral/code/?$", engagement.Code.as_view()),
    re_path(r"^referral/?$", engagement.MyReferrals.as_view()),
    re_path(r"^program/?$", proxy.Program.as_view()),
    re_path(r"^me/?$", proxy.Me.as_view()),
    re_path(r"^rewards/?$", proxy.Rewards.as_view()),
    re_path(r"^redeem/?$", proxy.Redeem.as_view()),
    re_path(r"^redemptions/?$", proxy.Redemptions.as_view()),
    re_path(r"^.*$", ProxyNotFound.as_view()),
]
