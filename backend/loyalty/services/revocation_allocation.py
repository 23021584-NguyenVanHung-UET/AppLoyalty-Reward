"""Pure lot allocation, with no Django or database dependency."""
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class RevokeLot:
    lot_id: int
    source_type: str
    source_order_id: int | None
    status: str
    points_remaining: int
    expires_at: datetime | None = None
    had_consumption: bool = False


@dataclass(frozen=True)
class LotAllocation:
    lot_id: int
    points: int
    lot_status_before: str
    status_after: str
    is_order_lot: bool


@dataclass(frozen=True)
class RevokePlan:
    allocations: list[LotAllocation]
    leftover: int
    debt_created: int
    unrecovered_points: int


def plan_revocation(lots, order_id, revoke_points, *, allow_negative_balance=True, available_balance=0):
    """Take own pending/available, then other available FEFO; never mutate inputs."""
    if revoke_points < 0:
        raise ValueError("Số điểm cần thu hồi không được âm.")

    def is_own(lot):
        return order_id is not None and lot.source_type == "ORDER" and lot.source_order_id == order_id

    def key(lot):
        group = (0 if lot.status == "PENDING" else 1) if is_own(lot) else 2
        return group, lot.expires_at is None, lot.expires_at, lot.lot_id

    candidates = sorted((lot for lot in lots if lot.points_remaining > 0 and
        (lot.status == "AVAILABLE" or (is_own(lot) and lot.status == "PENDING"))), key=key)
    left, capacity, allocations = revoke_points, max(0, available_balance), []
    for lot in candidates:
        if not left:
            break
        take = min(left, lot.points_remaining)
        if lot.status == "AVAILABLE" and not allow_negative_balance:
            take = min(take, capacity)
        if not take:
            continue
        left -= take
        if lot.status == "AVAILABLE":
            capacity -= take
        status = lot.status
        if take == lot.points_remaining:
            status = "VOIDED" if is_own(lot) and not lot.had_consumption else "CONSUMED"
        allocations.append(LotAllocation(lot.lot_id, take, lot.status, status, is_own(lot)))
    return RevokePlan(allocations, left, left if allow_negative_balance else 0,
                      0 if allow_negative_balance else left)


def plan_redemption(lots, points, available_balance):
    """Reuse FEFO allocation, treating every available lot as an other-order lot."""
    available = [lot for lot in lots if lot.status == "AVAILABLE"]
    return plan_revocation(available, None, points, allow_negative_balance=False,
                           available_balance=available_balance)
