"""Focused Phase 6b modal/legend export checks after full download verification.

Uses temporary data and owned ports 3140/8140; backend/native work stays in
Python 3.13. Inspect text passed to the actual PNG canvas as well as real files.
"""
from pathlib import Path
import tempfile

from playwright.sync_api import sync_playwright

from verify_data_quality import ROOT, servers
from verify_plot_exports import (fixtures, seed, wait_plots, open_export,
                                 close_export, plot, download, png_inspect)
from verify_time_y_zoom import instrument


def main():
    output = ROOT / 'data/verification/plot-exports'
    logs = output / 'final-ui-logs'
    logs.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='kiha-export-final-ui-') as temporary:
        with servers(Path(temporary), logs, 3140, 8140) as (web, api, _dataset):
            with sync_playwright() as playwright:
                request = playwright.request.new_context(base_url=api + '/')
                fixtures(request)
                browser = playwright.chromium.launch(headless=True)
                try:
                    page = browser.new_page(viewport={'width': 1440, 'height': 1000},
                                            accept_downloads=True)
                    page.route('**/src/utils/uplotSync.ts*', instrument)
                    seed(page)
                    page.goto(web)
                    page.wait_for_load_state('networkidle')
                    wait_plots(page)
                    panel = open_export(page)
                    rect = panel.bounding_box()
                    assert abs(rect['x'] + rect['width'] / 2 - 720) < 2, rect
                    assert abs(rect['y'] + rect['height'] / 2 - 500) < 2, rect
                    page.screenshot(path=str(output / 'centered-dialog.png'))
                    close_export(page, panel)
                    plot(page).locator('.uplot').evaluate('''el => {
                        const u = el.__verificationPlot;
                        u.series.forEach((s,i) => {
                            if (i && String(s.label).includes('plot_export_beta'))
                                u.setSeries(i,{show:false});
                        });
                    }''')
                    page.evaluate('''() => {
                        window.__pngTexts = [];
                        const base = CanvasRenderingContext2D.prototype.fillText;
                        CanvasRenderingContext2D.prototype.fillText = function(text,...rest) {
                            window.__pngTexts.push(String(text));
                            return base.call(this,text,...rest);
                        };
                    }''')
                    panel = open_export(page)
                    path, name = download(page, panel.get_by_role('button', name='Download PNG', exact=True),
                                          output, 'legend-hidden', 'png')
                    texts = page.evaluate('window.__pngTexts')
                    assert not any('plot_export_beta' in text for text in texts), texts
                    assert any('2 events; 2 samples repaired' in text for text in texts), texts
                    assert name.endswith('_both.png'), name
                    png_inspect(page, path, [])
                    close_export(page, panel)
                    plot(page).locator('.uplot').evaluate('''el => {
                        const u = el.__verificationPlot;
                        u.series.forEach((s,i) => {
                            if (i && String(s.label).includes('filtered')) u.setSeries(i,{show:false});
                        });
                    }''')
                    page.evaluate('window.__pngTexts = []')
                    panel = open_export(page)
                    path, name = download(page, panel.get_by_role('button', name='Download PNG', exact=True),
                                          output, 'legend-original-only', 'png')
                    texts = page.evaluate('window.__pngTexts')
                    assert not any('threshold multiplier' in text for text in texts), texts
                    assert name.endswith('_original.png'), name
                    png_inspect(page, path, [])
                    close_export(page, panel)
                finally:
                    browser.close()
                    request.dispose()
    print('PASS: centered dialog, Escape focus, hidden-TP PNG scope/counts, '
          'actual original-only filename/settings; owned servers stopped', flush=True)


if __name__ == '__main__':
    main()
