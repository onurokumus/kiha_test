# Component use: method and contribution policy

Method `component-usage-v1`, implemented in `backend/app/component_stats.py`.
Policy `active-tests-positive-rpm`, confirmed by the user on 2026-09-10.

## Scope

The Components page reports measured use in the current active library for each
typed UUID: individual propeller, electric motor and ESC. Select the test's shaft
RPM column in Edit and save it explicitly. The selected variable must already be
in revolutions per minute; neither its name nor unit is inferred or converted.
The same shaft speed describes all three associated components. An ESC's reported
RPM is the shaft operating condition, not a separate internal speed measurement.

Each active ready test contributes its complete current full-resolution Parquet
rows once to each assigned component. Test points (including overlaps), plot
selection/ranges, display reduction, temporary filters and spectrum RPM selection
do not change these totals. Existing persisted equations/fills/trims do change the
current data; this page does not reconstruct historical original values.

Trash removes the test's contribution immediately on the next calculation. Restore
adds it back, including restore under a different name; permanent deletion leaves
it excluded. No total is stored in trash, and there is no permanent lifetime ledger.
Existing one-hour trash retention is unchanged. Component UUID records outlive
their assignments. Reassigning a test removes its contribution from the former
component and adds it to the replacement; clearing an association removes it.

## Samples, time and missing values

For source j, let f_j be the positive finite stored sample rate in Hz. Each eligible
row has exposure w_i = 1/f_j seconds. Running means finite RPM r_i > 0. Zero and
negative RPM are stopped; null, NaN and either infinity are missing. All known
acquisition-gap rows are excluded first, even when a later fill gave them finite
values. Categories are disjoint: gap, missing RPM, stopped and running.

Runtime seconds W = sum(w_i over running rows); runtime minutes = W/60. The last
observed sample contributes one period as elsewhere in this application's n/fs
duration convention. This is nominal sample exposure, not trapezoidal integration
between first and last timestamp. Uneven timestamps never weight RPM by dropout
duration. Every timestamp must be finite and strictly increasing; otherwise the
entire source is excluded with an explanation, rather than silently reordered.

Actual consecutive timestamp jumps greater than 1.5/f_j are reported as additional
unrepresented gap seconds sum(delta_t - 1/f_j). They contribute no runtime. Known
inserted gap rows contribute their count/f_j to excluded gap seconds. Generated
or legacy timing is labelled explicitly: absent source rows cannot be inferred
from a generated axis. Sample-rate accuracy remains an input assumption; no new
resampling or clock recalibration is performed.

Imports now save `acquisition_gap_ranges` alongside `time_gap_ranges`. Both are
half-open row-index ranges. Edits shift/clip acquisition ranges on trim and retain
them through fill operations. DSP's existing `time_gap_ranges` behavior remains
unchanged, so existing plot/filter behavior is preserved.

Legacy unfilled data uses its existing gap ranges. A legacy fill that discarded
that history is excluded: missing history cannot truthfully be declared empty.
Explicit null records this unknown state across later edits; malformed gap history
also becomes unknown rather than an empty claim. Reimport the original CSV to
recover observation provenance. Filled ordinary missing RPM cells outside recorded
acquisition gaps use the current edited values, with a visible notice.

## RPM statistics and operating ranges

Statistics use only eligible running rows:

- Mean RPM: mu = sum(w_i * r_i) / W.
- Population variance: v = sum(w_i * (r_i - mu)^2) / W; SD = sqrt(v).
- Minimum and maximum: exact extrema of the native running rows.
- Operating ranges: runtime minutes for (0, 1000), [1000, 3000), [3000, 6000),
  [6000, 10000) and [10000, infinity) RPM. These are reporting buckets, not ratings.

At a single source's constant f_j, mean and population variance equal the usual
unweighted sample-array mean and ddof=0 variance. Across different sample rates,
weights are seconds, so a faster sampler does not gain artificial influence. The
weighted-mean definition follows [NumPy average](https://numpy.org/doc/stable/reference/generated/numpy.average.html);
the population SD convention follows [NumPy std](https://numpy.org/doc/stable/reference/generated/numpy.std.html).

The implementation merges per-batch/per-source means and population variances
using duration weights and the between-group mean difference. It does not subtract
large raw sums of squares. CSV-style nulls/nonfinite RPM are masked, never imputed.
If numerical overflow prevents a finite result, that source is explicitly excluded.
No running rows means zero observed runtime and unavailable mean/SD/extrema; a
single running row has SD zero. A completely unconfigured source has unknown
runtime, distinct from a computed stopped test. Display rounding does not change
the unrounded API results; tiny nonzero values remain visible.

## API, persistence and refresh

GET `/api/component-statistics` returns version, method, policy, calculation time,
range boundaries, typed component summaries and per-source identities, current
assignment/RPM revisions, coverage counts, bounds, sample rate, warnings and errors.
No scientific, identity or cache files are written by this endpoint. Missing
legacy dataset IDs are labelled; duplicate active IDs exclude both copies. Orphan
component references are retained as notices and never reassigned to a namesake.

PATCH `/api/tests/{name}/meta` accepts optional `component_rpm_column` (a current
non-time variable or null) and required `expected_component_rpm_revision` whenever
the selection is supplied. Omitted fields are preserved. No-op saves retain the
revision; changed selections increment it atomically. Stale revisions reject the
whole metadata patch, including accompanying notes/associations. Column rename
follows the selected RPM reference; column removal clears it and increments its
revision. Normal trim/fill/equation edits retain the selected current reference.

Only numerical source summaries are cached, at most 256 entries in memory. The
key includes method, path/file size/mtime/ctime/inode and exact field-presence/value
context for sample rate/count, time/RPM columns, fill policy and gap histories.
The presence bit distinguishes unknown acquisition history from absent legacy
history. Current active membership and component assignments are always read
again; the cache never stores component totals. Rename, source replacement, data
edits, RPM selection and timing/gap changes cannot reuse an incompatible summary.

Reads use catalog -> per-test read lock -> native read slot. Parquet files close
before locks are released. Only time and the selected RPM column are read, in
65,536-row batches. The catalog membership stays stable during one scan; each
source snapshot is atomic, but the response is not a globally simultaneous or
historical snapshot across independent metadata writers. The single-process server
constraint remains. Other windows require Refresh; entering Components also loads
again. Pending/failed requests hide the old table and support retry. Leaving the
page aborts browser delivery; an already-running server scan can finish its reads.

## Limits

Unconfigured/busy/failed/corrupt sources have unknown runtime and explicit coverage
issues. Totals from other usable sources remain available. A damaged registry
fails the page with Retry, rather than pretending there are no components. No
unit calibration, threshold tuning, signed-speed absolute conversion, historical
assignment ledger, component rename/merge/delete, live multi-client synchronization
or component-statistics export is added in this milestone.
