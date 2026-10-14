from django.core.management.base import BaseCommand, CommandError
from loyalty.services.tiers import evaluate_all
from loyalty.services.timing import parse_now


class Command(BaseCommand):
    help = "Evaluate membership tiers; downgrades obey the configured grace period."

    def add_arguments(self, parser):
        parser.add_argument("--as-of")
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--limit", type=int, default=500)
        parser.add_argument("--after-id", type=int, default=0)

    def handle(self, *args, **options):
        try:
            result = evaluate_all(parse_now(options["as_of"]), options["limit"],
                dry_run=options["dry_run"], after_id=options["after_id"])
            self.stdout.write(str(result))
        except ValueError as error:
            raise CommandError(str(error)) from None
