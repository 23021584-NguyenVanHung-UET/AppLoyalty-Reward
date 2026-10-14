from django.core.management.base import BaseCommand, CommandError
from loyalty.services.referrals import referral_batch
from loyalty.services.timing import parse_now, resolve_now


class Command(BaseCommand):
    help = "Verify and grant pending referrals using frozen entitlements."
    def add_arguments(self, parser):
        parser.add_argument("--as-of")
    def handle(self, *args, **options):
        try:
            now = resolve_now(parse_now(options["as_of"]))
        except ValueError as exc:
            raise CommandError(str(exc)) from None
        cursor, counts = 0, {}
        while True:
            batch = referral_batch(now, after_id=cursor)
            for key, value in batch["counts"].items():
                counts[key] = counts.get(key, 0)+value
            if not batch["has_more"]:
                break
            cursor = batch["next_after_id"]
        self.stdout.write("Referral results: " + ", ".join(f"{key}={value}" for key, value in counts.items()))
