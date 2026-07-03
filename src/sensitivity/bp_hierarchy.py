"""Ported from Scripts/Sensitivity/run01_bp_sensitivity_model_hierarchy_20260616.py.

Expanded PS Model Hierarchy — BP Isolation Analysis.

Compact 3-model comparison to isolate whether M1A attenuation is driven by
baseline BP adjustment or by BMI complete-case selection.

Model A: BP-only         — base PS + SBP + DBP
Model B: BP + prior meds — base PS + SBP + DBP + ACEi_365d + beta_blocker_365d
Model C: M1A (current)   — base PS + SBP + DBP + BMI + ACEi_365d + beta_blocker_365d

Each model uses its own complete-case population on the covariates it adds.
Outcomes: stroke_s1 (primary), b4_mci (primary) only.

**Model A (`A_bp_only`) is the manuscript's Supplemental Table 5 BP-adjusted
sensitivity analysis** (it adds only baseline SBP + DBP to the PS, as the
manuscript describes): N=58,269, stroke IPTW HR 0.87 (0.77–0.97), cognitive
0.84 (0.66–1.07). Models B and C are additional, more-adjusted specifications
(B adds prior-med flags -> N=58,272; C also adds BMI). The models read from
config.analysis.sensitivity.bp_hierarchy.

NOT a p-value search — a model hierarchy to isolate roles of confounders
and selection.
"""

from __future__ import annotations

import gc
import warnings

import numpy as np
import pandas as pd
from lifelines import CoxPHFitter
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.preprocessing import StandardScaler

from src.config import Config

warnings.filterwarnings("ignore")

# Cox fit return: (hr, ci_lo, ci_hi, p, n_events, n_total). "treated" (1=ARB,
# 0=DHP-CCB) is the exposure column in the survival dataset throughout.
CoxResult = tuple[float, float, float, float, int, int]


def _primary_outcomes(config: Config) -> list[tuple[str, str, str, str]]:
    """Primary outcomes as (name, time_col, event_col, role), derived from config."""
    return [
        (o.name, f"{o.name}_time_years", f"{o.name}_event", o.role)
        for o in config.analysis.outcomes.order
        if o.role == "primary"
    ]


def _fit_iptw_cox(data: pd.DataFrame, time_col: str, event_col: str, weight_col: str) -> CoxResult:
    d = data[[time_col, event_col, "treated", weight_col]].dropna()
    d = d[d[time_col] > 0]
    n_total = len(d)
    n_events = int(d[event_col].sum())
    if n_events < 5:
        return np.nan, np.nan, np.nan, np.nan, n_events, n_total
    for pen in (0.0, 0.01):
        try:
            cph = CoxPHFitter(penalizer=pen)
            cph.fit(d, duration_col=time_col, event_col=event_col, weights_col=weight_col, robust=True)
            hr = float(np.exp(cph.params_["treated"]))
            ci_lo = float(np.exp(cph.confidence_intervals_.loc["treated", "95% lower-bound"]))
            ci_hi = float(np.exp(cph.confidence_intervals_.loc["treated", "95% upper-bound"]))
            p_val = float(cph.summary.loc["treated", "p"])
            return hr, ci_lo, ci_hi, p_val, n_events, n_total
        except Exception:
            if pen == 0.0:
                continue
            return np.nan, np.nan, np.nan, np.nan, n_events, n_total
    return np.nan, np.nan, np.nan, np.nan, n_events, n_total


def _fmt(hr: float, lo: float, hi: float) -> str:
    if any(np.isnan(x) for x in (hr, lo, hi)):
        return "n/a"
    return f"{hr:.2f} ({lo:.2f}-{hi:.2f})"


