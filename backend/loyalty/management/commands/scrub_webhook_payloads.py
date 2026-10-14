from django.core.management.base import BaseCommand, CommandError
from loyalty.services.privacy import scrub_webhook_payloads


class Command(BaseCommand):
    help = "Remove personal payload data from terminal events older than retention."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int)

    def handle(self, *args, **options):
        total, after = 0, 0
        while True:
            try:
                result = scrub_webhook_payloads(options["days"], after_id=after)
            except ValueError as exc:
                raise CommandError(str(exc)) from None
            total += result["processed"]
            after = result["next_after_id"]
            if not result["more"]:
                break
        self.stdout.write(f"Scrubbed {total} webhook payloads.")
