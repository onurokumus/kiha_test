- [x] Handle KiHa semicolon/decimal-comma CSVs and clock timestamps as elapsed
  seconds from 0, with explicit time-column and sample-rate import options.
- [x] Preserve missing measured-time rows as NaN gaps, infer Hz from continuous
  regions, and prevent filters/spectra from bridging acquisition dropouts.
- [x] Add safe derived-variable equations, sampled preview, reusable formula
  recipes, cursor-aware variable insertion, and materialized plot-ready columns.
- [x] Expose existing-column renaming clearly in Edit while protecting the time
  column and rebuilding Parquet/pyramids atomically.
