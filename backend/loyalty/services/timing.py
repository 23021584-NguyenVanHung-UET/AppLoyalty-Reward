"""Shared explicit, timezone-aware clocks for services, tasks and commands."""
from django.utils import timezone
from django.utils.dateparse import parse_datetime


def resolve_now(now=None):
    value = timezone.now() if now is None else now
    if timezone.is_naive(value):
        raise ValueError("INVALID_TIME: Thời gian phải có múi giờ.")
    return value


def parse_now(value=None):
    if value is None:
        return resolve_now()
    try:
        parsed = parse_datetime(value)
    except (ValueError, TypeError):
        parsed = None
    if parsed is None:
        raise ValueError("INVALID_TIME: Thời gian phải đúng định dạng ISO 8601.")
    return resolve_now(parsed)
