"""Monitoring dashboard.

    streamlit run services/dashboard/src/dashboard/app.py

Reads the decision log and loan outcomes from PostgreSQL (``DATABASE_URL``)
or from parquet files (``DASHBOARD_DATA_DIR``), and shows them as of a month:
input stability updates with every month of applications, performance
appears only once a cohort has matured.
"""

import os
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

from credit_score import monitoring

MODEL_DIR = Path(os.environ.get("MODEL_DIR", monitoring.MODEL_DIR))

# Reference palette (dataviz skill): categorical slots in fixed order, a neutral for
# reference values, status colours reserved for alerts and always paired with a label.
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6"]
NEUTRAL = "#8a8985"
SEQUENTIAL = ["#cde2fb", "#104281"]
# Steps 150 to 700 of the sequential blue, for ordered categories such as cohorts.
BLUE_STEPS = [
    "#b7d3f6",
    "#9ec5f4",
    "#86b6ef",
    "#6da7ec",
    "#5598e7",
    "#3987e5",
    "#2a78d6",
    "#256abf",
    "#1c5cab",
    "#184f95",
    "#104281",
    "#0d366b",
]
ARMS = ["champion", "challenger"]
STATUS = {
    "alert": ("🔴", "Alert"),
    "investigate": ("🟠", "Investigate"),
    "watch": ("🟡", "Watch"),
}
ACTION = {
    "retrain": (st.error, "🔴", "Retrain"),
    "recalibrate": (st.warning, "🟠", "Recalibrate"),
    "monitor": (st.info, "🔵", "Monitor"),
    "none": (st.success, "🟢", "No action"),
}

st.set_page_config(page_title="Credit monitoring", page_icon="📈", layout="wide")


@st.cache_data(ttl=60, show_spinner="Reading the decision log…")
def load() -> dict[str, pd.DataFrame]:
    if data_dir := os.environ.get("DASHBOARD_DATA_DIR"):
        return monitoring.load_directory(Path(data_dir))
    url = os.environ.get("DATABASE_URL", "postgresql://credit:credit@127.0.0.1:5432/credit")
    return monitoring.load_database(url)


@st.cache_data(ttl=60, show_spinner="Computing indicators…")
def compute(as_of: str) -> dict:
    return monitoring.run(load(), as_of, MODEL_DIR)


def months_axis(title: str = "month") -> alt.X:
    return alt.X("month:O", title=title, axis=alt.Axis(labelAngle=-45))


def threshold_rules(values: dict[str, float]) -> alt.Chart:
    data = pd.DataFrame({"label": list(values), "y": list(values.values())})
    return (
        alt.Chart(data)
        .mark_rule(strokeDash=[4, 4], color=NEUTRAL)
        .encode(y="y:Q", tooltip=["label:N", alt.Tooltip("y:Q", format=".2f")])
    )


def line(
    data: pd.DataFrame,
    y: str,
    color: str | None = None,
    fmt: str = ".2f",
    y_title: str | None = None,
    palette: list[str] | None = None,
    domain: list[str] | None = None,
) -> alt.Chart:
    enc = {
        "x": months_axis(),
        "y": alt.Y(f"{y}:Q", title=y_title or y),
        "tooltip": ["month:O", alt.Tooltip(f"{y}:Q", format=fmt)],
    }
    if color:
        domain = domain or list(dict.fromkeys(data[color]))
        scale = alt.Scale(domain=domain, range=(palette or SERIES)[: len(domain)])
        enc["color"] = alt.Color(f"{color}:N", title=None, scale=scale, sort=domain)
        enc["tooltip"].insert(1, f"{color}:N")
    base = alt.Chart(data).encode(**enc)
    return base.mark_line(strokeWidth=2) + base.mark_point(size=60, filled=True)


def show(chart: alt.Chart, height: int = 280) -> None:
    st.altair_chart(chart.properties(height=height), use_container_width=True)


frames = load()
if frames["decisions"].empty:
    st.title("Credit monitoring")
    st.info("The decision log is empty. Run `make simulate` to replay 2017 through the stack.")
    st.stop()

