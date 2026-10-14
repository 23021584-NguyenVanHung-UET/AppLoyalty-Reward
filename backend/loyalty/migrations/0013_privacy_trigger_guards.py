from django.db import migrations


SQL = """
CREATE OR REPLACE FUNCTION loyalty_prevent_immutable_mutation() RETURNS trigger AS $$
DECLARE owner_shop bigint;
BEGIN
    -- A scoped purge may DELETE, never UPDATE. Inspect OLD while parent rows exist.
    IF TG_OP = 'DELETE' AND current_setting('loyalty.purge_shop', true) ~ '^[0-9]+$' THEN
        IF TG_TABLE_NAME IN ('loyalty_auditlog','loyalty_earnrule','loyalty_campaign','loyalty_tier') THEN
            owner_shop := (to_jsonb(OLD)->>'shop_id')::bigint;
        ELSIF TG_TABLE_NAME IN ('loyalty_pointstransaction','loyalty_tierhistory') THEN
            SELECT shop_id INTO owner_shop FROM loyalty_customerprofile WHERE id = OLD.profile_id;
        ELSIF TG_TABLE_NAME = 'loyalty_earningrevision' THEN
            SELECT shop_id INTO owner_shop FROM loyalty_earnrule WHERE id = OLD.rule_id;
            IF owner_shop IS NULL THEN
                SELECT shop_id INTO owner_shop FROM loyalty_campaign WHERE id = OLD.campaign_id;
            END IF;
        END IF;
        IF owner_shop::text = current_setting('loyalty.purge_shop', true) THEN RETURN OLD; END IF;
    END IF;
    IF TG_OP = 'UPDATE' AND TG_TABLE_NAME = 'loyalty_pointstransaction'
       AND current_setting('loyalty.redact_profile', true) = (to_jsonb(OLD)->>'profile_id')
       AND (to_jsonb(NEW)->>'note') = '[redacted]'
       AND (to_jsonb(NEW) - 'note') = (to_jsonb(OLD) - 'note') THEN
        RETURN NEW;
    END IF;
    IF TG_OP = 'UPDATE' AND TG_TABLE_NAME = 'loyalty_auditlog'
       AND current_setting('loyalty.redact_audit_shop', true) = (to_jsonb(OLD)->>'shop_id')
       AND (to_jsonb(NEW) - 'before' - 'after') = (to_jsonb(OLD) - 'before' - 'after') THEN
        RETURN NEW;
    END IF;
    RAISE EXCEPTION 'Loyalty history is append-only';
END;
$$ LANGUAGE plpgsql;
"""
REVERSE = """
CREATE OR REPLACE FUNCTION loyalty_prevent_immutable_mutation() RETURNS trigger AS $$
BEGIN RAISE EXCEPTION 'Loyalty history is append-only'; END;
$$ LANGUAGE plpgsql;
"""


class Migration(migrations.Migration):
    dependencies = [("loyalty", "0012_privacy_import")]
    operations = [migrations.RunSQL(SQL, reverse_sql=REVERSE)]
