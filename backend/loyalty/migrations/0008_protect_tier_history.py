from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("loyalty", "0007_earning_rules_and_tiers")]
    operations = [migrations.RunSQL("""
        CREATE TRIGGER loyalty_tierhistory_immutable BEFORE UPDATE OR DELETE ON loyalty_tierhistory
        FOR EACH ROW EXECUTE FUNCTION loyalty_prevent_immutable_mutation();
        CREATE TRIGGER loyalty_earningrevision_immutable BEFORE UPDATE OR DELETE ON loyalty_earningrevision
        FOR EACH ROW EXECUTE FUNCTION loyalty_prevent_immutable_mutation();
        CREATE TRIGGER loyalty_earnrule_nodelete BEFORE DELETE ON loyalty_earnrule
        FOR EACH ROW EXECUTE FUNCTION loyalty_prevent_immutable_mutation();
        CREATE TRIGGER loyalty_campaign_nodelete BEFORE DELETE ON loyalty_campaign
        FOR EACH ROW EXECUTE FUNCTION loyalty_prevent_immutable_mutation();
        CREATE TRIGGER loyalty_tier_nodelete BEFORE DELETE ON loyalty_tier
        FOR EACH ROW EXECUTE FUNCTION loyalty_prevent_immutable_mutation();
        """, reverse_sql="""
        DROP TRIGGER IF EXISTS loyalty_tierhistory_immutable ON loyalty_tierhistory;
        DROP TRIGGER IF EXISTS loyalty_earningrevision_immutable ON loyalty_earningrevision;
        DROP TRIGGER IF EXISTS loyalty_earnrule_nodelete ON loyalty_earnrule;
        DROP TRIGGER IF EXISTS loyalty_campaign_nodelete ON loyalty_campaign;
        DROP TRIGGER IF EXISTS loyalty_tier_nodelete ON loyalty_tier;
        """)]
