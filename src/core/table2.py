"""Ported from Scripts/Core/04_table2_run01_20260531.py.

Generates Table 2 (IPTW-Cox hazard ratios) for the four outcomes.

  [C8] Table 2 includes outcome_role, outcome_order, crude/adj/IPTW HR,
       Bonferroni and BH-FDR p-values across 2 PRIMARY outcomes only.
       Secondary outcomes show raw p-values labeled secondary.
  [C9] penalizer=0 by default; increases to penalizer=0.01 only on convergence
       failure, with logged warning.
"""

from __future__ import annotations

import warnings

import numpy as np
import pandas as pd
from lifelines import CoxPHFitter

from src.config import Config

warnings.filterwarnings("ignore")

# Readable labels for the covariate-adjusted Cox coefficients (Supp Table 4).
# Year dummies (yr_*) fall through with a generated "Index year YYYY" label.
COEF_LABELS: dict[str, str] = {
    "treated": "ARB vs DHP-CCB",
    "age_at_index": "Age at index, per year",
    "female": "Female sex",
    "race_black_r": "Race: Black/African American",
    "race_asian_r": "Race: Asian",
    "race_other_r": "Race: Other",
    "race_unknown_r": "Race: Unknown/Unmapped",
    "hispanic": "Hispanic/Latino ethnicity",
    "bl_diabetes": "Diabetes mellitus",
    "bl_ckd": "Chronic kidney disease",
    "bl_heart_failure": "Heart failure",
    "bl_cad_mi": "CAD/MI",
    "bl_afib": "Atrial fibrillation/flutter",
    "bl_pad": "Peripheral artery disease",
    "bl_tia": "Prior TIA",
}


def _coef_label(term: str) -> str:
    if term in COEF_LABELS:
        return COEF_LABELS[term]
    if term.startswith("yr_"):
        return f"Index year {term[3:]}"
    return term


def _emit_cox_coefficients(adj_models, out_path) -> None:
    """Write full covariate-adjusted Cox coefficients (HR, 95% CI, p) for the
    primary outcomes, from the exact adjusted models fit for Table 2."""
    if not adj_models:
        print("  No adjusted primary-outcome models available; skipping cox_coefficients.csv")
        return

    # Preserve covariate order from the first model's design matrix.
    first_model = next(iter(adj_models.values()))[1]
    terms = list(first_model.summary.index)

    rows = []
    for term in terms:
        row = {"covariate": term, "label": _coef_label(term)}
        for name, (label, model) in adj_models.items():
            s = model.summary
            if term in s.index:
                hr = float(np.exp(s.loc[term, "coef"]))
                lo = float(np.exp(s.loc[term, "coef lower 95%"]))
                hi = float(np.exp(s.loc[term, "coef upper 95%"]))
                p = float(s.loc[term, "p"])
                row[f"{name}_hr_ci"] = f"{hr:.2f} ({lo:.2f}-{hi:.2f})"
                row[f"{name}_p"] = round(p, 4)
            else:
                row[f"{name}_hr_ci"] = "n/a"
                row[f"{name}_p"] = np.nan
        rows.append(row)

    pd.DataFrame(rows).to_csv(out_path, index=False)
    print(f"Saved: {out_path}  ({len(rows)} covariates x {len(adj_models)} primary outcomes)")


