import React from 'react';
import { SelectedTestPoint, TestInfo } from '../../types';
import { buttonStyle, SelectStyle } from '../../constants/styles';

type PanelViewMode = 'tp' | 'full' | 'spectrum' | 'xy';
type PanelSource = 'tp' | 'full';

interface SelectedPointsPanelProps {
  selectedTPs: SelectedTestPoint[];
  hiddenTPs: Set<string>;
  onToggleVisibility: (id: string) => void;
  onRemoveTP: (id: string) => void;
  onClearAll: () => void;
  timeZoom: [number, number] | null;
  onResetTimeZoom: () => void;
  maxPoints?: number;
  loadingTestPointIds?: Set<string>;
  isEditMode?: boolean;
  onToggleEditMode?: () => void;
  viewMode: PanelViewMode;
  onViewModeChange: (mode: PanelViewMode) => void;
  specMode: 'fft' | 'welch';
  onSpecModeChange: (mode: 'fft' | 'welch') => void;
  specLogY: boolean;
  onSpecLogYChange: (logY: boolean) => void;
  /** Active test for full-test-sourced views (moved here from the header). */
  tests: TestInfo[];
  currentTest: string;
  onTestChange: (test: string) => void;
  specSource: PanelSource;
  onSpecSourceChange: (s: PanelSource) => void;
  xySource: PanelSource;
  onXYSourceChange: (s: PanelSource) => void;
}

