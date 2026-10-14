"""Atomic manual adjustments; ledger entries are never rewritten."""
from dataclasses import asdict
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction

from loyalty.models import AuditLog, CustomerProfile, PointsLot, PointsTransaction, ProgramConfig
from .errors import LoyaltyError
from .lifecycle import offset_debt
from .revocation_allocation import RevokeLot, plan_revocation
from .timing import resolve_now


def _replay(entry, payload):
    if entry.profile_id != payload["profile_id"] or entry.rule_snapshot.get("input") != payload:
        raise LoyaltyError("KEY_REUSED", "Khóa yêu cầu đã được dùng cho nội dung khác.")
    return {**entry.rule_snapshot["result"], "transaction_id": entry.pk, "duplicate": True}


def adjust_points(profile, delta, reason_code, note, actor, client_key, now=None):
    now = resolve_now(now)
    if (type(delta) is not int or not delta or
            abs(delta) > settings.LOYALTY_MAX_ADJUST_POINTS):
        raise LoyaltyError("INVALID_DELTA", "Số điểm điều chỉnh không hợp lệ hoặc vượt giới hạn.", 422)
    if reason_code not in PointsTransaction.ReasonCode.values:
        raise LoyaltyError("INVALID_REASON", "Mã lý do không hợp lệ.", 422)
    if not isinstance(note, str) or not 5 <= len(note.strip()) <= 500:
        raise LoyaltyError("INVALID_NOTE", "Ghi chú phải có từ 5 đến 500 ký tự.", 422)
    if not isinstance(client_key, str) or not 1 <= len(client_key.strip()) <= 193:
        raise LoyaltyError("INVALID_KEY", "Khóa yêu cầu không hợp lệ.", 422)
    if not isinstance(actor, str) or not 1 <= len(actor) <= 150:
        raise LoyaltyError("INVALID_ACTOR", "Người thực hiện không hợp lệ.", 422)
    payload = dict(profile_id=profile.pk, delta=delta, reason_code=reason_code,
                   note=note.strip(), actor=actor)
    key = f"adjust:{client_key}"
    try:
        with transaction.atomic():
            profile = CustomerProfile.objects.select_for_update().get(pk=profile.pk)
            existing = PointsTransaction.objects.filter(idempotency_key=key).first()
            if existing:
                return _replay(existing, payload)
            if profile.is_anonymized:
                raise LoyaltyError("CUSTOMER_ANONYMIZED", "Không thể điều chỉnh khách đã ẩn danh.")
            config = ProgramConfig.get_effective(profile.shop_id, now)
            if config is None:
                raise LoyaltyError("NO_CONFIG", "Chưa có cấu hình chương trình có hiệu lực.")
            before = dict(available_balance=profile.available_balance,
                          pending_balance=profile.pending_balance, lifetime_earned=profile.lifetime_earned)
            allocations, debt_offset, debt_created, lot = [], 0, 0, None
            if delta > 0:
                remaining, debt_offset = offset_debt(profile.available_balance, delta)
                lot = PointsLot.objects.create(profile=profile, source_type="ADJUSTMENT",
                    points_total=delta, points_remaining=remaining,
                    status="AVAILABLE" if remaining else "CONSUMED", available_at=now,
                    expires_at=now + timedelta(days=config.expiry_days) if config.expiry_days else None)
                profile.lifetime_earned += delta
            else:
                if not config.allow_negative_balance and profile.available_balance < -delta:
                    raise LoyaltyError("INSUFFICIENT_POINTS", "Không đủ điểm để trừ; chưa thay đổi số dư.")
                lots = list(PointsLot.objects.select_for_update().filter(profile=profile,
                    status="AVAILABLE", points_remaining__gt=0).order_by("pk"))
                plan = plan_revocation([RevokeLot(row.pk, row.source_type, row.source_order_id,
                    row.status, row.points_remaining, row.expires_at) for row in lots], None, -delta,
                    allow_negative_balance=config.allow_negative_balance,
                    available_balance=profile.available_balance)
                if plan.unrecovered_points:
                    raise LoyaltyError("INSUFFICIENT_POINTS", "Không đủ điểm để trừ; chưa thay đổi số dư.")
                by_id = {row.pk: row for row in lots}
                for allocation in plan.allocations:
                    row = by_id[allocation.lot_id]
                    row.points_remaining -= allocation.points
                    row.status = allocation.status_after
                    row.save(update_fields=["points_remaining", "status", "updated_at"])
                allocations = [asdict(row) for row in plan.allocations]
                debt_created = plan.debt_created
            profile.available_balance += delta
            profile.save(update_fields=["available_balance", "lifetime_earned", "updated_at"])
            result = dict(available_balance=profile.available_balance,
                          pending_balance=profile.pending_balance, lifetime_earned=profile.lifetime_earned)
            entry = PointsTransaction.objects.create(profile=profile, type="ADJUST", delta=delta,
                lot=lot, balance_after=profile.available_balance, idempotency_key=key,
                reason_code=reason_code, note=payload["note"], actor=actor,
                rule_snapshot=dict(code="adjust", config_version=config.version, input=payload,
                    allocations=allocations, debt_offset=debt_offset, debt_created=debt_created,
                    result=result))
            AuditLog.objects.create(shop_id=profile.shop_id, actor=actor, action="points.adjust",
                target_type="PointsTransaction", target_id=str(entry.pk), before=before,
                after={**result, "delta": delta, "reason_code": reason_code, "profile_id": profile.pk})
            return {**result, "transaction_id": entry.pk, "duplicate": False}
    except IntegrityError:
        # Different profiles may race for the globally unique key. The losing
        # transaction rolls back its lots, balances and audit before this read.
        existing = PointsTransaction.objects.filter(idempotency_key=key).first()
        if existing:
            return _replay(existing, payload)
        raise
