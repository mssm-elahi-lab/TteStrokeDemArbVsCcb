"""
monotherapy_sensitivity_run.py
Isolated monotherapy-only sensitivity for run01.
Excludes same-day thiazide co-initiators from run01_survival_dataset.parquet,
refits PS/IPTW, reruns Table 2 Cox models.
Writes all outputs to monotherapy_sensitivity_20260604/ only.
Does NOT modify run01 primary outputs.
"""
import sys
import warnings
import numpy as np
import pandas as pd
from pathlib import Path
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import roc_auc_score
from lifelines import CoxPHFitter
from scipy.stats import false_discovery_control   # Python >= 3.11 / lifelines

BASE    = Path("/Users/akarshsharma/Desktop/tte-project")
RUN_DIR = BASE / "AIRMS/results/final_candidate_runs_20260531/run01_v4_core_design_deathcensor"
V4_DIR  = BASE / "AIRMS/most recent extract"
OUT_DIR = RUN_DIR / "monotherapy_sensitivity_20260604"
OUT_DIR.mkdir(exist_ok=True)

# ── from run01_config.py ─────────────────────────────────────────────────────
THIAZIDE_WASHOUT     = {"hydrochlorothiazide","chlorthalidone","indapamide"}
PS_COVARIATES_FIXED  = [
    "age_at_index","female","race_black_r","race_asian_r","race_other_r",
    "race_unknown_r","hispanic","bl_diabetes","bl_ckd","bl_heart_failure",
    "bl_cad_mi","bl_afib","bl_pad","bl_tia",
]
PRIMARY_OUTCOMES     = ["stroke_s1","b4_mci"]
MT_BONFERRONI_K      = 2
PS_TRIM_LOWER        = 0.01
PS_TRIM_UPPER        = 0.99
RANDOM_SEED          = 42

OUTCOME_ORDER = [
    ("stroke_s1", "primary",   "Acute ischemic stroke"),
    ("b4_mci",    "primary",   "Probable dementia + MCI"),
    ("b4",        "secondary", "Probable dementia alone"),
    ("stroke_s2", "secondary", "Ischemic stroke + TIA"),
]

# ── Step 2: Load survival dataset ─────────────────────────────────────────────
print("Loading survival dataset (all columns)...")
sv = pd.read_parquet(RUN_DIR / "run01_survival_dataset.parquet")
print(f"  Loaded: {len(sv):,} rows, {len(sv.columns)} columns")
sv["index_date"] = pd.to_datetime(sv["index_date"])

# Identify yr_* columns
yr_cols = [c for c in sv.columns if c.startswith("yr_")]
print(f"  yr_* columns found: {len(yr_cols)}")

# Check required columns
required = (
    ["PERSON_ID","index_date","treated","ps","iptw"]
    + PS_COVARIATES_FIXED
    + [f"{o}_time_years" for o,*_ in OUTCOME_ORDER]
    + [f"{o}_event"      for o,*_ in OUTCOME_ORDER]
)
missing = [c for c in required if c not in sv.columns]
if missing:
    print(f"STOP — missing columns: {missing}")
    sys.exit(1)
print("  All required columns present.")

# ── Step 3: Load drugs, build same-day thiazide flag ─────────────────────────
print("Loading antihypertensive exposures...")
drugs = pd.read_parquet(
    V4_DIR / "raw_antihypertensive_exposures.parquet",
    columns=["PERSON_ID","DRUG_EXPOSURE_START_DATE","drug_name"]
)
drugs["DRUG_EXPOSURE_START_DATE"] = pd.to_datetime(drugs["DRUG_EXPOSURE_START_DATE"])
drugs["drug_lower"] = drugs["drug_name"].str.lower().str.strip()
thiaz_drugs = drugs[drugs["drug_lower"].isin(THIAZIDE_WASHOUT)].copy()

# Join to cohort
coh_idx = sv[["PERSON_ID","index_date"]].copy()
thiaz_sd = thiaz_drugs.merge(coh_idx, on="PERSON_ID", how="inner")
thiaz_sd = thiaz_sd[thiaz_sd["DRUG_EXPOSURE_START_DATE"] == thiaz_sd["index_date"]]
thiaz_persons = set(thiaz_sd["PERSON_ID"].unique())
print(f"  Same-day thiazide persons: {len(thiaz_persons):,}")
del drugs, thiaz_drugs, thiaz_sd   # free memory

# ── Step 4/5: Flag, count, exclude ───────────────────────────────────────────
sv["same_day_thiaz"] = sv["PERSON_ID"].isin(thiaz_persons)