export const SelectedPointsPanel: React.FC<SelectedPointsPanelProps> = ({
  selectedTPs,
  hiddenTPs,
  onToggleVisibility,
  onRemoveTP,
  onClearAll,
  timeZoom,
  onResetTimeZoom,
  maxPoints = 6,
  loadingTestPointIds = new Set(),
  isEditMode = false,
  onToggleEditMode,
  viewMode,
  onViewModeChange,
  specMode,
  onSpecModeChange,
  specLogY,
  onSpecLogYChange,
  tests,
  currentTest,
  onTestChange,
  specSource,
  onSpecSourceChange,
  xySource,
  onXYSourceChange,
}) => {
  const modeButton = (mode: PanelViewMode, label: string) => (
    <button
      onClick={() => onViewModeChange(mode)}
      style={{
        ...buttonStyle,
        background: viewMode === mode ? '#1e3a52' : buttonStyle.background,
        color: viewMode === mode ? '#569cd6' : buttonStyle.color,
        border: viewMode === mode ? '1px solid #569cd6' : buttonStyle.border,
      }}
    >
      {label}
    </button>
  );

  return (
    <div
      style={{
        background: '#252526',
        borderRadius: 4,
        border: '1px solid #3c3c3c',
        padding: 8,
        marginBottom: 8,
        display: 'flex',
        flexWrap: 'wrap',
        gap: 8,
        alignItems: 'flex-start',
        alignContent: 'flex-start',
        // auto height: wrapped rows (filter params, many chips) must stay
        // visible — a fixed 42px clipped them invisibly (bug 1.13)
        minHeight: 42,
      }}
    >
      {modeButton('tp', 'Test points')}
      {modeButton('full', 'Full test')}
      {modeButton('spectrum', 'Spectrum')}
      {modeButton('xy', 'XY')}
      {(viewMode === 'spectrum' || viewMode === 'xy') && (
        <>
          <span style={{ color: '#a0a0a0', fontSize: 11, lineHeight: '24px' }}>of:</span>
          {(['tp', 'full'] as PanelSource[]).map((s) => {
            const active = (viewMode === 'spectrum' ? specSource : xySource) === s;
            return (
              <button
                key={s}
                onClick={() =>
                  viewMode === 'spectrum' ? onSpecSourceChange(s) : onXYSourceChange(s)
                }
                style={{
                  ...buttonStyle,
                  background: active ? '#1e3a52' : buttonStyle.background,
                  color: active ? '#569cd6' : buttonStyle.color,
                  border: active ? '1px solid #569cd6' : buttonStyle.border,
                }}
                title={
                  s === 'tp'
                    ? 'compute over the selected test points'
                    : 'compute over the active test / its zoom range'
                }
              >
                {s === 'tp' ? 'TPs' : 'full'}
              </button>
            );
          })}
        </>
      )}
      {(viewMode === 'full' ||
        (viewMode === 'spectrum' && specSource === 'full') ||
        (viewMode === 'xy' && xySource === 'full')) && (
        <>
          <span style={{ color: '#a0a0a0', fontSize: 12, lineHeight: '24px' }}>test:</span>
          <select
            value={currentTest}
            onChange={(e) => onTestChange(e.target.value)}
            style={{ ...SelectStyle, maxWidth: 160 }}
          >
            {tests.map((t) => (
              <option key={t.name} value={t.name} disabled={t.status !== 'ready'}>
                {t.name}
                {t.status !== 'ready' ? ` (${t.status})` : ''}
              </option>
            ))}
          </select>
        </>
      )}
      {viewMode === 'spectrum' && (
        <>
          <select
            value={specMode}
            onChange={(e) => onSpecModeChange(e.target.value as 'fft' | 'welch')}
            style={{ ...SelectStyle, maxWidth: 140 }}
            title="spectrum estimator"
          >
            <option value="fft">FFT magnitude</option>
            <option value="welch">Welch PSD</option>
          </select>
          <button
            onClick={() => onSpecLogYChange(!specLogY)}
            style={{
              ...buttonStyle,
              background: specLogY ? '#1e3a52' : buttonStyle.background,
              color: specLogY ? '#569cd6' : buttonStyle.color,
              border: specLogY ? '1px solid #569cd6' : buttonStyle.border,
            }}
            title="log10 magnitude axis"
          >
            log
          </button>
        </>
      )}
      <span style={{ color: '#555' }}>|</span>
      <span style={{ color: '#a0a0a0', fontSize: 12, lineHeight: '24px' }}>
        Selected: ({selectedTPs.length}/{maxPoints})
      </span>
      {selectedTPs.length === 0 && (
        <span style={{ color: '#666', fontSize: 12, lineHeight: '24px' }}>
          None - click points on left plot
        </span>
      )}
      {selectedTPs.map((s) => (
        <div
          key={s.id}
          onClick={() => onToggleVisibility(s.id)}
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 4,
            padding: '3px 8px',
            background: hiddenTPs.has(s.id) ? '#2d2d2d' : '#3c3c3c',
            borderRadius: 3,
            cursor: 'pointer',
            opacity: hiddenTPs.has(s.id) ? 0.5 : 1,
            border: `1px solid ${s.color}`,
          }}
        >
          <div style={{ width: 10, height: 10, borderRadius: 2, background: s.color }} />
          <span
            style={{ fontSize: 12 }}
            title={`${s.test}${s.label ? ` — ${s.label}` : ''}`}
          >
            {s.name} <span style={{ color: '#909090', fontSize: 11 }}>· {s.test}</span>
          </span>
          {loadingTestPointIds.has(s.id) && (
            <span
              style={{
                marginLeft: 4,
                fontSize: 10,
                color: '#569cd6',
                animation: 'spin 1s linear infinite',
              }}
            >
              ⟳
            </span>
          )}
          <span
            onClick={(e) => {
              e.stopPropagation();
              onRemoveTP(s.id);
            }}
            style={{ marginLeft: 4, color: '#a0a0a0', cursor: 'pointer', fontSize: 11 }}
          >
            ✕
          </span>
        </div>
      ))}
      {selectedTPs.length > 0 && (
        <button onClick={onClearAll} style={buttonStyle}>
          Clear All
        </button>
      )}
      <div style={{ flex: 1 }} />
      {timeZoom && (
        <button onClick={onResetTimeZoom} style={buttonStyle}>
          Reset Time Zoom
        </button>
      )}
      {onToggleEditMode && (
        <button
          onClick={onToggleEditMode}
          style={{
            ...buttonStyle,
            background: isEditMode ? '#1e3a52' : buttonStyle.background,
            color: isEditMode ? '#569cd6' : buttonStyle.color,
            border: isEditMode ? '1px solid #569cd6' : buttonStyle.border,
          }}
        >
          {isEditMode ? '✓ Edit Mode' : 'Edit Plots'}
        </button>
      )}
    </div>
  );
};

// Add CSS animation for loading spinner
const style = document.createElement('style');
style.textContent = `
  @keyframes spin {
    from {
      transform: rotate(0deg);
    }
    to {
      transform: rotate(360deg);
    }
  }
`;
if (!document.head.querySelector('style[data-spinner]')) {
  style.setAttribute('data-spinner', 'true');
  document.head.appendChild(style);
}
