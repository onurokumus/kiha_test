"""Time-plot CSV/ZIP export and shared staging for native Spectrum exports.

Stage every source successfully before sending an attachment. Per-test read
locks cover processing and serialization; the completed temporary file needs
no dataset lock while the browser downloads it. Different tests are snapshots
taken in sequence, not one global revision. No source files are modified.
"""

from __future__ import annotations

from collections import OrderedDict
from contextlib import closing
from dataclasses import dataclass
import os
from pathlib import Path
import re
import tempfile
from typing import Literal
from urllib.parse import quote
import zipfile

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
import numpy as np
import pyarrow as pa
import pyarrow.csv as pa_csv
from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator

from . import analysis_metadata as analysis
from . import dsp, store, export_progress as progress
from .config import MAX_FILTER_SAMPLES
from .locks import data_read, test_read
from .status import BUSY_STATUSES


router = APIRouter()
MAX_EXPORT_SOURCES = 128
MAX_EXPORT_ROWS = MAX_FILTER_SAMPLES
_BATCH_SIZE = 65_536


class FilterParameters(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    kind: Literal["lowpass", "highpass", "bandpass", "bandstop",
                  "moving_avg", "detrend", "despike"]
    order: int = Field(default=4, ge=1, le=10)
    f1: float | None = None
    f2: float | None = None
    window_s: float | None = None
    max_spike_s: float = dsp.DEFAULT_MAX_SPIKE_S
    threshold: float = dsp.DEFAULT_DESPIKE_THRESHOLD
    abs_floor: float = dsp.DEFAULT_DESPIKE_ABS_FLOOR
    replacement: Literal["linear", "median"] = "linear"


class ExportSource(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    test: str = Field(min_length=1, max_length=256)
    tp_id: StrictInt | None = None
    t0: float | None = None
    t1: float | None = None
    px: int = Field(default=1500, ge=1, le=100_000)
    display: Literal["auto", "line", "envelope"] = "auto"
    expected_i0: StrictInt | None = Field(default=None, ge=0)
    expected_i1: StrictInt | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_scope(self):
        if self.tp_id is not None and (self.t0 is not None or self.t1 is not None):
            raise ValueError("tp_id cannot be combined with t0 or t1")
        if self.t0 is not None and self.t1 is not None and self.t0 > self.t1:
            raise ValueError("t0 must not exceed t1")
        if (self.expected_i0 is None) != (self.expected_i1 is None):
            raise ValueError("expected_i0 and expected_i1 must be supplied together")
        if (self.expected_i0 is not None
                and self.expected_i1 <= self.expected_i0):
            raise ValueError("expected bounds must be nonempty and increasing")
        return self


class PlotExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    column: str = Field(min_length=1, max_length=1024)
    data: Literal["original", "filtered", "both"]
    sources: list[ExportSource] = Field(min_length=1, max_length=MAX_EXPORT_SOURCES)
    filter: FilterParameters | None = None
    x_range: tuple[float, float] | None = None
    include_metadata: bool = False

    @model_validator(mode="after")
    def validate_export(self):
        if self.data != "original" and self.filter is None:
            raise ValueError("filtered data requires the displayed filter settings")
        if self.x_range is not None and self.x_range[0] > self.x_range[1]:
            raise ValueError("x_range must be increasing")
        if len(self.sources) > 1 and any(s.tp_id is None for s in self.sources):
            raise ValueError("a full-test export must contain exactly one source")
        identities = [(os.path.normcase(s.test), s.tp_id) for s in self.sources]
        if len(set(identities)) != len(identities):
            raise ValueError("duplicate export sources")
        return self


class BundlePlot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slot: StrictInt = Field(ge=1, le=9)
    request: PlotExportRequest


class PlotExportBundleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    include_metadata: bool = False
    layout: Literal["2x2", "3x3"]
    plots: list[BundlePlot] = Field(min_length=1, max_length=9)

    @model_validator(mode="after")
    def validate_plots(self):
        capacity = 4 if self.layout == "2x2" else 9
        if len(self.plots) > capacity:
            raise ValueError(f"{self.layout} holds at most {capacity} selected plots")
        slots = [plot.slot for plot in self.plots]
        if slots != sorted(set(slots)):
            raise ValueError("plot slots must be unique and in ascending grid order")
        references = sum(len(plot.request.sources) for plot in self.plots)
        if references > MAX_EXPORT_SOURCES:
            raise ValueError(
                f"bundle has {references} source references (max {MAX_EXPORT_SOURCES})")
        return self


def _test_path(name: str) -> Path:
    if any(char in name for char in ("/", "\\", ":", "\x00")) or name in {".", ".."}:
        raise HTTPException(400, "invalid test name")
    root = store.TESTS_DIR.resolve()
    path = (root / name).resolve()
    if path.parent != root or Path(name).name != name:
        raise HTTPException(400, "invalid test name")
    if not path.is_dir():
        raise HTTPException(404, f"test '{name}' not found")
    return path


def _check_ready(name: str) -> None:
    status = store.get_status(name).get("status")
    if status in BUSY_STATUSES:
        raise HTTPException(409, f"'{name}' is busy ({status}); retry once it is ready")
    if status != "ready":
        raise HTTPException(409, f"test '{name}' is unavailable for plot export")


def _resolve(source: ExportSource, column: str, *, allow_time: bool = False) -> tuple[dict, int, int]:
    _test_path(source.test)
    _check_ready(source.test)
    meta = store.get_meta(source.test)
    if meta is None:
        raise HTTPException(404, f"test '{source.test}' has no data metadata")
    if column not in meta["columns"] or (not allow_time and column == meta["time_column"]):
        raise HTTPException(400, f"unknown signal column '{column}' in '{source.test}'")
    try:
        i0, i1 = (store.testpoint_range(source.test, source.tp_id)
                  if source.tp_id is not None
                  else store.window_bounds(meta, source.t0, source.t1))
    except KeyError:
        raise HTTPException(404, f"test point {source.tp_id} not found in '{source.test}'")
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    if i1 <= i0:
        raise HTTPException(400, f"export range is empty in '{source.test}'")
    if source.expected_i0 is not None and (i0, i1) != (
            source.expected_i0, source.expected_i1):
        raise HTTPException(409, f"source bounds changed in '{source.test}'; refresh the plot")
    return meta, i0, i1


def _add_rows(total: int, rows: int) -> int:
    total += rows
    if total > MAX_EXPORT_ROWS:
        raise HTTPException(
            400, f"plot export spans {total} source rows (max {MAX_EXPORT_ROWS}); "
            "choose fewer test points or a narrower full-test range")
    return total


@dataclass
class ExportRowBudget:
    """Actual work shared across bundled CSVs, counted before X cropping."""

    rows: int = 0

    def add(self, rows: int) -> None:
        self.rows = _add_rows(self.rows, rows)


def _filename(request: PlotExportRequest) -> str:
    names = {s.test for s in request.sources}
    test_name = next(iter(names)) if len(names) == 1 else "multi-test"
    mode = "test-points" if request.sources[0].tp_id is not None else "full-test"
    # Attachment names are descriptive, bounded, and independent of paths.
    column = re.sub(r'[^\w.()-]+', "_", request.column, flags=re.UNICODE).strip("._")
    return f"{test_name[:80]}_{(column or 'signal')[:80]}_{mode}_{request.data}.csv"


def _write_source(output, source: ExportSource, column: str,
                  data: str, filter_spec: FilterParameters | None,
                  x_range: tuple[float, float] | None,
                  meta: dict, i0: int, i1: int, *, header: bool, record=None) -> int:
    """Write one source while its caller holds data_read; return kept rows."""
    time_column = meta["time_column"]
    samples = None
    if data != "original":
        progress.update('Filtering source')
        try:
            samples = dsp.filtered_samples(
                source.test, [column], t0=source.t0, t1=source.t1,
                px=source.px, display=source.display, tp_id=source.tp_id,
                **filter_spec.model_dump())
        except ValueError as exc:
            raise HTTPException(400, f"cannot filter '{source.test}': {exc}")
        # The same lock protects both resolutions; a mismatch is never a
        # legitimate reason to serialize the wrong source rows.
        if (samples.i0, samples.i1) != (i0, i1):
            raise HTTPException(409, "source bounds changed; refresh the plot")

        def filtered_batches():
            offset = samples.s0
            for batch in samples.frame.to_arrow().to_batches(max_chunksize=_BATCH_SIZE):
                yield offset, batch
                offset += batch.num_rows

        batches = filtered_batches()
    else:
        batches = store._iter_parquet_slice(
            store.TESTS_DIR / source.test / "data.parquet",
            [time_column, column], i0, i1)

    if record is not None:
        record["processing"] = samples.analysis if samples else {"method_version": "kiha-time-original-v1", "filter": None}
    origin = None
    written = 0
    progress.update('Writing CSV', total=i1 - i0, unit='rows')
    with closing(batches):
        for start, batch in batches:
            progress.update('Writing CSV', completed=max(0, min(i1, start) - i0), total=i1 - i0, unit='rows')
            t = store._batch_float64(batch, time_column)
            indices = np.arange(start, start + batch.num_rows, dtype=np.int64)
            if origin is None and source.tp_id is not None:
                # TP processing has no envelope shoulders; first batch starts
                # at the exact saved row origin in both original/DSP paths.
                origin = float(t[0])
            tp_time = t - origin if origin is not None else None
            plot_time = tp_time if tp_time is not None else t
            keep = (indices >= i0) & (indices < i1)
            if x_range is not None:
                lo, hi = x_range
                # Inclusive sample centers, with only float64 arithmetic
                # tolerance for subtracting a large TP time origin. Never
                # round source timestamps to the plot's six decimal places.
                scale = max(1.0, abs(lo), abs(hi), abs(origin or 0.0),
                            float(np.max(np.abs(t))) if t.size else 0.0)
                tolerance = 8 * np.finfo(np.float64).eps * scale
                keep &= (plot_time >= lo - tolerance) & (plot_time <= hi + tolerance)
            analysis.observe_rows(record, indices, t, keep)
            count = int(keep.sum())
            if not count:
                progress.update('Writing CSV', completed=max(0, min(i1, start + batch.num_rows) - i0), total=i1 - i0, unit='rows')
                continue
            mask = pa.array(keep)
            names = ["source_test", "test_point_id", "sample_index", "time_s", "tp_time_s"]
            arrays = [
                pa.repeat(source.test, count),
                (pa.repeat(str(source.tp_id), count) if source.tp_id is not None
                 else pa.nulls(count, type=pa.string())),
                pa.array(indices[keep]),
                batch.column(batch.schema.get_field_index(time_column)).filter(mask),
                (pa.array(tp_time[keep]) if tp_time is not None
                 else pa.nulls(count, type=pa.float64())),
            ]
            if data in {"original", "both"}:
                names.append(f"{column} [original]")
                arrays.append(batch.column(batch.schema.get_field_index(column)).filter(mask))
            if data in {"filtered", "both"}:
                names.append(f"{column} [filtered]")
                offset = start - samples.s0
                values = samples.filtered[column][offset:offset + batch.num_rows]
                selected_values = values[keep]
                # Computed nonfinite values are missing, as in /filter JSON.
                # Preserve the original Arrow column above without changing
                # its distinct null/NaN/infinite source representations.
                arrays.append(pa.array(selected_values,
                                       mask=~np.isfinite(selected_values)))
            pa_csv.write_csv(
                pa.RecordBatch.from_arrays(arrays, names=names), output,
                write_options=pa_csv.WriteOptions(include_header=header and written == 0))
            written += count
            progress.update('Writing CSV', completed=max(0, min(i1, start + batch.num_rows) - i0), total=i1 - i0, unit='rows')
    return written


def _preflight_export(request: PlotExportRequest, *, resolve=None):
    """Resolve metadata early; native reads revalidate under their own lock."""
    grouped: OrderedDict[str, list[ExportSource]] = OrderedDict()
    for source in request.sources:
        progress.checkpoint()
        _test_path(source.test)
        _check_ready(source.test)  # do not park behind a long busy writer
        grouped.setdefault(os.path.normcase(source.test), []).append(source)

    total = 0
    for sources in grouped.values():
        name = sources[0].test
        progress.update('Checking sources', source=name)
        with test_read(name, check=progress.checkpoint):
            for source in sources:
                _, i0, i1 = (resolve or _resolve)(source, request.column)
                total = _add_rows(total, i1 - i0)
    return grouped, total


def prepare_export(request: PlotExportRequest, *,
                   row_budget: ExportRowBudget | None = None, metadata=None):
    def write(output, source, meta, i0, i1, *, header, record=None):
        return _write_source(output, source, request.column, request.data,
                             request.filter, request.x_range, meta, i0, i1,
                             header=header, record=record)
    return stage_export(request, write, row_budget=row_budget, metadata=metadata)


def stage_export(request, write_source, *, row_budget=None, resolve=None, metadata=None,
                 empty_message="the selected X range contains no source samples"):
    """Return a fully staged open temporary file, or raise before headers.

    Bundles share an actual-work budget: a source growing between the bundle's
    preflight and this locked read cannot bypass the request-wide limit.
    """
    grouped, _ = _preflight_export(request, resolve=resolve)
    if metadata is not None:
        metadata.update(kind=getattr(request, "kind", "time"), request=request.model_dump(exclude={"include_metadata"}), sources=[])

    output = tempfile.SpooledTemporaryFile(max_size=2 * 1024 * 1024, mode="w+b")
    try:
        total = written = 0
        for sources in grouped.values():
            name = sources[0].test
            _check_ready(name)
            progress.update('Waiting for source', source=name)
            with data_read(name, check=progress.checkpoint):
                for source in sources:
                    progress.update('Reading source', source=f'{name} · TP {source.tp_id}' if source.tp_id is not None else name)
                    meta, i0, i1 = (resolve or _resolve)(source, request.column)
                    total = _add_rows(total, i1 - i0)
                    if row_budget is not None:
                        row_budget.add(i1 - i0)
                    if metadata is None:
                        written += write_source(output, source, meta, i0, i1, header=written == 0)
                    else:
                        columns = [getattr(request, 'x_column', request.column), request.column]
                        if getattr(source, 'rpm_col', None):
                            columns.append(source.rpm_col)
                        record = analysis.source_context(source.test, meta, columns, i0, i1,
                            tp_id=source.tp_id, t0=source.t0, t1=source.t1)
                        rows = write_source(output, source, meta, i0, i1, header=written == 0, record=record)
                        record['exported_rows'] = rows
                        metadata['sources'].append(record)
                        written += rows
        if written == 0:
            raise HTTPException(400, empty_message)
        size = output.tell()
        progress.checkpoint()
        output.seek(0)
        if metadata is not None:
            metadata.update(rows=written, sha256=analysis.file_digest(output))
        return output, size, written
    except BaseException:
        output.close()
        raise


def prepare_bundle(request: PlotExportBundleRequest):
    return stage_bundle(request, prepare_export, _filename)


def stage_bundle(request, prepare, filename, *, resolve=None):
    """Stage all CSV entries and finish the ZIP before attachment headers.

    Per-slot calls preserve their independent filter/scope/column semantics.
    They take sequential per-test snapshots, just like individual requests;
    this is deliberately not an immutable cross-slot dataset revision.
    """
    source_count = sum(len(plot.request.sources) for plot in request.plots)
    if source_count > MAX_EXPORT_SOURCES:
        raise HTTPException(400, f"bundle exceeds {MAX_EXPORT_SOURCES} source references")
    total = 0
    for plot in request.plots:
        progress.update('Checking plots', plot=plot.slot)
        try:
            _, rows = _preflight_export(plot.request, resolve=resolve(plot.request) if resolve else None)
            total = _add_rows(total, rows)
        except HTTPException as exc:
            raise HTTPException(exc.status_code, f"Plot {plot.slot}: {exc.detail}") from exc

    output = tempfile.SpooledTemporaryFile(max_size=2 * 1024 * 1024, mode="w+b")
    written = 0
    budget = ExportRowBudget()
    records = []
    try:
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED,
                             compresslevel=6) as archive:
            for position, plot in enumerate(request.plots, 1):
                progress.update('Preparing plot', plot=plot.slot, source=None)
                try:
                    metadata = {} if request.include_metadata else None
                    csv_output, _, rows = (prepare(plot.request, row_budget=budget, metadata=metadata)
                                          if metadata is not None else prepare(plot.request, row_budget=budget))
                except HTTPException as exc:
                    raise HTTPException(exc.status_code, f"Plot {plot.slot}: {exc.detail}") from exc
                with closing(csv_output):
                    entry = f"{position:02d}_slot-{plot.slot}_{filename(plot.request)}"
                    # Stream one completed CSV into one entry; never buffer all
                    # CSVs in memory or merge differently configured variables.
                    with archive.open(entry, "w", force_zip64=True) as destination:
                        progress.copy_file(csv_output, destination)
                if metadata is not None:
                    metadata.update(slot=plot.slot, file=entry)
                    records.append(metadata)
                written += rows
            if request.include_metadata:
                progress.update('Writing analysis metadata')
                archive.writestr('analysis.json', analysis.encode(analysis.document('csv', records, layout=request.layout)))
        size = output.tell()
        progress.checkpoint()
        output.seek(0)
        return output, size, written, source_count
    except BaseException:
        output.close()
        raise


class TemporaryCsvResponse(StreamingResponse):
    """Close the staged file even if sending headers/body is interrupted.

    Starlette background tasks run after a successful response. A failed
    transport can bypass them, so resource cleanup belongs in this finally.
    """

    def __init__(self, output, content, complete, **kwargs):
        super().__init__(content, **kwargs)
        self._output = output
        self._complete = complete
        self._job = progress.current()

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            self._output.close()
            if self._job:
                self._job.finish('completed' if self._complete[0] else 'canceled')


def _temporary_response(output, size: int, filename: str,
                        media_type: str, summary: dict[str, str]):
    job = progress.current()
    complete = [False]
    def stream():
        try:
            while chunk := output.read(256 * 1024):
                if job and job.canceled.is_set():
                    return  # short body cannot be mistaken for a complete file
                yield chunk
            complete[0] = True
        finally:
            output.close()

    disposition = f"attachment; filename*=UTF-8''{quote(filename)}"
    # The response's finally also covers a stream that was never iterated.
    # The temporary file is unnamed and automatically removed when closed.
    return TemporaryCsvResponse(
        output, stream(), complete, media_type=media_type,
        headers={"Content-Disposition": disposition, "Content-Length": str(size),
                 **summary})


@router.post("/api/plot-export")
async def tracked_plot_export(request: PlotExportRequest, http: Request):
    return await progress.run(http, lambda: api_plot_export(request))


def api_plot_export(request: PlotExportRequest):
    return single_export_response(request, prepare_export, _filename)


@router.post("/api/plot-export/bundle")
async def tracked_plot_bundle(request: PlotExportBundleRequest, http: Request):
    return await progress.run(http, lambda: api_plot_export_bundle(request))


def api_plot_export_bundle(request: PlotExportBundleRequest):
    output, size, rows, sources = prepare_bundle(request)
    names = {source.test for plot in request.plots for source in plot.request.sources}
    source_name = next(iter(names))[:80] if len(names) == 1 else "multi-test"
    filename = f"{source_name}_time-plots_{request.layout}_{len(request.plots)}-plots.zip"
    return _temporary_response(output, size, filename, "application/zip", {
        "X-Export-Rows": str(rows), "X-Export-Sources": str(sources),
        "X-Export-Plots": str(len(request.plots))})


def single_export_response(request, prepare, filename):
    metadata = {} if request.include_metadata else None
    output, size, rows = prepare(request, metadata=metadata) if metadata is not None else prepare(request)
    name = filename(request)
    media = 'text/csv'
    if metadata is not None:
        archive_output = tempfile.SpooledTemporaryFile(max_size=2 * 1024 * 1024, mode='w+b')
        try:
            with closing(output), zipfile.ZipFile(archive_output, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
                with archive.open(name, 'w', force_zip64=True) as destination:
                    progress.copy_file(output, destination)
                metadata.update(file=name)
                progress.update('Writing analysis metadata')
                archive.writestr('analysis.json', analysis.encode(analysis.document('csv', [metadata])))
            output = archive_output
            size = output.tell()
            output.seek(0)
            name = name.removesuffix('.csv') + '.zip'
            media = 'application/zip'
        except BaseException:
            archive_output.close()
            raise
    return _temporary_response(output, size, name, media, {
        'X-Export-Rows': str(rows), 'X-Export-Sources': str(len(request.sources))})
