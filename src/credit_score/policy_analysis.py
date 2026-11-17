"""Policy analysis: what a cut-off, a knock-out rule or a limit is worth.

Writes ``docs/policy_analysis.md``. Every number quoted in ``docs/policy.md``
comes from this report.

Economics are measured, not modelled: the population is loans that have run
their full term, and profit is the cash actually received minus the amount
lent and a funding cost. Assumptions are the constants below (ADR-004).
"""

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
import statsmodels.api as sm

from credit_score import scorecard
from credit_score.config import TARGET
from credit_score.data.audit import to_markdown
from credit_score.target import bad_within, charged_off

# --- Assumptions (ADR-004) -------------------------------------------------
LGD = 0.90  # loss on the balance outstanding at default, after recoveries
FUNDING_COST = 0.065  # cost of funds and servicing over the loan's life, as a share of the amount
STRESS = 1.34  # charge-offs multiplied by this at every score: 2016 against development
MATURED_TERM = 36
CUTOFFS = range(540, 641, 5)
LTI_EDGES = [0, 0.10, 0.20, 0.30, 0.40, np.inf]
LTI_LABELS = ["≤ 10%", "10–20%", "20–30%", "30–40%", "> 40%"]
REJECT_WINDOW = ("2013-01-01", "2013-09-30")  # rejected file's score is on the FICO scale here
PARCELING_FACTORS = (1.0, 2.0, 3.0)


def matured(dev_accepted: pd.DataFrame, card: dict) -> pd.DataFrame:
    """Development loans that have run their full term, with score and realised cash flows."""
    df = dev_accepted[dev_accepted["term"] == MATURED_TERM].copy()
    df = df[df["loan_status"].isin(["Fully Paid", "Charged Off"])]
    df["score"] = scorecard.score(df, card)
    df["band"] = scorecard.band(df["score"], card["bands"])
    df["charged_off"] = charged_off(df)
    df["profit"] = df["total_pymnt"] - df["loan_amnt"] * (1 + FUNDING_COST)
    # Stress: STRESS times as many charge-offs at every score, population size unchanged.
    p = df["charged_off"].mean()
    df["w_stress"] = np.where(df["charged_off"], STRESS, (1 - STRESS * p) / (1 - p))
    return df


def lgd_table(df: pd.DataFrame) -> pd.DataFrame:
    co = df[df["charged_off"]]
    ead = co["loan_amnt"] - co["total_rec_prncp"]
    recovered = co["recoveries"] - co["collection_recovery_fee"]
    rows = {}
    for label, part in [("all", co), *co.groupby(co["issue_d"].dt.year)]:
        e, r = ead.loc[part.index], recovered.loc[part.index]
        rows[str(label)] = {
            "charged-off loans": len(part),
            "balance at default, % of amount": e.sum() / part["loan_amnt"].sum() * 100,
            "recovered, % of balance": r.sum() / e.sum() * 100,
            "LGD %": (1 - r.sum() / e.sum()) * 100,
        }
    return pd.DataFrame.from_dict(rows, orient="index").rename_axis("issue year")


def economics(part: pd.DataFrame, total: int, weight: str | None = None) -> dict:
    """Outcome of approving ``part`` out of ``total`` applicants."""
    w = part[weight] if weight else pd.Series(1.0, index=part.index)
    lent = (part["loan_amnt"] * w).sum()
    return {
        "approved %": len(part) / total * 100,
        "bad 12m %": (part[TARGET] * w).sum() / w.sum() * 100 if len(part) else np.nan,
        "charged off %": (part["charged_off"] * w).sum() / w.sum() * 100 if len(part) else np.nan,
        "margin % of lent": (part["profit"] * w).sum() / lent * 100 if lent else np.nan,
        "profit per 1,000 applicants, $k": (part["profit"] * w).sum() / total,
    }


def cutoff_curve(df: pd.DataFrame, weight: str | None = None) -> pd.DataFrame:
    rows = {c: economics(df[df["score"] >= c], len(df), weight) for c in CUTOFFS}
    rows = {"approve all": economics(df, len(df), weight), **rows}
    return pd.DataFrame.from_dict(rows, orient="index").rename_axis("approve score ≥")


