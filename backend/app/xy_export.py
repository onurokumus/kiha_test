"""Original full-resolution XY pairs, staged with shared plot-export safeguards."""
from __future__ import annotations

from contextlib import closing
import os
import re
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
import numpy as np
import pyarrow as pa
import pyarrow.csv as pa_csv
from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator

from . import plot_export as shared, store, export_progress as progress

router = APIRouter()


class XYSource(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    test: str = Field(min_length=1, max_length=256)
    tp_id: StrictInt | None = None
    t0: float | None = None
    t1: float | None = None
    expected_i0: StrictInt = Field(ge=0)
    expected_i1: StrictInt = Field(gt=0)
    expected_time_column: str = Field(min_length=1, max_length=1024)

    @model_validator(mode="after")
    def validate_scope(self):
        shared.ExportSource.validate_scope(self)
        return self


class XYExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    kind: Literal["xy"] = "xy"
    column: str = Field(min_length=1, max_length=1024)  # Y, shared staging convention
    x_column: str = Field(min_length=1, max_length=1024)
    method_version: Literal["kiha-xy-v2"]
    sources: list[XYSource] = Field(min_length=1, max_length=shared.MAX_EXPORT_SOURCES)
    include_metadata: bool = False
    x_range: tuple[float, float] | None = None
    y_range: tuple[float, float] | None = None

    @model_validator(mode="after")
    def validate_export(self):
        for label, bounds in (("x_range", self.x_range), ("y_range", self.y_range)):
            if bounds is not None and bounds[0] > bounds[1]:
                raise ValueError(f"{label} must be increasing")
        if len(self.sources) > 1 and any(s.tp_id is None for s in self.sources):
            raise ValueError("a full-test export must contain exactly one source")
        identities = [(os.path.normcase(s.test), s.tp_id) for s in self.sources]
        if len(set(identities)) != len(identities):
            raise ValueError("duplicate export sources")
        return self


class XYBundlePlot(shared.BundlePlot):
    request: XYExportRequest


class XYBundleRequest(shared.PlotExportBundleRequest):
    plots: list[XYBundlePlot] = Field(min_length=1, max_length=9)


def resolver(request):
    def resolve(source, column):
        meta, i0, i1 = shared._resolve(source, column, allow_time=True)
        if request.x_column not in meta['columns']:
            raise HTTPException(400, f"unknown X column '{request.x_column}' in '{source.test}'")
        if meta['time_column'] != source.expected_time_column:
            raise HTTPException(409, f"time column changed in '{source.test}'; refresh the plot")
        return meta, i0, i1
    return resolve


def filename(request):
    names = {s.test for s in request.sources}
    name = next(iter(names)) if len(names) == 1 else 'multi-test'
    def safe(column):
        return (re.sub(r'[^\w.()-]+', '_', column).strip('._') or 'signal')[:55]
    scope = 'test-points' if request.sources[0].tp_id is not None else 'full-test'
    return f'{name[:60]}_{safe(request.column)}_vs_{safe(request.x_column)}_{scope}_xy.csv'


def _inside(values, bounds):
    if bounds is None:
        return np.ones(values.size, dtype=bool)
    lo, hi = bounds
    # Generic axes can be nano/micro units: a tolerance floor of 1 would
    # incorrectly include nearby values. Scale only to the actual bounds.
    tolerance = 8 * np.finfo(float).eps * max(abs(lo), abs(hi))
    return (values >= lo - tolerance) & (values <= hi + tolerance)


def prepare_export(request: XYExportRequest, *, row_budget=None, metadata=None):
    def write(output, source, meta, i0, i1, *, header, record=None):
        tcol = meta['time_column']; written = seen = 0
        columns = [tcol, request.x_column, request.column]
        if record is not None:
            record["processing"] = {"method_version": store.XY_METHOD_VERSION, "filter": None, "missing_values": "omit_nonfinite_pairs", "finite_pairs": 0, "nonfinite_pairs": 0}
        with closing(store.iter_xy_batches(source.test, columns, i0, i1)) as batches:
            for start, batch in batches:
                progress.update('Writing XY pairs', completed=seen, total=i1 - i0, unit='rows')
                seen += batch.num_rows
                try:
                    x = store._batch_float64(batch, request.x_column)
                    y = x if request.column == request.x_column else store._batch_float64(batch, request.column)
                    times = store._batch_float64(batch, tcol)
                except ValueError as exc:
                    raise HTTPException(400, f"cannot export '{source.test}': {exc}") from exc
                keep = np.isfinite(x) & np.isfinite(y) & _inside(x, request.x_range) & _inside(y, request.y_range)
                if record is not None:
                    shared.analysis.observe_rows(record, np.arange(start, start + batch.num_rows), times, keep)
                    finite = int((np.isfinite(x) & np.isfinite(y)).sum())
                    record['processing']['finite_pairs'] += finite
                    record['processing']['nonfinite_pairs'] += batch.num_rows - finite
                count = int(keep.sum())
                if not count:
                    continue
                metadata = {
                    'source_test': source.test, 'test_point_id': str(source.tp_id) if source.tp_id is not None else None,
                    'source_i0': i0, 'source_i1': i1, 'source_fs_hz': meta.get('fs_hz'),
                    'time_column': tcol, 'method_version': store.XY_METHOD_VERSION,
                    'source': 'stored', 'prefilter': 'none', 'missing_values': 'omit_nonfinite_pairs',
                    'crop_x_min': request.x_range[0] if request.x_range else None,
                    'crop_x_max': request.x_range[1] if request.x_range else None,
                    'crop_y_min': request.y_range[0] if request.y_range else None,
                    'crop_y_max': request.y_range[1] if request.y_range else None,
                }
                names = list(metadata) + ['sample_index', 'time_s']
                arrays = [pa.repeat(value, count) for value in metadata.values()]
                arrays += [pa.array(np.arange(start, start + batch.num_rows, dtype=np.int64)[keep]),
                           pa.array(times[keep], mask=~np.isfinite(times[keep]))]
                mask = pa.array(keep)
                names.append(f'{request.x_column} [X/Y]' if request.x_column == request.column else f'{request.x_column} [X]')
                arrays.append(batch.column(batch.schema.get_field_index(request.x_column)).filter(mask))
                if request.x_column != request.column:
                    names.append(f'{request.column} [Y]')
                    arrays.append(batch.column(batch.schema.get_field_index(request.column)).filter(mask))
                pa_csv.write_csv(pa.RecordBatch.from_arrays(arrays, names=names), output,
                                 write_options=pa_csv.WriteOptions(include_header=header and written == 0))
                written += count
        if seen != i1 - i0:
            raise HTTPException(409, f"stored row count changed in '{source.test}'; refresh the plot")
        return written
    return shared.stage_export(request, write, row_budget=row_budget, metadata=metadata, resolve=resolver(request),
                               empty_message='the selected XY range contains no finite pairs')


@router.post('/api/xy-export')
async def tracked_xy_export(request: XYExportRequest, http: Request):
    return await progress.run(http, lambda: api_xy_export(request))


def api_xy_export(request: XYExportRequest):
    return shared.single_export_response(request, prepare_export, filename)


@router.post('/api/xy-export/bundle')
async def tracked_xy_bundle(request: XYBundleRequest, http: Request):
    return await progress.run(http, lambda: api_xy_bundle(request))


def api_xy_bundle(request: XYBundleRequest):
    output, size, rows, sources = shared.stage_bundle(request, prepare_export, filename, resolve=resolver)
    return shared._temporary_response(output, size, f'xy-plots_{request.layout}_{len(request.plots)}-plots.zip',
        'application/zip', {'X-Export-Rows': str(rows), 'X-Export-Sources': str(sources),
                            'X-Export-Plots': str(len(request.plots))})
