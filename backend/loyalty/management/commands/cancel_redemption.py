from django.core.management.base import BaseCommand, CommandError

from loyalty.models import Redemption
from loyalty.services.redemption_jobs import close_issued
from loyalty.services.timing import parse_now
from shops.shopify import ShopifyError


class Command(BaseCommand):
    help = "Hủy mã ISSUED và hoàn điểm; mặc định chỉ xem kế hoạch."

    def add_arguments(self, parser):
        parser.add_argument("--id", type=int, required=True)
        parser.add_argument("--yes", action="store_true")
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--as-of", default=None)

    def handle(self, *args, **options):
        try:
            now = parse_now(options["as_of"])
            row = Redemption.objects.get(pk=options["id"])
            if row.status != "ISSUED":
                raise CommandError("REDEMPTION_NOT_ISSUED: Chỉ hủy được mã đang ở trạng thái ISSUED.")
            if options["dry_run"] or not options["yes"]:
                self.stdout.write(f"DRY_RUN: Sẽ vô hiệu mã và hoàn điểm cho lượt đổi {row.pk}; dùng --yes để thực hiện.")
                return
            result = close_issued(row.pk, "CANCELLED", now)
            messages = {"cancelled": "Đã hủy mã và hoàn điểm", "deferred": "Đang chờ xử lý webhook thanh toán",
                        "skipped": "Trạng thái đã thay đổi; bỏ qua hủy mã"}
            self.stdout.write(f"{result.upper()}: Lượt đổi {row.pk}: {messages[result]}.")
        except Redemption.DoesNotExist as exc:
            raise CommandError("REDEMPTION_NOT_FOUND: Không tìm thấy lượt đổi thưởng.") from exc
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
        except ShopifyError as exc:
            raise CommandError("DEACTIVATE_UNCONFIRMED: Chưa xác nhận hủy mã; điểm chưa được hoàn.") from exc

