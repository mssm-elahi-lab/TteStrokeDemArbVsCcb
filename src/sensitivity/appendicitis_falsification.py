"""Ported from Scripts/Sensitivity/extract_appendicitis_falsification_run01_20260618_airms_cloud_safe.py.

Appendicitis falsification (negative control) endpoint analysis.

Builds a patient-level appendicitis endpoint for the denominator cohort and
runs falsification Cox models (crude, covariate-adjusted, IPTW-weighted)
using the same modeling conventions as table2.py. Appendicitis is not
biologically plausible as an ARB vs CCB treatment effect; a null hazard
ratio is expected under proper confounding control.

NOTE: requires config.paths.appendicitis_narrow, a raw extract that was
pulled once via an external/cloud query and never saved into this project's
versioned data. See data/EXTRACTS.md. Raises FileNotFoundError if missing.
"""

from __future__ import annotations

import warnings
from datetime import datetime

import numpy as np
import pandas as pd
from lifelines import CoxPHFitter

from src.config import Config

warnings.filterwarnings("ignore")


def _fit_cox_model(
    data: pd.DataFrame,
    time_col: str,
    event_col: str,
    covariates: list[str],
    weight_col: str | None = None,
    label: str = "",
) -> dict:
    """Fit CoxPH; return dict of results. penalizer=0; retry 0.01 on failure."""
    d = data[[time_col, event_col] + covariates].copy()
    if weight_col:
        d["_wt"] = data[weight_col].values
    d = d.dropna()
    d = d[d[time_col] > 0]
    n_total = len(d)
    n_events = int(d[event_col].sum())

    if n_events < 5:
        print(f"  WARN [{label}]: only {n_events} events — skipping Cox model")
        return dict(
            model=label, hr=np.nan, ci_lo=np.nan, ci_hi=np.nan, p=np.nan,
            n_total=n_total, n_events=n_events, converged=False, note="Insufficient events (<5)",
        )

    cph = CoxPHFitter(penalizer=0)
    fit_kwargs = {"duration_col": time_col, "event_col": event_col}
    if weight_col:
        fit_kwargs["weights_col"] = "_wt"
        fit_kwargs["robust"] = True

    converged = True
    note = ""
    try:
        cph.fit(d, **fit_kwargs)
    except Exception as e1:
        print(f"  [{label}] penalizer=0 failed ({e1}); retrying with penalizer=0.01")
        try:
            cph = CoxPHFitter(penalizer=0.01)
            cph.fit(d, **fit_kwargs)
            converged = True
            note = "penalizer=0.01 used (convergence fallback)"
        except Exception as e2:
            print(f"  [{label}] WARN: Cox fit failed: {e2}")
            return dict(
                model=label, hr=np.nan, ci_lo=np.nan, ci_hi=np.nan, p=np.nan,
                n_total=n_total, n_events=n_events, converged=False, note=str(e2),
            )

    hr = float(np.exp(cph.params_["treated"]))
    ci_lo = float(np.exp(cph.confidence_intervals_.loc["treated", "95% lower-bound"]))
    ci_hi = float(np.exp(cph.confidence_intervals_.loc["treated", "95% upper-bound"]))
    p_val = float(cph.summary.loc["treated", "p"])

    print(f"  [{label}] HR={hr:.3f} (95%CI {ci_lo:.3f}-{ci_hi:.3f}) p={p_val:.4f} | N={n_total:,} events={n_events}")
    return dict(model=label, hr=hr, ci_lo=ci_lo, ci_hi=ci_hi, p=p_val, n_total=n_total, n_events=n_events, converged=converged, note=note)


