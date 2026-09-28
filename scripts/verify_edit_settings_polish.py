"""Verify compact Edit and Settings controls in a disposable browser profile.

Reads existing tests; intercepts metadata saves and page-default publication before
any server write. Personal settings and import/export fixtures stay in the temporary
browser profile. Requires a built running preview and Python Playwright/Chromium.
"""

import argparse, json, sys, tempfile
from pathlib import Path
from urllib.request import urlopen
from urllib.parse import unquote
from playwright.sync_api import sync_playwright, expect
sys.stdout.reconfigure(encoding='utf-8')
ROOT=Path(__file__).resolve().parents[1]
from verify_browser_zoom import set_browser_zoom, capture_browser_view
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--url',default='http://127.0.0.1:8087/ptt/')
URL=parser.parse_args().url
OUT=Path(tempfile.gettempdir())/'ptt-edit-settings-polish'; OUT.mkdir(exist_ok=True)
report={'checks':[], 'pageErrors':[], 'simulatedWrites':[], 'blockedWrites':[], 'geometry':[]}
def check(name): report['checks'].append(name); print(name,flush=True)
with tempfile.TemporaryDirectory(prefix='ptt-edit-settings-') as temp, sync_playwright() as p:
 temp=Path(temp); ext=temp/'extension';ext.mkdir()
 (ext/'manifest.json').write_text(json.dumps({'manifest_version':3,'name':'Isolated desktop zoom','version':'1.0','permissions':['tabs'],'background':{'service_worker':'background.js'}}))
 (ext/'background.js').write_text('chrome.runtime.onInstalled.addListener(()=>{});')
 context=p.chromium.launch_persistent_context(str(temp/'profile'),channel='chromium',headless=True,no_viewport=True,args=[f'--disable-extensions-except={ext}',f'--load-extension={ext}','--window-size=1440,1000'])
 page=context.pages[0];page.set_default_timeout(10000)
 page.on('pageerror',lambda error: report['pageErrors'].append(str(error)))
 worker=context.service_workers[0] if context.service_workers else context.wait_for_event('serviceworker')
 cdp=context.new_cdp_session(page); window=cdp.send('Browser.getWindowForTarget')['windowId']
 publish_fail=[True]
 def route_api(route):
  r=route.request
  if r.method in ('GET','HEAD','OPTIONS'): route.continue_();return
  if r.url.endswith('/settings/defaults') and r.method=='PUT':
   report['simulatedWrites'].append({'action':'defaults','payload':r.post_data_json})
   if publish_fail[0]: route.fulfill(status=500,json={'detail':'Fixture publication failed'})
   else: route.fulfill(json={'settings':r.post_data_json})
  elif r.url.endswith('/meta') and r.method=='PATCH':
   with urlopen(r.url[:-5]) as response: meta=json.load(response)
   meta.update(r.post_data_json)
   report['simulatedWrites'].append({'action':'metadata','payload':r.post_data_json})
   route.fulfill(json=meta)
  else:
   report['blockedWrites'].append({'method':r.method,'url':r.url});route.abort()
 context.route('**/api/**',route_api)
 def nav(name):
  page.get_by_role('navigation',name='Main navigation').get_by_role('button',name=name,exact=True).click(); page.wait_for_load_state('networkidle')
 def disclosure(label,container=None):
  return (container or page).locator('summary').filter(has_text=label).first
 def more():
  el=page.get_by_label('More settings actions',exact=True)
  if not el.locator('..').evaluate('e=>e.open'): el.click()
  return el
 def geometry(label,selector):
  result=page.locator(selector).evaluate('''e=>({width:innerWidth,viewportOverflow:document.documentElement.scrollWidth-innerWidth,localOverflow:e.scrollWidth-e.clientWidth, header:e.querySelector('header').getBoundingClientRect().toJSON()})''')
  report['geometry'].append({'label':label,**result})
  assert result['viewportOverflow']<=1 and result['localOverflow']<=1,(label,result)
 page.goto(URL);page.wait_for_load_state('networkidle')
 nav('Settings')
 expect(page.get_by_role('button',name='Save',exact=True)).to_be_disabled()
 more().focus();page.keyboard.press('Enter');expect(page.get_by_role('button',name='Export JSON',exact=True)).not_to_be_visible()
 page.keyboard.press('Enter');expect(page.get_by_role('button',name='Export JSON',exact=True)).to_be_visible()
 page.keyboard.press('Escape');expect(page.get_by_label('More settings actions',exact=True)).to_be_focused()
 check('Settings secondary menu: keyboard toggle and Escape return focus')
 more()
 with page.expect_download() as download: page.get_by_role('button',name='Export JSON',exact=True).click()
 original=json.loads(Path(download.value.path()).read_text());assert 'gridColumns' in original and 'xyXCols' in original
 imported={**original,'scatterX':'saved_signal_not_loaded','uploadFsHz':'4096'}
 page.locator('.settings-page input[type=file]').set_input_files({'name':'fixture-settings.json','mimeType':'application/json','buffer':json.dumps(imported).encode()})
 expect(page.locator('.settings-page [data-searchable-select]').first).to_contain_text('saved_signal_not_loaded')
 expect(page.get_by_role('button',name='Save',exact=True)).to_be_enabled()
 more()
 with page.expect_download() as download: page.get_by_role('button',name='Export JSON',exact=True).click()
 draft_export=json.loads(Path(download.value.path()).read_text());assert draft_export['scatterX']=='saved_signal_not_loaded' and draft_export['uploadFsHz']=='4096'
 page.get_by_role('button',name='Revert',exact=True).click();expect(page.get_by_role('button',name='Save',exact=True)).to_be_disabled()
 check('Settings import is a reviewable draft; export includes unsaved values; Revert restores')
 more();page.locator('.settings-page input[type=file]').set_input_files({'name':'broken.json','mimeType':'application/json','buffer':b'not json'})
 expect(page.get_by_text('could not import broken.json',exact=False)).to_be_visible()
 page.locator('.settings-page input[type=file]').set_input_files({'name':'fixture-settings.json','mimeType':'application/json','buffer':json.dumps(imported).encode()})
 page.get_by_role('button',name='Save',exact=True).click();expect(page.get_by_role('button',name='Save',exact=True)).to_be_disabled()
 page.reload();page.wait_for_load_state('networkidle');nav('Settings')
 expect(page.locator('.settings-page [data-searchable-select]').first).to_contain_text('saved_signal_not_loaded')
 check('Settings invalid import reports error; Save persists imported missing signal through reload')
 disclosure('Upload sample rate').click();rate=page.get_by_label('Default rate (Hz)',exact=True);rate.fill('-1')
 expect(page.get_by_role('button',name='Save',exact=True)).to_be_disabled();expect(page.get_by_text('Check rate',exact=True)).to_be_visible()
 more();expect(page.get_by_role('button',name='Make default for everyone',exact=True)).to_be_disabled()
 rate.fill('2048');more();page.get_by_role('button',name='Make default for everyone',exact=True).click()
 expect(page.get_by_role('alertdialog')).to_be_visible();page.get_by_role('button',name='Cancel',exact=True).click()
 expect(page.get_by_role('button',name='Make default for everyone',exact=True)).to_be_focused();assert not report['simulatedWrites']
 page.get_by_role('button',name='Make default for everyone',exact=True).click();page.get_by_role('button',name='Make page defaults',exact=True).click()
 expect(page.get_by_text('could not update page defaults:',exact=False)).to_be_visible()
 publish_fail[0]=False;more();page.get_by_role('button',name='Make default for everyone',exact=True).click();page.get_by_role('button',name='Make page defaults',exact=True).click()
 expect(page.get_by_text('These settings are now the page defaults for everyone.',exact=True)).to_be_visible()
 check('Settings rate validation, publication confirmation/cancel focus, simulated failure and retry')
 more();page.get_by_role('button',name='Reset to defaults',exact=True).click();expect(page.get_by_role('button',name='Save',exact=True)).to_be_enabled();page.get_by_role('button',name='Revert',exact=True).click()
 disclosure('XY pairings').click();expect(page.get_by_label('Preferred X signal for XY cell 9',exact=True)).to_be_visible()
 expect(page.get_by_label('Preferred Y signal for XY cell 9',exact=True)).to_be_visible()
 check('Settings all nine XY pairs stay reachable; Reset remains an unsaved draft')
 nav('Edit');edit=page.locator('.edit-view');save=page.get_by_role('button',name='Save notes and metadata',exact=True)
 page.get_by_label('Description',exact=True).fill('Browser-only verification note');expect(save).to_be_enabled()
 nav('Settings');expect(page.get_by_role('alertdialog')).to_be_visible();page.get_by_role('button',name='Cancel',exact=True).click();expect(page.locator('.edit-view textarea').first).to_have_value('Browser-only verification note')
 save.click();expect(save).to_be_disabled();assert report['simulatedWrites'][-1]['payload']['description']=='Browser-only verification note'
 check('Edit notes dirty guard survives navigation cancel; toolbar Save uses original PATCH payload (intercepted)')
 disclosure('Additional metadata').click();page.get_by_role('button',name='Add field',exact=True).click();page.get_by_label('Metadata field 1',exact=True).fill('ambient');page.get_by_label('Metadata value 1',exact=True).fill('20 C')
 disclosure('Additional metadata').click();expect(disclosure('Additional metadata')).to_contain_text('Unsaved changes')
 page.get_by_role('button',name='Reset drafts',exact=True).click();expect(save).to_be_disabled()
 disclosure('Data cleanup').click();page.get_by_role('combobox',name='Missing-value policy',exact=True).select_option('zero_fill');disclosure('Data cleanup').click();expect(disclosure('Data cleanup')).to_contain_text('Unsaved changes')
 page.get_by_role('button',name='Reset drafts',exact=True).click()
 check('Edit metadata and cleanup keep drafts, visible collapsed indicators and reset behavior')
 disclosure('Derived variables').click();page.get_by_label('Equation 1 result column',exact=True).fill('qa_result');page.get_by_label('Equation 1 expression',exact=True).fill('1 + 2')
 page.get_by_role('button',name='+ add equation',exact=True).click();expect(page.get_by_label('Equation 2 expression',exact=True)).to_be_visible()
 page.get_by_label('Remove equation 2',exact=True).click();disclosure('Derived variables').click();expect(disclosure('Derived variables')).to_contain_text('unsaved changes');page.get_by_role('button',name='Reset drafts',exact=True).click()
 disclosure('Rename or remove columns').click();first=page.get_by_role('textbox',name=__import__('re').compile('^Rename column')).first;first.fill('qa_renamed');disclosure('Rename or remove columns').click();expect(disclosure('Rename or remove columns')).to_contain_text('unsaved changes');page.get_by_role('button',name='Reset drafts',exact=True).click()
 check('Edit equation row operations and column rename drafts retain original controls and guards')
 for label,selector in [('Edit','.edit-view'),('Settings','.settings-page')]:
  nav(label)
  for width,zoom in [(1440,1),(1100,1),(800,1),(1440,1.25),(1440,1.5)]:
   cdp.send('Browser.setWindowBounds',{'windowId':window,'bounds':{'width':width,'height':1000}});set_browser_zoom(worker,page,zoom);page.wait_for_timeout(160)
   geometry(f'{label}-{width}-{zoom}',selector)
   if label=='Settings':
    more();menu=page.get_by_role('button',name='Export JSON',exact=True);expect(menu).to_be_visible();assert menu.bounding_box()['x']>=0;page.keyboard.press('Escape')
   capture_browser_view(cdp,OUT/f'{label.lower()}-{width}-{zoom}.png')
  set_browser_zoom(worker,page,1);cdp.send('Browser.setWindowBounds',{'windowId':window,'bounds':{'width':1440,'height':1000}})
 check('Desktop geometry and accessible menus at 1440/1100/800px plus actual 125%/150% browser zoom')
 nav('Settings')
 cdp.send('Browser.setWindowBounds',{'windowId':window,'bounds':{'width':960,'height':400}});set_browser_zoom(worker,page,1.5)
 trigger=more();trigger.focus()
 for action in ['Export JSON','Import JSON','Make default for everyone','Reset to defaults']:
  page.keyboard.press('Tab');button=page.get_by_role('button',name=action,exact=True);expect(button).to_be_focused()
  bounds=button.bounding_box();assert bounds and bounds['y']>=0 and bounds['y']+bounds['height']<=page.evaluate('innerHeight')+1,(action,bounds)
 panel=page.get_by_role('button',name='Export JSON',exact=True).locator('..')
 bounds=panel.bounding_box();assert bounds['y']+bounds['height']<=page.evaluate('innerHeight')+1,bounds
 capture_browser_view(cdp,OUT/'settings-short-menu-150.png')
 page.get_by_role('heading',name='Workspace settings',exact=True).click();expect(page.get_by_role('button',name='Export JSON',exact=True)).not_to_be_visible()
 check('Settings More fits short desktop at 150% zoom, keyboard scrolls to every action, outside heading click dismisses')
 set_browser_zoom(worker,page,1);cdp.send('Browser.setWindowBounds',{'windowId':window,'bounds':{'width':1440,'height':1000}})
 nav('Edit');disclosure('Derived variables').click();page.get_by_label('Equation 1 expression',exact=True).scroll_into_view_if_needed();expect(page.get_by_role('button',name='Save notes and metadata',exact=True)).to_be_in_viewport()
 check('Edit toolbar stays visible during long equation editor scrolling')
 assert not report['pageErrors'],report['pageErrors'];assert not report['blockedWrites'],report['blockedWrites']
 (OUT/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8');print('OUTPUT',OUT)
 context.close()