first = frames["decisions"]["month"].min()
months = [str(p) for p in pd.period_range(first, "2019-03", freq="M")]

st.title("Credit monitoring")
as_of = st.select_slider(
    "As of",
    options=months,
    value=months[-1],
    help="Indicators use only what was known at the end of this month. Performance needs "
    "14 months: 12 of observation and time for the charge-off to be booked.",
)
result = compute(as_of)
ref = result["reference"]
stab, perf, ops = result["stability"], result["performance"], result["operations"]
st.caption(
    f"Model `{ref['model_version']}` · policy `{ref['policy_version']}` · reference: "
    f"{ref['population']}"
)

rec = result["recommendation"]
box, icon, label = ACTION[rec["action"]]
box(f"**{label}.** {rec['why']}", icon=icon)

monthly = ops["monthly"]
latest = monthly.index.max()
booked = perf["booked"]
tiles = st.columns(5)
tiles[0].metric("Applications to date", f"{int(monthly['applications'].sum()):,}")
tiles[1].metric(
    f"Approval rate, {latest}",
    f"{monthly.loc[latest, 'approval %']:.1f}%",
    f"{monthly.loc[latest, 'approval %'] - ref['policy']['approval_rate'] * 100:+.1f} pp "
    "vs development",
    delta_color="off",
)
tiles[2].metric(f"Score PSI, {latest}", f"{stab['monthly'].loc[latest, 'score PSI']:.3f}")
if len(booked):
    pooled = booked["bads"].sum() / booked["loans"].sum() * 100
    predicted = (booked["predicted %"] * booked["loans"]).sum() / booked["loans"].sum()
    tiles[3].metric(
        "Observed / predicted, matured",
        f"{pooled / predicted:.2f}",
        f"{len(booked)} cohorts matured",
        delta_color="off",
    )
else:
    tiles[3].metric(
        "Observed / predicted, matured", "—", "no cohort matured yet", delta_color="off"
    )
tiles[4].metric(f"Latency p95, {latest}", f"{monthly.loc[latest, 'latency p95 ms']:.0f} ms")

found = result["alerts"]
tab_names = [
    "Stability",
    "Performance",
    "Policy and operation",
    "Vintages",
    f"Alerts ({len(found)})",
]
stability_tab, performance_tab, policy_tab, vintage_tab, alert_tab = st.tabs(tab_names)

with stability_tab:
    left, right = st.columns(2)
    with left:
        st.subheader("Score PSI")
        data = (
            stab["monthly"][["score PSI", "score PSI vs previous 3 months"]]
            .rename(
                columns={
                    "score PSI": "against development",
                    "score PSI vs previous 3 months": "against previous 3 months",
                }
            )
            .reset_index()
            .melt(id_vars="month", var_name="reference", value_name="PSI")
            .dropna()
        )
        show(
            line(
                data,
                "PSI",
                color="reference",
                fmt=".3f",
                domain=["against development", "against previous 3 months"],
            )
            + threshold_rules(
                {"watch": monitoring.PSI_WATCH, "watch, rolling": monitoring.ROLLING_PSI_WATCH}
            )
        )
        st.caption(
            "Against development the score can look stable while the mix changes: "
            "a riskier inflow can move it closer to development, not further."
        )
    with right:
        st.subheader("Score distribution")
        dist = result["score_distribution"]
        pick = st.selectbox("Month", dist.index[1:][::-1], key="dist_month")
        bars = (
            dist.loc[["reference", pick]]
            .reset_index()
            .melt(id_vars="month", var_name="score bin", value_name="share")
        )
        bars["share"] *= 100
        order = list(dist.columns)
        show(
            alt.Chart(bars)
            .mark_bar(cornerRadiusTopLeft=4, cornerRadiusTopRight=4)
            .encode(
                x=alt.X("score bin:N", sort=order, title="score (development deciles)"),
                xOffset=alt.XOffset("month:N", sort=["reference", pick]),
                y=alt.Y("share:Q", title="% of applications"),
                color=alt.Color(
                    "month:N",
                    title=None,
                    sort=["reference", pick],
                    scale=alt.Scale(range=[NEUTRAL, SERIES[0]]),
                ),
                tooltip=["month:N", "score bin:N", alt.Tooltip("share:Q", format=".1f")],
            )
        )
    st.subheader("PSI by variable")
    heat = (
        stab["variables"].reset_index().melt(id_vars="month", var_name="variable", value_name="PSI")
    )
    show(
        alt.Chart(heat)
        .mark_rect(stroke="white", strokeWidth=2)
        .encode(
            x=months_axis(),
            y=alt.Y(
                "variable:N",
                title=None,
                sort=stab["variables"].max().sort_values(ascending=False).index.tolist(),
            ),
            color=alt.Color(
                "PSI:Q", scale=alt.Scale(range=SEQUENTIAL, domain=[0, 0.25], clamp=True)
            ),
            tooltip=["month:O", "variable:N", alt.Tooltip("PSI:Q", format=".3f")],
        ),
        height=420,
    )
    st.caption("Colour saturates at 0.25, the investigate threshold.")
    with st.expander("Missing values by input, against development"):
        missing = stab["missing"].T.mul(100)
        missing.insert(0, "development", stab["missing_reference"].reindex(missing.index) * 100)
        st.dataframe(missing.style.format("{:.1f}%", na_rep="—"), use_container_width=True)

