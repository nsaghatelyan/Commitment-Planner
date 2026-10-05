from datetime import UTC, date, datetime


def utc_today() -> date:
    return datetime.now(UTC).date()


def utc_now() -> datetime:
    return datetime.now(UTC)
