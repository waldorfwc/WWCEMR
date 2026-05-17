"""Dose-combination suggester for the pellet workflow.

Given a target total mg for a hormone, returns combinations of
catalog dose strengths that sum exactly to the target — sorted by:
  1. In-stock first (every component has enough on-hand at the chosen
     location)
  2. Fewest pellets (smaller insertion footprint is preferred)
  3. Earliest-expiring lot used first (FIFO hint)

All arithmetic is done in tenths-of-mg internally to avoid float
issues with 12.5 / 37.5 / 87.5 mg doses.
"""
from __future__ import annotations

from collections import Counter
from typing import Optional

from sqlalchemy.orm import Session
from sqlalchemy import func

from app.models.pellet import (
    PelletDoseType, PelletLot, PelletStock,
)


MAX_RESULTS = 6      # cap the list we return to the UI
MAX_PELLETS = 12     # safety bound on combo size


def _to_tenths(mg: float) -> int:
    return int(round(float(mg) * 10))


def suggest_for_hormone(
    db: Session,
    hormone: str,
    target_mg: float,
    location: str = "white_plains",
) -> dict:
    """Return alternatives for a single hormone."""
    types = (db.query(PelletDoseType)
               .filter(PelletDoseType.hormone == hormone,
                       PelletDoseType.is_active.is_(True))
               .order_by(PelletDoseType.dose_mg.desc()).all())
    if not types:
        return {"target_mg": float(target_mg), "alternatives": []}

    # On-hand totals per dose type at this location
    stock_rows = (db.query(PelletDoseType.id,
                            func.coalesce(func.sum(PelletStock.doses_on_hand), 0))
                    .join(PelletLot, PelletLot.dose_type_id == PelletDoseType.id)
                    .join(PelletStock, PelletStock.lot_id == PelletLot.id)
                    .filter(PelletDoseType.hormone == hormone,
                            PelletStock.location == location)
                    .group_by(PelletDoseType.id).all())
    on_hand_by_type = {str(t_id): int(qty or 0) for (t_id, qty) in stock_rows}

    target_tenths = _to_tenths(target_mg)
    if target_tenths <= 0:
        return {"target_mg": float(target_mg), "alternatives": []}

    # Tenths-mg per dose type, sorted desc for greedy enumeration
    dose_values = [(str(t.id), _to_tenths(float(t.dose_mg)), float(t.dose_mg), t.label)
                    for t in types]
    dose_values.sort(key=lambda x: -x[1])

    # Enumerate combinations — bounded DFS that explores all multisets
    seen: set[tuple] = set()
    combos: list[Counter] = []

    def dfs(remaining: int, start_idx: int, current: Counter, depth: int):
        if remaining == 0:
            # Canonicalize the multiset to dedupe
            key = tuple(sorted(current.items()))
            if key not in seen:
                seen.add(key)
                combos.append(Counter(current))
            return
        if depth >= MAX_PELLETS:
            return
        for i in range(start_idx, len(dose_values)):
            _, tenths, _, _ = dose_values[i]
            if tenths > remaining:
                continue
            max_count = remaining // tenths
            for cnt in range(1, max_count + 1):
                current[i] += cnt
                dfs(remaining - tenths * cnt, i + 1, current, depth + cnt)
                current[i] -= cnt
                if current[i] == 0:
                    del current[i]

    dfs(target_tenths, 0, Counter(), 0)

    # Score every combo
    scored = []
    for c in combos:
        total_pellets = sum(c.values())
        per_dose = []
        all_ok = True
        for idx, count in c.items():
            t_id, tenths, dose_mg, label = dose_values[idx]
            on_hand = on_hand_by_type.get(t_id, 0)
            short = max(0, count - on_hand)
            if short > 0:
                all_ok = False
            per_dose.append({
                "dose_type_id": t_id,
                "dose_mg":      dose_mg,
                "label":        label,
                "count":        count,
                "on_hand":      on_hand,
                "short":        short,
            })
        per_dose.sort(key=lambda x: -x["dose_mg"])  # display largest first
        scored.append({
            "components":     per_dose,
            "total_pellets":  total_pellets,
            "in_stock":       all_ok,
            "sum_mg":         sum(x["dose_mg"] * x["count"] for x in per_dose),
        })

    # Sort: in-stock first, then fewer pellets, then sum_mg (stable for ties)
    scored.sort(key=lambda x: (not x["in_stock"], x["total_pellets"]))
    return {
        "target_mg":    float(target_mg),
        "location":     location,
        "alternatives": scored[:MAX_RESULTS],
        "any_in_stock": any(s["in_stock"] for s in scored),
    }


def suggest(
    db: Session,
    estradiol_mg: float = 0,
    testosterone_mg: float = 0,
    location: str = "white_plains",
) -> dict:
    return {
        "estradiol":    (suggest_for_hormone(db, "estradiol", estradiol_mg, location)
                           if estradiol_mg > 0 else None),
        "testosterone": (suggest_for_hormone(db, "testosterone", testosterone_mg, location)
                           if testosterone_mg > 0 else None),
        "location":     location,
    }
