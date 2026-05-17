"""Seed US-holiday blackout days for the surgery scheduler.

WWC observes (per practice manager): New Year's Day, Memorial Day,
Juneteenth, Independence Day, Labor Day, Thanksgiving, Day after
Thanksgiving, Christmas Day. (Office is also closed Christmas Eve in
practice but it's a half-day case-by-case — leave that to manual PTO.)

Holidays are computed deterministically per year (no external library).
Idempotent on (blackout_date, scope='office', reason='holiday').
"""
from __future__ import annotations

from datetime import date, timedelta
from sqlalchemy.orm import Session

from app.models.surgery import SurgeryBlackoutDay


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """Date of the nth occurrence of `weekday` (Mon=0..Sun=6) in month."""
    d = date(year, month, 1)
    while d.weekday() != weekday:
        d = d + timedelta(days=1)
    return d + timedelta(days=(n - 1) * 7)


def _last_weekday(year: int, month: int, weekday: int) -> date:
    """Last `weekday` in month (e.g. last Monday for Memorial Day)."""
    from calendar import monthrange
    last_dom = monthrange(year, month)[1]
    d = date(year, month, last_dom)
    while d.weekday() != weekday:
        d = d - timedelta(days=1)
    return d


def _observed(d: date) -> date:
    """Sat → Friday, Sun → Monday (handles month/year wrap)."""
    if d.weekday() == 5:   # Sat
        return d - timedelta(days=1)
    if d.weekday() == 6:   # Sun
        return d + timedelta(days=1)
    return d


def us_holidays(year: int) -> list[tuple[date, str]]:
    """Returns [(date, label)] for the WWC holiday set in a given year.
    Includes the actual date and the observed-weekday equivalent if they
    differ.
    """
    out = []

    # New Year's Day
    nyd = date(year, 1, 1)
    out.append((nyd, "New Year's Day"))
    if (obs := _observed(nyd)) != nyd:
        out.append((obs, "New Year's Day (observed)"))

    # Memorial Day — last Monday in May
    out.append((_last_weekday(year, 5, 0), "Memorial Day"))

    # Juneteenth (Jun 19)
    j = date(year, 6, 19)
    out.append((j, "Juneteenth"))
    if (obs := _observed(j)) != j:
        out.append((obs, "Juneteenth (observed)"))

    # Independence Day (Jul 4)
    ind = date(year, 7, 4)
    out.append((ind, "Independence Day"))
    if (obs := _observed(ind)) != ind:
        out.append((obs, "Independence Day (observed)"))

    # Labor Day — first Monday in Sept
    out.append((_nth_weekday(year, 9, 0, 1), "Labor Day"))

    # Thanksgiving — 4th Thursday in November
    tg = _nth_weekday(year, 11, 3, 4)
    out.append((tg, "Thanksgiving"))
    # Day after Thanksgiving — Friday
    out.append((tg + timedelta(days=1), "Day after Thanksgiving"))

    # Christmas Day
    xmas = date(year, 12, 25)
    out.append((xmas, "Christmas Day"))
    if (obs := _observed(xmas)) != xmas:
        out.append((obs, "Christmas Day (observed)"))

    return out


def seed_us_holidays(db: Session, *, through_year: int = None) -> dict:
    """Idempotently seed all WWC-observed holidays from this year through
    `through_year` (default = current year + 5)."""
    today = date.today()
    last = through_year or (today.year + 5)

    existing = {(b.blackout_date, b.label)
                for b in db.query(SurgeryBlackoutDay)
                            .filter(SurgeryBlackoutDay.scope == "office",
                                    SurgeryBlackoutDay.reason == "holiday")
                            .all()}

    inserted = 0
    for year in range(today.year, last + 1):
        for d, label in us_holidays(year):
            if (d, label) in existing:
                continue
            db.add(SurgeryBlackoutDay(
                blackout_date=d,
                scope="office",
                reason="holiday",
                label=label,
                is_recurring=True,
                created_by="system:holiday-seed",
            ))
            inserted += 1
    db.commit()
    return {"inserted": inserted, "through_year": last}
