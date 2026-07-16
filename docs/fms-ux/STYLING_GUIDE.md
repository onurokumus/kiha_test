# Styling Guide

Design system reference for building consistent dark-themed data visualization applications.
Derived from the FMS (Flight Test Visualization) app.

---

## Theme Overview

VS Code-inspired dark theme with blue accent colors. Flat design with subtle depth
via borders and box shadows. Minimal use of gradients. Animations are smooth and
purposeful, using `cubic-bezier(0.4, 0, 0.2, 1)` easing.

---

## Color Palette

### Backgrounds (darkest to lightest)

| Token              | Hex       | Usage                                      |
|--------------------|-----------|---------------------------------------------|
| `bg-base`          | `#1e1e1e` | Page background, deep containers, scrollbar tracks |
| `bg-surface`       | `#252526` | Panels, cards, tooltips, filter sections    |
| `bg-elevated`      | `#2d2d2d` | Menus, dropdowns, disabled buttons          |
| `bg-control`       | `#3c3c3c` | Buttons, inputs, selects, badges            |

### Text

| Token              | Hex       | Usage                                      |
|--------------------|-----------|---------------------------------------------|
| `text-primary`     | `#e0e0e0` | Body text, labels, button text              |
| `text-secondary`   | `#a0a0a0` | Axis labels, hints, secondary info          |
| `text-tertiary`    | `#909090` | Placeholder text, inactive toggles, loading hints |
| `text-disabled`    | `#666`    | Disabled button text                        |

### Borders

| Token              | Hex       | Usage                                      |
|--------------------|-----------|---------------------------------------------|
| `border-default`   | `#3c3c3c` | Panel borders, grid lines, dividers         |
| `border-control`   | `#555`    | Input/select borders, inactive toggles      |
| `border-hover`     | `#4a4a4a` | Panel hover state                           |
| `border-accent`    | `#569cd6` | Active toggles, focused inputs, menus       |

### Accent

| Token              | Hex       | Usage                                      |
|--------------------|-----------|---------------------------------------------|
| `accent-primary`   | `#569cd6` | Active states, links, highlights, scrollbar hover |
| `accent-bg`        | `#1e3a52` | Active badge/toggle background              |
| `accent-muted`     | `#4a6b8a` | Scrollbar thumb                             |
| `accent-cluster`   | `#2e5c8a` | Cluster dot fill                            |
| `accent-cluster-hover` | `#3a6fa0` | Cluster dot hover fill                  |

### Semantic

| Token              | Hex       | Usage                                      |
|--------------------|-----------|---------------------------------------------|
| `success-bg`       | `#1e5a2e` | Reload/action button background             |
| `success-border`   | `#2d8a4a` | Reload/action button border                 |
| `error-text`       | `#f48771` | Error messages                              |
| `error-action`     | `#e06c75` | Delete/destructive actions                  |
| `error-bright`     | `#f44747` | Alerts, critical indicators                 |

### Data Series Colors

Assigned sequentially to selected data series. Designed for contrast against `#1e1e1e` backgrounds:

```
#ce9178  Tan/Brown
#6a9955  Green
#c586c0  Purple
#dcdcaa  Yellow
#9cdcfe  Light Blue
#f44747  Red
#d7ba7d  Gold
#b5cea8  Light Green
#4ec9b0  Teal
#569cd6  Blue
```

---

## Typography

### Font Stack

```css
font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
```

Monospace (code/data): `Consolas, monospace`

### Size Scale

| Size   | Usage                                           |
|--------|-------------------------------------------------|
| `16px` | Large headers (loading screens)                 |
| `14px` | Section titles, tooltip headers                 |
| `13px` | Default body text                               |
| `12px` | Labels, tooltip content, select/input text      |
| `11px` | Buttons, small controls, filter text            |
| `10px` | Badges, loading spinners, tiny hints            |
| `9px`  | Micro controls (expand/collapse icons)          |

### Weights

| Weight | Usage                                          |
|--------|-------------------------------------------------|
| `400`  | Regular body text (default)                     |
| `600`  | Headers, labels, emphasis, badge text           |

### Rendering

```css
-webkit-font-smoothing: antialiased;
-moz-osx-font-smoothing: grayscale;
```

---

## Spacing

### Padding Scale