orig_arb = (sv["treated"]==1).sum()
orig_ccb = (sv["treated"]==0).sum()
excl_arb = int(sv[sv["treated"]==1]["same_day_thiaz"].sum())
excl_ccb = int(sv[sv["treated"]==0]["same_day_thiaz"].sum())
print(f"\nOriginal N: ARB={orig_arb:,}  CCB={orig_ccb:,}  Total={len(sv):,}")
print(f"Excluded (same-day thiaz): ARB={excl_arb:,}  CCB={excl_ccb:,}  Total={excl_arb+excl_ccb:,}")

# Event counts before exclusion
event_cols = [f"{o}_event" for o,*_ in OUTCOME_ORDER]
pre_events = {}
for arm, armval in [("ARB",1),("CCB",0)]:
    sub = sv[sv["treated"]==armval]
    for ec in event_cols:
        pre_events[f"{arm}_{ec}_pre"] = int(sub[ec].sum())

sv_mt = sv[~sv["same_day_thiaz"]].copy()
pre_arb = (sv_mt["treated"]==1).sum()
pre_ccb = (sv_mt["treated"]==0).sum()
print(f"Post-exclusion (pre-PS-refit): ARB={pre_arb:,}  CCB={pre_ccb:,}  Total={len(sv_mt):,}")

post_events = {}
for arm, armval in [("ARB",1),("CCB",0)]:
    sub = sv_mt[sv_mt["treated"]==armval]
    for ec in event_cols:
        post_events[f"{arm}_{ec}_post"] = int(sub[ec].sum())

# Cohort counts CSV
count_rows = []
for arm, armval, orig_n, excl_n, post_n in [
    ("ARB",1,orig_arb,excl_arb,pre_arb),
    ("CCB",0,orig_ccb,excl_ccb,pre_ccb),
    ("Total","both",orig_arb+orig_ccb,excl_arb+excl_ccb,pre_arb+pre_ccb),
]:
    row = {"arm":arm,"original_N":orig_n,"excluded_same_day_thiaz":excl_n,
           "pre_ps_refit_N":post_n}
    for ec in event_cols:
        if arm != "Total":
            row[f"{ec}_pre_excl"] = pre_events.get(f"{arm}_{ec}_pre",None)
            row[f"{ec}_post_excl"] = post_events.get(f"{arm}_{ec}_post",None)
    count_rows.append(row)
pd.DataFrame(count_rows).to_csv(OUT_DIR/"monotherapy_sensitivity_cohort_counts.csv",index=False)
print("Saved: monotherapy_sensitivity_cohort_counts.csv")

# ── Step 6: Refit PS ──────────────────────────────────────────────────────────
print("\nRefitting PS...")
ps_covs = PS_COVARIATES_FIXED + yr_cols
ps_df = sv_mt[ps_covs].copy()
ps_complete = ps_df.notna().all(axis=1)
print(f"  Complete PS cases: {ps_complete.sum():,} / {len(sv_mt):,}")

X = ps_df[ps_complete].values
y = sv_mt.loc[ps_complete, "treated"].values
scaler = StandardScaler()
X_s = scaler.fit_transform(X)
lr = LogisticRegression(penalty="l2",C=1.0,solver="lbfgs",max_iter=2000,random_state=RANDOM_SEED)
lr.fit(X_s, y)
ps_auc = roc_auc_score(y, lr.predict_proba(X_s)[:,1])
print(f"  PS AUC: {ps_auc:.4f}")

ps_new = pd.Series(np.nan, index=sv_mt.index)
ps_new[ps_complete] = lr.predict_proba(X_s)[:,1]
sv_mt = sv_mt.copy()
sv_mt["ps_new"] = ps_new.values

# ── Step 7: PS trimming ───────────────────────────────────────────────────────
ps_arb = sv_mt.loc[(sv_mt["treated"]==1) & sv_mt["ps_new"].notna(), "ps_new"]
ps_ccb = sv_mt.loc[(sv_mt["treated"]==0) & sv_mt["ps_new"].notna(), "ps_new"]
lo = min(np.nanpercentile(ps_arb, PS_TRIM_LOWER*100), np.nanpercentile(ps_ccb, PS_TRIM_LOWER*100))
hi = max(np.nanpercentile(ps_arb, PS_TRIM_UPPER*100), np.nanpercentile(ps_ccb, PS_TRIM_UPPER*100))
in_range = sv_mt["ps_new"].notna() & (sv_mt["ps_new"] >= lo) & (sv_mt["ps_new"] <= hi)
sv_final = sv_mt[in_range].copy()
trim_removed = len(sv_mt) - len(sv_final)
print(f"  PS trim removed: {trim_removed:,}; post-trim N: {len(sv_final):,}")
print(f"  Post-trim: ARB={(sv_final['treated']==1).sum():,}  CCB={(sv_final['treated']==0).sum():,}")