with performance_tab:
    if booked.empty:
        st.info(
            f"No cohort has matured by {as_of}. The first, January 2017, matures in March 2018."
        )
    else:
        ref_ks = ref["performance"]["approved"]["ks"]
        left, right = st.columns(2)
        with left:
            st.subheader("KS on booked loans")
            data = booked.reset_index()
            show(
                line(data, "KS %", fmt=".1f")
                + threshold_rules(
                    {
                        "development": ref_ks,
                        f"−{monitoring.KS_ALERT_PP:g} pp alert": ref_ks - monitoring.KS_ALERT_PP,
                    }
                )
            )
            st.caption(
                f"Monthly cohorts have 20 to 40 bads, so monthly KS is noisy; alerts "
                f"need at least {monitoring.MIN_BADS} bads."
            )
        with right:
            st.subheader("Bad rate: observed against predicted")
            data = booked.reset_index()[["month", "bad rate %", "predicted %"]].melt(
                id_vars="month", var_name="series", value_name="rate"
            )
            data["series"] = data["series"].map(
                {"bad rate %": "observed", "predicted %": "predicted (model PD)"}
            )
            show(line(data, "rate", color="series", y_title="% of booked loans, 12 months"))
        left, right = st.columns(2)
        with left:
            st.subheader("By band, matured cohorts pooled")
            st.dataframe(
                perf["bands"].style.format(
                    {
                        "loans": "{:,}",
                        "bads": "{:,}",
                        "bad rate %": "{:.2f}",
                        "predicted %": "{:.2f}",
                        "AUC": "{:.3f}",
                        "KS %": "{:.1f}",
                    }
                ),
                use_container_width=True,
            )
        with right:
            st.subheader("Calibration by decile")
            dec = perf["deciles"]
            if len(dec):
                data = pd.DataFrame(
                    {
                        "decile": dec.index,
                        "observed": dec["bad_rate"] * 100,
                        "predicted (model PD)": dec["predicted_rate"] * 100,
                    }
                ).melt(id_vars="decile", var_name="series", value_name="rate")
                show(
                    alt.Chart(data)
                    .mark_bar(cornerRadiusTopLeft=4, cornerRadiusTopRight=4)
                    .encode(
                        x=alt.X("decile:O", title="score decile (1 = riskiest)"),
                        xOffset="series:N",
                        y=alt.Y("rate:Q", title="% bad"),
                        color=alt.Color("series:N", title=None, scale=alt.Scale(range=SERIES)),
                        tooltip=["decile:O", "series:N", alt.Tooltip("rate:Q", format=".2f")],
                    )
                )
        with st.expander("Full population (simulation only)"):
            st.caption(
                "In production a rejected applicant has no outcome. The simulation "
                "knows every outcome, which shows how much booking only approved "
                "loans restricts the range: development KS is "
                f"{ref['performance']['all']['ks']:.1f}% on everyone and {ref_ks:.1f}% "
                "on loans the policy approves."
            )
            st.dataframe(perf["everyone"].style.format(precision=3), use_container_width=True)

