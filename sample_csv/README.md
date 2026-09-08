# PTT sample CSV pack

Upload these files from the site's **Uploads** page or drag them into the window.

- `ptt_demo_run_a.csv` and `ptt_demo_run_b.csv` are matching 32-second, 256 Hz propeller runs. Auto-split on `Test_ID` to create four test points, then compare the two tests in Analyze.
- `ptt_nan_gaps.csv` is a 24-second, 200 Hz run with blank, `NaN`, `null`, `None`, and `inf` sensor cells, one timestamp gap, and a text column that should be skipped. It exercises missing-data policies and the jitter warning.
- `ptt_kiha_dialect.csv` uses semicolons, decimal commas, quantized clock time, and a skipped text column. Auto-split on `Test_ID` should create three test points.
- `ptt_generated_time.csv` has an unusable `TIME` column. Ingest should replace it with a uniform 2048 Hz time axis; auto-split on `Run_ID` should create two test points.
- `ptt_invalid_single_row.csv` is intentionally invalid and should finish with the error `need at least 2 samples to form a series`.

All generated values are deterministic and synthetic. Re-run `node scripts/generate_sample_csv.mjs` to recreate the pack.
