from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone
from django_q.models import Schedule


class Command(BaseCommand):
    help = "Tạo hoặc cập nhật các lịch xử lý nền."

    def handle(self, *args, **options):
        with transaction.atomic():
            for func, name, minutes in [
                ("webhooks.tasks.sweep_webhook_events", "Quét và thử lại webhook", 1),
                ("loyalty.tasks.run_lifecycle", "Kích hoạt và hết hạn điểm", 5),
                ("loyalty.tasks.process_redemptions", "Xử lý mã đổi thưởng", 1),
                ("loyalty.tasks.sweep_notifications", "Thử lại thông báo email", 1),
            ]:
                schedule, created = Schedule.objects.update_or_create(func=func,
                    defaults={"name": name, "schedule_type": Schedule.MINUTES, "minutes": minutes,
                              "repeats": -1, "args": "", "kwargs": "", "hook": None})
                if schedule.next_run is None:
                    schedule.next_run = timezone.now()
                    schedule.save(update_fields=["next_run"])
                self.stdout.write(f"{'Đã tạo' if created else 'Đã cập nhật'} lịch: {name}, mỗi {minutes} phút.")
            Schedule.objects.update_or_create(func="loyalty.tasks.evaluate_tiers", defaults={
                "name": "Xét hạng thành viên", "schedule_type": Schedule.DAILY, "minutes": None,
                "repeats": -1, "args": "", "kwargs": "", "hook": None})
            self.stdout.write("Đã cập nhật lịch xét hạng thành viên hàng ngày.")
            Schedule.objects.update_or_create(func="loyalty.tasks.scrub_webhook_payloads", defaults={
                "name": "Scrub expired webhook payloads", "schedule_type": Schedule.DAILY, "minutes": None,
                "repeats": -1, "args": "", "kwargs": "", "hook": None})
            Schedule.objects.update_or_create(func="loyalty.tasks.expiring_notifications", defaults={
                "name": "Nhắc điểm sắp hết hạn", "schedule_type": Schedule.DAILY, "minutes": None,
                "repeats": -1, "args": "", "kwargs": "", "hook": None})
            Schedule.objects.update_or_create(func="loyalty.tasks.tier_grace_notifications", defaults={
                "name": "Nhắc giữ hạng trong thời gian ân hạn", "schedule_type": Schedule.DAILY, "minutes": None,
                "repeats": -1, "args": "", "kwargs": "", "hook": None})
            for func, name, schedule_type in [("loyalty.tasks.grant_birthdays", "Cấp điểm sinh nhật", Schedule.DAILY),
                ("loyalty.tasks.sweep_referrals", "Cấp thưởng giới thiệu", Schedule.HOURLY)]:
                Schedule.objects.update_or_create(func=func, defaults={"name": name, "schedule_type": schedule_type,
                    "minutes": None, "repeats": -1, "args": "", "kwargs": "", "hook": None})
