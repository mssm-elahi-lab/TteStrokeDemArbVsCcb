# `src/omop_extract/` — Provenance only (NOT executable by reviewers)

This directory is **documentation of data provenance**, not part of the runnable
analysis. The R scripts here produced the raw parquet extracts in
`data/raw/extract_v3/` from the Mount Sinai BioMe **OMOP database**
(SAP HANA / `CDMDEID` schema).

- **Requires OMOP/BioMe database access** (live institutional credentials); two
  helper scripts additionally use a public concept API over the internet.
- **A reviewer cannot run this.** The reproducible pipeline (`python main.py
  --all`) starts *downstream*, from the shared extracts — it does not touch this
  code.
- It is kept for transparency: it records exactly how the raw data were created.
  The extracts it produced are indexed by
  `data/raw/extract_v3/DOWNLOAD_MANIFEST.csv`.

See `index.md` in this directory for a per-file description and inputs/outputs.
Note `02b_build_indexed_cohort.R` is superseded by `src/core/build_cohort.py`
for the runnable pipeline and is retained only as provenance.
