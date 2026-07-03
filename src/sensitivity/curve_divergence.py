"""Ported from Scripts/Sensitivity/assess_curve_divergence_primary_outcomes.py.

Timing of ARB vs DHP-CCB IPTW survival-curve divergence for the two primary
outcomes (stroke_s1, b4_mci). Writes one CSV per analysis to
outputs/sensitivity/curve_divergence/:

  1. Overall IPTW Cox HR                            -> overall_hr_primary_outcomes.csv
  2. PH check (scaled Schoenfeld residuals ~ log t) -> time_interaction_primary_outcomes.csv
  3. Interval-specific weighted HRs                 -> interval_hr_primary_outcomes.csv
  4. Weighted KM absolute risks at landmarks        -> landmark_riskdiff_primary_outcomes.csv
  5. Lagged landmark analyses for b4_mci            -> lagged_b4_mci.csv

Landmarks and intervals are read from config.analysis.sensitivity.curve_divergence.
"""

from __future__ import annotations

import warnings

import numpy as np
import pandas as pd
from lifelines import CoxPHFitter, KaplanMeierFitter
from scipy.stats import pearsonr, spearmanr

from src.config import Config

warnings.filterwarnings("ignore")

# Exposure and weight columns in the survival dataset (1 = ARB, 0 = DHP-CCB).
TREATMENT = "treated"
WEIGHT = "iptw"


def _primary_outcomes(config: Config) -> dict[str, dict[str, str]]:
    """{name: {event, time}} for the primary outcomes, from config."""
    return {
        o.name: {"event": f"{o.name}_event", "time": f"{o.name}_time_years"}
        for o in config.analysis.outcomes.order
        if o.role == "primary"
    }


def _interval_cox(
    data: pd.DataFrame,
    time_col: str,
    event_col: str,
    weight_col: str,
    t_start: float,
    t_end: float | None,
    min_events: int = 10,
) -> dict:
    """Fit weighted Cox restricted to interval [t_start, t_end).
    Includes only persons at risk at t_start (time > t_start). Time is
    re-zeroed from t_start; events after t_end are censored."""
    d = data.copy()
    d = d[d[time_col] > t_start].copy()
    d["_t"] = d[time_col] - t_start
    if t_end is not None:
        width = t_end - t_start
        d["_e"] = ((d[event_col] == 1) & (d["_t"] <= width)).astype(int)
        d["_t"] = d["_t"].clip(upper=width)
    else:
        d["_e"] = d[event_col].astype(int)
    d = d[d["_t"] > 0]

    n = len(d)
    n_arb = int((d[TREATMENT] == 1).sum())
    n_ccb = int((d[TREATMENT] == 0).sum())
    ev = int(d["_e"].sum())
    ev_arb = int(d.loc[d[TREATMENT] == 1, "_e"].sum())
    ev_ccb = int(d.loc[d[TREATMENT] == 0, "_e"].sum())

    if ev < min_events:
        return dict(
            n_at_risk=n, n_arb=n_arb, n_ccb=n_ccb, n_events=ev, ev_arb=ev_arb, ev_ccb=ev_ccb,
            hr=None, ci_lower=None, ci_upper=None, p_value=None, converged=False,
            note=f"Sparse: {ev} events < threshold {min_events}; HR not estimated",
        )
    try:
        cph = CoxPHFitter()
        cph.fit(
            d[["_t", "_e", weight_col, TREATMENT]].dropna(),
            duration_col="_t",
            event_col="_e",
            weights_col=weight_col,
            robust=True,
        )
        row = cph.summary.loc[TREATMENT]
        return dict(
            n_at_risk=n, n_arb=n_arb, n_ccb=n_ccb, n_events=ev, ev_arb=ev_arb, ev_ccb=ev_ccb,
            hr=round(np.exp(row["coef"]), 4),
            ci_lower=round(np.exp(row["coef lower 95%"]), 4),
            ci_upper=round(np.exp(row["coef upper 95%"]), 4),
            p_value=round(row["p"], 6),
            converged=True,
            note="Stabilized IPTW, robust SE. Time re-zeroed from interval start.",
        )
    except Exception as exc:
        return dict(
            n_at_risk=n, n_arb=n_arb, n_ccb=n_ccb, n_events=ev, ev_arb=ev_arb, ev_ccb=ev_ccb,
            hr=None, ci_lower=None, ci_upper=None, p_value=None, converged=False,
            note=f"Model failed: {exc}",
        )


