"""Package a captured PNG and its matching loaded metadata, without rereads.

Wire format: one UTF-8 JSON line followed by PNG bytes. Stream into a bounded
temporary file; never persist browser images or trust a filename as a path.
"""
import hashlib
import json
import re
import struct
import tempfile
import zipfile

from fastapi import APIRouter, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from . import analysis_metadata as analysis, plot_export as shared, export_progress as progress

router = APIRouter()
MAX_IMAGE_BYTES = 64 * 1024 * 1024


def package_image(incoming):
    progress.update('Checking image')
    incoming.seek(0)
    line = incoming.readline(analysis.MAX_METADATA_BYTES + 1)
    if len(line) > analysis.MAX_METADATA_BYTES or not line.endswith(b'\n'):
        raise HTTPException(400, 'Image metadata is missing or exceeds 2 MiB.')
    try:
        def invalid_constant(_value):
            raise ValueError('nonfinite metadata')
        document = json.loads(line, parse_constant=invalid_constant)
        if document['schema'] != analysis.SCHEMA or document['format'] != 'png':
            raise ValueError('unsupported metadata schema')
        plots = document['plots']
        layout = document.get('layout')
        capacity = 1 if layout is None else {'2x2': 4, '3x3': 9}[layout]
        if not isinstance(plots, list) or not 1 <= len(plots) <= capacity:
            raise ValueError('invalid plot count')
        if layout:
            slots = [item['slot'] for item in plots]
            if any(type(slot) is not int or not 1 <= slot <= 9 for slot in slots) or slots != sorted(set(slots)):
                raise ValueError('invalid plot slots')
        for plot in plots:
            if (not isinstance(plot, dict) or not isinstance(plot.get('sources'), list)
                    or not plot['sources'] or not all(isinstance(source, dict) for source in plot['sources'])):
                raise ValueError('missing loaded source context')
        name = document.pop('filename')
        if not isinstance(name, str):
            raise ValueError('invalid filename')
        name.encode('utf-8')  # reject unpaired JSON surrogates before naming the archive
    except (ValueError, KeyError, TypeError, UnicodeError, RecursionError) as exc:
        raise HTTPException(400, f'Invalid image metadata: {exc}') from None
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', name).removesuffix('.png').strip('. ')[:160] or 'plot'
    while len(name.encode('utf-16-le')) > 320:
        name = name[:-1]  # keep Windows names bounded without splitting code points
    offset = incoming.tell()
    header = incoming.read(24)
    if header[:16] != b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR' or len(header) != 24:
        raise HTTPException(400, 'The image is not a PNG.')
    width, height = struct.unpack('>II', header[16:24])
    if not (0 < width <= 8192 and 0 < height <= 8192 and width * height <= 16_000_000):
        raise HTTPException(400, 'PNG exceeds the supported image dimensions.')
    incoming.seek(offset)
    digest = hashlib.sha256()
    size = 0
    while chunk := incoming.read(256 * 1024):
        progress.update('Checking image hash', completed=size, total=None, unit='bytes')
        digest.update(chunk)
        size += len(chunk)
    if size > MAX_IMAGE_BYTES:
        raise HTTPException(400, 'PNG exceeds 64 MiB.')
    filename = name + '.png'
    document.update(provenance_origin='loaded_browser_snapshot_no_source_reread',
                    file={'name': filename, 'sha256': digest.hexdigest(), 'size_bytes': size,
                          'width_px': width, 'height_px': height})
    metadata = analysis.encode(document)
    output = tempfile.SpooledTemporaryFile(max_size=2 * 1024 * 1024, mode='w+b')
    try:
        incoming.seek(offset)
        with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            with archive.open(filename, 'w', force_zip64=True) as target:
                progress.copy_file(incoming, target, stage='Packaging PNG')
            progress.update('Writing analysis metadata')
            archive.writestr('analysis.json', metadata)
        size = output.tell()
        output.seek(0)
        return shared._temporary_response(output, size, name + '.zip', 'application/zip', {})
    except BaseException:
        output.close()
        raise


@router.post('/api/plot-image-export')
async def export_image(request: Request):
    return await progress.run(request, lambda: receive_image(request), async_work=True, uploading=True)


async def receive_image(request: Request):
    limit = MAX_IMAGE_BYTES + analysis.MAX_METADATA_BYTES
    length = request.headers.get('content-length', '')
    expected = int(length) if length.isdecimal() else None
    with tempfile.SpooledTemporaryFile(max_size=2 * 1024 * 1024, mode='w+b') as incoming:
        total = 0
        async for chunk in progress.upload_chunks(request):
            total += len(chunk)
            progress.update('Uploading image', completed=total, total=expected, unit='bytes')
            if total > limit:
                raise HTTPException(413, 'Image package exceeds the export size limit.')
            await run_in_threadpool(incoming.write, chunk)
        progress.current().uploading = False
        return await run_in_threadpool(package_image, incoming)