def marginal_table(df: pd.DataFrame) -> pd.DataFrame:
    """Each 5-point slice of score on its own: is the marginal loan worth making?"""
    slices = pd.cut(df["score"], [-np.inf, *CUTOFFS, np.inf], right=False)
    rows = {}
    for interval, part in df.groupby(slices, observed=True):
        base, stress = economics(part, len(df)), economics(part, len(df), "w_stress")
        rows[f"[{interval.left:g}, {interval.right:g})"] = {
            "% of loans": base["approved %"],
            "charged off %": base["charged off %"],
            "margin % of lent": base["margin % of lent"],
            "margin under stress %": stress["margin % of lent"],
        }
    return pd.DataFrame.from_dict(rows, orient="index").rename_axis("score")


def best_cutoff(curve: pd.DataFrame) -> int:
    return int(curve.drop(index="approve all")["profit per 1,000 applicants, $k"].idxmax())


def swap_set(df: pd.DataFrame, cutoff: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    """New policy (score ≥ cutoff) against legacy rankings at the same approval rate."""
    new = df["score"] >= cutoff
    n = int(new.sum())
    by_fico = df.sort_values(["fico", "dti"], ascending=[False, True]).index[:n]
    grade_rank = df["sub_grade"].rank(method="first")
    policies = {
        f"New: score ≥ {cutoff}": new,
        "Legacy: bureau score ranking, lower DTI first on ties": df.index.isin(by_fico),
        "Reference: Lending Club sub-grade ranking": grade_rank <= n,
    }
    summary = pd.DataFrame.from_dict(
        {name: economics(df[mask], len(df)) for name, mask in policies.items()}, orient="index"
    ).rename_axis("policy")
    old = pd.Series(df.index.isin(by_fico), index=df.index)
    groups = {
        "Approved by both": new & old,
        "Swap-in: approved only by the new policy": new & ~old,
        "Swap-out: approved only by the legacy policy": ~new & old,
        "Rejected by both": ~new & ~old,
    }
    detail = pd.DataFrame.from_dict(
        {
            name: {
                "% of applicants": mask.mean() * 100,
                "mean score": df.loc[mask, "score"].mean(),
                "mean FICO": df.loc[mask, "fico"].mean(),
                "bad 12m %": df.loc[mask, TARGET].mean() * 100,
                "charged off %": df.loc[mask, "charged_off"].mean() * 100,
                "margin % of lent": df.loc[mask, "profit"].sum()
                / df.loc[mask, "loan_amnt"].sum()
                * 100,
            }
            for name, mask in groups.items()
        },
        orient="index",
    ).rename_axis("group")
    return summary, detail


KNOCKOUT_CANDIDATES = {
    "DTI above 35": lambda d: d["dti"] > 35,
    "DTI above 30": lambda d: d["dti"] > 30,
    "3 or more inquiries in 6 months": lambda d: d["inq_last_6mths"] >= 3,
    "10 or more accounts opened in 24 months": lambda d: d["acc_open_past_24mths"] >= 10,
    "Loan above 35% of annual income": lambda d: d["loan_to_income"] > 0.35,
    "Annual income below 25,000": lambda d: d["annual_inc"] < 25_000,
    "Employment length not reported": lambda d: d["emp_length"].isna(),
    "Purpose: small business": lambda d: d["purpose"] == "small_business",
}


def knockout_table(df: pd.DataFrame, cutoff: int) -> pd.DataFrame:
    """Each candidate rule judged on the loans the score cut-off would already approve."""
    passed = df[df["score"] >= cutoff]
    rows = {}
    for name, rule in KNOCKOUT_CANDIDATES.items():
        hit_all, hit = rule(df), rule(passed)
        part = passed[hit]
        base, stress = economics(part, len(passed)), economics(part, len(passed), "w_stress")
        rows[name] = {
            "% of all applicants": hit_all.mean() * 100,
            "% of score-approved": hit.mean() * 100,
            "charged off %": base["charged off %"],
            "margin % of lent": base["margin % of lent"],
            "margin under stress %": stress["margin % of lent"],
        }
    rest = economics(passed, len(passed))
    rows["(all score-approved, for comparison)"] = {
        "% of all applicants": len(passed) / len(df) * 100,
        "% of score-approved": 100.0,
        "charged off %": rest["charged off %"],
        "margin % of lent": rest["margin % of lent"],
        "margin under stress %": economics(passed, len(passed), "w_stress")["margin % of lent"],
    }
    return pd.DataFrame.from_dict(rows, orient="index").rename_axis("rule")


def limit_matrix(df: pd.DataFrame, value: str) -> pd.DataFrame:
    """Band by loan-to-income bucket. ``value`` is 'margin', 'stress' or 'loans'."""
    lti = pd.cut(df["loan_to_income"], LTI_EDGES, labels=LTI_LABELS)
    out = {}
    for (band, bucket), part in df.groupby([df["band"], lti], observed=True):
        if value == "loans":
            cell = len(part)
        else:
            weight = "w_stress" if value == "stress" else None
            cell = economics(part, len(df), weight)["margin % of lent"]
        out.setdefault(band, {})[bucket] = cell
    table = pd.DataFrame.from_dict(out, orient="index").reindex(columns=LTI_LABELS)
    return table.reindex(list("ABCDE")).rename_axis("band")


# Proposed policy (docs/policy.md). Kept here so its impact is measured by the same code.
PROPOSED = {
    "min_fico": 660,  # below it the scorecard has no training data
    "max_accounts_opened_24m": 9,
    "reject_below": 580,
    "approve_from": 595,
    "max_loan_to_income": {"A": 0.40, "B": 0.40, "C": 0.30, "D": 0.10},
}


def apply_policy(df: pd.DataFrame, policy: dict = PROPOSED) -> pd.Series:
    """Decision per loan: knock-outs first, then the score cut-offs."""
    decision = pd.Series("REVIEW", index=df.index)
    decision[df["score"] >= policy["approve_from"]] = "APPROVE"
    decision[df["emp_length"].isna() & (decision == "APPROVE")] = "REVIEW"
    decision[df["score"] < policy["reject_below"]] = "REJECT"
    decision[df["acc_open_past_24mths"] > policy["max_accounts_opened_24m"]] = "REJECT"
    decision[df["fico"] < policy["min_fico"]] = "REJECT"
    return decision


def policy_impact(df: pd.DataFrame, policy: dict = PROPOSED) -> pd.DataFrame:
    decision = apply_policy(df, policy)
    rows = {}
    for name in ("APPROVE", "REVIEW", "REJECT"):
        part = df[decision == name]
        base, stress = economics(part, len(df)), economics(part, len(df), "w_stress")
        rows[name] = {
            "% of applicants": base["approved %"],
            "bad 12m %": base["bad 12m %"],
            "charged off %": base["charged off %"],
            "margin % of lent": base["margin % of lent"],
            "margin under stress %": stress["margin % of lent"],
            "profit per 1,000 applicants, $k": base["profit per 1,000 applicants, $k"],
            "under stress, $k": stress["profit per 1,000 applicants, $k"],
        }
    return pd.DataFrame.from_dict(rows, orient="index").rename_axis("decision")


def limit_impact(df: pd.DataFrame, policy: dict = PROPOSED) -> pd.DataFrame:
    """Among approved loans, how many asked for more than their band's limit allows."""
    approved = df[apply_policy(df, policy) == "APPROVE"]
    cap = approved["band"].map(policy["max_loan_to_income"]) * approved["annual_inc"]
    over = approved["loan_amnt"] > cap.clip(upper=40_000)
    rows = {}
    for band, part in approved.groupby("band"):
        rows[band] = {
            "approved loans": len(part),
            "max loan-to-income %": policy["max_loan_to_income"][band] * 100,
            "% asking above the limit": over.loc[part.index].mean() * 100,
        }
    return pd.DataFrame.from_dict(rows, orient="index").rename_axis("band")


def reject_inference(accepted: pd.DataFrame, rejected: pd.DataFrame) -> dict:
    """Parceling on the two variables both files share: bureau score and DTI.

    The full scorecard cannot be applied to rejected applications, so this
    answers a narrower question: how much does a bad rate measured on
    approved loans understate the bad rate of everyone who applied?
    """
    lo, hi = REJECT_WINDOW
    acc = accepted[
        accepted["issue_d"].between(lo, hi) & (accepted["application_type"] == "Individual")
    ].copy()
    acc["bad"] = bad_within(acc, 12)
    acc = acc[acc["dti"].between(0, 100)]
    rej = rejected[rejected["application_d"].between(lo, hi)]
    usable = rej["risk_score"].between(300, 850) & rej["dti"].between(0, 100)
    coverage = {
        "accepted loans": len(acc),
        "rejected applications": len(rej),
        "rejected with a usable score and DTI": int(usable.sum()),
        "of those, score ≥ 660 (inside the scorecard's range)": int(
            (rej.loc[usable, "risk_score"] >= 660).sum()
        ),
    }
    rej = rej[usable].rename(columns={"risk_score": "fico"})

    known = acc[["fico_range_low", "dti"]].rename(columns={"fico_range_low": "fico"})
    x = sm.add_constant(known.astype(float))
    model = sm.Logit(acc["bad"].astype(float), x).fit(disp=0)
    acc["p"] = model.predict(x)
    rej_x = sm.add_constant(rej[["fico", "dti"]].astype(float), has_constant="add")
    rej = rej.assign(p_known=np.asarray(model.predict(rej_x), dtype=float))
    acc = acc.rename(columns={"fico_range_low": "fico"})

    rows = {}
    for floor in (660, 680, 700, 720):
        a, r = acc[acc["fico"] >= floor], rej[rej["fico"] >= floor]
        row = {
            "accepted": len(a),
            "rejected": len(r),
            "bad % on accepted": a["bad"].mean() * 100,
        }
        for k in PARCELING_FACTORS:
            inferred = (r["p_known"] * k).clip(upper=1).sum()
            row[f"through-the-door bad %, rejects ×{k:g}"] = (
                (a["bad"].sum() + inferred) / (len(a) + len(r)) * 100
            )
        rows[f"bureau score ≥ {floor}"] = row
    table = pd.DataFrame.from_dict(rows, orient="index").rename_axis("population")
    below = rej[rej["fico"] < 660]
    extrapolated = {
        "applications": len(below),
        "mean bureau score": below["fico"].mean(),
        "bad % predicted by extrapolation": below["p_known"].mean() * 100,
    }
    coefficients = pd.DataFrame({"coefficient": model.params, "p-value": model.pvalues})
    return {
        "coverage": pd.Series(coverage, name="count").rename_axis("").to_frame(),
        "table": table,
        "below_range": pd.Series(extrapolated, name="value").rename_axis("").to_frame(),
        "coefficients": coefficients.rename_axis("term"),
    }


def run(data: Path, models: Path) -> dict:
    card = json.loads((models / "scorecard-v1" / "card.json").read_text())
    modeling = pd.read_parquet(data / "modeling.parquet")
    accepted = pd.read_parquet(data / "accepted.parquet")
    cash = accepted.set_index("id")[["total_rec_prncp", "collection_recovery_fee", "emp_length"]]
    dev = modeling[modeling["split"] == "dev"].drop(columns=["emp_length"]).join(cash, on="id")
    df = matured(dev, card)
    base, stress = cutoff_curve(df), cutoff_curve(df, "w_stress")
    cut_base, cut_stress = best_cutoff(base), best_cutoff(stress)
    swap_summary, swap_detail = swap_set(df, cut_stress)
    return {
        "df": df,
        "lgd": lgd_table(df),
        "curve": base,
        "curve_stress": stress,
        "cut_base": cut_base,
        "cut_stress": cut_stress,
        "marginal": marginal_table(df),
        "swap_summary": swap_summary,
        "swap_detail": swap_detail,
        "knockouts": knockout_table(df, cut_stress),
        "limits": {v: limit_matrix(df, v) for v in ("margin", "stress", "loans")},
        "impact": policy_impact(df),
        "limit_impact": limit_impact(df),
        "ead_bad_12m": float(
            (df.loc[df[TARGET], "loan_amnt"] - df.loc[df[TARGET], "total_rec_prncp"]).sum()
            / df.loc[df[TARGET], "loan_amnt"].sum()
        ),
        "rejects": reject_inference(accepted, pd.read_parquet(data / "rejected.parquet")),
    }


def build_report(out: dict) -> str:
    df = out["df"]
    return (
        "\n\n".join(
            [
                "# Policy analysis",
                "Generated by `make policy-analysis`. Do not edit by hand.",
                "## Contract",
                f"- **Question.** Where should the score cut-off sit, which knock-out rules earn "
                f"their place, and how large a loan can each band carry?\n"
                f"- **Population.** Individual {MATURED_TERM}-month loans issued 2013–2015 that "
                f"have run their full term: {len(df):,} loans, "
                f"{df['charged_off'].mean():.2%} charged "
                "off. This is the scorecard's development sample, so the figures are in-sample "
                "for the model; the 2017 simulation is the out-of-sample test of the policy.\n"
                "- **Profit.** Cash received over the life of the loan, recoveries included, minus "
                f"the amount lent and a funding and servicing cost of {FUNDING_COST:.1%} of the "
                "amount. Measured, not modelled.\n"
                f"- **Stress.** {STRESS:g} times as many charge-offs at every score, the rise "
                "observed in 12-month defaults between development and 2016.\n"
                "- **Limits.** Applicants are loans Lending Club approved, priced by Lending "
                "Club's own grade. A margin here is a margin at those prices. 60-month loans are "
                "not in the economics because most have not matured.",
                "## Loss given default",
                f"Assumption used downstream: LGD = {LGD:.0%} of the balance at default.",
                to_markdown(out["lgd"]),
                "## Cut-off curve",
                f"Profit is highest at a cut-off of **{out['cut_base']}** in the base case and "
                f"**{out['cut_stress']}** under stress.",
                "Base case:",
                to_markdown(out["curve"]),
                "Under stress:",
                to_markdown(out["curve_stress"]),
                "## Marginal loans",
                "Each 5-point slice of score on its own. The cut-off belongs where the marginal "
                "slice stops paying for itself.",
                to_markdown(out["marginal"]),
                "## Swap-set",
                f"The score policy at {out['cut_stress']} against legacy rankings approving the "
                "same number of loans.",
                to_markdown(out["swap_summary"]),
                to_markdown(out["swap_detail"]),
                "## Knock-out candidates",
                f"Each rule is judged on loans the score cut-off of {out['cut_stress']} would "
                "already approve: a rule earns its place only if it removes loans the score lets "
                "through and that lose money.",
                to_markdown(out["knockouts"]),
                "## Limits: band by loan-to-income",
                "Margin, % of amount lent:",
                to_markdown(out["limits"]["margin"]),
                "Margin under stress, %:",
                to_markdown(out["limits"]["stress"]),
                "Loans in each cell:",
                to_markdown(out["limits"]["loans"]),
                "## Proposed policy",
                "Impact of the policy in `docs/policy.md` on this population: reject below a "
                f"bureau score of {PROPOSED['min_fico']}, with more than "
                f"{PROPOSED['max_accounts_opened_24m']} accounts opened in 24 months, or with a "
                f"score below {PROPOSED['reject_below']}; approve from {PROPOSED['approve_from']}; "
                "review in between and when employment length is not reported.",
                to_markdown(out["impact"]),
                "Limits by band:",
                to_markdown(out["limit_impact"]),
                f"Balance at default of loans that go bad within 12 months: "
                f"{out['ead_bad_12m']:.1%} of the amount lent.",
                "## Reject inference",
                f"Window: applications from {REJECT_WINDOW[0]} to {REJECT_WINDOW[1]}, the only "
                "period in which the rejected file's score is on the FICO scale and almost always "
                "present. The two files share two usable variables, bureau score and DTI, so the "
                "known-good-bad model below uses only those.",
                to_markdown(out["rejects"]["coverage"]),
                to_markdown(out["rejects"]["coefficients"], decimals=4),
                "Parceling: each rejected application gets the bad probability of an approved "
                "loan with the same score and DTI, multiplied by a factor for the unobserved "
                "reasons it was rejected.",
                to_markdown(out["rejects"]["table"]),
                "Rejected applications below a bureau score of 660, where no approved loan exists "
                "and the model can only extrapolate:",
                to_markdown(out["rejects"]["below_range"]),
            ]
        )
        + "\n"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path("data/processed"))
    parser.add_argument("--models", type=Path, default=Path("models"))
    parser.add_argument("--report", type=Path, default=Path("docs/policy_analysis.md"))
    args = parser.parse_args()
    out = run(args.data, args.models)
    args.report.write_text(build_report(out))
    print(f"cut-off: base {out['cut_base']}, stress {out['cut_stress']} -> {args.report}")


if __name__ == "__main__":
    main()
