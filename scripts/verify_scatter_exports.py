"""Read-only isolated scatter CSV/PNG browser verification. Uses stdlib + Playwright only."""
import argparse
import base64
import csv
import json
import os
from pathlib import Path
import re
import tempfile
from urllib.parse import parse_qs, unquote, urlparse
from playwright.sync_api import expect, sync_playwright

KEY='ptt.analysis-session.v1'
A,B,DS='Scatter_export_alpha','Scatter_export_beta','Scatter_reference'
X,Y='thrust_N','torque_Nm'
POINTS={A:[{'id':1,'name':'Precision, "quoted"\npoint','label':'steady','start_s':.01234567890123456,'end_s':1.1234567890123457},{'id':2,'name':'Overlap alpha','label':'steady','start_s':2,'end_s':3},{'id':9.125,'name':'Decimal identity','label':'other','start_s':3,'end_s':4},{'id':8,'name':'Missing Y','label':'missing','start_s':4,'end_s':5}],B:[{'id':1,'name':'Overlap beta','label':'steady','start_s':0,'end_s':1}],DS:[]}
VALUES={(A,1):(12.345678901234567,.00000123456789012345),(A,2):(50.000000000001,50.000000000001),(A,9.125):(85.98765432109876,90.12345678901234),(A,8):(30,None),(B,1):(50.000000000001,50.000000000001)}
VALID=[key for key,value in VALUES.items() if None not in value]
SOURCES=[{'name':name,'id':'scatter-export-'+name,'revision':'fixture-v1','status':'ready','columns':[X,Y],'test_points':[{'id':point['id'],'revision':'point-'+str(point['id'])} for point in points]} for name,points in POINTS.items()]
INSTRUMENT=r'''(() => {
window.__scatterSvgs=[]; window.__scatterToBlobMode='normal';
const objectUrl=URL.createObjectURL;
URL.createObjectURL=function(blob){if(blob.type.includes('svg'))blob.text().then(text=>window.__scatterSvgs.push(text));return objectUrl.call(this,blob);};
const toBlob=HTMLCanvasElement.prototype.toBlob;
HTMLCanvasElement.prototype.toBlob=function(callback,...rest){
const mode=window.__scatterToBlobMode;window.__scatterToBlobMode='normal';
if(mode==='fail'){setTimeout(()=>callback(null),0);return;}
if(mode==='hold'){window.__scatterHeld=()=>{delete window.__scatterHeld;toBlob.call(this,callback,...rest);};return;}
return toBlob.call(this,callback,...rest);};})();'''

