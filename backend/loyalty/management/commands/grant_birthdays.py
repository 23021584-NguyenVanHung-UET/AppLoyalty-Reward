from django.core.management.base import BaseCommand, CommandError
from loyalty.services.engagement import birthday_batch
from loyalty.services.timing import parse_now, resolve_now


class Command(BaseCommand):
    help = "Grant due birthday bonuses, catching up at most 7 days."
    def add_arguments(self, parser):
        parser.add_argument("--as-of")
    def handle(self, *args, **options):
        try:
            now = resolve_now(parse_now(options["as_of"]))
        except ValueError as exc:
            raise CommandError(str(exc)) from None
        cursor, granted = 0, 0
        while True:
            batch = birthday_batch(now, after_id=cursor)
            granted += batch["granted"]
            if not batch["has_more"]:
                break
            cursor = batch["next_after_id"]
        self.stdout.write(f"Birthday grants created: {granted}")
