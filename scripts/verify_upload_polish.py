"""Isolated Uploads polish checks: setup, metadata, history, lifecycle and desktop zoom.

Uses sibling verification helpers, temporary data and a disposable browser profile.
Only the backend child imports scientific libraries, through the project's Python
3.13 environment. Screenshots default to the system temporary directory.
"""
from pathlib import Path
import argparse
import tempfile, json, re
from playwright.sync_api import sync_playwright, expect
from verify_data_quality import servers, upload
from verify_browser_zoom import set_browser_zoom, capture_browser_view


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--frontend-port', type=int, default=3314)
    parser.add_argument('--backend-port', type=int, default=8314)
    parser.add_argument('--output', type=Path, default=Path(tempfile.gettempdir()) / 'ptt-upload-polish')
    args = parser.parse_args()
    out = args.output
    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='ptt-upload-polish-') as folder:
        temp=Path(folder)
        ext=temp/'extension';ext.mkdir()
        (ext/'manifest.json').write_text(json.dumps({'manifest_version':3,'name':'Upload zoom checks','version':'1.0','permissions':['tabs'],'background':{'service_worker':'background.js'}}))
        (ext/'background.js').write_text('chrome.runtime.onInstalled.addListener(()=>{});')
        with servers(temp,out,args.frontend_port,args.backend_port) as (web,api,dataset), sync_playwright() as p:
            request=p.request.new_context(base_url=api+'/')
            csv='time,rpm,signal\n'+''.join(f'{i/10},{100+i},{i}\n' for i in range(80))
            upload(request,'reference',csv)
            before={str(f):f.read_bytes() for f in (dataset/'tests/reference').rglob('*') if f.is_file() and f.name in ['data.parquet','raw.csv','meta.json','testpoints.json']}
            fixture=temp/'compact_setup.csv';fixture.write_text(csv)
            ctx=p.chromium.launch_persistent_context(str(temp/'profile'),channel='chromium',headless=True,no_viewport=True,args=[f'--disable-extensions-except={ext}',f'--load-extension={ext}','--window-size=1440,1000'])
            page=ctx.pages[0]; errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            worker=ctx.service_workers[0] if ctx.service_workers else ctx.wait_for_event('serviceworker')
            cdp=ctx.new_cdp_session(page);wid=cdp.send('Browser.getWindowForTarget')['windowId']
            try:
                page.goto(web);page.wait_for_load_state('networkidle');page.get_by_role('button',name='Uploads',exact=True).click()
                clear=page.get_by_role('button',name='Clear upload history filters',exact=True)
                expect(clear).to_be_disabled()
                search=page.get_by_role('searchbox',name='Search upload history')
                search.fill('missing');expect(clear).to_be_enabled();clear.click();expect(search).to_have_value('')
                expect(page.get_by_role('button',name='Analyze reference',exact=True)).to_be_visible()
                capture_browser_view(cdp,out/'history-1440.png')
                with page.expect_file_chooser() as chooser:page.get_by_role('button',name=re.compile('Import test data')).click()
                chooser.value.set_files(str(fixture))
                setup=page.get_by_role('region',name='Import setup',exact=True)
                uploader=setup.get_by_label('Uploaded by',exact=True);expect(uploader).to_be_focused();uploader.fill('Compact lab')
                disclosure=setup.locator('summary').filter(has_text='Description and components')
                expect(setup.get_by_role('textbox',name=re.compile('Description'))).not_to_be_visible()
                mode=setup.get_by_role('combobox',name='Time basis',exact=True)
                original=setup.get_by_label('Fallback rate (Hz)',exact=True).bounding_box()
                mode.select_option('column');current=setup.get_by_label('Fallback rate (Hz)',exact=True).bounding_box()
                assert current['x']==original['x'] and current['width']==original['width'],(current,original)
                mode.select_option('generated');current=setup.get_by_label('Sample rate (Hz)',exact=True).bounding_box()
                assert current['x']==original['x'] and current['width']==original['width'],(current,original)
                rate=setup.get_by_label('Sample rate (Hz)',exact=True);rate.fill('0');expect(setup.get_by_role('button',name='Upload 1 file',exact=True)).to_be_disabled();rate.fill('10')
                disclosure.focus();page.keyboard.press('Enter');description=setup.get_by_role('textbox',name=re.compile('Description'));description.fill('Setup description survives collapsing')
                setup.get_by_role('button',name='Add component set',exact=True).click()
                group=setup.get_by_role('group',name='Component set 1',exact=True)
                group.get_by_role('combobox',name='Electric motor',exact=True).select_option('__new')
                disclosure.click();expect(disclosure).to_contain_text('Unfinished component');expect(setup.get_by_role('button',name='Upload 1 file',exact=True)).to_be_disabled()
                disclosure.click();group.get_by_role('button',name='Cancel new component',exact=True).click()
                group.get_by_role('textbox',name='Set name',exact=True).fill('Front rig')
                disclosure.click();expect(disclosure).to_contain_text('Description added');expect(disclosure).to_contain_text('1 component set')
                for width,zoom in [(1440,1),(1100,1),(1440,1.25),(1440,1.5),(800,1)]:
                    cdp.send('Browser.setWindowBounds',{'windowId':wid,'bounds':{'width':width,'height':1000}});set_browser_zoom(worker,page,zoom)
                    setup.scroll_into_view_if_needed();assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'),(width,zoom)
                    assert setup.evaluate('el=>el.scrollWidth<=el.clientWidth+1'),(width,zoom)
                    expect(setup.get_by_role('button',name='Upload 1 file',exact=True)).to_be_enabled()
                    capture_browser_view(cdp,out/f'setup-{width}-{zoom}.png')
                set_browser_zoom(worker,page,1);cdp.send('Browser.setWindowBounds',{'windowId':wid,'bounds':{'width':1440,'height':1000}})
                setup.get_by_role('button',name='Upload 1 file',exact=True).click()
                expect(page.get_by_role('button',name='Analyze compact_setup',exact=True)).to_be_visible(timeout=20000)
                meta=request.get('tests/compact_setup').json();assert meta['description']=='Setup description survives collapsing';assert meta['component_sets'][0]['name']=='Front rig';assert meta['fs_hz']==10
                expect(page.get_by_role('button',name='Edit notes for compact_setup',exact=True)).to_have_text('Setup description survives collapsing')
                row=page.get_by_role('row').filter(has=page.get_by_role('button',name='Analyze compact_setup',exact=True))
                row.locator('summary',has_text='Manage').click();row.get_by_role('button',name='Delete compact_setup',exact=True).click();page.get_by_role('alertdialog').get_by_role('button',name='Move to trash',exact=True).click()
                trash=page.get_by_role('region',name='Trash bin',exact=True);expect(trash.get_by_role('button',name=re.compile('Trash')) .first).to_have_attribute('aria-expanded','true')
                trash.get_by_role('button',name='Restore…',exact=True).click();trash.get_by_role('button',name='Restore test',exact=True).click();expect(page.get_by_role('button',name='Analyze compact_setup',exact=True)).to_be_visible(timeout=20000)
                assert all(Path(f).read_bytes()==content for f,content in before.items());assert not errors,errors
                print('PASS: compact history filters, keyboard disclosure, stable time controls, validation, draft retention, upload metadata, delete/restore, 800/1100/1440 windows and 125%/150% browser zoom; reference dataset unchanged')
                (out/'results.json').write_text(json.dumps({'passed':True,'errors':errors,'screenshots':str(out)},indent=2))
            except Exception:
                page.screenshot(path=str(out/'failure.png'),full_page=True);print(page.locator('body').aria_snapshot()[-14000:]);raise
            finally:ctx.close();request.dispose()


if __name__ == '__main__':
    main()
