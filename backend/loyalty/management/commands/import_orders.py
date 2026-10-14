import json
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from loyalty.services.errors import LoyaltyError
from loyalty.services.importing import import_orders
from shops.models import Shop
from shops.shopify import ShopifyError


class Command(BaseCommand):
    help = "Bulk import historical orders. Dry-run by default; --yes writes points."

    def add_arguments(self, parser):
        parser.add_argument("--since")
        parser.add_argument("--until")
        timing = parser.add_mutually_exclusive_group()
        timing.add_argument("--activate-now", action="store_true")
        timing.add_argument("--keep-pending", action="store_true")
        parser.add_argument("--expiry-mode", choices=["from_now", "none"], default="from_now")
        parser.add_argument("--limit", type=int, default=10000)
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--yes", action="store_true")
        parser.add_argument("--resume", type=int)

    def handle(self, *args, **options):
        shop = Shop.objects.filter(domain=settings.SHOPIFY_SHOP_DOMAIN, is_active=True).first()
        if not shop:
            raise CommandError("Configured shop not found or inactive.")
        if not options["since"]:
            raise CommandError("--since is required, including when resuming.")
        try:
            result = import_orders(shop, since=options["since"], until=options["until"],
                activate_now=not options["keep_pending"], expiry_mode=options["expiry_mode"], limit=options["limit"],
                dry_run=options["dry_run"] or not options["yes"], yes=options["yes"], resume=options["resume"],
                progress=lambda counts: self.stdout.write(json.dumps(counts)))
        except (LoyaltyError, ShopifyError) as exc:
            # No URLs, order payloads or remote error messages in command output.
            safe_codes = {"READ_ALL_ORDERS_REQUIRED", "BULK_BUSY", "BULK_POLL_TIMEOUT", "BULK_QUERY_REJECTED", "BULK_FAILED",
                "BULK_CANCELED", "BULK_EXPIRED", "BULK_RESULT_EXPIRED", "BULK_OPERATION_EXPIRED", "BULK_STAGES_INCONSISTENT",
                "BULK_DOWNLOAD_FAILED", "BULK_ORDER_OUTSIDE_WINDOW", "INVALID_BULK_JSONL"}
            code = exc.code if isinstance(exc, LoyaltyError) else str(exc) if str(exc) in safe_codes else "IMPORT_FAILED"
            raise CommandError(code + ": retry with --resume for a recorded run; see docs/step12b-privacy-import.md.") from None
        self.stdout.write(json.dumps(result, ensure_ascii=False))