class Fixture:
    def __init__(self): self.requests=[]; self.blocked=[]; self.errors=[]
    def info(self,name): return {'name':name,'status':'ready','n_rows':600 if name!=DS else 3,'fs_hz':100,'duration_s':6,'n_columns':3,'source_file':name+'.csv','created_at':'2026-09-28T08:00:00Z'}
    def route(self,route):
        request=route.request; parsed=urlparse(request.url); path=unquote(parsed.path).split('/api',1)[1]; query=parse_qs(parsed.query)
        self.requests.append(request.method+' '+path)
        if request.method!='GET': self.blocked.append(request.method+' '+path);route.abort();return
        if path=='/settings/defaults': body={'settings':None}
        elif path=='/analysis-sources': body={'version':1,'sources':SOURCES}
        elif path=='/tests': body=[self.info(name) for name in POINTS]
        elif path=='/components': body={'version':1,'components':[]}
        elif path=='/trash': body={'entries':[],'retention_seconds':3600}
        elif path.startswith('/tests/'):
            parts=path.split('/');name=parts[2];assert name in POINTS,path
            if len(parts)==3: body={**self.info(name),'columns':['time_s',X,Y],'time_column':'time_s','t_start':0,'time_source':'measured','source_time_origin_s':100 if name==DS else 0}
            elif path.endswith('/testpoints'): body={'version':1,'test':name,'test_points':POINTS[name]}
            elif path.endswith('/tp_stats'):
                column=query['col'][0];body=[]
                for point in POINTS[name]:
                    mean=VALUES[(name,point['id'])][[X,Y].index(column)]
                    body.append({**point,'mean':mean,'min':None if mean is None else mean-2.25,'max':None if mean is None else mean+3.5,'std':.125,'n':200,'n_valid':200 if mean is not None else 0})
            elif '/testpoints/' in path and path.endswith('/data'):
                point=next(point for point in POINTS[name] if str(point['id'])==parts[-2]);times=[i/100 for i in range(100)]
                body={'test':name,'test_point':point,'mode':'raw','level':1,'n_raw':100,'i0':0,'i1':100,'point_budget':1500,'time_origin_s':point['start_s'],'duration_s':1,'series':{col:{'t':times,'y':[10+t for t in times]} for col in query['cols'][0].split(',')}}
            elif path.endswith('/data') and name==DS: body={'test':name,'mode':'raw','level':1,'n_raw':3,'i0':0,'i1':3,'point_budget':4000,'t':[0,1,2],'series':{X:[5.123456789012345,None,95.98765432109876],Y:[6.123456789012345,60,95.98765432109876]}}
            else: self.errors.append(path);route.fulfill(status=404,json={'detail':'Unexpected request'});return
        else: self.errors.append(path);route.fulfill(status=404,json={'detail':'Unexpected request'});return
        route.fulfill(status=200,json=body)
    def configure(self,page):
        settings={'scatterX':X,'scatterY':Y,'clustering':True,'gridColumns':[Y],'datasheetZone':DS,'datasheetVisible':False,'defaultViewMode':'tp'}
        session={'version':1,'sources':SOURCES,'currentTest':A,'xAxis':X,'yAxis':Y,'axesUserSet':True,'clusteringEnabled':True,'showHorizontalErrorBars':True,'showVerticalErrorBars':True,'datasheetVisible':False,'selections':[{'test':A,'tpId':1,'hidden':False,'color':'#f97316'}],'plotConfigs':[Y],'plotsUserEdited':True,'plotDensity':'single'}
        page.add_init_script(f"if(!localStorage.getItem({json.dumps(KEY)})){{localStorage.setItem('ptt.settings.v1',{json.dumps(json.dumps(settings))});localStorage.setItem({json.dumps(KEY)},{json.dumps(json.dumps(session))});}}")
        page.add_init_script(INSTRUMENT);page.route('**/api/**',self.route)

def settled(page):
    page.wait_for_load_state('networkidle');page.locator('.recharts-surface').first.wait_for()
    page.evaluate('() => new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))')
    expect(page.get_by_role('button',name='More',exact=True)).to_be_visible()

def open_dialog(page):
    more=page.get_by_role('button',name='More',exact=True);more.focus();page.keyboard.press('Enter')
    expect(more).to_have_attribute('aria-expanded','true')
    trigger=page.get_by_role('button',name='Export scatter plot',exact=True);trigger.focus();page.keyboard.press('Enter')
    dialog=page.get_by_role('dialog',name='Export scatter plot',exact=True);expect(dialog).to_be_visible()
    expect(more).to_have_attribute('aria-expanded','false')
    expect(dialog.get_by_role('checkbox',name='Include analysis metadata (ZIP)',exact=True)).to_have_count(0)
    assert dialog.evaluate('el=>el.contains(document.activeElement)');return dialog

def close(page,dialog): page.keyboard.press('Escape');expect(dialog).not_to_be_visible()

def download(page,dialog,fmt,out,tag):
    button=dialog.get_by_role('button',name='Download '+fmt,exact=True);expect(button).to_be_enabled()
    with page.expect_download(timeout=30000) as event: button.click()
    file=event.value;assert file.failure() is None
    assert file.suggested_filename.lower().endswith('.'+fmt.lower()),file.suggested_filename
    path=out/(tag+'.'+fmt.lower());file.save_as(path)
    expect(dialog.locator('[data-export-state="completed"]')).to_be_visible();return path

