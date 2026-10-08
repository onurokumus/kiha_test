import type uPlot from 'uplot';
import { compactHoverLabels, type PlotHoverValues } from './plotHoverValues';
import { createPlotHoverGroup, type PlotHoverGroup, type PlotHoverMember } from './plotHoverGroup';
import './uplotHover.css';

/** One compact box per visible canvas, coordinated by its grid. Body-level
 * positioning avoids clipped cards without reserving plot height. */
export function plotHoverPlugin(read: (plot: uPlot) => PlotHoverValues,
  shared: PlotHoverGroup | null, xAxis: string, yAxis?: string): uPlot.Plugin {
  const group = shared ?? createPlotHoverGroup();
  let tooltip: HTMLDivElement | null = null;
  let member: PlotHoverMember | null = null;
  const cleanup: (() => void)[] = [];
  const hide = () => { tooltip?.remove(); tooltip = null; };
  const render = (plot: uPlot) => {
    const rect = plot.over.getBoundingClientRect();
    const slot = plot.root.closest<HTMLElement>('[data-plot-hover-slot]')?.getBoundingClientRect();
    if (!plot.root.isConnected || rect.width <= 0 || rect.height <= 0 ||
        rect.bottom <= Math.max(0, slot?.top ?? 0) || rect.top >= Math.min(window.innerHeight, slot?.bottom ?? window.innerHeight) ||
        rect.right <= Math.max(0, slot?.left ?? 0) || rect.left >= Math.min(window.innerWidth, slot?.right ?? window.innerWidth)) {
      hide(); return;
    }
    const values = read(plot);
    const rows = values.rows.length ? values.rows : [{ label: '', values: values.columns.map(() => '—') }];
    tooltip ??= document.createElement('div');
    tooltip.className = 'plot-hover-values';
    tooltip.dataset.plotHoverSlot = plot.root.closest<HTMLElement>('[data-plot-hover-slot]')?.dataset.plotHoverSlot ?? '';
    tooltip.setAttribute('role', 'tooltip');
    tooltip.setAttribute('aria-label', `Plot values for ${values.heading}`);
    const table = document.createElement('table');
    table.setAttribute('aria-label', values.columns.join(', '));
    const body = table.createTBody();
    // Tiny Waterfall canvases can also use their source-label area. Keep the
    // complete scientific readout within its facet rather than dropping rows.
    const area = plot.root.closest<HTMLElement>('[data-plot-hover-area]')?.getBoundingClientRect() ?? rect;
    const availableHeight = Math.min(window.innerHeight - 16,
      slot ? slot.bottom - area.top - 8 : area.height);
    const limit = Math.max(1, Math.floor((availableHeight - 10) / 20));
    const labels = compactHoverLabels(rows);
    for (const [index, row] of rows.slice(0, limit).entries()) {
      const tr = body.insertRow(), label = document.createElement('th'); label.scope = 'row';
      label.setAttribute('aria-label', row.label);
      const marker = document.createElement('i'); marker.style.backgroundColor = row.color ?? 'currentColor';
      marker.setAttribute('aria-hidden', 'true');
      const name = document.createElement('span'); name.append(marker, document.createTextNode(labels[index]));
      label.append(name); tr.append(label);
      for (const [column, value] of row.values.entries()) {
        const cell = tr.insertCell(), prefix = values.prefixes?.[column];
        if (prefix) { const tag = document.createElement('small'); tag.textContent = `${prefix} `; cell.append(tag); }
        const number = document.createElement('span'); number.dataset.hoverValue = ''; number.textContent = value;
        cell.append(number);
        const unit = values.units?.[column];
        if (unit) { const suffix = document.createElement('small'); suffix.textContent = ` ${unit}`; cell.append(suffix); }
      }
    }
    tooltip.replaceChildren(table);
    if (rows.length > limit) {
      const more = document.createElement('p'); more.textContent = `+${rows.length - limit} traces`; tooltip.append(more);
    }
    const width = Math.max(30, Math.min(360, rect.width - 10, window.innerWidth - 16));
    tooltip.style.maxWidth = `${width}px`;
    tooltip.style.setProperty('--plot-hover-label-width', `${Math.max(24, Math.min(145, width * .35))}px`);
    tooltip.style.maxHeight = `${Math.max(20, availableHeight)}px`;
    if (!tooltip.isConnected) document.body.append(tooltip);
    if (table.scrollWidth > width - 14) {
      tooltip.style.width = `${width}px`; tooltip.classList.add('plot-hover-values--narrow');
    } else { tooltip.style.width = ''; tooltip.classList.remove('plot-hover-values--narrow'); }
    const bounds = tooltip.getBoundingClientRect(), edge = 8;
    tooltip.style.left = `${Math.max(edge, Math.min(rect.right - bounds.width - 4, window.innerWidth - bounds.width - edge))}px`;
    tooltip.style.top = `${Math.max(edge, Math.min(rect.top + 4,
      slot ? slot.bottom - bounds.height - 4 : rect.top + 4, window.innerHeight - bounds.height - edge))}px`;
  };
  return { hooks: {
    ready: plot => {
      member = { plot, xAxis, yAxis, render: () => render(plot), hide };
      cleanup.push(group.register(member));
      const listen = (target: EventTarget, type: string, handler: EventListener, capture = false) => {
        target.addEventListener(type, handler, capture);
        cleanup.push(() => target.removeEventListener(type, handler, capture));
      };
      listen(plot.over, 'mousemove', event => {
        if ((event as MouseEvent).buttons) { group.clear(); return; }
        if (member) group.move(member);
      });
      listen(plot.over, 'mouseleave', group.clear);
      listen(document, 'pointerdown', group.clear, true);
      listen(document, 'keydown', group.clear, true);
      listen(window, 'blur', group.clear);
      listen(window, 'resize', group.clear);
      listen(window, 'scroll', group.clear, true);
      listen(document, 'visibilitychange', group.clear);
    },
    setData: group.clear,
    setScale: group.clear,
    setSize: group.clear,
    setSeries: group.clear,
    destroy: () => { hide(); cleanup.forEach(remove => remove()); },
  } };
}
