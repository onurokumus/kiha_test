"""Phase 7b real annotation API/UI/canvas/PNG checks in an isolated desktop profile.

Uses existing real-upload fixtures and native Python3.13 servers. Instrumentation
only observes this browser's uPlot instances and native canvas drawing calls.
"""
import argparse
import json
import math
from pathlib import Path
import re
import tempfile
from uuid import uuid4
import zipfile

from playwright.sync_api import expect, sync_playwright
import verify_plot_exports as single
from verify_multi_plot_exports import CANVAS_INSTRUMENT
from verify_data_quality import ROOT, servers, wait_until
from verify_test_notes import source_hashes
from verify_time_y_zoom import instrument
from verify_browser_zoom import set_browser_zoom, capture_browser_view, wait_for_chart_layout


def run_checks(web, api, dataset, temporary, output):
    extension = temporary / 'extension'
    extension.mkdir()
    (extension / 'manifest.json').write_text(json.dumps({'manifest_version': 3, 'name': 'Annotation zoom verification',
        'version': '1.0', 'permissions': ['tabs'], 'background': {'service_worker': 'background.js'}}))
    (extension / 'background.js').write_text('chrome.runtime.onInstalled.addListener(()=>{});')
    with sync_playwright() as p:
        request = p.request.new_context(base_url=api + '/')
        single.fixtures(request)
        source_catalog = request.get('analysis-sources').json()['sources']
        before = source_hashes(dataset)
        unrelated = (dataset / f'tests/{single.C}/meta.json').read_bytes()
        # Deliberately rounded TP descriptors differ from the actual stored origin.
        trace = request.get(f'tests/{single.A}/testpoints/7/data', params={'cols': single.LOAD}).json()
        origin = trace['series'][single.LOAD]['analysis']['source_centers']['first_time_s']
        assert origin != single.POINTS[0]['start_s'], origin
        marker_time = origin + .25
        interval_times = [origin + .35, origin + .5]
        context = p.chromium.launch_persistent_context(str(temporary / 'profile'), channel='chromium', headless=True,
            no_viewport=True, args=[f'--disable-extensions-except={extension}', f'--load-extension={extension}', '--window-size=1440,1000'])
        page = context.pages[0]
        page_errors, console_errors, writes = [], [], []
        page.on('pageerror', lambda e: page_errors.append(str(e)))
        page.on('console', lambda e: console_errors.append(e.text) if e.type == 'error' else None)
        page.on('request', lambda r: writes.append([r.method, r.url]) if r.url.startswith(api)
                and r.method in ('POST', 'PUT', 'PATCH', 'DELETE') else None)
        settings = {'scatterX': single.LOAD, 'scatterY': single.REFERENCE, 'gridColumns': single.COLS, 'clustering': False}
        session = {'version': 1, 'sources': source_catalog, 'currentTest': single.A, 'xAxis': single.LOAD, 'yAxis': single.REFERENCE,
            'axesUserSet': True, 'plotConfigs': single.COLS, 'plotsUserEdited': True, 'plotDensity': 'quad',
            'scatterCollapsed': True, 'viewMode': 'tp', 'selections': [
                {'test': test, 'tpId': tp, 'hidden': False} for test, tp in ((single.A, 7), (single.A, 9), (single.B, 7))]}
        page.add_init_script(f'''if(!sessionStorage.getItem('annotations-seeded')){{
          sessionStorage.setItem('annotations-seeded','1');
          localStorage.setItem('ptt.settings.v1',{json.dumps(json.dumps(settings))});
          localStorage.setItem('ptt.analysis-session.v1',{json.dumps(json.dumps(session))});}}''')
        page.add_init_script(CANVAS_INSTRUMENT)
        context.route('**/src/utils/uplotSync.ts*', instrument)
        worker = context.service_workers[0] if context.service_workers else context.wait_for_event('serviceworker')
        cdp = context.new_cdp_session(page)
        full = False

        def plot():
            return single.plot(page, full=full)

        def settle():
            plot().locator('.uplot').wait_for()
            page.wait_for_load_state('networkidle')
            wait_for_chart_layout(page)

        def notes():
            button = plot().get_by_role('button', name=f'Time notes for {single.LOAD}', exact=True)
            button.focus(); page.keyboard.press('Enter')
            dialog = page.get_by_role('dialog', name=f'Time notes for {single.LOAD}', exact=True)
            expect(dialog.get_by_role('button', name='Reload saved notes', exact=True)).to_be_enabled()
            if not full:
                source = dialog.get_by_role('combobox', name='Source', exact=True)
                value = source.locator('option').evaluate_all('(els)=>els.find(e=>e.textContent.includes("plot_export_alpha · TP 7")).value')
                source.select_option(value)
                expect(dialog.get_by_role('button', name='Reload saved notes', exact=True)).to_be_enabled()
            return dialog

        def fill(dialog, start, text, end=None):
            dialog.get_by_role('combobox', name='Kind', exact=True).select_option('interval' if end is not None else 'marker')
            dialog.get_by_label('Start (test seconds)' if end is not None else 'Time (test seconds)', exact=True).fill(str(start))
            if end is not None:
                dialog.get_by_label('End (test seconds)', exact=True).fill(str(end))
            dialog.get_by_role('textbox', name='Note', exact=True).fill(text)

        def save(dialog):
            dialog.get_by_role('button', name='Save annotation', exact=True).click()
            expect(dialog.get_by_role('status')).to_have_text('Annotation saved')

        def close(dialog):
            page.keyboard.press('Escape'); expect(dialog).not_to_be_visible()
            expect(plot().get_by_role('button', name=f'Time notes for {single.LOAD}', exact=True)).to_be_focused()

        def assert_drawing(stored=marker_time, expected_count=None):
            settle()
            data = plot().locator('.uplot').evaluate('''async (el, x) => {
              const u=el.__verificationPlot,c=u.ctx,strokes=[],fills=[];let path=[];
              const originals={beginPath:c.beginPath,moveTo:c.moveTo,lineTo:c.lineTo,stroke:c.stroke,fillRect:c.fillRect};
              c.beginPath=function(...a){path=[];return originals.beginPath.apply(this,a);};
              c.moveTo=function(...a){path.push(a);return originals.moveTo.apply(this,a);};
              c.lineTo=function(...a){path.push(a);return originals.lineTo.apply(this,a);};
              c.stroke=function(...a){if(this.getLineDash().length===2 && this.globalAlpha>.8 && this.globalAlpha<.9)strokes.push([...path]);return originals.stroke.apply(this,a);};
              c.fillRect=function(...a){if(this.globalAlpha>.07 && this.globalAlpha<.09)fills.push(a);return originals.fillRect.apply(this,a);};
              try{u.redraw();await new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)));}finally{Object.assign(c,originals);}
              return {strokes,fills,x:u.valToPos(x,'x',true),range:[u.scales.x.min,u.scales.x.max],count:Number(el.dataset.annotationCount)};
            }''', stored if full else stored - origin)
            if expected_count is not None:
                assert data['count'] == expected_count, data
            x = stored if full else stored - origin
            if data['range'][0] <= x <= data['range'][1]:
                assert any(abs(point[0] - data['x']) < 1e-7 for path in data['strokes'] for point in path), data
            return data

        def png(label, multiple=False, hidden=False):
            if multiple:
                page.get_by_role('button', name='Export selected plots', exact=True).click()
                modal = page.get_by_role('dialog', name='Export selected plots', exact=True)
                button = modal.get_by_role('button', name='Download combined PNG', exact=True)
            else:
                modal = single.open_export(page, full=full)
                button = modal.get_by_role('button', name='Download PNG', exact=True)
            modal.get_by_role('checkbox', name='Include analysis metadata (ZIP)', exact=True).check()
            path, _ = single.download(page, button, output, label, 'zip')
            with zipfile.ZipFile(path) as archive:
                document = json.loads(archive.read('analysis.json'))
                for item in document['plots']:
                    notes = item['annotations']
                    assert notes['visible'] is not hidden, item
                    assert bool(notes['items']) is not hidden, item
                    if not hidden:
                        assert any(a['text'] == 'Vibration started 🔧 <b>literal</b>' for a in notes['items']), item
                png_path = output / (label + '.png')
                png_path.write_bytes(archive.read(document['file']['name']))
                single.png_inspect(page, png_path, [])
            capture = page.evaluate('window.__multiPngCaptures.at(-1)')
            text = json.dumps(capture, ensure_ascii=False)
            assert ('Vibration started' in text) is not hidden, text[:400]
            page.keyboard.press('Escape'); expect(modal).not_to_be_visible()

        try:
            page.goto(web); settle()
            dialog = notes()
            expect(dialog.get_by_text('No saved annotations for this test.', exact=True)).to_be_visible()
            fill(dialog, marker_time, 'Invalid interval', marker_time - .1)
            expect(dialog.get_by_role('button', name='Save annotation', exact=True)).to_be_disabled()
            fill(dialog, marker_time, 'x' * 2001)
            expect(dialog.get_by_role('button', name='Save annotation', exact=True)).to_be_disabled()
            fill(dialog, marker_time, 'Vibration started 🔧 <b>literal</b>'); save(dialog)
            fill(dialog, interval_times[0], 'Elevated vibration\nInspect mount', interval_times[1]); save(dialog)
            # Second test's notes are independent and rendered in its own TP frame.
            select = dialog.get_by_role('combobox', name='Source', exact=True)
            target = select.locator('option').evaluate_all('(els)=>els.find(e=>e.textContent.includes("plot_export_beta")).value')
            select.select_option(target)
            expect(dialog.get_by_text('No saved annotations for this test.', exact=True)).to_be_visible()
            fill(dialog, marker_time + .08, 'Second test comparison'); save(dialog)
            close(dialog)
            assert_drawing(expected_count=3)
            print('PASS: legacy reads, keyboard create, source-specific marker/interval, exact TP origin and canvas lines', flush=True)

            dialog = notes()
            dialog.get_by_role('button', name='Edit annotation A2', exact=True).click()
            dialog.get_by_role('textbox', name='Note', exact=True).fill('Edited interval')
            # Real failed response followed by retry must keep draft and old storage.
            url = api + f'/tests/{single.A}/annotations'
            page.route(url, lambda route: route.fulfill(status=503, json={'detail': 'Injected annotation save failure'})
                       if route.request.method == 'PUT' else route.continue_())
            dialog.get_by_role('button', name='Save annotation', exact=True).click()
            expect(dialog.get_by_role('alert')).to_contain_text('Injected annotation save failure')
            expect(dialog.get_by_role('textbox', name='Note', exact=True)).to_have_value('Edited interval')
            assert request.get(f'tests/{single.A}/annotations').json()['annotations'][1]['text'].startswith('Elevated')
            page.unroute(url); save(dialog)
            dialog.get_by_role('button', name='Edit annotation A2', exact=True).click()
            dialog.get_by_role('textbox', name='Note', exact=True).fill('Stale local draft')
            remote = request.get(f'tests/{single.A}/annotations').json()
            items = remote['annotations'] + [{'id': str(uuid4()), 'start_s': 5, 'end_s': None, 'text': 'Remote note'}]
            assert request.put(f'tests/{single.A}/annotations', data={'expected_revision': remote['revision'],
                'expected_data_bounds': remote['data_bounds'], 'annotations': items}).ok
            dialog.get_by_role('button', name='Save annotation', exact=True).click()
            expect(dialog.get_by_role('alert')).to_contain_text('Reload saved notes')
            expect(dialog.get_by_role('textbox', name='Note', exact=True)).to_have_value('Stale local draft')
            dialog.get_by_role('button', name='Reload saved notes', exact=True).click()
            dialog.get_by_role('button', name='Confirm', exact=True).click()
            expect(dialog.get_by_role('button', name='Edit annotation A3', exact=True)).to_be_visible()
            dialog.get_by_role('button', name='Edit annotation A3', exact=True).click()
            dialog.get_by_role('button', name='Delete annotation', exact=True).click()
            dialog.get_by_role('button', name='Keep editing', exact=True).click()
            assert len(request.get(f'tests/{single.A}/annotations').json()['annotations']) == 3
            dialog.get_by_role('button', name='Delete annotation', exact=True).click()
            dialog.get_by_role('button', name='Confirm', exact=True).click()
            expect(dialog.get_by_role('status')).to_have_text('Annotation deleted')
            fill(dialog, marker_time, 'Unsaved')
            page.keyboard.press('Escape')
            expect(dialog.get_by_role('alert')).to_contain_text('discard')
            dialog.get_by_role('button', name='Keep editing', exact=True).click()
            expect(dialog.get_by_role('textbox', name='Note', exact=True)).to_have_value('Unsaved')
            page.keyboard.press('Escape'); dialog.get_by_role('button', name='Confirm', exact=True).click()
            expect(dialog).not_to_be_visible()
            print('PASS: edit/delete/cancel, 503 retry, revision409 preserves draft, explicit reload, unsaved Escape guard', flush=True)

            png('tp-annotations')
            png('multi-annotations', multiple=True)
            checkbox = page.get_by_role('checkbox', name='Show time notes', exact=True)
            checkbox.uncheck()
            expect(plot().locator('.uplot')).to_have_attribute('data-annotation-count', '0')
            page.wait_for_function("JSON.parse(localStorage.getItem('ptt.analysis-session.v1')).annotationsVisible === false")
            page.reload(); settle()
            expect(page.get_by_role('checkbox', name='Show time notes', exact=True)).not_to_be_checked()
            png('hidden-annotations', hidden=True)
            checkbox.check(); assert_drawing(expected_count=3)

            # Failed reads cannot be presented as an empty annotation document.
            page.route(url, lambda route: route.fulfill(status=503, json={'detail': 'Injected annotation read failure'}))
            dialog = notes()
            expect(dialog.get_by_role('alert')).to_contain_text('Injected annotation read failure')
            close(dialog)
            modal = single.open_export(page)
            expect(modal.get_by_role('button', name='Download PNG', exact=True)).to_be_disabled()
            expect(modal).to_contain_text('Time notes for')
            page.keyboard.press('Escape')
            page.unroute(url)
            dialog = notes(); expect(dialog.get_by_role('button', name='Edit annotation A1', exact=True)).to_be_visible(); close(dialog)

            # Legend visibility follows the same source rule as exports.
            series_index = plot().locator('.uplot').evaluate('el=>el.__verificationPlot.series.findIndex(s=>s.label === "Short spike · plot_export_alpha · original")')
            assert series_index > 0
            plot().locator('.uplot').evaluate('(el,i)=>el.__verificationPlot.setSeries(i,{show:false})', series_index)
            expect(plot().locator('.uplot')).to_have_attribute('data-annotation-count', '1')
            plot().locator('.uplot').evaluate('(el,i)=>el.__verificationPlot.setSeries(i,{show:true})', series_index)
            assert_drawing(expected_count=3)
            plot().get_by_role('button', name=f'Filter {single.LOAD}', exact=True).click()
            plot().get_by_role('combobox', name='Filter', exact=True).select_option('moving_avg')
            plot().get_by_label('Window (s)', exact=True).fill('.021')
            expect(plot()).to_have_attribute('data-filter-display', 'filtered', timeout=15000)
            assert_drawing(expected_count=3)
            png('tp-filtered-annotations')
            page.get_by_role('button', name='Full test', exact=True).click(); full = True; settle()
            assert_drawing(expected_count=2)
            png('full-annotations')
            print('PASS: single/multi PNG canvas/text/sidecars, hidden PNG, visibility reload, legend and full-test projection', flush=True)

            for width, factor in ((1440, 1), (1100, 1), (1440, 1.25), (1440, 1.5)):
                window = cdp.send('Browser.getWindowForTarget')
                cdp.send('Browser.setWindowBounds', {'windowId': window['windowId'], 'bounds': {'width': width, 'height': 1000}})
                set_browser_zoom(worker, page, factor); settle()
                assert_drawing(expected_count=2)
                plot().get_by_role('button', name=f'Expand {single.LOAD}', exact=True).click(); settle()
                assert_drawing(expected_count=2)
                dialog = notes(); single.check_panel_layout(dialog)
                assert dialog.evaluate('el=>{const r=el.getBoundingClientRect();return Math.abs(r.x+r.width/2-innerWidth/2)<2 && Math.abs(r.y+r.height/2-innerHeight/2)<2;}')
                if factor == 1.5:
                    capture_browser_view(cdp, output / 'notes-dialog-150.png')
                dialog.get_by_role('button', name='Use view center', exact=True).click()
                value = float(dialog.get_by_label('Time (test seconds)', exact=True).input_value())
                assert math.isfinite(value) and 0 <= value <= 9
                page.keyboard.press('Escape'); dialog.get_by_role('button', name='Confirm', exact=True).click()
                capture_browser_view(cdp, output / f'full-{width}-{factor}.png')
                plot().get_by_role('button', name=f'Minimize {single.LOAD}', exact=True).click(); settle()
            set_browser_zoom(worker, page, 1)
            page.get_by_role('button', name='Test points', exact=True).click(); full = False; settle()
            over = plot().locator('.u-over'); box = over.bounding_box()
            page.mouse.move(box['x'] + box['width'] * .05, box['y'] + box['height'] * .5)
            page.mouse.wheel(0, -120); page.wait_for_timeout(250)
            assert_drawing()
            box = over.bounding_box()
            page.mouse.move(box['x'] + box['width'] * .4, box['y'] + box['height'] * .5)
            page.mouse.down(button='middle')
            page.mouse.move(box['x'] + box['width'] * .5, box['y'] + box['height'] * .5, steps=8)
            page.mouse.up(button='middle'); assert_drawing()
            page.get_by_role('button', name='Reset zoom', exact=True).click(); assert_drawing(expected_count=3)
            page.screenshot(path=str(output / 'tp-restored.png'), full_page=True)
            assert source_hashes(dataset) == before
            assert (dataset / f'tests/{single.C}/meta.json').read_bytes() == unrelated
            assert not page_errors, page_errors
            assert all('503' in e or '409' in e for e in console_errors), console_errors
            assert all('/annotations' in url or url.endswith(('/plot-image-export', '/export-progress')) for _, url in writes), writes
            # A deliberate fixture trim is a separate operation after the source
            # preservation checks. Notes keep their original stored coordinates.
            page.get_by_role('button', name='Full test', exact=True).click(); full = True; settle()
            page.wait_for_function("JSON.parse(localStorage.getItem('ptt.analysis-session.v1')).viewMode === 'full'")
            annotation_file = dataset / f'tests/{single.A}/annotations.json'
            saved_bytes = annotation_file.read_bytes()
            response = request.post(f'tests/{single.A}/edit', data={'trim_t0': .5, 'trim_t1': 1.55})
            assert response.ok, response.text()
            state = wait_until(lambda: (s if (s := request.get(f'tests/{single.A}/status').json())['status'] in ('ready','error') else None), 'fixture trim')
            assert state['status'] == 'ready', state
            assert annotation_file.read_bytes() == saved_bytes
            for col in single.COLS + ['unplotted_secret']:
                assert request.get(f'tests/{single.A}/tp_stats', params={'col': col}).ok
            after_trim = source_hashes(dataset)
            page.reload(); settle()
            expect(plot().locator('.uplot')).to_have_attribute('data-annotation-count', '1')
            dialog = notes()
            expect(dialog.get_by_text('Outside or partly outside current data', exact=True)).to_have_count(2)
            dialog.get_by_role('button', name='Edit annotation A1', exact=True).click()
            assert float(dialog.get_by_label('Time (test seconds)', exact=True).input_value()) == marker_time
            dialog.get_by_role('textbox', name='Note', exact=True).fill('Retained outside trimmed data')
            save(dialog); close(dialog)
            assert source_hashes(dataset) == after_trim
            assert not page_errors, page_errors
            print('PASS: real trim retains note file/timestamps, clips interval, flags retained notes, permits text correction outside data', flush=True)
            (output / 'results.json').write_text(json.dumps({'origin': origin, 'marker': marker_time,
                'source_files_unchanged': len(before), 'writes': writes, 'console_errors': console_errors,
                'page_errors': page_errors}, indent=2), encoding='utf-8')
            print(f'PASS: desktop resize/125%/150%/maximize/restore/wheel/pan, no runtime errors, {len(before)} source files unchanged', flush=True)
        except Exception:
            page.screenshot(path=str(output / 'failure.png'), full_page=True)
            print(page.locator('body').aria_snapshot()[-8000:], flush=True)
            raise
        finally:
            context.close(); request.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--frontend-port', type=int, default=3190)
    parser.add_argument('--backend-port', type=int, default=8190)
    args = parser.parse_args()
    output = ROOT / 'data/verification/annotations'
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='ptt-annotations-') as folder:
        temporary = Path(folder)
        with servers(temporary, output, args.frontend_port, args.backend_port) as (web, api, dataset):
            run_checks(web, api, dataset, temporary, output)
    print('PASS: isolated fixtures/profile removed; owned servers stopped', flush=True)


if __name__ == '__main__':
    main()