def check_csv(path,expected,datasheet=False):
    with path.open(encoding='utf-8-sig',newline='') as stream: rows=list(csv.DictReader(stream))
    assert all(None not in row for row in rows),rows
    tp=[row for row in rows if row['type']=='test_point'];ds=[row for row in rows if row['type']=='datasheet']
    actual={(row['test'],float(row['point_id'])):row for row in tp}
    assert len(actual)==len(tp)==len(expected) and set(actual)==set(expected),(actual,expected)
    for key in expected:
        row=actual[key];point=next(p for p in POINTS[key[0]] if p['id']==key[1])
        assert row['name']==point['name'] and row['label']==point['label'],row
        assert row['x_variable']==X and row['y_variable']==Y
        for axis,mean in zip(('x','y'),VALUES[key]):
            assert float(row[axis])==mean,(key,row)
            assert float(row[axis+'_min'])==mean-2.25
            assert float(row[axis+'_max'])==mean+3.5
    assert len(rows)==len(tp)+len(ds) and len(ds)==(2 if datasheet else 0),rows
    if datasheet:
        assert all(row['test']==DS for row in ds)
        assert [float(row['point_id']) for row in ds]==[100,102]
        assert [float(row['x']) for row in ds]==[5.123456789012345,95.98765432109876]
        assert [float(row['y']) for row in ds]==[6.123456789012345,95.98765432109876]
    return {'csv':path.name,'rows':len(rows)}

def png(page,dialog,out,tag,ranges=True,datasheet=False):
    svg=page.locator('.analyze-scatter-pane svg.recharts-surface')
    size=svg.evaluate("el=>[Number(el.getAttribute('width'))*2,Number(el.getAttribute('height'))*2]")
    page.evaluate('window.__scatterSvgs=[]');path=download(page,dialog,'PNG',out,tag)
    page.wait_for_function('window.__scatterSvgs.length===1');text=page.evaluate('window.__scatterSvgs[0]')
    (out/(tag+'.svg')).write_text(text,encoding='utf-8')
    assert X in text and Y in text and 'recharts-tooltip-cursor' not in text and 'foreignObject' not in text
    if ranges: assert 'data-range-axis="x"' in text and 'data-range-axis="y"' in text
    if datasheet: assert 'datasheet-scatter-series' in text
    result=page.evaluate('''async encoded=>{const image=new Image();image.src='data:image/png;base64,'+encoded;await image.decode();const c=document.createElement('canvas');c.width=image.naturalWidth;c.height=image.naturalHeight;const ctx=c.getContext('2d');ctx.drawImage(image,0,0);const {data}=ctx.getImageData(0,0,c.width,c.height);let opaque=0,ink=0,orange=0;for(let i=0;i<data.length;i+=4){if(data[i+3]===255)opaque++;if(Math.min(data[i],data[i+1],data[i+2])<245)ink++;if(Math.abs(data[i]-249)<8&&Math.abs(data[i+1]-115)<8&&Math.abs(data[i+2]-22)<8)orange++;}return {width:c.width,height:c.height,opaque,ink,orange,corner:[...data.slice(0,4)]};}''',base64.b64encode(path.read_bytes()).decode())
    assert [result['width'],result['height']]==[round(v) for v in size],(size,result)
    assert result['opaque']==result['width']*result['height'] and result['ink']>300 and result['corner']==[255,255,255,255],result
    return {'png':path.name,**result}

def toggle(page,name,value):
    page.get_by_role('button',name='More',exact=True).click();switch=page.get_by_role('switch',name=name,exact=True)
    if switch.get_attribute('aria-checked')!=str(value).lower(): switch.click()
    page.keyboard.press('Escape');settled(page)

