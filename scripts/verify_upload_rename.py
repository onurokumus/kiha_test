"""Uploads inline rename: isolated fixtures, identity recovery, keyboard and real zoom.
Run: python -X utf8 scripts/verify_upload_rename.py
All API requests are intercepted; mutations affect only this in-memory fixture.
"""
import argparse
import copy
import json
from pathlib import Path
import re
import tempfile
from urllib.parse import parse_qs, unquote, urlparse
from playwright.sync_api import expect, sync_playwright
from verify_browser_zoom import capture_browser_view, set_browser_zoom

KEY = 'ptt.analysis-session.v1'
A, B, BUSY = 'Rename_alpha', 'Rename_beta', 'Rename_processing'
X, Y = 'thrust_n', 'torque_nm'
POINTS = [dict(id=i, name=f'Point {i}', label='steady', start_s=(i-1)*2, end_s=i*2) for i in (1,2)]
PROJECT = """() => {
 const s=JSON.parse(localStorage.getItem('ptt.analysis-session.v1'));
 return {currentTest:s.currentTest,xAxis:s.xAxis,yAxis:s.yAxis,
 selections:[...s.selections].sort((a,b)=>(a.test+':'+a.tpId).localeCompare(b.test+':'+b.tpId)),
 filterState:{...s.filterState,tpKeys:[...s.filterState.tpKeys].sort()},
 mainZoom:s.mainZoom,plotConfigs:s.plotConfigs};
}"""

class Fixture:
    def __init__(self):
        self.tests={A:dict(id='alpha-id',status='ready',source='alpha_source.csv'),
                    B:dict(id='beta-id',status='ready',source='beta_source.csv'),
                    BUSY:dict(id='processing-id',status='ingesting',source='processing.csv')}
        self.posts, self.errors, self.held = [], [], []
        self.fail_next = self.hold_next = self.hold_list = self.fail_catalog = False
        self.held_lists = []
    def info(self,name):
        item=self.tests[name]
        return dict(name=name,status=item['status'],n_rows=400,fs_hz=100,duration_s=4,
                    n_columns=3,size_bytes=12345,source_file=item['source'],
                    description='Preserved description',created_at='2026-09-28T08:00:00Z')
    def sources(self):
        return [dict(name=n,id=t['id'],revision='fixture-v1',status=t['status'],columns=[X,Y],
                     test_points=[dict(id=p['id'],revision=f"point-{p['id']}") for p in POINTS]
                     if t['status']=='ready' else []) for n,t in self.tests.items()]
    def finish(self,route,old,new):
        self.tests[new]=self.tests.pop(old)
        route.fulfill(json={'ok':True,'name':new})
    def release(self):
        pending,self.held=self.held,[]
        for route,old,new in pending: self.finish(route,old,new)
    def route(self,route):
        req=route.request; url=urlparse(req.url)
        path=unquote(url.path).split('/api',1)[1]; query=parse_qs(url.query)
        if req.method=='POST' and path.endswith('/rename'):
            old,new=path.split('/')[2],query['new_name'][0]
            self.posts.append(dict(old=old,new=new))
            assert old in self.tests and new not in self.tests
            assert re.fullmatch(r'[A-Za-z0-9._-]+',new) and re.search(r'[A-Za-z0-9]',new)
            if self.fail_next:
                self.fail_next=False
                route.fulfill(status=409,json={'detail':'Fixture rename refused'})
            elif self.hold_next:
                self.hold_next=False; self.held.append((route,old,new))
            else: self.finish(route,old,new)
            return
        assert req.method=='GET', f'Unexpected write: {req.method} {path}'
        if path=='/settings/defaults': body={'settings':None}
        elif path=='/tests':
            body=[self.info(n) for n in self.tests]
            if self.hold_list:
                self.hold_list=False; self.held_lists.append((route,body)); return
        elif path=='/analysis-sources':
            if self.fail_catalog:
                self.fail_catalog=False
                route.fulfill(status=503,json={'detail':'Fixture identity outage'}); return
            body={'version':1,'sources':self.sources()}
        elif path=='/components': body={'version':1,'components':[]}
        elif path=='/trash': body={'entries':[],'retention_seconds':3600}
        elif path.startswith('/tests/'):
            parts=path.split('/'); name=parts[2]
            if name not in self.tests:
                self.errors.append(path); route.fulfill(status=404,json={'detail':'Vanished test'}); return
            if len(parts)==3:
                body={**self.info(name),'columns':['time_s',X,Y],'time_column':'time_s','t_start':0}
            elif path.endswith('/testpoints'): body={'version':1,'test':name,'test_points':POINTS}
            elif path.endswith('/tp_stats'):
                body=[{**p,'mean':p['id']*(10 if query['col'][0]==X else 3),
                       'min':0,'max':50,'n':200,'n_valid':200} for p in POINTS]
            elif '/testpoints/' in path and path.endswith('/data'):
                point=POINTS[int(parts[-2])-1]; times=[i/100 for i in range(200)]
                body={'test':name,'test_point':point,'mode':'raw','level':1,'n_raw':200,
                      'i0':0,'i1':200,'point_budget':1500,'time_origin_s':point['start_s'],'duration_s':2,
                      'series':{col:{'t':times,'y':[10+t for t in times]} for col in query['cols'][0].split(',')}}
            else: raise AssertionError(path)
        else: raise AssertionError(path)
        route.fulfill(json=body)
    def configure(self,page):
        settings=dict(scatterX=X,scatterY=Y,clustering=False,gridColumns=[Y],defaultViewMode='tp')
        session=dict(version=1,sources=self.sources(),currentTest=A,xAxis=X,yAxis=Y,axesUserSet=True,
                     clusteringEnabled=False,selections=[dict(test=A,tpId=1,hidden=False,color='#f97316'),
                                                         dict(test=B,tpId=2,hidden=True,color='#7c3aed')],
                     filterState=dict(tpKeys=[A+':1',B+':2'],labels=['steady'],
                                      parameterFilters=[dict(id='bound',column=X,mode='mean',min=0,max=100)]),
                     mainZoom=[0,80,0,80],plotConfigs=[Y],plotsUserEdited=True,plotDensity='single')
        page.add_init_script(f"""if(!sessionStorage.getItem('rename-seeded')) {{
            sessionStorage.setItem('rename-seeded','1'); localStorage.clear();
            localStorage.setItem('ptt.settings.v1',{json.dumps(json.dumps(settings))});
            localStorage.setItem({json.dumps(KEY)},{json.dumps(json.dumps(session))});
        }}""")
        page.route('**/api/**',self.route)

