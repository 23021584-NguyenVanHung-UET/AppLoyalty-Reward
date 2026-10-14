from django.core.management.base import BaseCommand, CommandError

from loyalty.services.redemption_jobs import process_redemptions_services, redemption_plan, redemption_summary
from loyalty.services.timing import parse_now


class Command(BaseCommand):
    help = "Thử lại mã chờ và xử lý mã hết hạn."

    def add_arguments(self, parser):
        parser.add_argument("--as-of", default=None)
        parser.add_argument("--limit", type=int, default=200)
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        try:
            now = parse_now(options["as_of"])
            if options["dry_run"]:
                plan = redemption_plan(now, options["limit"])
                self.stdout.write(f"DRY_RUN: Chạy thử {len(plan)} lượt; không gọi Shopify, không ghi dữ liệu.")
                for row_id, action in plan:
                    labels = {"retry": "thử phát hành lại", "reconcile": "đối chiếu trước khi kết thúc",
                              "expire": "vô hiệu mã hết hạn và xử lý hoàn điểm"}
                    self.stdout.write(f"{action.upper()}: Lượt đổi {row_id}: {labels[action]}.")
            else:
                self.stdout.write(redemption_summary(process_redemptions_services(now, options["limit"])))
        except ValueError as exc:
            raise CommandError(str(exc)) from exc

