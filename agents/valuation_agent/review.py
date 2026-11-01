"""Review CVP's Q4 marks against Q3 (rules R1-R4) and decide each holding's Q4 value in USD."""
from __future__ import annotations

from decimal import Decimal

from agents.bank_agent.rules import normalize_name
from agents.common.money import D, cents, to_json_number


def held_at_period_end(q3_holdings: dict, purchases: list, sales: list) -> dict:
    """company -> {'cost_usd', 'purchased_in_q4'} for every stake held at the period end."""
    sold = {normalize_name(s["company"]) for s in sales}
    held = {}
    for name, h in q3_holdings.items():
        if normalize_name(name) not in sold:
            held[name] = {"cost_usd": h.cost_usd, "purchased_in_q4": None}
    for p in purchases:
        existing = next((k for k in held if normalize_name(k) == normalize_name(p["company"])), None)
        if existing:  # follow-on investment adds to cost
            held[existing]["cost_usd"] += D(p["amount_usd"])
        else:
            held[p["company"]] = {"cost_usd": D(p["amount_usd"]), "purchased_in_q4": p["date"]}
    return held


def review_marks(q3_holdings: dict, marks: list, notes: list, purchases: list, sales: list,
                 fx_to_usd: dict, threshold_pct: Decimal) -> tuple[list[dict], dict]:
    """Return (review entries, valuation of held stakes).

    valuation: company -> {'value_usd', 'cost_usd', 'basis'} for stakes included in the NAV.
    Change for R1 is measured in the company's own currency, so an FX move is not mistaken
    for a change in the company's value.
    """
    held = held_at_period_end(q3_holdings, purchases, sales)
    marks_by = {normalize_name(m["company"]): m for m in marks}
    notes_by = {normalize_name(n["company"]): n["note"] for n in notes}
    q3_by = {normalize_name(k): v for k, v in q3_holdings.items()}
    held_by = {normalize_name(k): (k, v) for k, v in held.items()}

    names, seen = [], set()
    for n in list(q3_holdings) + [m["company"] for m in marks] + list(held):
        if normalize_name(n) not in seen:
            seen.add(normalize_name(n))
            names.append(n)

    entries, valuation = [], {}
    for name in sorted(names, key=str.lower):
        key = normalize_name(name)
        q3, mark, note = q3_by.get(key), marks_by.get(key), notes_by.get(key)
        is_held = key in held_by
        flags, reasons = [], []
        currency = (mark or {}).get("currency") or (q3.currency if q3 else "USD")
        q3_fv = q3.fair_value_local if q3 else None
        q4_fv = D(mark["fair_value"]) if mark else None
        change_pct = None
        value_usd, basis = None, None

        if mark and not is_held:
            flags.append("R4")
            reasons.append("CVP gave a Q4 mark but the fund does not hold this company at 31 Dec; "
                           "excluded from NAV")
        if is_held and not mark:
            flags.append("R3")
            purchased = held_by[key][1]["purchased_in_q4"]
            if purchased:
                value_usd, basis = held_by[key][1]["cost_usd"], "cost (bought in Q4, not yet valued)"
                reasons.append(f"No Q4 mark: bought {purchased}, carried at cost "
                               f"USD {cents(value_usd):,.2f} until first valuation"
                               + (f" (CVP note: {note})" if note else ""))
            else:
                value_usd, basis = q3.fair_value_usd, "Q3 fair value (no Q4 mark - stale)"
                reasons.append("No Q4 mark for a stake held since before Q4; carried at Q3 fair value "
                               "pending CVP" + (f" (CVP note: {note})" if note else ""))
        if mark and is_held:
            rate = fx_to_usd.get(currency)
            if rate is None:
                raise ValueError(f"No FX rate to USD for {currency} ({name})")
            value_usd, basis = cents(q4_fv * rate), "CVP Q4 mark"
            if q3 is not None:
                if q3.currency != currency:
                    raise ValueError(f"{name}: currency changed from {q3.currency} to {currency}")
                change_pct = (q4_fv - q3_fv) / q3_fv * 100
                if abs(change_pct) >= threshold_pct:
                    flags.append("R1")
                    reasons.append(f"Fair value moved {change_pct:+.1f}% in {currency} "
                                   f"(threshold {threshold_pct}%)")
                if q3.method != mark["valuation_method"]:
                    flags.append("R2")
                    reasons.append(f"Valuation method changed from {q3.method} to {mark['valuation_method']}")
        if not is_held and not mark:
            reasons.append("Not held at 31 Dec (sold in Q4); no Q4 mark expected; excluded from NAV"
                           + (f" (CVP note: {note})" if note else ""))
        if (not flags and change_pct is not None and currency != "USD" and q3 is not None
                and value_usd is not None):
            usd_pct = (value_usd - q3.fair_value_usd) / q3.fair_value_usd * 100
            reasons.append(f"Within threshold in {currency} ({change_pct:+.1f}%); the {usd_pct:+.1f}% move in USD "
                           f"is EUR/USD, not company value; method unchanged")
        if is_held:
            valuation[held_by[key][0]] = {"value_usd": value_usd, "cost_usd": held_by[key][1]["cost_usd"],
                                          "basis": basis}

        entry = {
            "company": name,
            "currency": currency,
            "q3_fair_value": to_json_number(q3_fv) if q3_fv is not None else None,
            "q4_fair_value": to_json_number(q4_fv) if q4_fv is not None else None,
            "change_pct": round(float(change_pct), 2) if change_pct is not None else None,
            "q3_method": q3.method if q3 else None,
            "q4_method": mark["valuation_method"] if mark else None,
            "flags": flags,
            "reason": "; ".join(reasons) if reasons else "Within threshold; method unchanged",
            # extra fields
            "held_at_31_dec": is_held,
            "q3_fair_value_usd": to_json_number(q3.fair_value_usd) if q3 else None,
            "q4_value_usd_in_nav": to_json_number(value_usd) if value_usd is not None else None,
            "q4_value_basis": basis,
            "change_pct_usd": (round(float((value_usd - q3.fair_value_usd) / q3.fair_value_usd * 100), 2)
                               if (q3 and value_usd is not None) else None),
        }
        entries.append(entry)
    return entries, valuation