def remap(value,old,new):
    if isinstance(value,dict): return {k:remap(v,old,new) for k,v in value.items()}
    if isinstance(value,list): return [remap(v,old,new) for v in value]
    if isinstance(value,str):
        return new if value==old else new+value[len(old):] if value.startswith(old+':') else value
    return value

def check_workspace(page,expected):
    expected=copy.deepcopy(expected)
    expected['selections'].sort(key=lambda s:s['test']+':'+str(s['tpId']))
    expected['filterState']['tpKeys'].sort()
    page.wait_for_function('expected=>JSON.stringify(('+PROJECT+')())===JSON.stringify(expected)',arg=expected)
    assert page.evaluate(PROJECT)==expected

def icon(page,name): return page.get_by_role('button',name='Rename test '+name,exact=True)
def editor(page,name):
    icon(page,name).focus(); page.keyboard.press('Enter')
    form=page.get_by_role('form',name='Rename test '+name,exact=True)
    field=form.get_by_role('textbox',name='New name for '+name,exact=True)
    expect(field).to_be_focused(); expect(field).to_have_value(name)
    assert field.evaluate('e=>e.selectionStart===0&&e.selectionEnd===e.value.length')
    return form,field
def check_row(page,fixture,name):
    button=page.get_by_role('button',name='Analyze '+name,exact=True); expect(button).to_be_visible()
    row=page.get_by_role('row').filter(has=button)
    expect(row.get_by_text(fixture.tests[name]['source'],exact=True)).to_be_visible()
    expect(row.get_by_role('button',name='Edit notes for '+name,exact=True)).to_have_text('Preserved description')
    assert row.get_by_role('link',name='Download original CSV for '+name).get_attribute('href').endswith('/tests/'+name+'/raw')
    expect(icon(page,name)).to_be_focused()

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url',default='http://127.0.0.1:8087/ptt/'); args=parser.parse_args()
    output=Path(tempfile.gettempdir())/'ptt-upload-rename'; output.mkdir(exist_ok=True)
    report={'checks':[],'page_errors':[],'console_errors':[]}
    with tempfile.TemporaryDirectory(prefix='ptt-upload-rename-') as temp,sync_playwright() as p:
        temp=Path(temp); extension=temp/'extension'; extension.mkdir()
        (extension/'manifest.json').write_text(json.dumps({'manifest_version':3,'name':'Rename zoom verification',
            'version':'1.0','permissions':['tabs'],'background':{'service_worker':'background.js'}}))
        (extension/'background.js').write_text('chrome.runtime.onInstalled.addListener(()=>{});')
        context=p.chromium.launch_persistent_context(str(temp/'profile'),channel='chromium',headless=True,no_viewport=True,
            args=[f'--disable-extensions-except={extension}',f'--load-extension={extension}','--window-size=1440,1000'])
        page=context.pages[0]; page.set_default_timeout(10000)
        fixture=Fixture(); fixture.configure(page)
        page.on('pageerror',lambda e:report['page_errors'].append(str(e)))
        page.on('console',lambda m:report['console_errors'].append(m.text) if m.type=='error' else None)
        worker=context.service_workers[0] if context.service_workers else context.wait_for_event('serviceworker')
        cdp=context.new_cdp_session(page); window=cdp.send('Browser.getWindowForTarget')['windowId']
        try:
            page.goto(args.url); page.wait_for_load_state('networkidle')
            expected=page.evaluate(PROJECT); assert len(expected['selections'])==2
            assert expected['mainZoom']==[0,80,0,80]
            page.get_by_role('navigation',name='Main navigation').get_by_role('button',name='Uploads',exact=True).click()
            expect(icon(page,BUSY)).to_be_disabled()
            form,field=editor(page,B); save=form.get_by_role('button',name='Save name',exact=True)
            expect(save).to_be_disabled()
            for invalid in ['', '   ', '../bad', 'bad/name', 'bad name', '...', A]:
                field.fill(invalid); expect(save).to_be_disabled(); page.keyboard.press('Enter')
            assert not fixture.posts
            field.fill('Draft_not_saved'); page.keyboard.press('Tab'); assert not fixture.posts
            page.keyboard.press('Escape'); expect(form).not_to_be_visible(); expect(icon(page,B)).to_be_focused()
            form,field=editor(page,B); field.fill('Another_draft')
            form.get_by_role('button',name='Cancel rename',exact=True).click()
            expect(icon(page,B)).to_be_focused(); assert not fixture.posts
            report['checks'].append('validation, unchanged, blur, Escape and Cancel')

            fixture.hold_list=True
            for _ in range(220):
                if fixture.held_lists: break
                page.wait_for_timeout(20)
            assert fixture.held_lists, 'Expected an old-name Uploads poll'
            b1=B+'_inactive'; form,field=editor(page,B); field.fill('  '+b1+'  '); page.keyboard.press('Enter')
            expect(form).not_to_be_visible(); expected=remap(expected,B,b1); check_workspace(page,expected)
            assert expected['currentTest']==A; check_row(page,fixture,b1)
            assert fixture.posts==[dict(old=B,new=b1)]
            for held_route,body in fixture.held_lists: held_route.fulfill(json=body)
            fixture.held_lists.clear()
            page.wait_for_timeout(150)
            check_workspace(page,expected); expect(icon(page,B)).to_have_count(0)
            assert not fixture.errors,fixture.errors
            report['checks'].append('inactive rename preserves current test, selected/hidden TPs, colors, filters, axes, zoom')

            a1=A+'_active'; form,field=editor(page,A); field.fill(a1)
            fixture.hold_next=True; page.keyboard.press('Enter'); page.keyboard.press('Enter')
            for _ in range(200):
                if fixture.held: break
                page.wait_for_timeout(20)
            assert len(fixture.held)==1
            expect(field).to_be_disabled()
            expect(form.get_by_role('button',name='Saving…',exact=True)).to_be_disabled()
            expect(page.get_by_role('button',name='Analyze '+A,exact=True)).to_be_disabled()
            expect(page.get_by_role('button',name='Edit notes for '+A,exact=True)).to_be_disabled()
            page.keyboard.press('Enter'); page.wait_for_timeout(100); assert len(fixture.posts)==2
            fixture.release(); expect(form).not_to_be_visible()
            expected=remap(expected,A,a1); check_workspace(page,expected); check_row(page,fixture,a1)
            report['checks'].append('active rename, busy navigation and delayed double-submit guard')

            b2=B+'_retried'; form,field=editor(page,b1); field.fill(b2); fixture.fail_next=True
            form.get_by_role('button',name='Save name',exact=True).click()
            expect(page.get_by_text('Fixture rename refused',exact=False)).to_be_visible()
            expect(field).to_have_value(b2); expect(field).to_be_focused()
            expect(form.get_by_role('button',name='Save name',exact=True)).to_be_enabled()
            check_workspace(page,expected)
            form.get_by_role('button',name='Save name',exact=True).click(); expect(form).not_to_be_visible()
            expected=remap(expected,b1,b2); check_workspace(page,expected); check_row(page,fixture,b2)
            assert len(fixture.posts)==4; report['checks'].append('server failure retains draft, focus and retry')

            search=page.get_by_role('searchbox',name='Search upload history',exact=True); search.fill(a1)
            a2='Different_after_search'; form,field=editor(page,a1); field.fill(a2)
            form.get_by_role('button',name='Save name',exact=True).click(); expect(form).not_to_be_visible()
            expected=remap(expected,a1,a2); check_workspace(page,expected)
            expect(icon(page,a2)).to_have_count(0); expect(search).to_be_focused()
            page.get_by_role('button',name='Clear upload history filters',exact=True).click()
            expect(icon(page,a2)).to_be_visible()
            report['checks'].append('filtered rename preserves search and returns focus')

            baseline=page.evaluate('devicePixelRatio')
            for width,height,zoom in [(1100,800,1),(1440,1000,1.25),(1440,1000,1.5)]:
                cdp.send('Browser.setWindowBounds',{'windowId':window,'bounds':{'width':width,'height':height}})
                set_browser_zoom(worker,page,zoom)
                page.wait_for_function('dpr=>Math.abs(devicePixelRatio-dpr)<.01',arg=baseline*zoom)
                form,field=editor(page,b2); field.fill('Long_draft_'+'readable_name_'*8)
                field.scroll_into_view_if_needed()
                assert form.evaluate("""form=>{
                    const r=form.getBoundingClientRect();
                    return r.x>=0&&r.right<=innerWidth+1&&form.scrollWidth<=form.clientWidth+1&&
                    [...form.querySelectorAll('input,button')].every(el=>{
                        const b=el.getBoundingClientRect();return b.width>0&&b.x>=r.x-1&&b.right<=r.right+1;});
                }""")
                capture_browser_view(cdp,output/f'rename-{width}-{zoom}.png')
                page.keyboard.press('Escape'); expect(icon(page,b2)).to_be_focused()
            report['checks'].append('1100px and actual 125/150% browser zoom')

            page.reload(); page.wait_for_load_state('networkidle'); check_workspace(page,expected)
            report['checks'].append('renamed workspace survives page reload')
            page.get_by_role('button',name='Uploads',exact=True).click()
            a3=a2+'_verified'
            form,field=editor(page,a2); field.fill(a3); fixture.fail_catalog=True
            form.get_by_role('button',name='Save name',exact=True).click()
            expect(form).not_to_be_visible()
            expect(page.get_by_role('button',name='Retry source check',exact=True)).to_be_visible()
            expect(icon(page,a3)).to_be_disabled()
            expect(page.get_by_role('searchbox',name='Search upload history',exact=True)).to_be_focused()
            page.get_by_role('button',name='Retry source check',exact=True).click()
            expected=remap(expected,a2,a3); check_workspace(page,expected)
            expect(icon(page,a3)).to_be_enabled()
            assert len(fixture.posts)==6
            report['checks'].append('successful rename with failed recovery retries without another POST')
            page.evaluate('''name=>{
                const settings=JSON.parse(localStorage.getItem('ptt.settings.v1'));
                settings.datasheetZone=name;settings.datasheetVisible=false;
                localStorage.setItem('ptt.settings.v1',JSON.stringify(settings));
                const s=JSON.parse(localStorage.getItem('ptt.analysis-session.v1'));s.datasheetVisible=false;
                localStorage.setItem('ptt.analysis-session.v1',JSON.stringify(s));
            }''',b2)
            page.reload(); page.wait_for_load_state('networkidle')
            expected=page.evaluate(PROJECT)
            page.get_by_role('button',name='Uploads',exact=True).click()
            b3=b2+'_reference'; form,field=editor(page,b2); field.fill(b3)
            form.get_by_role('button',name='Save name',exact=True).click()
            expect(form).not_to_be_visible()
            expected=remap(expected,b2,b3); check_workspace(page,expected)
            assert page.evaluate("JSON.parse(localStorage.getItem('ptt.settings.v1')).datasheetZone")==b3
            report['checks'].append('configured datasheet name follows renamed source')
            assert len(fixture.posts)==7 and not fixture.errors, (fixture.posts,fixture.errors)
            assert not report['page_errors'],report
            assert len(report['console_errors'])==2 and all(any(code in e for code in ('409','503')) for e in report['console_errors']),report
            report['simulated_posts']=fixture.posts; report['workspace']=page.evaluate(PROJECT)
        except Exception:
            capture_browser_view(cdp,output/'failure.png')
            print(page.locator('body').aria_snapshot()[-13000:],flush=True); raise
        finally:
            (output/'results.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
            context.close()
    print(f"PASS {len(report['checks'])} groups; all API writes simulated. Evidence: {output}",flush=True)

if __name__=='__main__': main()