def _fit_cox(data, time_col, event_col, covariates=None, weight_col=None, return_model=False):
    """Fit Cox model; return (hr, ci_lo, ci_hi, p, n_events, n_total).

    If ``return_model`` is True, the fitted ``CoxPHFitter`` (or None on failure)
    is appended as a 7th element so callers can extract the full coefficient
    table from the exact same fit — no separate refit.
    """
    d = data[[time_col, event_col] + (covariates or [])].copy()
    if weight_col:
        d["_wt"] = data[weight_col].values
    d = d.dropna()
    d = d[d[time_col] > 0]
    n_total = len(d)
    n_events = int(d[event_col].sum())

    def _pack(hr, lo, hi, p, model):
        base = (hr, lo, hi, p, n_events, n_total)
        return base + (model,) if return_model else base

    if n_events < 5:
        return _pack(np.nan, np.nan, np.nan, np.nan, None)

    # [C9] No penalizer by default; add only if convergence fails.
    cph = CoxPHFitter(penalizer=0)
    fit_kwargs = {"duration_col": time_col, "event_col": event_col}
    if weight_col:
        fit_kwargs["weights_col"] = "_wt"
        fit_kwargs["robust"] = True  # sandwich SE for IPTW

    try:
        cph.fit(d, **fit_kwargs)
        hr = float(np.exp(cph.params_["treated"]))
        ci_lo = float(np.exp(cph.confidence_intervals_.loc["treated", "95% lower-bound"]))
        ci_hi = float(np.exp(cph.confidence_intervals_.loc["treated", "95% upper-bound"]))
        p_val = float(cph.summary.loc["treated", "p"])
        return _pack(hr, ci_lo, ci_hi, p_val, cph)
    except Exception:
        try:
            print(f"  Retrying {event_col} with penalizer=0.01 (convergence fallback)")
            cph2 = CoxPHFitter(penalizer=0.01)
            cph2.fit(d, **fit_kwargs)
            hr = float(np.exp(cph2.params_["treated"]))
            ci_lo = float(np.exp(cph2.confidence_intervals_.loc["treated", "95% lower-bound"]))
            ci_hi = float(np.exp(cph2.confidence_intervals_.loc["treated", "95% upper-bound"]))
            p_val = float(cph2.summary.loc["treated", "p"])
            return _pack(hr, ci_lo, ci_hi, p_val, cph2)
        except Exception as e:
            print(f"  Warning: Cox fit failed for {event_col}: {e}")
            return _pack(np.nan, np.nan, np.nan, np.nan, None)


def _fmt_hr(hr: float, lo: float, hi: float) -> str:
    if any(np.isnan(x) for x in [hr, lo, hi]):
        return "n/a"
    return f"{hr:.2f} ({lo:.2f}-{hi:.2f})"


