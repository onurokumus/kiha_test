"""Native-bin Spectrum exports; share time export locking, budgets and staging.

Rows from independent sources are stacked with identity, never frequency-joined.
X crop is applied AFTER the complete estimator; logarithms are display-only.
"""
from __future__ import annotations

import os
import re
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
import numpy as np
import pyarrow as pa
import pyarrow.csv as pa_csv
from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator

from . import dsp, plot_export as shared, export_progress as progress

router = APIRouter()


class SpectrumSource(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    test: str = Field(min_length=1, max_length=256)
    tp_id: StrictInt | None = None
    t0: float | None = None
    t1: float | None = None
    expected_i0: StrictInt = Field(ge=0)
    expected_i1: StrictInt = Field(gt=0)
    expected_fs_hz: float = Field(gt=0)
    nperseg: StrictInt = Field(default=4096, ge=1, le=8_000_000)
    rpm_col: str | None = Field(default=None, min_length=1, max_length=1024)
    expected_mean_rpm: float | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def validate_scope(self):
        # Keep exact saved-TP / nominal Full semantics aligned with time exports.
        shared.ExportSource.validate_scope(self)
        return self


class SpectrumExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    kind: Literal["spectrum"] = "spectrum"
    column: str = Field(min_length=1, max_length=1024)
    mode: Literal["fft", "welch"]
    axis: Literal["hz", "per_rev"]
    method_version: Literal["kiha-spectrum-v2"]
    sources: list[SpectrumSource] = Field(min_length=1, max_length=shared.MAX_EXPORT_SOURCES)
    include_metadata: bool = False
    x_range: tuple[float, float] | None = None

    @model_validator(mode="after")
    def validate_export(self):
        if self.x_range is not None and self.x_range[0] > self.x_range[1]:
            raise ValueError("x_range must be increasing")
        if len(self.sources) > 1 and any(s.tp_id is None for s in self.sources):
            raise ValueError("a full-test export must contain exactly one source")
        identities = [(os.path.normcase(s.test), s.tp_id) for s in self.sources]
        if len(set(identities)) != len(identities):
            raise ValueError("duplicate export sources")
        if self.axis == "per_rev" and any(
                not s.rpm_col or s.expected_mean_rpm is None for s in self.sources):
            raise ValueError("order export requires each displayed RPM column and mean")
        return self


class SpectrumBundlePlot(shared.BundlePlot):
    request: SpectrumExportRequest


class SpectrumBundleRequest(shared.PlotExportBundleRequest):
    plots: list[SpectrumBundlePlot] = Field(min_length=1, max_length=9)


def filename(request: SpectrumExportRequest) -> str:
    names = {s.test for s in request.sources}
    name = next(iter(names)) if len(names) == 1 else "multi-test"
    column = re.sub(r'[^\w.()-]+', '_', request.column).strip('._') or 'signal'
    scope = 'test-points' if request.sources[0].tp_id is not None else 'full-test'
    return f"{name[:80]}_{column[:80]}_{scope}_{request.mode}_{request.axis}.csv"


def prepare_export(request: SpectrumExportRequest, *, row_budget=None, metadata=None):
    def write(output, source, meta, i0, i1, *, header, record=None):
        progress.update('Calculating spectrum')
        if meta["fs_hz"] != source.expected_fs_hz:
            raise HTTPException(409, f"sample rate changed in '{source.test}'; refresh the plot")
        if source.rpm_col and source.rpm_col not in meta["columns"]:
            raise HTTPException(400, f"unknown RPM column '{source.rpm_col}' in '{source.test}'")
        try:
            result = dsp.spectrum_samples(
                source.test, request.column, mode=request.mode,
                t0=source.t0, t1=source.t1, tp_id=source.tp_id,
                nperseg=source.nperseg, rpm_col=source.rpm_col)
        except (ValueError, KeyError) as exc:
            raise HTTPException(400, f"cannot export '{source.test}': {exc}") from exc
        info = result.metadata
        progress.checkpoint()
        if record is not None:
            record["processing"] = info
        if (info['i0'], info['i1']) != (i0, i1) or info['method']['version'] != request.method_version:
            raise HTTPException(409, "Spectrum context changed; refresh the plot")
        mean = info.get('mean_rpm')
        if source.expected_mean_rpm is not None and mean != source.expected_mean_rpm:
            raise HTTPException(409, f"mean RPM changed in '{source.test}'; refresh the plot")
        with np.errstate(over='ignore', invalid='ignore', divide='ignore'):
            axis = result.freqs if request.axis == 'hz' else result.freqs * 60 / mean
        if not np.isfinite(axis).all():
            raise HTTPException(400, f"nonfinite frequency axis in '{source.test}'; check sample rate and RPM")
        keep = np.ones(len(axis), dtype=bool)
        if request.x_range is not None:
            lo, hi = request.x_range
            tolerance = 8 * np.finfo(float).eps * max(1., abs(lo), abs(hi))
            keep = (axis >= lo - tolerance) & (axis <= hi + tolerance)
        indices = np.flatnonzero(keep)
        if record is not None:
            record["exported_bins"] = {"count": int(indices.size), "first": int(indices[0]) if indices.size else None, "last": int(indices[-1]) if indices.size else None}
            record["axis"] = {"kind": request.axis, "order_formula": "frequency_hz * 60 / mean_abs_rpm" if request.axis == "per_rev" else None, "y_values": "linear", "psd_units": "U^2/Hz" if request.mode == "welch" else None}
        # Repeated metadata makes each source block independently interpretable;
        # all fields come from this calculation, not the client's display JSON.
        metadata = {
            'source_test': source.test, 'test_point_id': str(source.tp_id) if source.tp_id is not None else None,
            'source_i0': info['i0'], 'source_i1': info['i1'],
            'time_start_s': info['time_start_s'], 'time_end_s': info['time_end_s'],
            'fs_hz': info['fs_hz'], 'n_samples': info['n_samples'],
            'finite_count': info['finite_count'], 'nan_count': info['nan_count'],
            'estimator': request.mode, 'display_x_axis': request.axis,
            'crop_x_min': request.x_range[0] if request.x_range else None,
            'crop_x_max': request.x_range[1] if request.x_range else None,
            **{f'method_{key}': value for key, value in info['method'].items()},
            **{f'quality_{key}': value for key, value in info['quality'].items()},
        }
        if request.axis == 'per_rev':
            metadata.update({key: info.get(key) for key in (
                'rpm_col', 'mean_rpm', 'min_rpm', 'max_rpm', 'rpm_finite_count', 'rpm_nan_count')})
        for start in range(0, len(indices), shared._BATCH_SIZE):
            progress.update('Writing spectral bins', completed=start, total=len(indices), unit='bins')
            bins = indices[start:start + shared._BATCH_SIZE]
            names = list(metadata) + ['bin_index', 'frequency_hz']
            arrays = [pa.repeat(value, len(bins)) for value in metadata.values()]
            arrays += [pa.array(bins), pa.array(result.freqs[bins])]
            if request.axis == 'per_rev':
                names.append('order_cycles_per_rev')
                arrays.append(pa.array(axis[bins]))
            names.append(f"{request.column} [{'amplitude U' if request.mode == 'fft' else 'PSD U^2/Hz'}]")
            arrays.append(pa.array(result.mag[bins]))
            pa_csv.write_csv(pa.RecordBatch.from_arrays(arrays, names=names), output,
                             write_options=pa_csv.WriteOptions(include_header=header and start == 0))
        return len(indices)

    return shared.stage_export(request, write, row_budget=row_budget, metadata=metadata)


@router.post('/api/spectrum-export')
async def tracked_spectrum_export(request: SpectrumExportRequest, http: Request):
    return await progress.run(http, lambda: api_spectrum_export(request))


def api_spectrum_export(request: SpectrumExportRequest):
    return shared.single_export_response(request, prepare_export, filename)


@router.post('/api/spectrum-export/bundle')
async def tracked_spectrum_bundle(request: SpectrumBundleRequest, http: Request):
    return await progress.run(http, lambda: api_spectrum_bundle(request))


def api_spectrum_bundle(request: SpectrumBundleRequest):
    output, size, rows, sources = shared.stage_bundle(request, prepare_export, filename)
    return shared._temporary_response(output, size,
        f'spectrum-plots_{request.layout}_{len(request.plots)}-plots.zip', 'application/zip', {
            'X-Export-Rows': str(rows), 'X-Export-Sources': str(sources),
            'X-Export-Plots': str(len(request.plots))})
