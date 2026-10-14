"""Short, profile-first transactions for lot activation and expiry."""
import logging
from dataclasses import dataclass, fields

from django.db import IntegrityError, transaction

from loyalty.models import AuditLog, CustomerProfile, PointsLot, PointsTransaction, ProgramConfig
from .timing import resolve_now

logger = logging.getLogger(__name__)


@dataclass
class LifecycleResult:
    processed: int = 0
    activated: int = 0
    offset_total: int = 0
    expired: int = 0
    expired_points: int = 0
    skipped: int = 0
    errors: int = 0

    def __add__(self, other):
        return LifecycleResult(**{f.name: getattr(self, f.name) + getattr(other, f.name) for f in fields(self)})


def due_lots(now, activation):
    if activation:
        return PointsLot.objects.filter(status="PENDING", available_at__lte=now).order_by("available_at", "pk")
    return PointsLot.objects.filter(status__in=["AVAILABLE", "PENDING"], expires_at__lte=now,
                                    points_remaining__gt=0).order_by("expires_at", "pk")


def lifecycle_plan(now=None, limit=500):
    now = resolve_now(now)
    validate_limit(limit)
    pending = list(due_lots(now, True)[:limit])
    plan = [(lot.pk, "expire" if lot.expires_at and lot.expires_at <= now and lot.points_remaining else "activate")
            for lot in pending]
    others = due_lots(now, False).exclude(pk__in=[lot.pk for lot in pending])[:max(0, limit - len(pending))]
    return plan + [(lot.pk, "expire") for lot in others]


def validate_limit(limit):
    if type(limit) is not int or limit < 0:
        raise ValueError("INVALID_LIMIT: Giới hạn phải là số nguyên không âm.")


def offset_debt(available_balance, points):
    offset = min(max(0, -available_balance), points)
    return points - offset, offset


def _expire_locked(profile, lot, now):
    key = f"expire:lot:{lot.pk}"
    if (lot.status not in ("AVAILABLE", "PENDING") or not lot.expires_at
            or lot.expires_at > now or lot.points_remaining <= 0
            or PointsTransaction.objects.filter(idempotency_key=key).exists()):
        return LifecycleResult(skipped=1)
    points, before = lot.points_remaining, lot.status
    if before == "AVAILABLE":
        profile.available_balance -= points
    else:
        profile.pending_balance -= points
    config = ProgramConfig.get_effective(profile.shop, at=now)
    profile.save(update_fields=["available_balance", "pending_balance", "updated_at"])
    lot.points_remaining, lot.status = 0, "EXPIRED"
    lot.save(update_fields=["points_remaining", "status", "updated_at"])
    PointsTransaction.objects.create(profile=profile, type="EXPIRE", delta=-points,
        balance_after=profile.available_balance, lot=lot, order_id=lot.source_order_id,
        idempotency_key=key, actor="system", reason_code="", rule_snapshot={
            "code": "expire", "expires_at": lot.expires_at.isoformat(), "now": now.isoformat(),
            "points": points, "lot_status_before": before,
            "program_config_id": config.pk if config else None,
            "program_config_version": config.version if config else None})
    return LifecycleResult(expired=1, expired_points=points)


def _process_lot(lot_id, profile_id, now, activation):
    try:
        with transaction.atomic():
            profile = CustomerProfile.objects.select_for_update().get(pk=profile_id)
            lot = PointsLot.objects.select_for_update().get(pk=lot_id, profile=profile)
            if not activation:
                return _expire_locked(profile, lot, now)
            if lot.status != "PENDING" or lot.available_at is None or lot.available_at > now:
                return LifecycleResult(skipped=1)
            if lot.expires_at and lot.expires_at <= now and lot.points_remaining > 0:
                return _expire_locked(profile, lot, now)
            points = lot.points_remaining
            remaining, offset = offset_debt(profile.available_balance, points)
            profile.pending_balance -= points
            profile.available_balance += points
            lot.points_remaining = remaining
            lot.status = "CONSUMED" if offset and remaining == 0 else "AVAILABLE"
            lot.save(update_fields=["points_remaining", "status", "updated_at"])
            profile.save(update_fields=["available_balance", "pending_balance", "updated_at"])
            if offset:
                AuditLog.objects.create(shop=profile.shop, actor="system", action="lot.activated",
                    target_type="PointsLot", target_id=str(lot.pk),
                    before={"status": "PENDING", "points_remaining": points},
                    after={"status": lot.status, "points_remaining": remaining, "offset": offset,
                           "now": now.isoformat()})
            from .notifications import notify
            historical = lot.transactions.filter(type="EARN", rule_snapshot__historical=True).exists()
            if not historical:
                notify(profile, "POINTS_AVAILABLE", lot.pk, lot=lot)
            return LifecycleResult(activated=1, offset_total=offset)
    except IntegrityError:
        if PointsTransaction.objects.filter(idempotency_key=f"expire:lot:{lot_id}").exists():
            return LifecycleResult(skipped=1)
        raise


def _batch(now, limit, activation, excluded=(), attempted=None):
    now = resolve_now(now)
    validate_limit(limit)
    candidates = list(due_lots(now, activation).exclude(pk__in=excluded).values_list("pk", "profile_id")[:limit])
    if attempted is not None:
        attempted.update(lot_id for lot_id, _ in candidates)
    result = LifecycleResult(processed=len(candidates))
    for lot_id, profile_id in candidates:
        try:
            result += _process_lot(lot_id, profile_id, now, activation)
        except Exception:
            logger.error("Lỗi xử lý vòng đời điểm: lot_id=%s profile_id=%s", lot_id, profile_id)
            result.errors += 1
    return result


def activate_due_lots(now=None, limit=500):
    return _batch(now, limit, True)


def expire_due_lots(now=None, limit=500):
    return _batch(now, limit, False)


def run_lifecycle_services(now=None, limit=500):
    now = resolve_now(now)
    attempted = set()
    activated = _batch(now, limit, True, attempted=attempted)
    # Do not immediately retry a failed activation/expiry candidate in the second phase.
    return activated + _batch(now, max(0, limit - activated.processed), False, excluded=attempted)


def lifecycle_summary(result):
    return (f"Vòng đời điểm: xét {result.processed}, kích hoạt {result.activated}, bù nợ {result.offset_total}, "
            f"hết hạn {result.expired} lô/{result.expired_points} điểm, bỏ qua {result.skipped}, lỗi {result.errors}.")