| Value      | Usage                                        |
|------------|----------------------------------------------|
| `2px 6px`  | Micro controls (expand buttons)              |
| `3px 6px`  | Compact inputs                               |
| `3px 8px`  | Compact controls                             |
| `3px 10px` | Standard buttons                             |
| `4px 6px`  | Input fields                                 |
| `4px 8px`  | Select dropdowns, icon buttons               |
| `6px`      | Plot container internal padding              |
| `6px 8px`  | Menu items                                   |
| `8px`      | Standard panel padding                       |
| `8px 12px` | Menu headers, tooltips                       |
| `8px 16px` | Page header                                  |
| `12px`     | Main layout padding                          |

### Gap Scale (Flexbox)

| Value  | Usage                                         |
|--------|-----------------------------------------------|
| `2px`  | Minimal (tightly packed items)                |
| `4px`  | Checkbox + label pairs                        |
| `6px`  | Filter parameter groups                       |
| `8px`  | Standard flex layouts, badge grids            |
| `12px` | Panel internal content                        |
| `16px` | Header sections                               |

### Margin

| Value  | Usage                                         |
|--------|-----------------------------------------------|
| `4px`  | Small element spacing                         |
| `8px`  | Panel-to-panel vertical spacing               |
| `12px` | Section spacing                               |

---

## Borders

### Radius

| Value  | Usage                                         |
|--------|-----------------------------------------------|
| `2px`  | Micro controls                                |
| `3px`  | Buttons, inputs, selects, badges              |
| `4px`  | Panels, cards, tooltips, menus, scrollbars    |
| `50%`  | Circles (dots, indicators)                    |

### Styles

```
Standard:  1px solid #3c3c3c   (panels, dividers)
Control:   1px solid #555      (inputs, selects, inactive toggles)
Active:    1px solid #569cd6   (focused inputs, active toggles, menus)
Success:   1px solid #2d8a4a   (action buttons)
Selected:  2px solid {color}   (dot stroke, item highlight)
```

---

## Components

### Buttons

**Standard button:**
```css
background: #3c3c3c;
color: #e0e0e0;
border: 1px solid #555;
padding: 3px 10px;
border-radius: 3px;
cursor: pointer;
font-size: 11px;
```

**Action / success button:**
```css
background: #1e5a2e;
color: #e0e0e0;
border: 1px solid #2d8a4a;
border-radius: 3px;
padding: 4px 8px;
font-size: 14px;
cursor: pointer;
```

**Toggle button (active):**
```css
background: #1e3a52;
color: #569cd6;
border: 1px solid #569cd6;
border-radius: 3px;
padding: 3px 8px;
font-size: 11px;
cursor: pointer;
```

**Toggle button (inactive):**
```css
background: #3c3c3c;
color: #909090;
border: 1px solid #555;
```

**Disabled state:**
```css
background: #2d2d2d;
color: #666;
border: 1px solid #3c3c3c;
cursor: not-allowed;
```

### Inputs & Selects

```css
background: #3c3c3c;
color: #e0e0e0;
border: 1px solid #555;
border-radius: 3px;
padding: 4px 6px;       /* inputs */
padding: 4px 8px;       /* selects */
font-size: 12px;        /* selects */
font-size: 11px;        /* inputs */
outline: none;
```

Checkbox accent: `accent-color: #569cd6;`

### Panels / Cards

```css
background: #252526;
border: 1px solid #3c3c3c;
border-radius: 4px;
padding: 8px;
```

**Hover (optional):**
```css
border-color: #4a4a4a;
box-shadow: 0 2px 12px rgba(0, 0, 0, 0.2);
```

### Tooltips

```css
background: #252526;
border: 1px solid #3c3c3c;
border-radius: 4px;
padding: 8px 12px;
font-size: 12px;
color: #e0e0e0;
```

**Tooltip header:**
```css
color: #569cd6;
font-weight: 600;
font-size: 14px;
margin-bottom: 8px;
padding-bottom: 6px;
border-bottom: 1px solid #3c3c3c;
```

### Menus / Dropdowns

```css
position: fixed;
background: #2d2d2d;
border: 1px solid #569cd6;
border-radius: 4px;
box-shadow: 0 4px 12px rgba(0, 0, 0, 0.4);
min-width: 200px;
max-height: 350px;
z-index: 1000;
animation: fadeIn 0.15s ease-out;
```

