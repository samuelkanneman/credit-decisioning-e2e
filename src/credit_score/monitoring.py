"""Monitoring: stability, performance, policy and operations, vintages, alerts.

Everything is computed from what production records (the decision log) and
what servicing reports (loan outcomes), "as of" a month. Input stability is
known as soon as applications arrive; performance only once a cohort has
matured, and only for loans that were booked, as in reality. Metrics on the
full population, which only a simulation can see, are labelled as such.

    python -m credit_score.monitoring --as-of 2019-03
"""

import argparse
import json
import os
from pathlib import Path

import numpy as np
import pandas as pd

from credit_score import metrics, registry, scoring

MODEL_DIR = registry.champion_dir()

# Thresholds (docs/retraining_criteria.md)
PSI_WATCH, PSI_INVESTIGATE = 0.10, 0.25
# Against the previous three months. Set from the noise of the simulation without injected
# drift (largest month: 0.010), not from a textbook band: see docs/retraining_criteria.md.
ROLLING_MONTHS, ROLLING_PSI_WATCH = 3, 0.05
APPROVAL_MOM_PP = 5.0
KS_DIAGNOSE_PP, KS_ALERT_PP = 3.0, 5.0
CALIBRATION_BAND = (0.80, 1.25)
RULE_CHANGE = 0.50
RULE_MIN_RATE = 0.01
APPROVAL_CHANGE_PP = 10.0
MIN_BADS = 30  # below this a monthly KS or calibration ratio is too noisy to alert on
BOOKING_LAG = 2  # months from 90 days past due to a visible charge-off
LEVELS = {"watch": 1, "investigate": 2, "alert": 3}


# ---------------------------------------------------------------- loading ----


def load_reference(model_dir: Path = MODEL_DIR) -> tuple[dict, dict]:
    card = json.loads((model_dir / "card.json").read_text())
    reference = json.loads((model_dir / "reference.json").read_text())
    return card, reference


def load_database(database_url: str) -> dict[str, pd.DataFrame]:
    import psycopg

    queries = {
        "decisions": "SELECT application_id, received_at, arm, decision, decision_rule, "
        "approved_amount, requested_amount, score, pd, band, latency_ms, model_version, "
        "policy_version, application FROM decision_log",
        "outcomes": "SELECT * FROM loan_outcomes",
        "runs": "SELECT * FROM simulation_log",
    }
    out = {}
    with psycopg.connect(database_url) as conn:
        for name, query in queries.items():
            cur = conn.execute(query)
            out[name] = pd.DataFrame(cur.fetchall(), columns=[c.name for c in cur.description])
    return prepare(out)


def load_directory(path: Path) -> dict[str, pd.DataFrame]:
    """The same frames from parquet files, for tests and offline runs."""
    frames = {
        name: pd.read_parquet(path / f"{name}.parquet")
        for name in ("decisions", "outcomes", "runs")
    }
    frames["decisions"]["application"] = frames["decisions"]["application"].map(json.loads)
    return prepare(frames)


