from django.core.management.base import BaseCommand, CommandError

from webhooks.models import WebhookEvent
from webhooks.tasks import STUCK_RECEIVED_AFTER
from webhooks.services import reset_events


class Command(BaseCommand):
    help = "Đặt lại webhook để xử lý lại; mặc định chỉ xem trước, cần --yes để ghi."

    def add_arguments(self, parser):
        parser.add_argument("--event-id", type=int, help="Mã nội bộ WebhookEvent, không phải header event id.")
        parser.add_argument("--topic", help="Chỉ chọn topic này.")
        parser.add_argument("--status", choices=["SKIPPED", "FAILED"], help="Lọc trạng thái hiện tại.")
        parser.add_argument("--limit", type=int, default=100, help="Số bản ghi tối đa, mặc định 100.")
        parser.add_argument("--dry-run", action="store_true", help="Chỉ xem trước kể cả khi có --yes.")
        parser.add_argument("--yes", action="store_true", help="Xác nhận đặt lại các bản ghi đã chọn.")

    def handle(self, *args, **options):
        if options["limit"] < 1:
            raise CommandError("--limit phải lớn hơn 0.")
        events = WebhookEvent.objects.filter(status__in=[WebhookEvent.Status.SKIPPED, WebhookEvent.Status.FAILED])
        if options["event_id"] is not None:
            events = events.filter(pk=options["event_id"])
        if options["topic"]:
            events = events.filter(topic=options["topic"])
        if options["status"]:
            events = events.filter(status=options["status"])
        dry_run = options["dry_run"] or not options["yes"]
        if dry_run:
            ids = list(events.order_by("received_at", "pk").values_list("pk", flat=True)[:options["limit"]])
        else:
            ids = reset_events(events, options["limit"])
        self.stdout.write(f"{'Dự kiến đặt lại' if dry_run else 'Đã đặt lại'} {len(ids)} webhook.")
        self.stdout.write(f"Sweeper chỉ chọn khi thời điểm nhận đã cũ ít nhất {STUCK_RECEIVED_AFTER.total_seconds() / 60:g} phút.")
        if dry_run:
            self.stdout.write("Thêm --yes để thực hiện; không enqueue trực tiếp.")