**Menu item:**
```css
padding: 6px 8px;
cursor: pointer;
display: flex;
align-items: center;
gap: 8px;
font-size: 12px;
transition: background 0.15s ease;
```

**Menu item hover:**
```css
background: #3c3c3c;
```

### Badges / Tags

**Active badge:**
```css
font-size: 10px;
color: #569cd6;
background: #1e3a52;
padding: 2px 6px;
border-radius: 3px;
```

**Data series badge:**
```css
background: #3c3c3c;
border: 1px solid {series-color};
border-radius: 3px;
padding: 3px 8px;
font-size: 11px;
```

**Dimmed / hidden badge:**
```css
background: #2d2d2d;
opacity: 0.5;
```

### Scrollbars

```css
::-webkit-scrollbar {
  width: 8px;
}
::-webkit-scrollbar-track {
  background: #1e1e1e;
  border-radius: 4px;
}
::-webkit-scrollbar-thumb {
  background: #4a6b8a;
  border-radius: 4px;
}
::-webkit-scrollbar-thumb:hover {
  background: #569cd6;
}
```

---

## Layout

### Page Structure

```
Full viewport (100vw x 100vh, overflow hidden)
  Header bar (fixed height, padding: 8px 16px)
  Content area (flex: 1, display: flex, gap: 12px, padding: 12px)
    Left panel (width: 40%)
      Stacked panels (flexDirection: column, gap: 8px)
    Right panel (flex: 1)
      Grid or flex content
```

### Flexbox Patterns

**Vertical stack:**
```css
display: flex;
flex-direction: column;
gap: 8px;
min-height: 0;     /* critical for overflow in flex columns */
```

**Horizontal row:**
```css
display: flex;
align-items: center;
gap: 8px;
```

**Fill available space:**
```css
flex: 1;
min-height: 0;     /* prevents overflow issues */
```

### Grid (for time series plots)

```css
display: grid;
grid-template-columns: repeat(3, 1fr);
grid-template-rows: repeat(3, 1fr);
gap: 8px;
flex: 1;
overflow: hidden;
```

**Expanded (single item):**
```css
grid-template-columns: 1fr;
grid-template-rows: 1fr;
```

---

## Shadows

| Level      | Value                                | Usage              |
|------------|--------------------------------------|--------------------|
| Small      | `0 1px 4px rgba(0, 0, 0, 0.2)`     | Active press       |
| Medium     | `0 2px 8px rgba(0, 0, 0, 0.3)`     | Button hover       |
| Large      | `0 2px 12px rgba(0, 0, 0, 0.2)`    | Panel hover        |
| Elevated   | `0 4px 12px rgba(0, 0, 0, 0.4)`    | Menus, dropdowns   |

---

## Animations & Transitions

### Standard Easing

```css
cubic-bezier(0.4, 0, 0.2, 1)     /* primary easing for all transitions */
```

### Transition Presets

```css
/* General purpose */
transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);

/* Quick interactions (hover, toggle) */
transition: all 0.15s cubic-bezier(0.4, 0, 0.2, 1);

/* Color-only changes */
transition: background 0.15s ease;

/* SVG dot color */
transition: fill 0.3s ease, stroke 0.3s ease;
```

### Keyframe Animations

**Fade in (menus, overlays):**
```css
@keyframes fadeIn {
  from { opacity: 0; transform: translateY(-4px); }
  to   { opacity: 1; transform: translateY(0); }
}
/* duration: 0.15s ease-out */
```

**Scale in (grid items, panels):**
```css
@keyframes scaleIn {
  from { opacity: 0; transform: scale(0.95); }
  to   { opacity: 1; transform: scale(1); }
}
/* duration: 0.3s ease-out */
```

**Expand plot:**
```css
@keyframes expandPlot {
  0%   { transform: scale(0.9); opacity: 0.8; }
  100% { transform: scale(1);   opacity: 1; }
}
/* duration: 0.4s cubic-bezier(0.4, 0, 0.2, 1) */
```

**Loading spinner:**
```css
@keyframes spin {
  from { transform: rotate(0deg); }
  to   { transform: rotate(360deg); }
}
/* duration: 1s linear infinite */
```

### Interactive Transforms

```css
/* Hover scale up */
transform: scale(1.1);

/* Active press down */
transform: scale(0.95);

/* Icon rotation */
transform: rotate(90deg);
```

---

## Chart Styling (Recharts)

### Grid

```
strokeDasharray: "3 3"
stroke: #3c3c3c
```

