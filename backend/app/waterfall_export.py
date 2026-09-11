"""Waterfall grid exports through the shared locked, cancelable staging path."""
import re
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
import numpy as np
import pyarrow as pa
import pyarrow.csv as pa_csv
from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator

from . import plot_export as shared, export_progress as progress, waterfall

router = APIRouter()


class WaterfallSource(shared.ExportSource):
    expected_i0: StrictInt = Field(ge=0)
    expected_i1: StrictInt = Field(gt=0)
    expected_fs_hz: float = Field(gt=0)


class WaterfallRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    kind: Literal['waterfall'] = 'waterfall'
    column: str = Field(min_length=1, max_length=1024)
    method_version: Literal['kiha-waterfall-v1', 'kiha-waterfall-v2']
    sources: list[WaterfallSource] = Field(min_length=1, max_length=shared.MAX_EXPORT_SOURCES)
    nperseg: StrictInt = 1024
    overlap: StrictInt = 50
    resolution_hz: float | None = None
    grid_frequency_range_hz: tuple[float, float] | None = None
    grid_time_range_s: tuple[float, float] | None = None
    x_range: tuple[float, float] | None = None
    y_range: tuple[float, float] | None = None
    include_metadata: bool = False

    @model_validator(mode='after')
    def validate_options(self):
        waterfall.validate_detail_options(self.method_version == waterfall.DETAIL_VERSION,
            self.resolution_hz, self.grid_frequency_range_hz, self.grid_time_range_s)
        if ((self.resolution_hz is None and self.nperseg not in waterfall.WINDOWS)
                or self.overlap not in (0, 25, 50, 75)):
            raise ValueError('invalid waterfall window/overlap')
        for bounds in (self.x_range, self.y_range):
            if bounds and bounds[0] >= bounds[1]:
                raise ValueError('axis range must increase')
        identities = [(s.test, s.tp_id) for s in self.sources]
        if len(set(identities)) != len(identities):
            raise ValueError('duplicate sources')
        return self


class BundlePlot(shared.BundlePlot):
    request: WaterfallRequest


class BundleRequest(shared.PlotExportBundleRequest):
    plots: list[BundlePlot] = Field(min_length=1, max_length=9)


def filename(request):
    column = re.sub(r'[^\w.()-]+', '_', request.column).strip('._') or 'signal'
    return f'{column[:80]}_waterfall-grid.csv'


def prepare_export(request, *, row_budget=None, metadata=None):
    def write(output, source, meta, i0, i1, *, header, record=None):
        if source.expected_fs_hz != meta['fs_hz']:
            raise HTTPException(409, 'sample rate changed; reload the waterfall')
        progress.update('Calculating waterfall FFT')
        try:
            result = waterfall.calculate(source.test, request.column, source.t0, source.t1,
                tp_id=source.tp_id, nperseg=request.nperseg, overlap=request.overlap,
                high_detail=request.method_version == waterfall.DETAIL_VERSION, resolution_hz=request.resolution_hz,
                frequency_range=request.grid_frequency_range_hz, time_range=request.grid_time_range_s)
        except (ValueError, KeyError) as exc:
            raise HTTPException(400, str(exc)) from exc
        if (result['i0'], result['i1']) != (i0, i1):
            raise HTTPException(409, 'source interval changed; reload the waterfall')
        if record is not None:
            record['processing'] = {key: value for key, value in result.items() if key != 'magnitude'}
            record['csv_values'] = 'Linear peak amplitude; cells intersecting the viewport; explicit aggregate bounds'
        f = np.asarray(result['frequency_edges_hz'])
        t = np.asarray(result['time_edges_s'])
        cols = np.arange(max(0, len(f) - 1))
        rows = np.arange(max(0, len(t) - 1))
        if request.x_range:
            cols = cols[(f[1:] >= request.x_range[0]) & (f[:-1] <= request.x_range[1])]
        if request.y_range:
            rows = rows[(t[1:] >= request.y_range[0]) & (t[:-1] <= request.y_range[1])]
        total = 0
        for row in rows:
            progress.checkpoint()
            if not len(cols):
                continue
            scalar = {'source_test': source.test, 'test_point_id': source.tp_id, 'variable': request.column,
                'source_i0': i0, 'source_i1': i1, 'fs_hz': result['fs_hz'],
                'method_version': result['method']['version'], 'nperseg': result['method']['nperseg'],
                'noverlap': result['method']['noverlap'], 'nan_count': result['nan_count'],
                'time_factor': result['reduction']['time_factor'], 'frequency_factor': result['reduction']['frequency_factor'],
                'elapsed_start_s': t[row], 'elapsed_end_s': t[row + 1],
                'first_frame_source_time_s': result['source_frame_start_s'][row],
                'last_frame_source_time_s': result['source_frame_end_s'][row]}
            arrays = {key: pa.repeat(value, len(cols)) for key, value in scalar.items()}
            arrays.update(frequency_start_hz=pa.array(f[cols]), frequency_end_hz=pa.array(f[cols + 1]),
                          magnitude_U=pa.array(np.asarray(result['magnitude'][row])[cols]))
            pa_csv.write_csv(pa.table(arrays), output, write_options=pa_csv.WriteOptions(include_header=header and total == 0))
            total += len(cols)
        return total
    return shared.stage_export(request, write, row_budget=row_budget, metadata=metadata,
                               empty_message='the selected waterfall viewport contains no cells')


@router.post('/api/waterfall-export')
async def export(request: WaterfallRequest, http: Request):
    return await progress.run(http, lambda: shared.single_export_response(request, prepare_export, filename))


@router.post('/api/waterfall-export/bundle')
async def bundle(request: BundleRequest, http: Request):
    def run():
        output, size, rows, sources = shared.stage_bundle(request, prepare_export, filename)
        return shared._temporary_response(output, size, f'waterfall-plots_{request.layout}.zip',
            'application/zip', {'X-Export-Rows': str(rows), 'X-Export-Sources': str(sources)})
    return await progress.run(http, run)
