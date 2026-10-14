from django.db import migrations


HISTORY_TABLES = (
    "loyalty_programconfig",
    "loyalty_pointtransaction",
    "loyalty_pointallocation",
)


def protect_history(apps, schema_editor):
    vendor = schema_editor.connection.vendor
    quote = schema_editor.quote_name
    if vendor == "postgresql":
        schema_editor.execute(
            """
            CREATE FUNCTION loyalty_reject_history_mutation() RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION 'Loyalty history is append-only';
            END;
            $$ LANGUAGE plpgsql;
            """
        )
        for table in HISTORY_TABLES:
            schema_editor.execute(
                f"CREATE TRIGGER {quote(table + '_immutable')} "
                f"BEFORE UPDATE OR DELETE ON {quote(table)} "
                "FOR EACH ROW EXECUTE FUNCTION loyalty_reject_history_mutation()"
            )
    elif vendor == "sqlite":
        for table in HISTORY_TABLES:
            for action in ("UPDATE", "DELETE"):
                schema_editor.execute(
                    f"CREATE TRIGGER {quote(table + '_no_' + action.lower())} "
                    f"BEFORE {action} ON {quote(table)} BEGIN "
                    "SELECT RAISE(ABORT, 'Loyalty history is append-only'); END"
                )
    else:
        raise NotImplementedError("History protection requires PostgreSQL or SQLite.")


def unprotect_history(apps, schema_editor):
    vendor = schema_editor.connection.vendor
    quote = schema_editor.quote_name
    if vendor == "postgresql":
        for table in HISTORY_TABLES:
            schema_editor.execute(
                f"DROP TRIGGER IF EXISTS {quote(table + '_immutable')} ON {quote(table)}"
            )
        schema_editor.execute("DROP FUNCTION IF EXISTS loyalty_reject_history_mutation()")
    elif vendor == "sqlite":
        for table in HISTORY_TABLES:
            for action in ("update", "delete"):
                schema_editor.execute(
                    f"DROP TRIGGER IF EXISTS {quote(table + '_no_' + action)}"
                )


class Migration(migrations.Migration):
    dependencies = [
        ("loyalty", "0002_pointtransaction_pointlot_pointallocation_and_more"),
    ]

    operations = [migrations.RunPython(protect_history, unprotect_history)]