# ── Step 8: Stabilized ATE IPTW ──────────────────────────────────────────────
p_treat = sv_final["treated"].mean()
iptw_new = np.where(
    sv_final["treated"]==1,
    p_treat / sv_final["ps_new"],
    (1 - p_treat) / (1 - sv_final["ps_new"])
)
lo_w = np.nanpercentile(iptw_new, 1)
hi_w = np.nanpercentile(iptw_new, 99)
iptw_new = np.clip(iptw_new, lo_w, hi_w)
sv_final = sv_final.copy()
sv_final["iptw_new"] = iptw_new
print(f"  IPTW range: [{iptw_new.min():.3f}, {iptw_new.max():.3f}]")

# ── Step 9: Balance ───────────────────────────────────────────────────────────
def smd_binary(a, b):
    p1, p2 = a.mean(), b.mean()
    if p1*(1-p1) + p2*(1-p2) == 0: return np.nan
    return (p1 - p2) / np.sqrt((p1*(1-p1) + p2*(1-p2)) / 2)

def smd_continuous(a, b):
    s = np.sqrt((a.std()**2 + b.std()**2) / 2)
    if s == 0: return np.nan
    return (a.mean() - b.mean()) / s

def smd_weighted(col, df, w_col):
    arb = df[df["treated"]==1]; ccb = df[df["treated"]==0]
    wa = arb[w_col]; wc = ccb[w_col]
    ma = np.average(arb[col].dropna(), weights=wa.loc[arb[col].notna()])
    mc = np.average(ccb[col].dropna(), weights=wc.loc[ccb[col].notna()])
    if col == "age_at_index":
        var_a = np.average((arb[col].dropna()-ma)**2, weights=wa.loc[arb[col].notna()])
        var_c = np.average((ccb[col].dropna()-mc)**2, weights=wc.loc[ccb[col].notna()])
        s = np.sqrt((var_a + var_c)/2)
        return (ma-mc)/s if s>0 else np.nan
    pa, pc = ma, mc
    denom = np.sqrt((pa*(1-pa)+pc*(1-pc))/2)
    return (pa-pc)/denom if denom>0 else np.nan

bal_rows = []
arb_f = sv_final[sv_final["treated"]==1]
ccb_f = sv_final[sv_final["treated"]==0]
for col in PS_COVARIATES_FIXED + yr_cols:
    if col not in sv_final.columns: continue
    smd_pre  = (smd_continuous if col=="age_at_index" else smd_binary)(arb_f[col].dropna(), ccb_f[col].dropna())
    smd_post = smd_weighted(col, sv_final, "iptw_new")
    bal_rows.append({"covariate":col,"smd_pre":round(smd_pre,4) if not np.isnan(smd_pre) else None,
                     "smd_post":round(smd_post,4) if not np.isnan(smd_post) else None})

bal_df = pd.DataFrame(bal_rows)
bal_df.to_csv(OUT_DIR/"monotherapy_sensitivity_balance.csv",index=False)
max_post_smd = bal_df["smd_post"].abs().max()
print(f"\n  Max post-IPTW |SMD|: {max_post_smd:.4f}")
print("Saved: monotherapy_sensitivity_balance.csv")

# ── Step 10/11: Cox models ────────────────────────────────────────────────────
print("\nFitting Cox models...")
cov_cols = PS_COVARIATES_FIXED + yr_cols
results = []