def check_browser_zoom(url):
    from verify_browser_zoom import set_browser_zoom, wait_for_chart_layout
    out=Path(os.environ.get('TEMP',tempfile.gettempdir()))/'ptt-scatter-exports'
    out.mkdir(parents=True,exist_ok=True)
    results=[]
    with tempfile.TemporaryDirectory(prefix='scatter-export-zoom-') as temporary, sync_playwright() as pw:
        directory=Path(temporary);extension=directory/'extension';extension.mkdir()
        (extension/'manifest.json').write_text(json.dumps({'manifest_version':3,'name':'Scatter export zoom check','version':'1.0','permissions':['tabs'],'background':{'service_worker':'background.js'}}))
        (extension/'background.js').write_text('chrome.runtime.onInstalled.addListener(()=>{});')
        context=pw.chromium.launch_persistent_context(str(directory/'profile'),channel='chromium',headless=True,no_viewport=True,args=[f'--disable-extensions-except={extension}',f'--load-extension={extension}','--window-size=1440,1000'])
        try:
            worker=context.service_workers[0] if context.service_workers else context.wait_for_event('serviceworker')
            page=context.pages[0];fixture=Fixture();fixture.configure(page);errors=[]
            page.on('pageerror',lambda error:errors.append(str(error)))
            page.on('console',lambda msg:errors.append(msg.text) if msg.type=='error' else None)
            page.goto(url);settled(page);initial=page.evaluate('devicePixelRatio')
            for factor in [1.25,1.5]:
                set_browser_zoom(worker,page,factor)
                page.wait_for_function('dpr=>Math.abs(devicePixelRatio-dpr)<.001',arg=initial*factor)
                wait_for_chart_layout(page);dialog=open_dialog(page)
                assert dialog.evaluate('el=>{const r=el.getBoundingClientRect();return r.x>=0&&r.y>=0&&r.right<=innerWidth&&r.bottom<=innerHeight&&el.scrollWidth<=el.clientWidth+1;}')
                tag='browser-zoom-'+str(round(factor*100))
                results.append(check_csv(download(page,dialog,'CSV',out,tag),VALID))
                results.append(png(page,dialog,out,tag));page.screenshot(path=str(out/(tag+'-dialog.png')))
                close(page,dialog);expect(page.get_by_role('button',name='More',exact=True)).to_be_focused()
            assert not fixture.blocked and not fixture.errors and not errors,(fixture.blocked,fixture.errors,errors)
        finally: context.close()
    (out/'browser-zoom-results.json').write_text(json.dumps(results,indent=2),encoding='utf-8')
    print('PASS actual 125% and 150% browser zoom: CSV/PNG downloads, dialog fit and keyboard focus.',flush=True)

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--url',default=os.environ.get('PTT_PREVIEW_URL','http://127.0.0.1:8087/ptt/'));parser.add_argument('--zoom-only',action='store_true');args=parser.parse_args()
    if args.zoom_only:
        check_browser_zoom(args.url);return
    out=Path(os.environ.get('TEMP',tempfile.gettempdir()))/'ptt-scatter-exports';out.mkdir(parents=True,exist_ok=True)
    report={'checks':[],'errors':[],'console_errors':[]}
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True);context=browser.new_context(viewport={'width':1440,'height':1000},accept_downloads=True);page=context.new_page();page.set_default_timeout(15000)
        fixture=Fixture();fixture.configure(page);page.on('pageerror',lambda error:report['errors'].append(str(error)));page.on('console',lambda msg:report['console_errors'].append(msg.text) if msg.type=='error' else None)
        try:
            page.goto(args.url);settled(page);expect(page.locator('.recharts-scatter-symbol')).to_have_count(3);expect(page.locator('[data-range-point-id]')).to_have_count(4)
            dialog=open_dialog(page);baseline=download(page,dialog,'CSV',out,'all-points');report['checks'].append(check_csv(baseline,VALID));report['checks'].append(png(page,dialog,out,'clustered-selected-ranges'));assert report['checks'][-1]['orange']>20;close(page,dialog)
            canvas=page.get_by_label('Plot canvas for test-point overview',exact=True);canvas.focus();page.keyboard.press('Shift+F10');action=page.get_by_role('menuitem',name='Export CSV / PNG…',exact=True);expect(action).to_be_focused();page.keyboard.press('Enter');dialog=page.get_by_role('dialog',name='Export scatter plot',exact=True);expect(dialog).to_be_visible();close(page,dialog)
            dialog=open_dialog(page)
            # Native dialog may hand focus to browser chrome at the end of a Tab cycle.
            for _ in range(6): page.keyboard.press('Tab');assert dialog.evaluate('el=>el.contains(document.activeElement)||document.activeElement===document.body')
            close(page,dialog);expect(page.get_by_role('button',name='More',exact=True)).to_be_focused()
            bounds=canvas.bounding_box();px,py=bounds['x']+bounds['width']*.55,bounds['y']+bounds['height']*.42;before=page.locator('.recharts-xAxis').text_content();page.mouse.move(px,py);page.mouse.wheel(0,-500);page.wait_for_function("before=>document.querySelector('.recharts-xAxis').textContent!==before",arg=before)
            page.mouse.move(px,py);page.mouse.down();page.mouse.move(px+35,py+20,steps=8);page.mouse.up();settled(page);dialog=open_dialog(page);zoomed=download(page,dialog,'CSV',out,'zoomed-panned');assert baseline.read_bytes()==zoomed.read_bytes();report['checks'].append(png(page,dialog,out,'zoomed-panned'));close(page,dialog)
            page.get_by_role('group',name='Scatter navigation',exact=True).get_by_role('button',name='Reset zoom',exact=True).click();toggle(page,'Cluster overlapping scatter points',False);expect(page.locator('.recharts-scatter-symbol')).to_have_count(4);dialog=open_dialog(page);assert baseline.read_bytes()==download(page,dialog,'CSV',out,'unclustered').read_bytes();close(page,dialog)
            toggle(page,'Show datasheet line from '+DS,True);expect(page.locator('.datasheet-scatter-series .recharts-scatter-symbol')).to_have_count(2);dialog=open_dialog(page);report['checks'].append(check_csv(download(page,dialog,'CSV',out,'with-datasheet'),VALID,True));report['checks'].append(png(page,dialog,out,'with-datasheet',datasheet=True));close(page,dialog)
            page.get_by_role('button',name=re.compile('^Filters')).click();region=page.get_by_role('region',name='Scatter filters');region.get_by_role('button',name='Labels',exact=True).click();region.get_by_role('checkbox',name='other',exact=True).check();region.get_by_role('button',name='Close filters',exact=True).click();settled(page);dialog=open_dialog(page);report['checks'].append(check_csv(download(page,dialog,'CSV',out,'label-filter'),[(A,9.125)],True));close(page,dialog)
            page.get_by_role('button',name=re.compile('^Filters')).click();region.get_by_role('checkbox',name=B+' (1)',exact=True).check();region.get_by_role('button',name='Close filters',exact=True).click();settled(page);dialog=open_dialog(page);report['checks'].append(check_csv(download(page,dialog,'CSV',out,'datasheet-only'),[],True));report['checks'].append(png(page,dialog,out,'datasheet-only',False,True));close(page,dialog)
            toggle(page,'Show datasheet line from '+DS,False);dialog=open_dialog(page);expect(dialog.get_by_role('button',name='Download CSV',exact=True)).to_be_disabled();expect(dialog.get_by_role('button',name='Download PNG',exact=True)).to_be_disabled();close(page,dialog);page.get_by_role('button',name='Clear all scatter filters',exact=True).click();settled(page)
            for width,height in [(1100,720),(960,500)]:
                page.set_viewport_size({'width':width,'height':height});settled(page);dialog=open_dialog(page);assert dialog.evaluate('el=>{const r=el.getBoundingClientRect();return r.x>=0&&r.y>=0&&r.right<=innerWidth&&r.bottom<=innerHeight&&el.scrollWidth<=el.clientWidth+1;}');report['checks'].append(png(page,dialog,out,'resize-'+str(width)));page.screenshot(path=str(out/('dialog-'+str(width)+'.png')));close(page,dialog)
            page.set_viewport_size({'width':1440,'height':1000});settled(page);dialog=open_dialog(page);page.evaluate("window.__scatterToBlobMode='fail'");dialog.get_by_role('button',name='Download PNG',exact=True).click();expect(dialog.get_by_role('alert')).to_contain_text('Export failed.');report['checks'].append(png(page,dialog,out,'encoding-retry'))
            downloads=[];page.on('download',lambda file:downloads.append(file.suggested_filename));page.evaluate("window.__scatterToBlobMode='hold'");dialog.get_by_role('button',name='Download PNG',exact=True).click();page.wait_for_function("typeof window.__scatterHeld==='function'");expect(dialog.get_by_role('button',name='Download CSV',exact=True)).to_be_disabled();dialog.get_by_role('button',name='Cancel export',exact=True).click();page.evaluate('window.__scatterHeld()');expect(dialog.locator('[data-export-state="canceled"]')).to_be_visible();assert not downloads,downloads;close(page,dialog)
            assert not fixture.blocked and not fixture.errors,(fixture.blocked,fixture.errors);assert not report['errors'] and not report['console_errors'],report;page.screenshot(path=str(out/'final-scatter.png'))
        except Exception:
            page.screenshot(path=str(out/'failure.png'));print(page.locator('body').aria_snapshot()[-14000:],flush=True);print(json.dumps(report,indent=2),flush=True);raise
        finally:
            report['requests']=fixture.requests;report['blocked_writes']=fixture.blocked;(out/'results.json').write_text(json.dumps(report,indent=2),encoding='utf-8');context.close();browser.close()
    print('PASS scatter CSV/PNG browser checks; no API writes or browser errors. Evidence: '+str(out),flush=True)

if __name__=='__main__': main()
