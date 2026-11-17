"""Score one application from an exported scorecard. Pure Python.

This is the runtime the scoring API uses. It reads the same ``card`` that
training exports and must agree with the vectorised implementation in
``credit_score.scorecard`` to the point; a test holds the two together.
"""

import math
from bisect import bisect_right

# Adverse-action style reasons, one per model variable.
REASONS: dict[str, tuple[str, str]] = {
    "acc_open_past_24mths": ("R01", "Too many accounts opened in the last 24 months"),
    "fico": ("R02", "Credit bureau score too low"),
    "mths_since_recent_inq": ("R03", "Recent credit inquiry"),
    "mo_sin_rcnt_tl": ("R04", "Account opened too recently"),
    "inq_last_6mths": ("R05", "Too many inquiries in the last 6 months"),
    "dti": ("R06", "Debt-to-income ratio too high"),
    "mo_sin_old_rev_tl_op": ("R07", "Length of revolving credit history too short"),
    "bc_open_to_buy": ("R08", "Insufficient available credit on bankcards"),
    "purpose": ("R09", "Loan purpose associated with higher risk"),
    "tot_hi_cred_lim": ("R10", "Total credit limits too low"),
    "loan_to_income": ("R11", "Loan amount too high relative to income"),
    "home_ownership": ("R12", "Home ownership status"),
    "annual_inc": ("R13", "Income too low"),
    "mths_since_recent_bc": ("R14", "Bankcard opened too recently"),
    "term": ("R15", "Loan term too long"),
    "mort_acc": ("R16", "No mortgage account on file"),
    "total_rev_hi_lim": ("R17", "Revolving credit limits too low"),
}
MAX_REASONS = 4


def is_missing(value) -> bool:
    return value is None or (isinstance(value, float) and math.isnan(value))


def derive(application: dict) -> dict:
    """Add the inputs the model derives: same rules as ``features.build_features``."""
    out = dict(application)
    amount, income = out.get("loan_amnt"), out.get("annual_inc")
    valid = not is_missing(amount) and not is_missing(income) and income > 0
    out["loan_to_income"] = amount / income if valid else None
    return out


def bin_label(value, spec: dict) -> str:
    """Name of the bin a value falls into: the unit for per-variable stability."""
    if is_missing(value):
        return "missing"
    if spec["type"] == "categorical":
        for position, group in enumerate(spec["categories"]):
            if value in group:
                return f"bin {position}"
        return "other"
    return f"bin {bisect_right(spec['splits'], value)}"


def variable_points(value, spec: dict) -> int:
    if is_missing(value):
        return spec["missing_points"]
    if spec["type"] == "categorical":
        for group, points in zip(spec["categories"], spec["points"], strict=True):
            if value in group:
                return points
        return spec["other_points"]
    return spec["points"][bisect_right(spec["splits"], value)]


def max_points(spec: dict) -> int:
    candidates = [*spec["points"], spec["missing_points"]]
    if spec["type"] == "categorical":
        candidates.append(spec["other_points"])
    return max(candidates)


def pd_from_score(score: float, calibration: dict) -> float:
    return 1 / (1 + math.exp(-(calibration["intercept"] + calibration["slope"] * score)))


def band_from_score(score: float, bands: list[dict]) -> str:
    cuts = [b["min_score"] for b in bands[1:]]
    return bands[bisect_right(cuts, score)]["band"]


def reasons(points: dict[str, int], card: dict) -> list[dict]:
    """The variables that cost the most points against their own maximum."""
    lost = [
        (max_points(spec) - points[spec["name"]], position, spec["name"])
        for position, spec in enumerate(card["variables"])
    ]
    worst = sorted((item for item in lost if item[0] > 0), key=lambda t: (-t[0], t[1]))
    out = []
    for shortfall, _, name in worst[:MAX_REASONS]:
        code, description = REASONS[name]
        out.append(
            {"code": code, "variable": name, "description": description, "points_lost": shortfall}
        )
    return out


def score_application(application: dict, card: dict) -> dict:
    """Score, PD, band, points per variable and reasons for one application."""
    values = derive(application)
    points = {s["name"]: variable_points(values.get(s["name"]), s) for s in card["variables"]}
    total = sum(points.values())
    return {
        "score": total,
        "pd": pd_from_score(total, card["calibration"]),
        "band": band_from_score(total, card["bands"]),
        "points": points,
        "reasons": reasons(points, card),
    }


def required_inputs(card: dict, derived: dict[str, tuple[str, ...]]) -> list[str]:
    """Fields a caller must send: derived variables are replaced by their sources."""
    names: list[str] = []
    for spec in card["variables"]:
        for name in derived.get(spec["name"], (spec["name"],)):
            if name not in names:
                names.append(name)
    return names
