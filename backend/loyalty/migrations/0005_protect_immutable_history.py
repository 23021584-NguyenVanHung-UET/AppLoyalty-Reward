from django.db import migrations


FORWARD_SQL = """
CREATE FUNCTION loyalty_prevent_immutable_mutation() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'Loyalty history is append-only';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER loyalty_pointstransaction_immutable
BEFORE UPDATE OR DELETE ON loyalty_pointstransaction
FOR EACH ROW EXECUTE FUNCTION loyalty_prevent_immutable_mutation();

CREATE TRIGGER loyalty_auditlog_immutable
BEFORE UPDATE OR DELETE ON loyalty_auditlog
FOR EACH ROW EXECUTE FUNCTION loyalty_prevent_immutable_mutation();
"""

REVERSE_SQL = """
DROP TRIGGER IF EXISTS loyalty_pointstransaction_immutable ON loyalty_pointstransaction;
DROP TRIGGER IF EXISTS loyalty_auditlog_immutable ON loyalty_auditlog;
DROP FUNCTION IF EXISTS loyalty_prevent_immutable_mutation();
"""


class Migration(migrations.Migration):
    dependencies = [("loyalty", "0004_data_model")]

    operations = [migrations.RunSQL(FORWARD_SQL, reverse_sql=REVERSE_SQL)]