def _weighted_km_risk(
    data: pd.DataFrame, time_col: str, event_col: str, weight_col: str, t_land: float
) -> tuple[float, int]:
    """Return weighted KM cumulative risk at t_land and N-at-risk."""
    kmf = KaplanMeierFitter()
    kmf.fit(
        data[time_col].values,
        event_observed=data[event_col].values,
        weights=data[weight_col].values,
    )
    surv = float(kmf.predict(t_land))
    risk = 1.0 - surv
    n_at_risk = int((data[time_col] >= t_land).sum())
    return risk, n_at_risk


def run(config: Config) -> None:
    out_dir = config.paths.output_sensitivity / "curve_divergence"
    out_dir.mkdir(parents=True, exist_ok=True)

    cd_cfg = config.analysis.sensitivity.curve_divergence
    outcomes = _primary_outcomes(config)
    landmarks = list(cd_cfg.landmarks_years)
    intervals = list(cd_cfg.intervals_years)

    dataset_path = config.paths.output_core / "survival_dataset.parquet"
    df = pd.read_parquet(dataset_path)
    print(f"survival_dataset: {df.shape[0]:,} rows")
    print(f"  ARB n={int((df[TREATMENT] == 1).sum()):,}  |  CCB n={int((df[TREATMENT] == 0).sum()):,}")
    for name, ocfg in outcomes.items():
        print(f"  {name}: {int(df[ocfg['event']].sum()):,} events")

    overall_rows = []
    interaction_rows = []
    interval_rows = []
    landmark_rows = []
    lagged_rows = []

    # ==========================================================================
    # ANALYSIS 1 — Overall IPTW Cox HR
    # ==========================================================================
    print("\n-- Analysis 1: Overall IPTW Cox --------------------------------")

    for oname, ocfg in outcomes.items():
        d = df[[ocfg["time"], ocfg["event"], WEIGHT, TREATMENT]].dropna()
        d = d[d[ocfg["time"]] > 0]

        cph = CoxPHFitter()
        cph.fit(d, duration_col=ocfg["time"], event_col=ocfg["event"], weights_col=WEIGHT, robust=True)
        row = cph.summary.loc[TREATMENT]
        hr = np.exp(row["coef"])
        ci_lo = np.exp(row["coef lower 95%"])
        ci_hi = np.exp(row["coef upper 95%"])
        pval = row["p"]

        print(
            f"  {oname}: HR={hr:.3f} ({ci_lo:.3f}-{ci_hi:.3f}), p={pval:.4f}"
            f"  [N={len(d):,}, events={int(d[ocfg['event']].sum())}]"
        )
        overall_rows.append(
            {
                "outcome": oname,
                "model": "overall_iptw_cox_robust_se",
                "n": len(d),
                "n_events": int(d[ocfg["event"]].sum()),
                "n_arb": int((d[TREATMENT] == 1).sum()),
                "n_ccb": int((d[TREATMENT] == 0).sum()),
                "ev_arb": int(d.loc[d[TREATMENT] == 1, ocfg["event"]].sum()),
                "ev_ccb": int(d.loc[d[TREATMENT] == 0, ocfg["event"]].sum()),
                "hr": round(hr, 4),
                "ci_lower_95": round(ci_lo, 4),
                "ci_upper_95": round(ci_hi, 4),
                "p_value": round(pval, 6),
                "note": "ARB (treated=1) vs DHP-CCB (treated=0). Stabilized IPTW, robust SE.",
            }
        )

    # ==========================================================================
    # ANALYSIS 2 — PH check: Scaled Schoenfeld residuals ~ log(event time)
    # ==========================================================================
    print("\n-- Analysis 2: PH check (Schoenfeld residuals x log-time) ------")

    for oname, ocfg in outcomes.items():
        d = df[[ocfg["time"], ocfg["event"], WEIGHT, TREATMENT]].dropna()
        d = d[d[ocfg["time"]] > 0]

        cph = CoxPHFitter()
        cph.fit(d, duration_col=ocfg["time"], event_col=ocfg["event"], weights_col=WEIGHT, robust=True)

        try:
            residuals = cph.compute_residuals(d, kind="scaled_schoenfeld")
            sch_vals = residuals[TREATMENT].values
            log_t = np.log(residuals.index.astype(float))

            mask = np.isfinite(log_t) & np.isfinite(sch_vals)
            log_t = log_t[mask]
            sch_vals = sch_vals[mask]

            pearson_r, pearson_p = pearsonr(log_t, sch_vals)
            spearman_r, spearman_p = spearmanr(log_t, sch_vals)

            print(
                f"  {oname}: Pearson r={pearson_r:.4f} (p={pearson_p:.4f}) | "
                f"Spearman r={spearman_r:.4f} (p={spearman_p:.4f})"
            )
            interaction_rows.append(
                {
                    "outcome": oname,
                    "method": "scaled_schoenfeld_residual_vs_log_t",
                    "n_events_used": int(mask.sum()),
                    "pearson_r_with_log_t": round(pearson_r, 4),
                    "pearson_p": round(pearson_p, 6),
                    "spearman_r_with_log_t": round(spearman_r, 4),
                    "spearman_p": round(spearman_p, 6),
                    "ph_violation_p_lt_0_05": bool(pearson_p < 0.05),
                }
            )

        except Exception as e:
            print(f"  {oname}: Schoenfeld residuals failed — {e}")
            interaction_rows.append({"outcome": oname, "method": "scaled_schoenfeld_residual_vs_log_t", "note": f"Failed: {e}"})

    # ==========================================================================
    # ANALYSIS 3 — Interval-specific weighted HRs
    # ==========================================================================
    print("\n-- Analysis 3: Interval-specific IPTW Cox -----------------------")

    for oname, ocfg in outcomes.items():
        for t0, t1 in intervals:
            r = _interval_cox(df, ocfg["time"], ocfg["event"], WEIGHT, t0, t1)
            label = f"{t0}-{t1 if t1 is not None else 'max'}yr"
            print(
                f"  {oname} [{label}]: N={r['n_at_risk']:,}, "
                f"events={r['n_events']} (ARB:{r['ev_arb']}, CCB:{r['ev_ccb']}), "
                f"HR={r['hr']}, 95%CI=[{r['ci_lower']},{r['ci_upper']}], p={r['p_value']}"
            )
            interval_rows.append(
                {
                    "outcome": oname,
                    "interval": label,
                    "t_start_yr": t0,
                    "t_end_yr": t1 if t1 is not None else "max",
                    "n_at_risk_interval_entry": r["n_at_risk"],
                    "n_arb": r["n_arb"],
                    "n_ccb": r["n_ccb"],
                    "n_events": r["n_events"],
                    "ev_arb": r["ev_arb"],
                    "ev_ccb": r["ev_ccb"],
                    "hr": r["hr"],
                    "ci_lower_95": r["ci_lower"],
                    "ci_upper_95": r["ci_upper"],
                    "p_value": r["p_value"],
                    "model_converged": r["converged"],
                    "note": r["note"],
                }
            )

    # ==========================================================================
    # ANALYSIS 4 — Weighted KM absolute risks at landmark time points
    # ==========================================================================
    print("\n-- Analysis 4: Weighted landmark risk differences ---------------")

    for oname, ocfg in outcomes.items():
        max_t = df[ocfg["time"]].quantile(0.90)  # 90th percentile for feasibility

        for t_land in landmarks:
            if t_land > max_t:
                print(f"  {oname} at {t_land}yr: skipped (90th pct follow-up = {max_t:.1f}yr)")
                landmark_rows.append(
                    {
                        "outcome": oname,
                        "landmark_yr": t_land,
                        "arb_weighted_km_risk": None,
                        "ccb_weighted_km_risk": None,
                        "risk_difference_arb_minus_ccb": None,
                        "arb_n_at_risk": None,
                        "ccb_n_at_risk": None,
                        "ci_lower_95": None,
                        "ci_upper_95": None,
                        "ci_method": "not estimated",
                        "note": f"Landmark {t_land}yr exceeds 90th pct of follow-up ({max_t:.1f}yr); estimate would be unreliable.",
                    }
                )
                continue

            arb_df = df[df[TREATMENT] == 1]
            ccb_df = df[df[TREATMENT] == 0]

            arb_risk, arb_n = _weighted_km_risk(arb_df, ocfg["time"], ocfg["event"], WEIGHT, t_land)
            ccb_risk, ccb_n = _weighted_km_risk(ccb_df, ocfg["time"], ocfg["event"], WEIGHT, t_land)
            rd = arb_risk - ccb_risk

            print(
                f"  {oname} at {t_land}yr: ARB={arb_risk:.4f}, CCB={ccb_risk:.4f}, "
                f"RD={rd:+.4f}  [ARB N>=t={arb_n:,}, CCB N>=t={ccb_n:,}]"
            )
            landmark_rows.append(
                {
                    "outcome": oname,
                    "landmark_yr": t_land,
                    "arb_weighted_km_risk": round(arb_risk, 5),
                    "ccb_weighted_km_risk": round(ccb_risk, 5),
                    "risk_difference_arb_minus_ccb": round(rd, 5),
                    "arb_n_at_risk": arb_n,
                    "ccb_n_at_risk": ccb_n,
                    "ci_lower_95": None,
                    "ci_upper_95": None,
                    "ci_method": "Not estimated — bootstrap not run per analysis protocol. Point estimates only.",
                    "note": (
                        "Weighted Kaplan-Meier (stabilized IPTW applied as frequency weights). "
                        "N-at-risk = count with observed time >= landmark. "
                        "Negative RD favors ARB (lower cumulative risk)."
                    ),
                }
            )

    # ==========================================================================
    # ANALYSIS 5 — Consistent-divergence assessment: summarized in audit note.
    # ==========================================================================

    # ==========================================================================
    # ANALYSIS 6 — Lagged landmark analyses for b4_mci
    # ==========================================================================
    print("\n-- Analysis 6: Lagged analyses for b4_mci -----------------------")

    oname = "b4_mci"
    ocfg = outcomes[oname]

    for lag_yr in [0, 1, 2]:
        d = df[df[ocfg["time"]] > lag_yr].copy()
        d["_t"] = d[ocfg["time"]] - lag_yr
        d["_e"] = d[ocfg["event"]].astype(int)
        d = d[d["_t"] > 0]

        n = len(d)
        ev = int(d["_e"].sum())
        ev_arb = int(d.loc[d[TREATMENT] == 1, "_e"].sum())
        ev_ccb = int(d.loc[d[TREATMENT] == 0, "_e"].sum())
        n_arb = int((d[TREATMENT] == 1).sum())
        n_ccb = int((d[TREATMENT] == 0).sum())
        lag_label = "no_lag" if lag_yr == 0 else f"lag_{lag_yr}yr"

        if ev < 10:
            print(f"  b4_mci [{lag_label}]: N={n:,}, events={ev} — too sparse for model")
            lagged_rows.append(
                {
                    "outcome": oname, "lag_label": lag_label, "lag_years": lag_yr,
                    "n_retained": n, "n_arb": n_arb, "n_ccb": n_ccb,
                    "n_events": ev, "ev_arb": ev_arb, "ev_ccb": ev_ccb,
                    "hr": None, "ci_lower_95": None, "ci_upper_95": None, "p_value": None,
                    "note": f"Sparse: {ev} events; HR not estimated",
                }
            )
            continue

        try:
            cph = CoxPHFitter()
            cph.fit(
                d[["_t", "_e", WEIGHT, TREATMENT]].dropna(),
                duration_col="_t",
                event_col="_e",
                weights_col=WEIGHT,
                robust=True,
            )
            row = cph.summary.loc[TREATMENT]
            hr = np.exp(row["coef"])
            ci_lo = np.exp(row["coef lower 95%"])
            ci_hi = np.exp(row["coef upper 95%"])
            pval = row["p"]

            print(
                f"  b4_mci [{lag_label}]: N={n:,}, events={ev} "
                f"(ARB:{ev_arb}, CCB:{ev_ccb}), HR={hr:.3f} ({ci_lo:.3f}-{ci_hi:.3f}), p={pval:.4f}"
            )
            lagged_rows.append(
                {
                    "outcome": oname,
                    "lag_label": lag_label,
                    "lag_years": lag_yr,
                    "n_retained": n,
                    "n_arb": n_arb,
                    "n_ccb": n_ccb,
                    "n_events": ev,
                    "ev_arb": ev_arb,
                    "ev_ccb": ev_ccb,
                    "hr": round(hr, 4),
                    "ci_lower_95": round(ci_lo, 4),
                    "ci_upper_95": round(ci_hi, 4),
                    "p_value": round(pval, 6),
                }
            )

        except Exception as exc:
            print(f"  b4_mci [{lag_label}]: model failed — {exc}")
            lagged_rows.append(
                {
                    "outcome": oname, "lag_label": lag_label, "lag_years": lag_yr,
                    "n_retained": n, "n_arb": n_arb, "n_ccb": n_ccb,
                    "n_events": ev, "ev_arb": ev_arb, "ev_ccb": ev_ccb,
                    "hr": None, "ci_lower_95": None, "ci_upper_95": None, "p_value": None,
                    "note": f"Model failed: {exc}",
                }
            )

    # ==========================================================================
    # Save all CSV outputs
    # ==========================================================================
    print("\n-- Saving outputs -------------------------------------------------")

    out = {
        "overall_hr_primary_outcomes.csv": overall_rows,
        "time_interaction_primary_outcomes.csv": interaction_rows,
        "interval_hr_primary_outcomes.csv": interval_rows,
        "landmark_riskdiff_primary_outcomes.csv": landmark_rows,
        "lagged_b4_mci.csv": lagged_rows,
    }

    for fname, rows in out.items():
        path = out_dir / fname
        pd.DataFrame(rows).to_csv(path, index=False)
        print(f"  Saved: {fname}")

    print("curve_divergence complete.")


if __name__ == "__main__":
    from src.config import load_config

    run(load_config())