def run(config: Config) -> None:
    analysis_end_date = pd.Timestamp(config.analysis.end_date)
    ps_covariates_fixed = list(config.analysis.propensity_score.covariates_fixed)

    survival_path = config.paths.output_core / "survival_dataset.parquet"
    appendicitis_narrow_path = config.paths.appendicitis_narrow

    out_dir = config.paths.output_sensitivity / "appendicitis_falsification"
    out_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 70)
    print("APPENDICITIS FALSIFICATION ANALYSIS")
    print(f"Analysis end date (fixed): {analysis_end_date.date()}")
    print("=" * 70)

    for p in [survival_path, appendicitis_narrow_path]:
        if not p.exists():
            raise FileNotFoundError(f"Input file not found: {p}")

    surv = pd.read_parquet(survival_path)
    print(f"\n[1] Loaded survival_dataset: {len(surv):,} rows")

    assert "PERSON_ID" in surv.columns, "STOP: PERSON_ID missing from survival dataset"
    assert "index_date" in surv.columns, "STOP: index_date missing from survival dataset"

    app_raw = pd.read_parquet(appendicitis_narrow_path)
    print(f"[2] Loaded appendicitis NARROW: {len(app_raw):,} rows")

    assert "PERSON_ID" in app_raw.columns, "STOP: PERSON_ID missing from appendicitis file"

    surv["PERSON_ID"] = surv["PERSON_ID"].astype(str).str.strip()
    app_raw["PERSON_ID"] = app_raw["PERSON_ID"].astype(str).str.strip()
    print("[3] PERSON_ID coerced to string in both files")

    surv["index_date"] = pd.to_datetime(surv["index_date"])
    surv["censor_date"] = pd.to_datetime(surv["censor_date"])
    if "clinical_end_date" in surv.columns:
        surv["clinical_end_date"] = pd.to_datetime(surv["clinical_end_date"])
    else:
        print("  WARN: clinical_end_date not found in survival dataset; using censor_date only")

    app_raw["CONDITION_START_DATE"] = pd.to_datetime(app_raw["CONDITION_START_DATE"], errors="coerce")
    app_raw["CONDITION_START_DATETIME"] = pd.to_datetime(app_raw["CONDITION_START_DATETIME"], errors="coerce")

    missing_date_mask = app_raw["CONDITION_START_DATE"].isna()
    app_raw.loc[missing_date_mask, "CONDITION_START_DATE"] = app_raw.loc[
        missing_date_mask, "CONDITION_START_DATETIME"
    ].dt.normalize()

    n_missing_dates = app_raw["CONDITION_START_DATE"].isna().sum()
    print(f"[4] Parsed date fields. Appendicitis rows with no date: {n_missing_dates:,}")

    denominator_n = len(surv)
    print(f"\n[5] Denominator N (survival dataset): {denominator_n:,}")
    print(f"    ARB (treated=1): {(surv['treated'] == 1).sum():,}")
    print(f"    CCB (treated=0): {(surv['treated'] == 0).sum():,}")

    denominator_ids = set(surv["PERSON_ID"].unique())
    app_in_denom = app_raw[app_raw["PERSON_ID"].isin(denominator_ids)].copy()

    n_app_persons_raw = app_raw["PERSON_ID"].nunique()
    n_app_persons_denom = app_in_denom["PERSON_ID"].nunique()

    print(f"\n[6] Appendicitis records (raw): {len(app_raw):,} across {n_app_persons_raw:,} unique persons")
    print(f"    Appendicitis records in denominator: {len(app_in_denom):,} across {n_app_persons_denom:,} persons")

    app_in_denom = app_in_denom.dropna(subset=["CONDITION_START_DATE"])
    print(f"    After dropping records with no date: {len(app_in_denom):,} rows")

    out_raw = out_dir / "appendicitis_conditions_raw_NARROW.parquet"
    app_in_denom.to_parquet(out_raw, index=False)
    print(f"    Saved: {out_raw}")

    print("\n[7] Building patient-level endpoint...")

    first_app = (
        app_in_denom.groupby("PERSON_ID")["CONDITION_START_DATE"]
        .min()
        .reset_index()
        .rename(columns={"CONDITION_START_DATE": "first_appendicitis_date"})
    )

    ep = surv[
        ["PERSON_ID", "index_date", "censor_date", "clinical_end_date", "treated", "iptw"]
        + [c for c in ps_covariates_fixed if c in surv.columns]
        + [c for c in surv.columns if c.startswith("yr_")]
    ].copy()
    ep = ep.merge(first_app, on="PERSON_ID", how="left")

    assert len(ep) == denominator_n, f"STOP: endpoint merge changed row count: {len(ep)} != {denominator_n}"
    print(f"    Endpoint rows = {len(ep):,} (matches denominator N)")

    ep["any_appendicitis_ever"] = ep["first_appendicitis_date"].notna().astype(int)

    ep["prevalent_appendicitis_on_or_before_index"] = (
        ep["first_appendicitis_date"].notna() & (ep["first_appendicitis_date"] <= ep["index_date"])
    ).astype(int)

    ep["first_appendicitis_date_after_index"] = ep.apply(
        lambda r: r["first_appendicitis_date"]
        if pd.notna(r["first_appendicitis_date"]) and r["first_appendicitis_date"] > r["index_date"]
        else pd.NaT,
        axis=1,
    )

    ep["incident_appendicitis_after_index"] = ep["first_appendicitis_date_after_index"].notna().astype(int)

    ep["appendicitis_before_or_on_censor_date"] = (
        ep["incident_appendicitis_after_index"].eq(1)
        & ep["first_appendicitis_date_after_index"].notna()
        & (ep["first_appendicitis_date_after_index"] <= ep["censor_date"])
    ).astype(int)

    ep["days_to_appendicitis_after_index"] = (
        ep["first_appendicitis_date_after_index"] - ep["index_date"]
    ).dt.days

    ep["days_to_appendicitis_censored"] = np.where(
        ep["appendicitis_before_or_on_censor_date"] == 1,
        (ep["first_appendicitis_date_after_index"] - ep["index_date"]).dt.days,
        (ep["censor_date"] - ep["index_date"]).dt.days,
    )

    for flag_col in [
        "any_appendicitis_ever",
        "prevalent_appendicitis_on_or_before_index",
        "incident_appendicitis_after_index",
        "appendicitis_before_or_on_censor_date",
    ]:
        vals = set(ep[flag_col].unique())
        assert vals.issubset({0, 1}), f"STOP: {flag_col} contains non-binary values: {vals}"
    print("    Binary flag checks passed")

    incident_rows = ep[ep["incident_appendicitis_after_index"] == 1]
    if len(incident_rows) > 0:
        bad = incident_rows[incident_rows["first_appendicitis_date_after_index"] <= incident_rows["index_date"]]
        assert len(bad) == 0, f"STOP: {len(bad)} incident events on or before index_date"
    print("    Incident event timing check passed")

    bad_censor = ep[
        (ep["appendicitis_before_or_on_censor_date"] == 1)
        & (ep["first_appendicitis_date_after_index"] > ep["censor_date"])
    ]
    assert len(bad_censor) == 0, f"STOP: {len(bad_censor)} events counted after censor date"
    print("    Censor date boundary check passed")

    out_ep = out_dir / "appendicitis_falsification_endpoint.parquet"
    ep.to_parquet(out_ep, index=False)
    print(f"    Saved: {out_ep}")

    n_any = ep["any_appendicitis_ever"].sum()
    n_prevalent = ep["prevalent_appendicitis_on_or_before_index"].sum()
    n_incident_raw = ep["incident_appendicitis_after_index"].sum()
    n_incident_censored = ep["appendicitis_before_or_on_censor_date"].sum()

    print("\n" + "=" * 70)
    print("COUNTS SUMMARY")
    print("=" * 70)
    print(f"  Denominator N:                           {denominator_n:>8,}")
    print(f"  Patients with any appendicitis record:   {n_any:>8,}")
    print(f"  Prevalent appendicitis (on/before index):{n_prevalent:>8,}")
    print(f"  Incident appendicitis (after index):     {n_incident_raw:>8,}")
    print(f"  Incident within follow-up (<=censor):    {n_incident_censored:>8,}")

    ep_analysis_all = ep[ep["prevalent_appendicitis_on_or_before_index"] == 0].copy()
    n_analysis = len(ep_analysis_all)
    ep_analysis_all["person_years"] = ep_analysis_all["days_to_appendicitis_censored"] / 365.25

    py_arb = ep_analysis_all.loc[ep_analysis_all["treated"] == 1, "person_years"].sum()
    py_ccb = ep_analysis_all.loc[ep_analysis_all["treated"] == 0, "person_years"].sum()
    ev_arb = ep_analysis_all.loc[ep_analysis_all["treated"] == 1, "appendicitis_before_or_on_censor_date"].sum()
    ev_ccb = ep_analysis_all.loc[ep_analysis_all["treated"] == 0, "appendicitis_before_or_on_censor_date"].sum()

    ir_arb = (ev_arb / py_arb * 1000) if py_arb > 0 else np.nan
    ir_ccb = (ev_ccb / py_ccb * 1000) if py_ccb > 0 else np.nan

    print(f"\n  --- After prevalent exclusion (analysis N = {n_analysis:,}) ---")
    print(
        f"  ARB arm:  N={ep_analysis_all[ep_analysis_all['treated'] == 1].shape[0]:,} | events={int(ev_arb)} "
        f"| person-years={py_arb:,.1f} | IR={ir_arb:.2f}/1000 PY"
    )
    print(
        f"  CCB arm:  N={ep_analysis_all[ep_analysis_all['treated'] == 0].shape[0]:,} | events={int(ev_ccb)} "
        f"| person-years={py_ccb:,.1f} | IR={ir_ccb:.2f}/1000 PY"
    )

    ana = ep[ep["prevalent_appendicitis_on_or_before_index"] == 0].copy()
    ana["app_event"] = ana["appendicitis_before_or_on_censor_date"].astype(int)
    ana["app_time_years"] = ana["days_to_appendicitis_censored"] / 365.25

    bad_time = (ana["app_time_years"] <= 0).sum()
    if bad_time > 0:
        print(f"  WARN: {bad_time} rows with app_time_years <= 0; clamping to 0.001 for Cox")
        ana.loc[ana["app_time_years"] <= 0, "app_time_years"] = 0.001

    out_ana = out_dir / "appendicitis_falsification_analysis_dataset.parquet"
    ana.to_parquet(out_ana, index=False)
    print(f"\n  Saved analysis dataset: {out_ana}")
    print(f"  Analysis N (after prevalent exclusion): {len(ana):,}")
    print(f"  Event N: {int(ana['app_event'].sum())}")

    print("\n" + "=" * 70)
    print("COX MODEL RESULTS — Falsification endpoint: Appendicitis")
    print("=" * 70)

    year_cols = [c for c in ana.columns if c.startswith("yr_")]
    adj_cols = ["treated"] + [c for c in ps_covariates_fixed if c in ana.columns] + year_cols

    res_crude = _fit_cox_model(ana, "app_time_years", "app_event", ["treated"], label="Crude")
    res_adj = _fit_cox_model(ana, "app_time_years", "app_event", adj_cols, label="Adjusted")
    res_iptw = _fit_cox_model(ana, "app_time_years", "app_event", covariates=["treated"], weight_col="iptw", label="IPTW")

    results = [res_crude, res_adj, res_iptw]

    rows = []
    for res in results:
        rows.append(
            {
                "model": res["model"],
                "endpoint": "appendicitis",
                "endpoint_role": "falsification_negative_control",
                "n_total": res["n_total"],
                "n_events": res["n_events"],
                "hr": round(res["hr"], 4) if not np.isnan(res["hr"]) else np.nan,
                "ci_lo": round(res["ci_lo"], 4) if not np.isnan(res["ci_lo"]) else np.nan,
                "ci_hi": round(res["ci_hi"], 4) if not np.isnan(res["ci_hi"]) else np.nan,
                "p_value": round(res["p"], 6) if not np.isnan(res["p"]) else np.nan,
                "hr_ci_formatted": (
                    f"{res['hr']:.2f} ({res['ci_lo']:.2f}-{res['ci_hi']:.2f})" if not np.isnan(res["hr"]) else "n/a"
                ),
                "converged": res["converged"],
                "note": res["note"],
            }
        )

    df_results = pd.DataFrame(rows)
    out_csv = out_dir / "appendicitis_falsification_results.csv"
    df_results.to_csv(out_csv, index=False)
    print(f"\nSaved: {out_csv}")
    print(df_results.to_string(index=False))
    print("appendicitis_falsification complete.")


if __name__ == "__main__":
    from src.config import load_config

    run(load_config())
    print("=" * 70)