def run(config: Config) -> None:
    analysis = config.analysis
    random_seed = analysis.random_seed
    ps_trim_lower = analysis.propensity_score.trim_lower
    ps_trim_upper = analysis.propensity_score.trim_upper
    base_ps = list(analysis.propensity_score.covariates_fixed)

    bp_cfg = analysis.sensitivity.bp_hierarchy
    binary_flags = list(bp_cfg.binary_flag_covariates)
    aug_short_names = list(bp_cfg.short_names)
    outcomes = _primary_outcomes(config)

    out_dir = config.paths.output_sensitivity / "bp_hierarchy"
    out_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 70)
    print("BP model hierarchy sensitivity")
    print("=" * 70)

    sv = pd.read_parquet(config.paths.output_core / "survival_dataset.parquet")
    print(f"survival_dataset: {len(sv):,}  ARB={(sv['treated'] == 1).sum():,}  CCB={(sv['treated'] == 0).sum():,}")
    for _, _, ecol, _ in outcomes:
        n_arb = int(sv.loc[sv["treated"] == 1, ecol].sum())
        n_ccb = int(sv.loc[sv["treated"] == 0, ecol].sum())
        print(f"  {ecol}: {int(sv[ecol].sum())} events (ARB={n_arb}, CCB={n_ccb})")

    # Augmented BP / med covariates, renamed to the short names used by the models.
    aug = pd.read_parquet(
        config.paths.baseline_covariates_augmented,
        columns=["PERSON_ID", *bp_cfg.rename_map.keys(), *binary_flags],
    ).rename(columns=bp_cfg.rename_map)
    for col in binary_flags:
        aug[col] = aug[col].fillna(0).astype(float)

    base = sv.merge(aug[["PERSON_ID", *aug_short_names, *binary_flags]], on="PERSON_ID", how="left")
    assert len(base) == len(sv), "Row count changed after merge"
    del aug
    gc.collect()

    year_cols = [c for c in sv.columns if c.startswith("yr_")]
    print(f"  Year dummy columns: {len(year_cols)}  ({year_cols[0]}-{year_cols[-1]})")

    summary_rows = []

    for model in bp_cfg.models:
        model_id = model.id
        cc_vars = list(model.complete_case)
        add_vars = list(model.add_covariates)
        ps_vars = base_ps + year_cols + add_vars

        print(f"\n{'=' * 70}")
        print(f"Model {model_id}")
        print(f"  Complete-case on: {cc_vars}")
        print(f"  PS additions:     {add_vars}")

        df = base.copy()
        cc_mask = df[cc_vars].notna().all(axis=1)
        df_cc = df[cc_mask].copy()

        n_pre = len(df)
        n_cc = len(df_cc)
        n_arb_pre = int((df["treated"] == 1).sum())
        n_ccb_pre = int((df["treated"] == 0).sum())
        n_arb_cc = int((df_cc["treated"] == 1).sum())
        n_ccb_cc = int((df_cc["treated"] == 0).sum())
        ret_arb = 100 * n_arb_cc / n_arb_pre
        ret_ccb = 100 * n_ccb_cc / n_ccb_pre

        print(f"  Pre-CC: {n_pre:,}  Post-CC: {n_cc:,}  ({100 * n_cc / n_pre:.1f}% retained)")
        print(f"  ARB: {n_arb_cc:,} / {n_arb_pre:,} ({ret_arb:.1f}%)  CCB: {n_ccb_cc:,} / {n_ccb_pre:,} ({ret_ccb:.1f}%)")
        print(f"  Differential retention: ARB-CCB = {ret_arb - ret_ccb:+.1f}pp")

        miss_info = {}
        for col in add_vars:
            if col in binary_flags:
                miss_info[col] = "0% (binary, filled)"
            else:
                m_arb = int(df.loc[df["treated"] == 1, col].isna().sum())
                m_ccb = int(df.loc[df["treated"] == 0, col].isna().sum())
                p_arb = 100 * m_arb / n_arb_pre
                p_ccb = 100 * m_ccb / n_ccb_pre
                miss_info[col] = f"ARB={p_arb:.1f}% CCB={p_ccb:.1f}% diff={p_arb - p_ccb:+.1f}pp"
        for k, v in miss_info.items():
            print(f"    miss {k}: {v}")

        X = df_cc[ps_vars].values
        y = df_cc["treated"].values
        scaler = StandardScaler()
        Xs = scaler.fit_transform(X)
        lr = LogisticRegression(penalty="l2", C=1.0, solver="lbfgs", max_iter=3000, random_state=random_seed)
        lr.fit(Xs, y)
        ps_vals = lr.predict_proba(Xs)[:, 1]
        auc = roc_auc_score(y, ps_vals)
        print(f"  PS AUC: {auc:.4f}  range: [{ps_vals.min():.4f}, {ps_vals.max():.4f}]")

        df_cc = df_cc.copy()
        df_cc["ps_m"] = ps_vals

        arb_ps = df_cc.loc[df_cc["treated"] == 1, "ps_m"]
        ccb_ps = df_cc.loc[df_cc["treated"] == 0, "ps_m"]
        lo = min(np.percentile(arb_ps, ps_trim_lower * 100), np.percentile(ccb_ps, ps_trim_lower * 100))
        hi = max(np.percentile(arb_ps, ps_trim_upper * 100), np.percentile(ccb_ps, ps_trim_upper * 100))
        df_trim = df_cc[df_cc["ps_m"].between(lo, hi)].copy()
        n_trim = len(df_trim)
        n_trimmed = len(df_cc) - n_trim
        print(f"  PS trim [{lo:.4f}, {hi:.4f}]  removed={n_trimmed:,}  post-trim N={n_trim:,}")

        p_treat = df_trim["treated"].mean()
        w = np.where(
            df_trim["treated"] == 1,
            p_treat / df_trim["ps_m"],
            (1 - p_treat) / (1 - df_trim["ps_m"]),
        )
        w_lo = np.percentile(w, 1)
        w_hi = np.percentile(w, 99)
        w = np.clip(w, w_lo, w_hi)
        df_trim["iptw_m"] = w

        w_arb = w[df_trim["treated"].values == 1]
        w_ccb = w[df_trim["treated"].values == 0]
        ess_arb = w_arb.sum() ** 2 / (w_arb**2).sum()
        ess_ccb = w_ccb.sum() ** 2 / (w_ccb**2).sum()
        ess_pct = 100 * (ess_arb + ess_ccb) / n_trim
        print(f"  IPTW: mean={w.mean():.3f} range=[{w.min():.3f},{w.max():.3f}]  ESS={ess_arb + ess_ccb:.0f} ({ess_pct:.1f}%)")

        max_smd = 0.0
        all_bal_cols = base_ps + add_vars
        for col in all_bal_cols:
            if col not in df_trim.columns:
                continue
            a = df_trim.loc[df_trim["treated"] == 1, col]
            b = df_trim.loc[df_trim["treated"] == 0, col]
            pooled_sd = np.sqrt((a.std() ** 2 + b.std() ** 2) / 2)
            if pooled_sd <= 0:
                continue
            wa = df_trim.loc[df_trim["treated"] == 1, "iptw_m"]
            wb = df_trim.loc[df_trim["treated"] == 0, "iptw_m"]
            mean_a_w = np.average(a, weights=wa)
            mean_b_w = np.average(b, weights=wb)
            smd_post = abs((mean_a_w - mean_b_w) / pooled_sd)
            max_smd = max(max_smd, smd_post)
        print(f"  Max post-IPTW |SMD|: {max_smd:.4f}")

        for name, time_col, event_col, role in outcomes:
            n_ev_arb = int(df_trim.loc[df_trim["treated"] == 1, event_col].sum())
            n_ev_ccb = int(df_trim.loc[df_trim["treated"] == 0, event_col].sum())
            hr, lo_ci, hi_ci, p, n_ev, _ = _fit_iptw_cox(df_trim, time_col, event_col, "iptw_m")
            label = _fmt(hr, lo_ci, hi_ci)
            p_str = f"{p:.4f}" if not np.isnan(p) else "n/a"
            print(f"  [{role}] {name}: N={n_trim:,} events={n_ev} (ARB={n_ev_arb} CCB={n_ev_ccb})  IPTW HR={label}  p={p_str}")

            summary_rows.append(
                {
                    "model_id": model_id,
                    "cc_vars": "+".join(cc_vars),
                    "ps_additions": "+".join(add_vars),
                    "outcome": name,
                    "outcome_role": role,
                    "n_full_run01": n_pre,
                    "n_cc": n_cc,
                    "pct_retained": round(100 * n_cc / n_pre, 1),
                    "ret_arb_pct": round(ret_arb, 1),
                    "ret_ccb_pct": round(ret_ccb, 1),
                    "diff_retention_pp": round(ret_arb - ret_ccb, 1),
                    "n_post_trim": n_trim,
                    "n_arb_trim": int((df_trim["treated"] == 1).sum()),
                    "n_ccb_trim": int((df_trim["treated"] == 0).sum()),
                    "n_events_total": n_ev,
                    "n_events_arb": n_ev_arb,
                    "n_events_ccb": n_ev_ccb,
                    "ps_auc": round(auc, 4),
                    "max_smd_post": round(max_smd, 4),
                    "ess_pct": round(ess_pct, 1),
                    "iptw_hr": round(hr, 3) if not np.isnan(hr) else np.nan,
                    "iptw_ci_lo": round(lo_ci, 3) if not np.isnan(lo_ci) else np.nan,
                    "iptw_ci_hi": round(hi_ci, 3) if not np.isnan(hi_ci) else np.nan,
                    "iptw_p": round(p, 4) if not np.isnan(p) else np.nan,
                    "iptw_hr_ci": label,
                }
            )

        del df_cc, df_trim
        gc.collect()

    print(f"\n{'=' * 70}")
    print("Reference: primary (no BP adjustment, full cohort)")
    for name, time_col, event_col, role in outcomes:
        n_ev_arb = int(sv.loc[sv["treated"] == 1, event_col].sum())
        n_ev_ccb = int(sv.loc[sv["treated"] == 0, event_col].sum())
        hr, lo_ci, hi_ci, p, n_ev, _ = _fit_iptw_cox(sv, time_col, event_col, "iptw")
        label = _fmt(hr, lo_ci, hi_ci)
        p_str = f"{p:.4f}" if not np.isnan(p) else "n/a"
        print(f"  [{role}] {name}: N={len(sv):,} events={n_ev} (ARB={n_ev_arb} CCB={n_ev_ccb})  IPTW HR={label}  p={p_str}")

        summary_rows.insert(
            0,
            {
                "model_id": "run01_primary",
                "cc_vars": "none",
                "ps_additions": "none (run01 base only)",
                "outcome": name,
                "outcome_role": role,
                "n_full_run01": len(sv),
                "n_cc": len(sv),
                "pct_retained": 100.0,
                "ret_arb_pct": 100.0,
                "ret_ccb_pct": 100.0,
                "diff_retention_pp": 0.0,
                "n_post_trim": len(sv),
                "n_arb_trim": int((sv["treated"] == 1).sum()),
                "n_ccb_trim": int((sv["treated"] == 0).sum()),
                "n_events_total": n_ev,
                "n_events_arb": n_ev_arb,
                "n_events_ccb": n_ev_ccb,
                "ps_auc": round(roc_auc_score(sv["treated"], sv["ps"]), 4),
                "max_smd_post": np.nan,
                "ess_pct": np.nan,
                "iptw_hr": round(hr, 3) if not np.isnan(hr) else np.nan,
                "iptw_ci_lo": round(lo_ci, 3) if not np.isnan(lo_ci) else np.nan,
                "iptw_ci_hi": round(hi_ci, 3) if not np.isnan(hi_ci) else np.nan,
                "iptw_p": round(p, 4) if not np.isnan(p) else np.nan,
                "iptw_hr_ci": label,
            },
        )

    df_out = pd.DataFrame(summary_rows)
    out_path = out_dir / "bp_model_hierarchy.csv"
    df_out.to_csv(out_path, index=False)
    print(f"\nSaved: {out_path}")

    print("\n" + "=" * 70)
    print("FINAL COMPARISON TABLE")
    print("=" * 70)
    display_cols = [
        "model_id",
        "outcome",
        "n_cc",
        "pct_retained",
        "diff_retention_pp",
        "n_events_total",
        "iptw_hr_ci",
        "iptw_p",
        "max_smd_post",
        "ess_pct",
    ]
    for _, grp in df_out.groupby("outcome", sort=False):
        print(f"\n  {grp['outcome'].iloc[0]}:")
        print(grp[display_cols].to_string(index=False))

    print("bp_hierarchy complete.")


if __name__ == "__main__":
    from src.config import load_config

    run(load_config())
