from django.core.management.base import BaseCommand, CommandError

from loyalty.services.lifecycle import lifecycle_plan, lifecycle_summary, run_lifecycle_services
from loyalty.services.timing import parse_now


class Command(BaseCommand):
    help = "Kích hoạt và hết hạn lô điểm theo thời gian chỉ định."

    def add_arguments(self, parser):
        parser.add_argument("--as-of", help="Thời gian ISO 8601 có múi giờ.")
        parser.add_argument("--limit", type=int, default=500)
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        try:
            now = parse_now(options["as_of"])
            if options["dry_run"]:
                plan = lifecycle_plan(now, options["limit"])
                for lot_id, action in plan:
                    self.stdout.write(f"Lô {lot_id}: {'kích hoạt' if action == 'activate' else 'hết hạn'}")
                self.stdout.write(f"Dự kiến thay đổi {len(plan)} lô; không ghi dữ liệu.")
            else:
                self.stdout.write(lifecycle_summary(run_lifecycle_services(now, options["limit"])))
        except ValueError as error:
            raise CommandError(str(error)) from None
