# Component use: method and contribution policy

Method `component-usage-v2`, implemented in `backend/app/component_stats.py`.
Policy `active-tests-positive-rpm`, confirmed by the user on 2026-09-10.

## Scope

The Components page reports measured use in the current active library for each
typed UUID: individual propeller, electric motor and ESC. A test can have up to
16 named sets, each with its own hardware and signal columns. Select each set's
shaft RPM column in Edit and save it explicitly. The selected variable must already be
in revolutions per minute; neither its name nor unit is inferred or converted.
Within a set, the same shaft speed describes all three associated components. An ESC's reported
RPM is the shaft operating condition, not a separate internal speed measurement.

Each active ready test contributes its set's complete current full-resolution Parquet
rows once to each assigned component. A physical component may belong to only one
set in a test; ambiguous manually edited assignments are excluded. Different
sets can run for different amounts of time. Test points (including overlaps), plot
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

## Motor temperature and power during operation

Each set can independently select a motor temperature and power signal. These
statistics use only rows already eligible as running for that set. Each channel
has its own finite-value mask: missing temperature does not discard valid power,
and missing either channel never reduces valid RPM runtime. Unassigned channels,
null/NaN/infinite values, and unavailable optional columns have no measured
coverage. Their missing coverage is shown against the set's running exposure.
An unreadable or numerically overflowing optional signal is excluded with a
notice while usable RPM results remain available.

Source units are explicit. Temperature is converted to degrees Celsius before
accumulation: C is unchanged, `(F - 32) / 1.8`, or `K - 273.15`. Power is converted
to watts: W is unchanged, kW is multiplied by 1000. No unit or power type is
inferred from column names. Choose compatible source signals across tests; the
application does not distinguish electrical input power from shaft output power.
These are power statistics, not energy integration or efficiency calculations.
Conversions follow [NIST SP 811](https://physics.nist.gov/cuu/pdf/sp811.pdf)
and the [SI prefix table](https://www.nist.gov/pml/special-publication-330/sp-330-section-3).

For each channel, measured seconds are `finite_running_samples / fs`; mean,
population SD, minimum and maximum use only these samples. Across tests and
batches, means and variances are merged using that channel's measured seconds,
so missing samples and differing sample rates cannot skew the weighting.
No observed samples gives null mean/SD/extrema. Coverage retains measured and
missing sample counts and seconds. Summary units are always C and W, while
source details retain the selected source units and column names.

Motor temperature contributes only to the motor's aggregate; the selected power
signal describes the set's operating conditions for each associated propeller,
motor and ESC, like shaft RPM. Selecting a component shows its contributing sets.
The full-test plots remain available for inspecting temperature or power while
stopped; component telemetry summaries specifically describe running exposure.

## API, persistence and refresh

Canonical assignments use `component_sets`, an ordered list of objects with stable
UUID `id` (or the compatibility ID `legacy`), unique single-line `name`, typed
`components`, `rpm_column`, `motor_temperature_column`, `motor_temperature_unit`
(`C`, `F`, `K`), `power_column`, and `power_unit` (`W`, `kW`). Signal mappings are
optional and refer to current non-time columns. Uploads choose named hardware
sets; signal mappings are selected in Edit after ingestion. Sets are immutable
upload identity and survive local/server-only resume and failed ingestion.

Absent `component_sets` reads the former fields as `legacy` / `Set 1` without
writing metadata. Explicit `[]` means no sets. PATCH `/api/tests/{name}/meta`
accepts `component_sets` plus `expected_component_sets_revision`; both bindings
and accompanying metadata commit atomically. Stale revisions reject the entire
patch. Names/IDs/hardware references/columns/units are validated before writing.
Omitted fields are preserved. No-op canonical saves retain revisions.

The old `components` and `component_rpm_column` fields remain a first-set read
projection. Legacy-only tests continue accepting legacy writes; changes advance
the sets revision to protect open migration drafts. Once canonical sets are saved,
old assignment/RPM writes are rejected instead of overwriting newer configuration.
Mixed old/new assignment payloads are rejected. Column rename follows all three
bindings in every set; drop clears the affected mappings. Set revisions change
when their mapping changes. Export provenance includes all sets and the revision;
invalid saved configuration is identified without disabling unrelated analysis.

GET `/api/component-statistics` returns version, method, policy, calculation time,
range boundaries, typed component summaries and one source contribution per set,
including set identity/name, selected columns/units, dataset identity, current
assignment/set/RPM revisions, coverage counts, bounds, sample rate, warnings and errors.
No scientific, identity or cache files are written by this endpoint. Missing
legacy dataset IDs are labelled; duplicate IDs across distinct active test folders
exclude both copies. Multiple sets in one test share one valid dataset identity. Orphan
component references are retained as notices and never reassigned to a namesake.

For legacy-only metadata, PATCH `/api/tests/{name}/meta` accepts optional `component_rpm_column` (a current
non-time variable or null) and required `expected_component_rpm_revision` whenever
the selection is supplied. Omitted fields are preserved. No-op saves retain the
revision; changed selections increment it atomically. Stale revisions reject the
whole metadata patch, including accompanying notes/associations. Column rename
follows the selected RPM reference; column removal clears it and increments its
revision. Normal trim/fill/equation edits retain the selected current reference.

Only numerical source summaries are cached, at most 256 entries in memory. The
key includes method, path/file size/mtime/ctime/inode and exact field-presence/value
context for sample rate/count, schema, time/RPM/telemetry columns, selected units,
fill policy and gap histories.
The presence bit distinguishes unknown acquisition history from absent legacy
history. Current active membership and component assignments are always read
again; the cache never stores component totals. Rename, source replacement, data
edits, RPM selection and timing/gap changes cannot reuse an incompatible summary.

Reads use catalog -> per-test read lock -> native read slot. Parquet files close
before locks are released. Only time, selected RPM and optional telemetry columns are read, in
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
