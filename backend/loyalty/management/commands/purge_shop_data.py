from django.core.management.base import BaseCommand, CommandError
from loyalty.services.privacy import purge_shop_data
from loyalty.services.errors import LoyaltyError


class Command(BaseCommand):
    help = "Irreversibly purge one shop. Requires --yes and exact domain confirmation."

    def add_arguments(self, parser):
        parser.add_argument("--shop-domain", required=True)
        parser.add_argument("--yes", action="store_true")
        parser.add_argument("--confirm-domain", help="Noninteractive equivalent of typing the exact domain.")

    def handle(self, *args, **options):
        if not options["yes"]:
            raise CommandError("Pass --yes and confirm the exact shop domain.")
        domain = options["shop_domain"]
        confirmation = options["confirm_domain"]
        if confirmation is None:
            confirmation = input("Type the exact shop domain to permanently purge: ").strip()
        try:
            total = purge_shop_data(domain, confirmation)
        except LoyaltyError as exc:
            raise CommandError(exc.code) from None
        self.stdout.write(f"Purged {total} records.")