def run(config: Config) -> None:
    output_core = config.paths.output_core
    output_core.mkdir(parents=True, exist_ok=True)

    survival_path = output_core / "survival_dataset.parquet"
    sv = pd.read_parquet(survival_path)
    print(f"Loaded survival_dataset: {len(sv):,} rows")

    # [C2/C3] outcome columns use pipeline naming convention from compute_outcomes;
    # primary outcomes first, secondary second (order matches config.analysis.outcomes)
    outcomes = [
        ("stroke_s1", "stroke_s1_time_years", "stroke_s1_event", "primary", 1, "Acute ischemic stroke"),
        (
            "b4_mci",
            "b4_mci_time_years",
            "b4_mci_event",
            "primary",
            2,
            "Probable dementia + mild cognitive impairment",
        ),
        ("b4", "b4_time_years", "b4_event", "secondary", 3, "Probable dementia alone"),
        (
            "stroke_s2",
            "stroke_s2_time_years",
            "stroke_s2_event",
            "secondary",
            4,
            "Ischemic stroke + transient ischemic attack",
        ),
    ]

    ps_covariates_fixed = list(config.analysis.propensity_score.covariates_fixed)
    ps_adj_cols = [c for c in ps_covariates_fixed if c in sv.columns]
    year_cols = [c for c in sv.columns if c.startswith("yr_")]

    rows = []
    adj_models: dict[str, tuple[str, object]] = {}  # primary outcome -> (label, fitted adjusted Cox)
    for name, time_col, event_col, role, order, label in outcomes:
        if time_col not in sv.columns or event_col not in sv.columns:
            print(f"  Skipping {label} -- columns not found in survival dataset")
            continue

        n_events_arb = int(sv.loc[sv["treated"] == 1, event_col].sum())
        n_events_ccb = int(sv.loc[sv["treated"] == 0, event_col].sum())

        hr_cr, lo_cr, hi_cr, p_cr, _, _ = _fit_cox(sv, time_col, event_col, covariates=["treated"])

        adj_cols = ["treated"] + ps_adj_cols + year_cols
        adj_cols = [c for c in adj_cols if c in sv.columns]
        hr_adj, lo_adj, hi_adj, p_adj, _, _, adj_model = _fit_cox(
            sv, time_col, event_col, covariates=adj_cols, return_model=True
        )
        if role == "primary" and adj_model is not None:
            adj_models[name] = (label, adj_model)

        hr_iptw, lo_iptw, hi_iptw, p_iptw, _, _ = _fit_cox(
            sv, time_col, event_col, covariates=["treated"], weight_col="iptw"
        )

        rows.append(
            {
                "outcome": name,
                "outcome_role": role,
                "outcome_order": order,
                "Outcome": label,
                "N_ARB_events": n_events_arb,
                "N_CCB_events": n_events_ccb,
                "crude_hr_ci": _fmt_hr(hr_cr, lo_cr, hi_cr),
                "crude_p_raw": p_cr if not np.isnan(p_cr) else np.nan,
                "crude_p": round(p_cr, 4) if not np.isnan(p_cr) else np.nan,
                "adj_hr_ci": _fmt_hr(hr_adj, lo_adj, hi_adj),
                "adj_p_raw": p_adj if not np.isnan(p_adj) else np.nan,
                "adj_p": round(p_adj, 4) if not np.isnan(p_adj) else np.nan,
                "iptw_hr_ci": _fmt_hr(hr_iptw, lo_iptw, hi_iptw),
                "iptw_p_raw": p_iptw if not np.isnan(p_iptw) else np.nan,
                "iptw_p": round(p_iptw, 4) if not np.isnan(p_iptw) else np.nan,
            }
        )
        print(
            f"  [{role}] {label}: ARB={n_events_arb} CCB={n_events_ccb} "
            f"| IPTW HR={_fmt_hr(hr_iptw, lo_iptw, hi_iptw)} p={p_iptw:.4f}"
        )

    table2 = pd.DataFrame(rows)

    bonferroni_k = config.analysis.multiple_testing.bonferroni_k(config.analysis.outcomes)

    # [C8] Multiple testing correction across PRIMARY outcomes only
    # Use raw (unrounded) IPTW p-values to avoid rounding artifacts
    primary_mask = table2["outcome_role"] == "primary"
    primary_ps = table2.loc[primary_mask, "iptw_p_raw"].values.astype(float)

    table2["primary_family_p_bonferroni"] = np.nan
    if not any(np.isnan(primary_ps)):
        bonf = np.minimum(primary_ps * bonferroni_k, 1.0)
        table2.loc[primary_mask, "primary_family_p_bonferroni"] = bonf

    # BH-FDR — robust to scipy version
    table2["primary_family_p_bh_fdr"] = np.nan
    if not any(np.isnan(primary_ps)) and len(primary_ps) > 0:
        try:
            from scipy.stats import false_discovery_control

            bh = false_discovery_control(primary_ps, method="bh")
            table2.loc[primary_mask, "primary_family_p_bh_fdr"] = bh
        except (ImportError, AttributeError, TypeError):
            # Correct monotonic step-up BH adjustment (Benjamini & Hochberg 1995)
            n_tests = len(primary_ps)
            order = np.argsort(primary_ps)
            bh_adj = np.empty(n_tests)
            bh_adj[order] = primary_ps[order] * n_tests / np.arange(1, n_tests + 1)
            for i in range(n_tests - 2, -1, -1):
                bh_adj[order[i]] = min(bh_adj[order[i]], bh_adj[order[i + 1]])
            bh_adj = np.minimum(bh_adj, 1.0)
            table2.loc[primary_mask, "primary_family_p_bh_fdr"] = bh_adj

    table2["sig_bonferroni"] = np.where(
        primary_mask,
        table2["primary_family_p_bonferroni"].lt(0.05).map({True: "*", False: ""}),
        "[secondary]",
    )
    table2["sig_bh_fdr"] = np.where(
        primary_mask,
        table2["primary_family_p_bh_fdr"].lt(0.05).map({True: "*", False: ""}),
        "[secondary]",
    )

    out_csv = output_core / "hazard_ratios.csv"
    table2.to_csv(out_csv, index=False)
    print(f"Saved: {out_csv}")

    # GAP5 — Supp Table 4: full covariate-adjusted Cox coefficients for the
    # primary outcomes, extracted from the same adjusted fits used above.
    _emit_cox_coefficients(adj_models, output_core / "cox_coefficients.csv")

    print("\nHazard ratios (ARB vs DHP-CCB):")
    print(table2.to_string(index=False))
    print("table2 complete.")


if __name__ == "__main__":
    from src.config import load_config

    run(load_config())
