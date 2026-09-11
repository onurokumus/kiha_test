"""Phase 6e real Spectrum CSV/ZIP/PNG checks, isolated sources and Chromium.

Reuse owned-server lifecycle (Python 3.13 native runtime), independent NumPy /
SciPy references, sequential stats warmup, real browser zoom and canvas probes.
Global Python is stdlib/Playwright only. No user data or profile is touched.
"""
import argparse
import csv
import io
import json
import math
import os
from pathlib import Path
import subprocess
import tempfile
import time
import zipfile

from playwright.sync_api import expect, sync_playwright
import verify_spectrum_analysis as analysis
import verify_plot_exports as single
import verify_multi_plot_exports as multi
from verify_data_quality import ROOT, servers, upload
from verify_filter_overlay import dataset_hashes
from verify_browser_zoom import capture_browser_view, set_browser_zoom, wait_for_chart_layout
from verify_time_y_zoom import instrument

BETA = 'spectrum_other_rate'


def run_checks(web, api, dataset, temporary, output):
    cache, evidence, images, posts, downloads, errors, writes = {}, [], [], [], [], [], []
    def oracle(payload, source):
        query = {'test': source['test'], 'col': payload['column'], 'mode': payload['mode']}
        query.update({k: v for k, v in source.items() if k in ('tp_id', 't0', 't1', 'nperseg', 'rpm_col') and v is not None})
        key = json.dumps(query, sort_keys=True)
        if key not in cache:
            result = subprocess.run([str(ROOT/'backend/.venv/Scripts/python.exe'), '-c', analysis.ORACLE_CODE],
                cwd=ROOT/'backend', env={**os.environ, 'KIHA_DATA_DIR': str(dataset)}, input=key,
                capture_output=True, text=True, timeout=40,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            assert result.returncode == 0, result.stderr
            cache[key] = json.loads(result.stdout)
        return cache[key]

    def verify_csv(content, payload):
        rows = list(csv.DictReader(io.StringIO(content)))
        expected = []
        for source in payload['sources']:
            ref = oracle(payload, source)
            axis = [f*60/ref['mean_rpm'] for f in ref['freqs']] if payload['axis'] == 'per_rev' else ref['freqs']
            lo, hi = payload.get('x_range') or (-math.inf, math.inf)
            tol = 8 * 2.220446049250313e-16 * max(1., abs(lo), abs(hi)) if payload.get('x_range') else 0
            for index, value in enumerate(axis):
                if lo-tol <= value <= hi+tol:
                    expected.append((source, ref, index, value))
        assert len(rows) == len(expected), (len(rows), len(expected), payload)
        column = payload['column'] + (' [amplitude U]' if payload['mode'] == 'fft' else ' [PSD U^2/Hz]')
        assert list(rows[0])[-1] == column and not any('[original]' in name or '[filtered]' in name for name in rows[0])
        for row, (source, ref, index, value) in zip(rows, expected):
            assert row['source_test'] == source['test'] and row['test_point_id'] == str(source.get('tp_id', ''))
            assert int(row['bin_index']) == index
            assert math.isclose(float(row['frequency_hz']), ref['freqs'][index], rel_tol=1e-13, abs_tol=1e-14)
            assert math.isclose(float(row[column]), ref['mag'][index], rel_tol=1e-9, abs_tol=2e-14)
            if payload['axis'] == 'per_rev':
                assert math.isclose(float(row['order_cycles_per_rev']), value, rel_tol=1e-13, abs_tol=1e-14)
                assert math.isclose(float(row['mean_rpm']), ref['mean_rpm'], rel_tol=1e-13)
            for field in ('fs_hz', 'n_samples', 'finite_count', 'nan_count', 'time_start_s', 'time_end_s'):
                assert math.isclose(float(row[field]), ref[field], rel_tol=1e-13, abs_tol=1e-12), (field, row[field], ref[field])
            assert int(row['source_i0']) == ref['i0'] == source['expected_i0']
            assert int(row['source_i1']) == ref['i1'] == source['expected_i1']
        for source in payload['sources']:
            block = next((row for row in rows if row['source_test'] == source['test'] and row['test_point_id'] == str(source.get('tp_id', ''))), None)
            if block:
                for key, value in oracle(payload, source)['metadata']['method'].items():
                    actual = block['method_'+key]
                    if isinstance(value, (int, float)) and not isinstance(value, bool):
                        assert math.isclose(float(actual), value, rel_tol=1e-13)
                    else:
                        assert actual == ('' if value is None else str(value).lower() if isinstance(value, bool) else value), (key, actual, value)
        return {'rows': len(rows), 'sources': payload['sources'], 'mode': payload['mode'], 'axis': payload['axis'], 'x_range': payload.get('x_range')}

    def plot(page, column=analysis.SIGNAL):
        return page.get_by_role('group', name=f'{column} spectrum plot', exact=True).first

    def settled(page):
        page.wait_for_load_state('networkidle')
        groups = page.get_by_role('group').filter(has=page.locator('button[aria-label^="Spectrum details for"]'))
        for target in groups.all():
            target.locator('.uplot').wait_for()
            expect(target).not_to_contain_text('Calculating spectrum')
            expect(target).not_to_contain_text('Recalculating spectrum')
        wait_for_chart_layout(page)

    def panel(page, column=analysis.SIGNAL):
        button = plot(page, column).get_by_role('button', name=f'Export {column} plot', exact=True)
        button.focus(); page.keyboard.press('Enter')
        result = page.get_by_role('dialog', name=f'Export {column} plot', exact=True)
        expect(result).to_be_visible()
        result.get_by_role('checkbox',name='Include analysis metadata (ZIP)',exact=True).uncheck(); single.check_panel_layout(result)
        expect(result.get_by_role('radio')).to_have_count(0)
        return result

    def close(page, modal):
        page.keyboard.press('Escape'); expect(modal).not_to_be_visible()

    def select_slots(modal, slots, layout='2x2'):
        modal.get_by_role('radio', name='2 × 2' if layout=='2x2' else '3 × 3', exact=True).check()
        for slot, checkbox in enumerate(modal.locator('input[type=checkbox][aria-label^="Select plot "]').all(), 1):
            checkbox.set_checked(slot in slots)

    def csv_file(page, modal, label):
        before = len(posts)
        path, name = single.download(page, modal.get_by_role('button', name='Download CSV', exact=True), output, label, 'csv')
        assert len(posts) == before+1
        payload = posts[-1]
        result = verify_csv(path.read_text(encoding='utf-8'), payload)
        evidence.append({'file': path.name, 'filename': name, **result})
        return payload, result

    def zip_file(page, modal, label, slots):
        path, name = single.download(page, modal.get_by_role('button', name='Download CSV ZIP', exact=True), output, label, 'zip')
        payload = posts[-1]; assert [entry['slot'] for entry in payload['plots']] == slots
        with zipfile.ZipFile(path) as archive:
            assert len(archive.namelist()) == len(slots)
            for position, (entry_name, entry) in enumerate(zip(archive.namelist(), payload['plots']), 1):
                assert entry_name.startswith(f'{position:02d}_slot-{entry["slot"]}_')
                evidence.append({'file': path.name, 'entry': entry_name,
                    **verify_csv(archive.read(entry_name).decode('utf-8'), entry['request'])})
        return payload

    def png_file(page, modal, label, combined=False, slots=None, layout='2x2'):
        page.evaluate('window.__multiPngCaptures=[]')
        if combined:
            result = multi.download_png(page, modal, output, label, slots, layout)
        else:
            path, name = single.download(page, modal.get_by_role('button', name='Download PNG', exact=True), output, label, 'png')
            result = {'file': path.name, 'filename': name, **single.png_inspect(page, path, [])}
        capture = page.evaluate('window.__multiPngCaptures.at(-1)')
        cards = capture['panels'] if combined else [capture]
        texts = []
        for card in cards:
            content = ' '.join(row['text'] for row in card['texts'])
            assert 'kiha-spectrum-v2' in content and 'rows [' in content and 'prefilter=none' in content, content
            assert 'native bins' in content and 'X (' in content and 'Y (' in content, content
            texts.append(content)
        result['annotation_text'] = texts; images.append(result)
        return texts

    extension = temporary/'extension'; extension.mkdir()
    (extension/'manifest.json').write_text(json.dumps({'manifest_version':3, 'name':'Spectrum export zoom',
        'version':'1.0', 'permissions':['tabs'], 'background':{'service_worker':'background.js'}}))
    (extension/'background.js').write_text('chrome.runtime.onInstalled.addListener(()=>{});')
    with sync_playwright() as playwright:
        request = playwright.request.new_context(base_url=api+'/')
        analysis.fixtures(request)
        lines = ['time,'+','.join(analysis.COLS)]
        for index in range(12288):
            t = index/1024
            lines.append(f'{t},{2*math.sin(2*math.pi*73.5*t)},{3*math.cos(t)},{math.sin(t)},900')
        assert upload(request, BETA, '\n'.join(lines))['status'] == 'ready'
        assert request.put(f'tests/{BETA}/testpoints', data={'version':1, 'test':BETA,
            'test_points':[{'id':7,'name':'Other rate','start_idx':16,'end_idx':8208,'start_s':0.5,'end_s':8.5}]}).ok
        for col in analysis.COLS: assert request.get(f'tests/{BETA}/tp_stats', params={'col':col}).ok
        source_catalog = request.get('analysis-sources').json()['sources']
        hashes = dataset_hashes(dataset)
        context = playwright.chromium.launch_persistent_context(str(temporary/'profile'), channel='chromium',
            headless=True, no_viewport=True, accept_downloads=True, args=[f'--disable-extensions-except={extension}',
            f'--load-extension={extension}', '--window-size=1440,1000'])
        page = context.pages[0]; state = {'fail':False, 'hold':False, 'partial':False, 'legacy':False}
        held = []
        page.on('download', lambda item: downloads.append(item.suggested_filename))
        page.on('pageerror', lambda error: errors.append(error.stack or str(error)))
        page.on('console', lambda message: errors.append(message.text) if message.type=='error' else None)
        page.on('request', lambda req: writes.append((req.method,req.url)) if req.url.startswith(api)
            and req.method not in ('GET','HEAD','OPTIONS') and '/export-progress' not in req.url and '/spectrum-export' not in req.url else None)
        def export_route(route):
            payload = route.request.post_data_json; posts.append(payload)
            if state['fail']:
                payload = json.loads(json.dumps(payload))
                target = payload['plots'][-1]['request'] if 'plots' in payload else payload
                # Valid schema and preflight; actual DSP rejects stale RPM during staging.
                target['sources'][-1]['expected_mean_rpm'] = 0.123
                response = route.fetch(post_data=json.dumps(payload))
                assert response.status==409 and 'attachment' not in response.headers.get('content-disposition','')
                route.fulfill(response=response)
            elif state['hold']:
                held.append((route,route.fetch()))
            else: route.continue_()
        def spectrum_route(route):
            if state['partial'] and 'col=signal_N' in route.request.url and 'tp_id=9' in route.request.url:
                route.fulfill(status=503,json={'detail':'Isolated Spectrum failure'}); return
            if state['legacy']:
                response=route.fetch(); body=response.json(); body.pop('method',None)
                route.fulfill(response=response,json=body)
            else: route.continue_()
        context.route('**/src/utils/uplotSync.ts*', instrument)
        context.route('**/spectrum-export', export_route)
        context.route('**/spectrum-export/bundle', export_route)
        context.route('**/spectrum?*', spectrum_route)
        try:
            analysis.seed(page, source_catalog)
            page.add_init_script(f'''if(!sessionStorage.getItem('export-extra')){{
                const state=JSON.parse(localStorage.getItem('ptt.analysis-session.v1'));
                state.selections.push({{test:{json.dumps(BETA)},tpId:7,hidden:false}});
                localStorage.setItem('ptt.analysis-session.v1',JSON.stringify(state));
                sessionStorage.setItem('export-extra','1');
            }}''')
            page.add_init_script(multi.CANVAS_INSTRUMENT)
            worker = context.service_workers[0] if context.service_workers else context.wait_for_event('serviceworker')
            page.goto(web); settled(page)
            modal = panel(page)
            payload, result = csv_file(page,modal,'fft-default')
            assert payload['x_range'] is None and len(payload['sources']) == 3
            assert result['rows'] == 8193+4097+4097
            png_file(page,modal,'fft-default'); close(page,modal)
            modal = panel(page,analysis.REPAIRED); csv_file(page,modal,'fft-missing');close(page,modal)
            initial = plot(page).locator('.uplot').evaluate('el=>[el.__verificationPlot.scales.x.min,el.__verificationPlot.scales.x.max]')
            plot(page).locator('.u-over').hover();page.mouse.wheel(0,-400);page.wait_for_timeout(250)
            modal = panel(page); cropped,_=csv_file(page,modal,'fft-frequency-zoom')
            assert cropped['x_range'] and cropped['x_range'][1]-cropped['x_range'][0] < initial[1]-initial[0]
            png_file(page,modal,'fft-frequency-zoom');close(page,modal)
            over=plot(page).locator('.u-over');box=over.bounding_box()
            page.keyboard.down('Shift');page.mouse.move(box['x']+box['width']*.55,box['y']+box['height']*.5)
            page.mouse.down();page.mouse.move(box['x']+box['width']*.65,box['y']+box['height']*.5,steps=8)
            page.mouse.up();page.keyboard.up('Shift');page.wait_for_timeout(200)
            modal=panel(page);panned,_=csv_file(page,modal,'fft-frequency-pan')
            assert panned['x_range'] and panned['x_range']!=cropped['x_range'];close(page,modal)
            plot(page).locator('.u-over').dblclick();page.wait_for_timeout(200)
            modal=panel(page);reset,_=csv_file(page,modal,'fft-reset');assert reset['x_range'] is None;close(page,modal)
            page.get_by_role('combobox',name='Estimator',exact=True).select_option('welch');settled(page)
            page.get_by_role('group',name='Spectrum x-axis').get_by_role('button',name='Per rev',exact=True).click();settled(page)
            modal=panel(page);linear,_=csv_file(page,modal,'welch-order-linear');close(page,modal)
            page.get_by_role('button',name='Log scale',exact=True).click();settled(page)
            modal=panel(page);logged,_=csv_file(page,modal,'welch-order-log')
            assert logged==linear and (output/'welch-order-linear.csv').read_bytes()==(output/'welch-order-log.csv').read_bytes()
            texts=png_file(page,modal,'welch-order-log');assert 'log10' in texts[0] and 'units=U²/Hz' in texts[0];close(page,modal)
            plot(page).locator('.u-over').hover();page.mouse.wheel(0,-300);page.wait_for_timeout(200)
            modal=panel(page);cropped,_=csv_file(page,modal,'welch-order-zoom');assert cropped['x_range'];close(page,modal)
            print('PASS: native default/end bins, exact saved rows, cross-test rates, missing values, FFT/PSD reference arrays, frequency crop/reset and order/log semantics',flush=True)

            plot(page).get_by_role('button',name=f'Expand {analysis.SIGNAL}',exact=True).click();settled(page)
            expect(multi.trigger(page)).to_be_disabled()
            # Actual visible uPlot legend, not only application TP selection.
            legend=plot(page).locator('.u-legend .u-series').filter(has_text=BETA)
            legend.locator('.u-label').click();page.wait_for_timeout(150)
            modal=panel(page);hidden,_=csv_file(page,modal,'legend-hidden')
            assert len(hidden['sources'])==2 and all(s['test']!=BETA for s in hidden['sources'])
            texts=png_file(page,modal,'legend-hidden');assert BETA not in texts[0];close(page,modal)
            plot(page).locator('.uplot').evaluate('el=>el.__verificationPlot.series.forEach((s,i)=>i&&el.__verificationPlot.setSeries(i,{show:false}))')
            modal=panel(page);before=len(downloads);modal.get_by_role('button',name='Download CSV',exact=True).click()
            expect(modal.get_by_role('alert')).to_contain_text('Show at least one');assert len(downloads)==before;close(page,modal)
            plot(page).get_by_role('button',name=f'Minimize {analysis.SIGNAL}',exact=True).click();settled(page)
            plot(page).locator('.u-over').hover();page.mouse.wheel(0,-300);page.wait_for_timeout(200)
            modal=multi.open_panel(page);select_slots(modal,[1,3,4],layout='2x2')
            quad=zip_file(page,modal,'selected-2x2',[1,3,4])
            assert quad['plots'][0]['request']['x_range'] and all(p['request']['x_range'] is None for p in quad['plots'][1:])
            png_file(page,modal,'selected-2x2',True,[1,3,4]);close(page,modal)
            modal=multi.open_panel(page);select_slots(modal,[1,2,4],layout='3x3')
            zip_file(page,modal,'selected-3x3',[1,2,4]);png_file(page,modal,'selected-3x3',True,[1,2,4],'3x3');close(page,modal)
            # Real 9-slot layout, including duplicate variables.
            page.evaluate('''cols=>{const state=JSON.parse(localStorage.getItem('ptt.analysis-session.v1'));
                state.plotDensity='nine';state.plotConfigs=[...cols,...cols,cols[0]];
                localStorage.setItem('ptt.analysis-session.v1',JSON.stringify(state));}''',analysis.COLS)
            page.reload();settled(page)
            modal=multi.open_panel(page);expect(modal.locator('input[type=checkbox][aria-label^="Select plot "]')).to_have_count(9)
            zip_file(page,modal,'nine-slots',list(range(1,10)))
            png_file(page,modal,'nine-slots',True,list(range(1,10)),'3x3');close(page,modal)
            print('PASS: visible legend, all-hidden rejection, maximize/restore, selected2x2/3x3 and nine-slot ZIP/PNG',flush=True)

            modal=multi.open_panel(page);select_slots(modal,[1,4],layout='2x2')
            before=len(downloads);state['fail']=True
            modal.get_by_role('button',name='Download CSV ZIP',exact=True).click()
            expect(modal.get_by_role('alert')).to_contain_text('Plot 4:');assert len(downloads)==before
            state['fail']=False;zip_file(page,modal,'retry-after-late-failure',[1,4])
            before=len(downloads);state['hold']=True
            modal.get_by_role('button',name='Download CSV ZIP',exact=True).click()
            deadline=time.monotonic()+20
            while not held and time.monotonic()<deadline: page.wait_for_timeout(50)
            assert held;close(page,modal);state['hold']=False
            for route,response in held: route.fulfill(response=response)
            held.clear();page.wait_for_timeout(350);assert len(downloads)==before
            state['partial']=True
            page.get_by_role('combobox',name='Estimator',exact=True).select_option('fft');page.wait_for_load_state('networkidle')
            expect(plot(page)).to_contain_text('could not be loaded')
            modal=panel(page)
            expect(modal.get_by_role('button',name='Download CSV',exact=True)).to_be_disabled()
            expect(modal.get_by_role('button',name='Download PNG',exact=True)).to_be_disabled();close(page,modal)
            state['partial']=False
            page.get_by_role('combobox',name='Estimator',exact=True).select_option('welch');settled(page)
            state['legacy']=True
            page.get_by_role('combobox',name='Estimator',exact=True).select_option('fft');settled(page)
            modal=panel(page);expect(modal.get_by_role('button',name='Download CSV',exact=True)).to_be_disabled()
            expect(modal).to_contain_text('current backend');close(page,modal)
            state['legacy']=False
            page.get_by_role('combobox',name='Estimator',exact=True).select_option('welch');settled(page)
            print('PASS: real late-source failure with no partial attachment, Retry, Close suppression, partial-result and legacy-context export guards',flush=True)

            # Full time zoom determines the estimator's source rows, not its frequency crop.
            page.get_by_role('group',name='Data source').get_by_role('button',name='Full test',exact=True).click();settled(page)
            modes=page.get_by_role('group',name='Plot mode',exact=True)
            modes.get_by_role('button',name='Full test',exact=True).click();page.wait_for_load_state('networkidle')
            time_plot=page.get_by_role('group',name=f'{analysis.SIGNAL} full test plot',exact=True).first
            time_plot.locator('.u-over').wait_for();wait_for_chart_layout(page)
            time_plot.locator('.u-over').hover();page.mouse.wheel(0,-300);page.wait_for_timeout(700);page.wait_for_load_state('networkidle')
            interval=page.evaluate("JSON.parse(localStorage.getItem('ptt.analysis-session.v1')).fullRange")
            assert interval and interval[0]>0
            modes.get_by_role('button',name='Spectrum',exact=True).click();settled(page)
            modal=panel(page);full,_=csv_file(page,modal,'full-time-interval')
            assert full['sources'][0]['t0']==interval[0] and full['sources'][0]['t1']==interval[1]
            assert full['x_range'] is None and full['sources'][0]['expected_i0']>0;close(page,modal)
            modal=multi.open_panel(page);select_slots(modal,[1,5,9],layout='3x3');zip_file(page,modal,'full-slots',[1,5,9]);close(page,modal)
            cdp=context.new_cdp_session(page);window=cdp.send('Browser.getWindowForTarget')['windowId'];baseline=page.evaluate('devicePixelRatio')
            for width,factor in ((1100,1),(1440,1.25),(1440,1.5)):
                cdp.send('Browser.setWindowBounds',{'windowId':window,'bounds':{'width':width,'height':1000}})
                set_browser_zoom(worker,page,factor)
                page.wait_for_function('dpr=>Math.abs(devicePixelRatio-dpr)<.001',arg=baseline*factor);settled(page)
                tag=f'full-{width}-{factor}'
                modal=multi.open_panel(page);select_slots(modal,[1,4],layout='2x2');single.check_panel_layout(modal)
                capture_browser_view(cdp,output/f'{tag}-dialog.png')
                zip_file(page,modal,tag,[1,4]);png_file(page,modal,tag,True,[1,4]);close(page,modal)
                plot(page).get_by_role('button',name=f'Expand {analysis.SIGNAL}',exact=True).click();settled(page)
                modal=panel(page);png_file(page,modal,tag+'-expanded');close(page,modal)
                expect(plot(page).get_by_role('button',name=f'Export {analysis.SIGNAL} plot',exact=True)).to_be_focused()
                plot(page).get_by_role('button',name=f'Minimize {analysis.SIGNAL}',exact=True).click();settled(page)
            assert hashes==dataset_hashes(dataset),'Source files changed'
            unexpected=[error for error in errors if '503' not in error and '409' not in error]
            assert not unexpected,unexpected
            assert not writes,writes
            (output/'results.json').write_text(json.dumps({'csv_entries':evidence,'images':images,'native_oracles':len(cache),
                'downloads':downloads,'source_files_unchanged':sorted(hashes),'unexpected_errors':unexpected,'unexpected_writes':writes},indent=2),encoding='utf-8')
            print(f'PASS: Full source/time interval, keyboard/focus/Escape,1100px/125%/150%, {len(evidence)} CSV entries, {len(images)} PNGs, {len(hashes)} unchanged files',flush=True)
        except Exception:
            page.screenshot(path=str(output/'failure.png'),full_page=True)
            (output/'failure-state.json').write_text(json.dumps({'errors':errors,'posts':posts,
                'body':page.locator('body').inner_text()},indent=2),encoding='utf-8')
            raise
        finally:
            context.close();request.dispose()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--frontend-port',type=int,default=3150)
    parser.add_argument('--backend-port',type=int,default=8150)
    args=parser.parse_args()
    output=ROOT/'data/verification/spectrum-exports';output.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='kiha-spectrum-exports-') as directory:
        temporary=Path(directory)
        with servers(temporary,output,args.frontend_port,args.backend_port) as (web,api,dataset):
            run_checks(web,api,dataset,temporary,output)
    print('PASS: owned servers stopped; temporary data/profile removed',flush=True)


if __name__=='__main__': main()