with policy_tab:
    left, right = st.columns(2)
    with left:
        st.subheader("Approval rate by arm")
        arms = (
            ops["arms"].reset_index().melt(id_vars="month", var_name="arm", value_name="approval %")
        )
        show(
            line(arms, "approval %", color="arm", fmt=".1f", domain=ARMS)
            + threshold_rules({"development": ref["policy"]["approval_rate"] * 100})
        )
    with right:
        st.subheader("Decisions by rule")
        rules = (
            ops["rules"].reset_index().melt(id_vars="month", var_name="rule", value_name="share")
        )
        order = ["K1", "K2", "S1", "S2", "R1", "R2", "S3"]
        present = [r for r in order if r in set(rules["rule"])]
        show(
            alt.Chart(rules)
            .mark_bar()
            .encode(
                x=months_axis(),
                y=alt.Y("share:Q", title="% of applications", stack="zero"),
                color=alt.Color(
                    "rule:N",
                    sort=present,
                    title=None,
                    scale=alt.Scale(domain=present, range=SERIES[: len(present)]),
                ),
                order=alt.Order("rule_order:Q"),
                tooltip=["month:O", "rule:N", alt.Tooltip("share:Q", format=".1f")],
            )
            .transform_calculate(rule_order=f"indexof({present}, datum.rule)")
        )
    st.subheader("Champion and challenger")
    st.dataframe(result["champion_challenger"].style.format(precision=2), use_container_width=True)
    st.subheader("Latency of the decision service")
    lat = monthly.reset_index()[["month", "latency p50 ms", "latency p95 ms"]].melt(
        id_vars="month", var_name="percentile", value_name="ms"
    )
    show(line(lat, "ms", color="percentile", fmt=".0f", y_title="ms per decision"), 240)
    if "errors" in monthly:
        st.caption(
            f"Failed requests to date: {int(monthly['errors'].sum()):,}. Latency is "
            "measured under the simulator's 16 concurrent clients."
        )

with vintage_tab:
    st.subheader("Cumulative 90+ days past due, booked loans")
    vint = result["vintages"]
    if vint.empty or vint.shape[1] == 0:
        st.info("No vintage has enough months on book yet.")
    else:
        data = (
            vint.reset_index()
            .melt(id_vars="issued", var_name="months on book", value_name="rate")
            .dropna()
        )
        show(
            alt.Chart(data)
            .mark_line(strokeWidth=2)
            .encode(
                x=alt.X("months on book:Q"),
                y=alt.Y("rate:Q", title="% of booked loans"),
                color=alt.Color(
                    "issued:O",
                    title="issued",
                    scale=alt.Scale(
                        domain=list(vint.index),
                        range=[
                            BLUE_STEPS[round(i * (len(BLUE_STEPS) - 1) / max(len(vint) - 1, 1))]
                            for i in range(len(vint))
                        ],
                    ),
                ),
                tooltip=["issued:O", "months on book:Q", alt.Tooltip("rate:Q", format=".2f")],
            ),
            380,
        )
        st.caption(
            "Ordered cohorts use one hue, light to dark. The last two months of each "
            "cohort are left out: a loan that just reached 90 days is not charged off yet."
        )

with alert_tab:
    if found.empty:
        st.success("No alert.", icon="🟢")
    else:
        shown = found.copy()
        shown["level"] = shown["level"].map(lambda v: " ".join(STATUS[v]))
        st.dataframe(
            shown.style.format({"value": "{:.3f}", "threshold": "{:.2f}"}),
            use_container_width=True,
            hide_index=True,
        )
    st.caption("Thresholds and what each combination triggers: docs/retraining_criteria.md.")