### Axes

```
tick:   { fill: '#a0a0a0', fontSize: 11 }
label:  { fill: '#a0a0a0', fontSize: 12 }
stroke: #3c3c3c
```

### Lines

```
strokeWidth: 1.5
dot: false
isAnimationActive: false
```

### Scatter Points

- Default radius: `6px`
- Selected radius: `8px`
- Hover: glow effect with `filter: brightness(1.3) drop-shadow(0 0 4px currentColor)`
- Selected stroke: `2px solid #fff`

### Clusters

- Radius: `10px + log10(count) * 5`, max `20px`
- Fill: `#2e5c8a` (default), `#3a6fa0` (hover)
- Stroke: `#1e1e1e` (default), `#569cd6` (hover)
- Count label: `#e0e0e0`, weight `600`

---

## Interactive States

### Hover

| Element       | Property              | Change                           |
|---------------|-----------------------|----------------------------------|
| Button        | background            | Lighten or accent                |
| Panel/Card    | border-color          | `#3c3c3c` -> `#4a4a4a`          |
| Panel/Card    | box-shadow            | Add `0 2px 12px rgba(0,0,0,.2)` |
| Menu item     | background            | Add `#3c3c3c`                   |
| Dot           | filter                | `brightness(1.3) drop-shadow`   |
| Dot           | radius                | `baseRadius * 1.4`              |
| Cluster       | radius                | `+2px`                          |
| Scrollbar     | thumb background      | `#4a6b8a` -> `#569cd6`          |

### Selected / Active

| Element       | Property              | Value                            |
|---------------|-----------------------|----------------------------------|
| Toggle button | background            | `#1e3a52`                        |
| Toggle button | color                 | `#569cd6`                        |
| Toggle button | border                | `1px solid #569cd6`              |
| Dot           | stroke                | `2px solid #fff`                 |
| Dot           | radius                | `+2px` from default              |
| Checkbox      | accent-color          | `#569cd6`                        |

### Disabled

| Element       | Property              | Value                            |
|---------------|-----------------------|----------------------------------|
| Button        | background            | `#2d2d2d`                        |
| Button        | color                 | `#666`                           |
| Button        | border                | `1px solid #3c3c3c`             |
| Button        | cursor                | `not-allowed`                    |

### Loading

- Spinner character: `\u27F3` (rotating arrow)
- Color: `#569cd6`
- Animation: `spin 1s linear infinite`
- Loading screen: centered flex, `#1e1e1e` background

---

## Z-Index Layers

| Layer         | Value   | Usage                                |
|---------------|---------|--------------------------------------|
| Base          | `auto`  | Normal document flow                 |
| Backdrop      | `999`   | Menu backdrop overlay                |
| Menu          | `1000`  | Dropdown menus, popups               |

---

## Utility Patterns

### No Select

```css
user-select: none;
-webkit-user-select: none;
-moz-user-select: none;
-ms-user-select: none;
```

### Non-interactive Overlay

```css
pointer-events: none;
user-select: none;
```

### Cursor States

| State      | Cursor        | When                             |
|------------|---------------|----------------------------------|
| Clickable  | `pointer`     | Buttons, dots, links, toggles    |
| Panning    | `grabbing`    | Active drag on chart              |
| Disabled   | `not-allowed` | Disabled buttons                  |
| Default    | `default`     | Normal state                      |

---

## Global Reset

```css
* {
  margin: 0;
  padding: 0;
  box-sizing: border-box;
}

body {
  margin: 0;
  font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
  background: #1e1e1e;
  color: #e0e0e0;
  overflow: hidden;
}

button, select {
  font-family: inherit;
}
```

---

## Quick Reference Cheatsheet

```
Backgrounds:   #1e1e1e -> #252526 -> #2d2d2d -> #3c3c3c
Text:          #e0e0e0 -> #a0a0a0 -> #909090 -> #666
Borders:       #3c3c3c (panels)  |  #555 (controls)  |  #569cd6 (active)
Accent:        #569cd6 (primary) |  #1e3a52 (bg)
Radius:        3px (controls)    |  4px (panels)
Font sizes:    11px (buttons)    |  12px (labels/inputs) |  13px (body)
Easing:        cubic-bezier(0.4, 0, 0.2, 1)
Shadows:       rgba(0, 0, 0, 0.2-0.4)
```
