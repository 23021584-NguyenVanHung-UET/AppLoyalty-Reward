from django.core.management.base import BaseCommand, CommandError

from loyalty.models import CustomerProfile
from loyalty.services.integrity import check_profile_integrity, check_profile_warnings
from loyalty.services.timing import resolve_now

MESSAGES = {
    "ledger_balance_mismatch": "Tổng sổ cái không khớp số dư",
    "pending_lots_mismatch": "Điểm chờ không khớp các lô chờ",
    "negative_pending_balance": "Số dư chờ bị âm",
    "available_lots_mismatch": "Điểm khả dụng không khớp tổng các lô khả dụng",
    "debt_with_available_lots": "Số dư âm nhưng vẫn có lô khả dụng còn điểm",
    "expired_lot_has_points": "Lô hết hạn vẫn còn điểm",
    "lot_remaining_exceeds_total": "Điểm còn lại của lô vượt tổng điểm ban đầu",
    "order_over_revoked": "Điểm thu hồi của đơn vượt điểm đã cấp",
    "redemption_entries_invalid": "Giao dịch trừ hoặc hoàn điểm đổi thưởng không hợp lệ",
    "redemption_net_mismatch": "Tổng giao dịch đổi thưởng không khớp trạng thái",
    "active_redemption_refunded": "Lượt đổi đang hoạt động đã bị hoàn điểm",
    "redemption_missing_code": "Lượt đổi đã phát hành thiếu mã hoặc ID Shopify",
}
WARNINGS = {"tier_rank_mismatch": "Hạng chưa khớp điểm xét hạng; có thể đang chờ job hoặc trong thời gian ân hạn",
            "activation_overdue": "Lô chờ quá ngày kích hoạt ít nhất một ngày",
            "expiry_overdue": "Lô quá hạn hơn một ngày chưa được xử lý",
            "redemption_pending_overdue": "Lượt đổi chờ phát hành quá một giờ",
            "redemption_expiry_overdue": "Mã quá hạn hơn hai giờ chưa được xử lý",
            "redemption_used_after_refund": "Mã được dùng sau khi đã hoàn điểm; cần kiểm tra thủ công"}


class Command(BaseCommand):
    help = "Kiểm tra tính nhất quán giữa sổ cái, số dư và lô điểm."

    def add_arguments(self, parser):
        parser.add_argument("--profile", type=int, help="Chỉ kiểm tra hồ sơ theo mã nội bộ.")

    def handle(self, *args, **options):
        profiles = CustomerProfile.objects.order_by("pk")
        if options["profile"] is not None:
            profiles = profiles.filter(pk=options["profile"])
            if not profiles.exists():
                raise CommandError("Không tìm thấy hồ sơ cần kiểm tra.")
        checked = invalid = 0
        now = resolve_now()
        for profile in profiles.iterator():
            checked += 1
            problems = check_profile_integrity(profile)
            for code in check_profile_warnings(profile, now):
                self.stdout.write(f"Cảnh báo hồ sơ {profile.pk}: {code} - {WARNINGS[code]}")
            if problems:
                invalid += 1
                self.stdout.write(f"Hồ sơ {profile.pk}: " + "; ".join(MESSAGES[code] for code in problems))
        self.stdout.write(f"Đã kiểm tra {checked} hồ sơ; hợp lệ: {checked - invalid}; có lỗi: {invalid}.")
        if invalid:
            raise CommandError("Phát hiện dữ liệu điểm không nhất quán.", returncode=1)