for outcome_key, role, label in OUTCOME_ORDER:
    time_col  = f"{outcome_key}_time_years"
    event_col = f"{outcome_key}_event"
    print(f"\n  {label}...")

    # strip rows with missing time/event
    sub_full = sv_final[[time_col, event_col, "treated", "iptw_new"]
                         + [c for c in cov_cols if c in sv_final.columns]
                        ].dropna(subset=[time_col, event_col]).copy()
    sub_full = sub_full[sub_full[time_col] > 0].copy()
    n_ev = int(sub_full[event_col].sum())
    n_arb_ev = int(sub_full[sub_full["treated"]==1][event_col].sum())
    n_ccb_ev = int(sub_full[sub_full["treated"]==0][event_col].sum())
    print(f"    N={len(sub_full):,}  events={n_ev} (ARB={n_arb_ev}, CCB={n_ccb_ev})")

    def fit_cox(df, time_col, event_col, covariates, weights=None, label_=""):
        df2 = df[[time_col, event_col] + covariates].dropna().copy()
        df2 = df2[df2[time_col]>0].copy()
        if weights is not None:
            df2["_w"] = weights.loc[df2.index].values
        try:
            cph = CoxPHFitter(penalizer=0)
            if weights is not None:
                cph.fit(df2, duration_col=time_col, event_col=event_col,
                        weights_col="_w", robust=True)
            else:
                cph.fit(df2, duration_col=time_col, event_col=event_col)
        except Exception:
            print(f"    {label_} convergence fail — retry penalizer=0.01")
            cph = CoxPHFitter(penalizer=0.01)
            if weights is not None:
                cph.fit(df2, duration_col=time_col, event_col=event_col,
                        weights_col="_w", robust=True)
            else:
                cph.fit(df2, duration_col=time_col, event_col=event_col)
        s = cph.summary.loc["treated"]
        hr   = round(np.exp(s["coef"]), 4)
        lo95 = round(np.exp(s["coef lower 95%"]), 4)
        hi95 = round(np.exp(s["coef upper 95%"]), 4)
        pval = round(float(s["p"]), 6)
        ci_str = f"{hr:.2f} ({lo95:.2f}–{hi95:.2f})"
        return hr, lo95, hi95, pval, ci_str

    # Crude
    hr_c, lo_c, hi_c, p_c, ci_c = fit_cox(sub_full, time_col, event_col, ["treated"], label_="crude")
    # MV adjusted
    adj_covs = ["treated"] + [c for c in cov_cols if c in sub_full.columns]
    hr_a, lo_a, hi_a, p_a, ci_a = fit_cox(sub_full, time_col, event_col, adj_covs, label_="adj")
    # IPTW
    hr_i, lo_i, hi_i, p_i, ci_i = fit_cox(sub_full, time_col, event_col, ["treated"],
                                            weights=sub_full["iptw_new"], label_="iptw")

    results.append({
        "outcome": outcome_key, "outcome_role": role, "Outcome": label,
        "N_ARB_events": n_arb_ev, "N_CCB_events": n_ccb_ev,
        "crude_hr_ci": ci_c, "crude_p": round(p_c,4),
        "adj_hr_ci":   ci_a, "adj_p":   round(p_a,4),
        "iptw_hr_ci":  ci_i, "iptw_p_raw": p_i,
    })
    print(f"    crude={ci_c} p={p_c:.4f}  adj={ci_a} p={p_a:.4f}  iptw={ci_i} p={p_i:.4f}")

# Multiple testing correction (Bonferroni + BH-FDR) across 2 primary outcomes
primary_ps = [r["iptw_p_raw"] for r in results if r["outcome_role"]=="primary"]
bonf = [min(p * MT_BONFERRONI_K, 1.0) for p in primary_ps]
bh   = list(false_discovery_control(primary_ps, method="bh"))

pi = 0
for r in results:
    if r["outcome_role"] == "primary":
        r["primary_family_p_bonferroni"] = round(bonf[pi], 6)
        r["primary_family_p_bh_fdr"]     = round(bh[pi], 6)
        r["sig_bonferroni"] = "*" if bonf[pi] < 0.05 else ""
        r["sig_bh_fdr"]     = "*" if bh[pi]   < 0.05 else ""
        pi += 1
    else:
        r["primary_family_p_bonferroni"] = None
        r["primary_family_p_bh_fdr"]     = None
        r["sig_bonferroni"] = "[secondary]"
        r["sig_bh_fdr"]     = "[secondary]"

t2_df = pd.DataFrame(results)
t2_df.to_csv(OUT_DIR/"monotherapy_sensitivity_table2.csv", index=False)
print("\nSaved: monotherapy_sensitivity_table2.csv")

# ── Step 12: Final N for summary ───────────────────────────────────────────────
final_arb = int((sv_final["treated"]==1).sum())
final_ccb = int((sv_final["treated"]==0).sum())

print("\n=== COMPLETE ===")
print(f"Final sensitivity N: ARB={final_arb:,}  CCB={final_ccb:,}  Total={len(sv_final):,}")
for r in results:
    print(f"  {r['Outcome']}: iptw {r['iptw_hr_ci']} p={r['iptw_p_raw']:.4f}"
          + (f"  bonf={r['primary_family_p_bonferroni']:.4f}" if r['primary_family_p_bonferroni'] else ""))

# Write summary dict to file for the MD step
import json
summary_data = {
    "orig_arb": int(orig_arb), "orig_ccb": int(orig_ccb),
    "excl_arb": excl_arb, "excl_ccb": excl_ccb,
    "pre_ps_arb": int(pre_arb), "pre_ps_ccb": int(pre_ccb),
    "trim_removed": trim_removed,
    "final_arb": final_arb, "final_ccb": final_ccb,
    "ps_auc": round(ps_auc, 4),
    "max_post_smd": round(float(max_post_smd), 4),
    "iptw_min": round(float(iptw_new.min()), 3),
    "iptw_max": round(float(iptw_new.max()), 3),
    "results": results,
}
(OUT_DIR / "_summary_data.json").write_text(json.dumps(summary_data, indent=2))
print("Saved: _summary_data.json (for MD generation)")