def save_directory(frames: dict[str, pd.DataFrame], path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    for name in ("decisions", "outcomes", "runs"):
        frame = frames[name].copy()
        if name == "decisions":
            frame["application"] = frame["application"].map(json.dumps)
            frame = frame.drop(columns=["month"])
        frame.to_parquet(path / f"{name}.parquet", index=False)


def prepare(frames: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    # The canonical KS breaks ties with a seeded jitter applied in row order, so rows need
    # an order that does not depend on how the database returned them.
    dec = frames["decisions"].sort_values("application_id").reset_index(drop=True)
    dec["received_at"] = pd.to_datetime(dec["received_at"], utc=True)
    dec["month"] = dec["received_at"].dt.strftime("%Y-%m")
    out = frames["outcomes"].copy()
    for col in ("issue_month", "matured_from"):
        out[col] = pd.to_datetime(out[col])
    out["default_mob"] = out["default_mob"].astype("Int64")
    return {"decisions": dec, "outcomes": out, "runs": frames["runs"].copy()}


def months_until(as_of: str, month: str) -> int:
    a, m = pd.Period(as_of, "M"), pd.Period(month, "M")
    return (a - m).n


def matured(dec: pd.DataFrame, out: pd.DataFrame, as_of: str) -> pd.DataFrame:
    """Decisions joined to outcomes that are known at ``as_of``."""
    known = out[out["matured_from"] <= pd.Period(as_of, "M").to_timestamp()]
    return dec.merge(known, on="application_id", how="inner")


# -------------------------------------------------------------- stability ----


def score_psi(scores: pd.Series, reference: dict, against: pd.Series | None = None) -> float:
    """PSI on the development deciles, against development or against another sample."""
    edges = np.concatenate([[-np.inf], reference["score"]["edges"], [np.inf]])
    shares = np.histogram(scores, edges)[0] / len(scores)
    if against is None:
        return metrics.psi_from_shares(reference["score"]["shares"], shares)
    base = np.histogram(against, edges)[0] / len(against)
    return metrics.psi_from_shares(base, shares)


def variable_labels(dec: pd.DataFrame, card: dict) -> pd.DataFrame:
    """Bin label of every model variable for every application."""
    derived = [scoring.derive(app) for app in dec["application"]]
    return pd.DataFrame(
        {
            spec["name"]: [scoring.bin_label(app.get(spec["name"]), spec) for app in derived]
            for spec in card["variables"]
        },
        index=dec.index,
    )


def stability(dec: pd.DataFrame, card: dict, reference: dict) -> dict[str, pd.DataFrame]:
    labels = variable_labels(dec, card)
    inputs = pd.DataFrame(list(dec["application"]), index=dec.index)
    monthly, by_variable, missing = {}, {}, {}
    months = sorted(dec["month"].unique())
    for position, (month, part) in enumerate(dec.groupby("month")):
        previous = dec[dec["month"].isin(months[max(0, position - ROLLING_MONTHS) : position])]
        monthly[month] = {
            "applications": len(part),
            "mean score": part["score"].mean(),
            "score PSI": score_psi(part["score"], reference),
            "score PSI vs previous 3 months": (
                score_psi(part["score"], reference, previous["score"])
                if position >= ROLLING_MONTHS
                else np.nan
            ),
        }
        by_variable[month] = {
            name: metrics.psi_from_shares(
                reference["variables"][name],
                labels.loc[part.index, name].value_counts(normalize=True).to_dict(),
            )
            for name in labels.columns
        }
        missing[month] = inputs.loc[part.index].isna().mean().to_dict()
    missing_table = pd.DataFrame.from_dict(missing, orient="index").rename_axis("month")
    return {
        "monthly": pd.DataFrame.from_dict(monthly, orient="index").rename_axis("month"),
        "variables": pd.DataFrame.from_dict(by_variable, orient="index").rename_axis("month"),
        "missing": missing_table,
        "missing_reference": pd.Series(reference["inputs_missing"]),
    }


def score_distribution(dec: pd.DataFrame, reference: dict) -> pd.DataFrame:
    """Share of applications per reference score bin, by month and for the reference."""
    edges = np.concatenate([[-np.inf], reference["score"]["edges"], [np.inf]])
    labels = (
        [f"< {edges[1]:g}"]
        + [f"{a:g}–{b:g}" for a, b in zip(edges[1:-2], edges[2:-1], strict=True)]
        + [f"≥ {edges[-2]:g}"]
    )
    rows = {"reference": dict(zip(labels, reference["score"]["shares"], strict=True))}
    for month, part in dec.groupby("month"):
        shares = np.histogram(part["score"], edges)[0] / len(part)
        rows[month] = dict(zip(labels, shares, strict=True))
    return pd.DataFrame.from_dict(rows, orient="index").rename_axis("month")


# ------------------------------------------------------------ performance ----


def _perf(part: pd.DataFrame) -> dict:
    bads = int(part["bad_12m"].sum())
    enough = bads >= 2 and bads < len(part)
    return {
        "loans": len(part),
        "bads": bads,
        "bad rate %": part["bad_12m"].mean() * 100 if len(part) else np.nan,
        "predicted %": part["pd"].mean() * 100 if len(part) else np.nan,
        "AUC": metrics.auc(part["bad_12m"], part["score"]) if enough else np.nan,
        "KS %": metrics.ks_deciles(part["bad_12m"], part["score"]) if enough else np.nan,
    }


def performance(dec, out, as_of: str) -> dict[str, pd.DataFrame]:
    known = matured(dec, out, as_of)
    booked = known[known["decision"] == "APPROVE"]
    by_month = pd.DataFrame.from_dict(
        {m: _perf(p) for m, p in booked.groupby("month")}, orient="index"
    ).rename_axis("month")
    if len(by_month):
        by_month["observed / predicted"] = by_month["bad rate %"] / by_month["predicted %"]
    everyone = pd.DataFrame.from_dict(
        {m: _perf(p) for m, p in known.groupby("month")}, orient="index"
    ).rename_axis("month")
    bands = pd.DataFrame.from_dict(
        {b: _perf(p) for b, p in booked.groupby("band")}, orient="index"
    ).rename_axis("band")
    deciles = (
        metrics.decile_table(booked["bad_12m"], booked["score"], booked["pd"])
        if booked["bad_12m"].nunique() == 2 and len(booked) >= 100
        else pd.DataFrame()
    )
    return {"booked": by_month, "everyone": everyone, "bands": bands, "deciles": deciles}


def vintages(dec, out, as_of: str, max_mob: int = 24) -> pd.DataFrame:
    """Cumulative share of booked loans that reached 90+ days past due, by months on book."""
    booked = dec[dec["decision"] == "APPROVE"].merge(out, on="application_id")
    cutoff = pd.Period(as_of, "M")
    rows = {}
    for month, part in booked.groupby("month"):
        # The last BOOKING_LAG months are left out: a loan that reached 90 days past due
        # just before the as-of date has not been charged off yet, so it would not show.
        observable = min(max_mob, (cutoff - pd.Period(month, "M")).n - BOOKING_LAG)
        mob = part["default_mob"]
        rows[month] = {
            k: float(((mob <= k) & mob.notna()).mean() * 100) for k in range(1, observable + 1)
        }
    table = pd.DataFrame.from_dict(rows, orient="index").rename_axis("issued")
    table.columns.name = "months on book"
    return table


# ------------------------------------------------------ policy, operation ----


def policy_operations(dec: pd.DataFrame, runs: pd.DataFrame) -> dict[str, pd.DataFrame]:
    grouped = dec.groupby("month")
    monthly = pd.DataFrame(
        {
            "applications": grouped.size(),
            "approval %": grouped["decision"].apply(lambda s: (s == "APPROVE").mean() * 100),
            "review %": grouped["decision"].apply(lambda s: (s == "REVIEW").mean() * 100),
            "reject %": grouped["decision"].apply(lambda s: (s == "REJECT").mean() * 100),
            "latency p50 ms": grouped["latency_ms"].median(),
            "latency p95 ms": grouped["latency_ms"].quantile(0.95),
        }
    )
    if len(runs):
        errors = runs.set_index("month")
        monthly["errors"] = errors["client_errors"] + errors["server_errors"]
    rules = (pd.crosstab(dec["month"], dec["decision_rule"], normalize="index") * 100).rename_axis(
        columns=None
    )
    arms = (
        dec.assign(approved=dec["decision"] == "APPROVE")
        .pivot_table(index="month", columns="arm", values="approved", aggfunc="mean")
        .mul(100)
        .rename_axis(columns=None)
    )
    return {"monthly": monthly, "rules": rules, "arms": arms}


def champion_challenger(dec, out, as_of: str) -> pd.DataFrame:
    known = matured(dec, out, as_of)
    rows = {}
    for arm, part in dec.groupby("arm"):
        booked = known[(known["arm"] == arm) & (known["decision"] == "APPROVE")]
        rows[arm] = {
            "applications": len(part),
            "approval %": (part["decision"] == "APPROVE").mean() * 100,
            "matured booked loans": len(booked),
            "bad rate % (booked)": booked["bad_12m"].mean() * 100 if len(booked) else np.nan,
            "predicted %": booked["pd"].mean() * 100 if len(booked) else np.nan,
        }
    return pd.DataFrame.from_dict(rows, orient="index").rename_axis("arm")


# ----------------------------------------------------------------- alerts ----


def _alert(month, area, indicator, value, threshold, level, message):
    return {
        "month": month,
        "area": area,
        "indicator": indicator,
        "value": value,
        "threshold": threshold,
        "level": level,
        "message": message,
    }


def alerts(stab, perf, ops, reference) -> pd.DataFrame:
    found = []
    for month, row in stab["monthly"].iterrows():
        psi = row["score PSI"]
        if psi > PSI_WATCH:
            level = "investigate" if psi > PSI_INVESTIGATE else "watch"
            found.append(
                _alert(
                    month,
                    "stability",
                    "score PSI",
                    psi,
                    PSI_WATCH,
                    level,
                    "Score distribution moved away from development",
                )
            )
        rolling = row["score PSI vs previous 3 months"]
        if rolling > ROLLING_PSI_WATCH:
            found.append(
                _alert(
                    month,
                    "stability",
                    "score PSI vs previous 3 months",
                    rolling,
                    ROLLING_PSI_WATCH,
                    "watch",
                    "Score distribution changed against the last three months",
                )
            )
    for month, row in stab["variables"].iterrows():
        for name, psi in row.items():
            if psi > PSI_WATCH:
                level = "investigate" if psi > PSI_INVESTIGATE else "watch"
                found.append(
                    _alert(
                        month,
                        "stability",
                        f"PSI {name}",
                        psi,
                        PSI_WATCH,
                        level,
                        f"Distribution of {name} moved away from development",
                    )
                )
    ref = reference["performance"]["approved"]
    for month, row in perf["booked"].iterrows():
        if row["bads"] < MIN_BADS:
            continue
        drop = ref["ks"] - row["KS %"]
        if drop > KS_DIAGNOSE_PP:
            level = "alert" if drop > KS_ALERT_PP else "investigate"
            found.append(
                _alert(
                    month,
                    "performance",
                    "KS drop (booked) pp",
                    drop,
                    KS_DIAGNOSE_PP,
                    level,
                    "Discrimination on booked loans below development",
                )
            )
        ratio = row["observed / predicted"]
        lo, hi = CALIBRATION_BAND
        if not lo <= ratio <= hi:
            found.append(
                _alert(
                    month,
                    "performance",
                    "observed / predicted",
                    ratio,
                    hi,
                    "alert",
                    "Bad rate outside the calibration band: PD needs recalibration",
                )
            )
    lo, hi = CALIBRATION_BAND
    as_of = perf["booked"].index.max() if len(perf["booked"]) else None
    for band, row in perf["bands"].iterrows():
        ratio = row["bad rate %"] / row["predicted %"]
        if row["bads"] >= MIN_BADS and not lo <= ratio <= hi:
            found.append(
                _alert(
                    as_of,
                    "performance",
                    f"observed / predicted, band {band}",
                    ratio,
                    hi if ratio > hi else lo,
                    "investigate",
                    f"Band {band} outside the calibration band, matured cohorts pooled",
                )
            )
    approval_ref = reference["policy"]["approval_rate"] * 100
    for month, row in ops["monthly"].iterrows():
        gap = row["approval %"] - approval_ref
        if abs(gap) > APPROVAL_CHANGE_PP:
            found.append(
                _alert(
                    month,
                    "policy",
                    "approval rate vs development pp",
                    gap,
                    APPROVAL_CHANGE_PP,
                    "investigate",
                    "Approval rate far from the development reference",
                )
            )
    approval = ops["monthly"]["approval %"]
    for month, change in approval.diff().dropna().items():
        if abs(change) > APPROVAL_MOM_PP:
            found.append(
                _alert(
                    month,
                    "policy",
                    "approval rate change pp",
                    change,
                    APPROVAL_MOM_PP,
                    "investigate",
                    f"Approval rate moved {change:+.1f} pp in one month",
                )
            )
    rules = ops["rules"]
    for rule in rules.columns:
        if not rule.startswith(("K", "R")):  # hard rules and review reasons
            continue
        series = rules[rule] / 100
        for (_, before), (month, now) in zip(
            series.iloc[:-1].items(), series.iloc[1:].items(), strict=True
        ):
            if before >= RULE_MIN_RATE and abs(now / before - 1) > RULE_CHANGE:
                found.append(
                    _alert(
                        month,
                        "policy",
                        f"rule {rule} rate change",
                        now / before - 1,
                        RULE_CHANGE,
                        "investigate",
                        f"Rule {rule} fires {now:.1%} of the time, {before:.1%} the month before",
                    )
                )
    table = pd.DataFrame(
        found, columns=["month", "area", "indicator", "value", "threshold", "level", "message"]
    )
    rank = table["level"].map(LEVELS)
    return table.assign(_rank=-rank).sort_values(["month", "_rank"]).drop(columns="_rank")


def _consecutive(months: list[str], n: int = 2) -> bool:
    periods = sorted(pd.Period(m, "M") for m in set(months))
    run = 1
    for a, b in zip(periods, periods[1:], strict=False):
        run = run + 1 if (b - a).n == 1 else 1
        if run >= n:
            return True
    return n <= 1 and bool(periods)


def recommendation(found: pd.DataFrame) -> dict:
    """Apply docs/retraining_criteria.md to the alerts."""

    def months(mask):
        return found.loc[mask, "month"].tolist()

    calibration = months(found["indicator"] == "observed / predicted")
    ks_alert = months((found["indicator"] == "KS drop (booked) pp") & (found["level"] == "alert"))
    score_shift = months((found["indicator"] == "score PSI") & (found["level"] == "investigate"))
    variables = found[found["indicator"].str.startswith("PSI ") & (found["level"] == "investigate")]
    many_variables = (variables.groupby("month").size() >= 3).any()
    if _consecutive(ks_alert) or score_shift or many_variables:
        return {
            "action": "retrain",
            "why": "Discrimination fell for two matured months in a row, or the population "
            "the model scores has moved (score PSI above 0.25, or three or more "
            "variables above 0.25 in one month).",
        }
    if _consecutive(calibration):
        return {
            "action": "recalibrate",
            "why": "The ranking holds but observed bad rates sit outside the calibration "
            "band for two matured months in a row: refit the score-to-PD mapping, "
            "keep the points.",
        }
    if len(found):
        return {"action": "monitor", "why": "Isolated alerts: investigate, no model change yet."}
    return {"action": "none", "why": "No alert."}


# ----------------------------------------------------------------- report ----


def run(frames: dict[str, pd.DataFrame], as_of: str, model_dir: Path = MODEL_DIR) -> dict:
    card, reference = load_reference(model_dir)
    dec = frames["decisions"]
    dec = dec[dec["month"] <= as_of]
    stab = stability(dec, card, reference)
    perf = performance(dec, frames["outcomes"], as_of)
    ops = policy_operations(dec, frames["runs"])
    found = alerts(stab, perf, ops, reference)
    return {
        "as_of": as_of,
        "reference": reference,
        "stability": stab,
        "score_distribution": score_distribution(dec, reference),
        "performance": perf,
        "operations": ops,
        "champion_challenger": champion_challenger(dec, frames["outcomes"], as_of),
        "vintages": vintages(dec, frames["outcomes"], as_of),
        "alerts": found,
        "recommendation": recommendation(found),
    }


def build_report(result: dict) -> str:
    from credit_score.data.audit import to_markdown

    ref = result["reference"]
    stab, perf, ops = result["stability"], result["performance"], result["operations"]
    top_psi = stab["variables"].max().sort_values(ascending=False).head(5)
    found = result["alerts"]
    summary = (
        found.groupby(["area", "indicator", "level"])
        .agg(
            months=("month", "count"),
            first=("month", "min"),
            last=("month", "max"),
            worst=("value", lambda v: v.abs().max()),
        )
        .reset_index()
        .set_index("area")
        if len(found)
        else pd.DataFrame({"note": ["no alerts"]}).rename_axis("area")
    )
    rec = result["recommendation"]
    return (
        "\n\n".join(
            [
                "# Monitoring report",
                f"Generated by `make monitor`. Do not edit by hand. As of **{result['as_of']}**, "
                f"model `{ref['model_version']}`, policy `{ref['policy_version']}`. Reference: "
                f"{ref['population']}.",
                f"## Recommendation: **{rec['action']}**",
                rec["why"],
                "## Alerts",
                to_markdown(summary),
                "## Stability",
                to_markdown(stab["monthly"], decimals=4),
                "Highest PSI reached by each of the five most-moved variables:",
                to_markdown(top_psi.rename("max PSI").rename_axis("variable").to_frame(), 4),
                "## Performance on booked loans, matured cohorts",
                f"Development reference on loans the same policy approves: KS "
                f"{ref['performance']['approved']['ks']:.2f}%, AUC "
                f"{ref['performance']['approved']['auc']:.4f}, bad rate "
                f"{ref['performance']['approved']['bad_rate']:.2%}.",
                to_markdown(perf["booked"], decimals=3),
                "By band, pooled:",
                to_markdown(perf["bands"], decimals=3),
                "Full population, simulation only (in production, rejected applicants have no "
                f"outcome). Development reference: KS {ref['performance']['all']['ks']:.2f}%, AUC "
                f"{ref['performance']['all']['auc']:.4f}.",
                to_markdown(perf["everyone"], decimals=3),
                "## Policy and operation",
                f"Development reference approval rate: {ref['policy']['approval_rate']:.1%}.",
                to_markdown(ops["monthly"], decimals=2),
                "Share of applications decided by each rule, %:",
                to_markdown(ops["rules"], decimals=2),
                "Champion and challenger:",
                to_markdown(result["champion_challenger"], decimals=3),
                "## Vintages",
                "Cumulative % of booked loans that reached 90+ days past due, by months on book:",
                to_markdown(
                    result["vintages"][
                        [
                            c
                            for c in result["vintages"].columns
                            if c in (3, 6, 9, 12, 15, 18, 21, 24)
                        ]
                    ],
                    2,
                ),
            ]
        )
        + "\n"
    )


def export_reference_inputs(data: Path, out_dir: Path, rows: int = 20_000) -> Path:
    """A development sample of the raw inputs, the reference for the Evidently reports."""
    card, _ = load_reference()
    inputs = [c for c in scoring.required_inputs(card, {}) if c != "loan_to_income"]
    inputs += ["loan_amnt", "emp_length"]
    dev = pd.read_parquet(data / "modeling.parquet")
    dev = dev[dev["split"] == "dev"]
    sample = dev.sample(min(rows, len(dev)), random_state=1)[list(dict.fromkeys(inputs))]
    path = out_dir / "reference_inputs.parquet"
    sample.to_parquet(path, index=False)
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--as-of", default="2019-03")
    parser.add_argument(
        "--database-url",
        default=os.environ.get("DATABASE_URL", "postgresql://credit:credit@127.0.0.1:5432/credit"),
    )
    parser.add_argument("--from-dir", type=Path, help="read parquet frames instead of the database")
    parser.add_argument("--export-dir", type=Path, help="also save the frames as parquet here")
    parser.add_argument("--report", type=Path, default=Path("docs/monitoring_report.md"))
    parser.add_argument("--data", type=Path, default=Path("data/processed"))
    args = parser.parse_args()
    frames = load_directory(args.from_dir) if args.from_dir else load_database(args.database_url)
    if args.export_dir:
        save_directory(frames, args.export_dir)
    result = run(frames, args.as_of)
    args.report.write_text(build_report(result))
    print(
        f"recommendation: {result['recommendation']['action']}; "
        f"{len(result['alerts'])} alerts -> {args.report}"
    )
    if args.export_dir and (args.data / "modeling.parquet").exists():
        export_reference_inputs(args.data, args.export_dir)


if __name__ == "__main__":
    main()
