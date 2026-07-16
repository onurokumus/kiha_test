# Animation Guide

## Overview

Added smooth, professional animations throughout the application to enhance user experience and provide visual feedback. This includes time plot controls and interactive scatter plot points.

## Animations Added

## Scatter Plot Point Interactions

### 1. **Point Hover Effect**
- **Scale up**: Points grow by 40% on hover for better visibility
- **Brightness**: Point color brightens by 30% with CSS filter
- **Glow effect**: Drop shadow creates a soft glow
- **Timing**: 0.3s cubic-bezier easing for smooth feel

```css
filter: brightness(1.3) drop-shadow(0 0 4px currentColor);
```

### 2. **Pulsing Glow Rings**
- **Outer ring**: Expands and fades continuously (1.5s cycle)
- **Middle ring**: Static semi-transparent ring for depth
- **Opacity animation**: Pulses between 0.3 and 0.15
- **Only on hover**: Rings appear/disappear smoothly

```jsx
<animate
  attributeName="r"
  values={`${glowRadius};${glowRadius + 2};${glowRadius}`}
  dur="1.5s"
  repeatCount="indefinite"
/>
```

### 3. **Inner Highlight**
- **Shine effect**: White highlight appears on top-left of point
- **Position**: Offset by 25% of radius for 3D effect
- **Opacity**: 40% for subtle reflection
- **Dynamic sizing**: Scales proportionally with point size

### 4. **Selection State**
- **Selected points**: Larger base size (8px vs 6px)
- **White stroke**: 2px white border on selected points
- **Maintains on hover**: Selection state visible during hover
- **Smooth transitions**: All state changes animated

## Time Plot Controls

### 5. **Button Hover Effect**
- **Scale up**: Button grows by 10% on hover (`transform: scale(1.1)`)
- **Background change**: Lighter background color (#4a4a4a)
- **Shadow**: Adds depth with box shadow
- **Timing**: 0.3s cubic-bezier easing for smooth feel

```css
.expandButton:hover {
  background: #4a4a4a;
  transform: scale(1.1);
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.3);
}
```

### 6. **Button Click Effect**
- **Scale down**: Button shrinks slightly on click (`transform: scale(0.95)`)
- **Ripple effect**: Circular ripple animation from center
- **Feedback**: Provides tactile click feedback

```css
.expandButton:active {
  transform: scale(0.95);
}
```

### 7. **Icon Rotation on Hover**
- **90° rotation**: Icon rotates when hovering over button
- **Smooth transition**: 0.3s cubic-bezier animation
- **Reverses**: Rotates back when hover ends

```css
.expandButton:hover .expandButtonIcon {
  transform: rotate(90deg);
}
```

### 8. **Icon Animation on Expand**
- **Pulse + Rotate**: Icon pulses and rotates 360° when expanding
- **Scale effect**: Grows to 130% midway through animation
- **Duration**: 0.5s

```css
@keyframes pulseIcon {
  0% { transform: scale(1) rotate(0deg); }
  50% { transform: scale(1.3) rotate(180deg); }
  100% { transform: scale(1) rotate(360deg); }
}
```

### 9. **Icon Animation on Collapse**
- **Reverse rotation**: Rotates -360° (counter-clockwise)
- **Scale pulse**: Grows to 120% midway
- **Duration**: 0.5s

```css
@keyframes rotateIcon {
  0% { transform: scale(1) rotate(0deg); }
  50% { transform: scale(1.2) rotate(-180deg); }
  100% { transform: scale(1) rotate(-360deg); }
}
```

### 10. **Plot Container Animations**
- **Hover effect**: Border highlight and shadow on hover
- **Expand animation**: Scale up from 90% when expanding
- **Collapse animation**: Brief scale to 105% when collapsing

```css
@keyframes expandPlot {
  0% { transform: scale(0.9); opacity: 0.8; }
  100% { transform: scale(1); opacity: 1; }
}
```

### 11. **Grid Layout Transitions**
- **Smooth grid changes**: 0.3s transition when switching between 3x3 and 1x1 layout
- **Fade in effect**: Plots fade in when appearing
- **Cubic-bezier easing**: Professional easing curve

```css
.gridContainer {
  transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
}
```

## User Experience Benefits

1. **Visual Feedback**: Users immediately see their actions acknowledged
2. **State Communication**: Different states (hover, active, expanded, selected) are clearly communicated
3. **Professional Feel**: Smooth animations make the app feel polished
4. **Reduced Confusion**: Clear transitions help users understand what's happening
5. **Engagement**: Delightful micro-interactions keep users engaged
6. **Discoverability**: Hover effects help users discover interactive elements
7. **Precision**: Enlarged points on hover make selection easier on dense plots
8. **Depth**: Multi-layer effects create visual hierarchy and 3D feel

## Performance Considerations

- **CSS Animations**: All animations use CSS transforms and opacity for GPU acceleration
- **SVG Animations**: Point animations use native SVG `<animate>` elements (hardware accelerated)
- **No Layout Thrashing**: Transforms don't trigger layout recalculation
- **Optimized Timing**: Short durations (0.3s-0.5s) prevent lag feeling
- **Cubic-bezier Easing**: Natural-feeling acceleration/deceleration
- **Conditional Rendering**: Glow effects only render when hovered (performance optimization)
- **Pointer Events**: Inner elements use `pointer-events: none` to prevent event conflicts

## Files Created/Modified

### Scatter Plot Animations
1. **[src/components/plots/AnimatedDot.tsx](src/components/plots/AnimatedDot.tsx)** (ENHANCED)
   - Added hover state management
   - Multi-layer glow ring system
   - Dynamic scaling and brightness effects
   - Inner highlight for 3D appearance
   - Pulsing animations using SVG `<animate>`

### Time Plot Animations
2. **[src/components/plots/TimePlot.tsx](src/components/plots/TimePlot.tsx)**
   - Added CSS module import
   - Applied animation classes to button and container

3. **[src/components/plots/TimePlot.module.css](src/components/plots/TimePlot.module.css)** (NEW)
   - Button animations and hover effects
   - Icon rotation and pulse animations
   - Plot container transitions

4. **[src/components/plots/TimeSeriesGrid.tsx](src/components/plots/TimeSeriesGrid.tsx)**
   - Added CSS module for grid transitions
   - Wrapper divs for fade-in animations

5. **[src/components/plots/TimeSeriesGrid.module.css](src/components/plots/TimeSeriesGrid.module.css)** (NEW)
   - Grid layout transitions
   - Plot wrapper fade-in effects

## Browser Compatibility

All animations use widely supported CSS features:
- ✓ Chrome/Edge (latest 2 versions)
- ✓ Firefox (latest 2 versions)
- ✓ Safari (latest 2 versions)
- ✓ Uses `cubic-bezier()` timing function (universal support)
- ✓ CSS transforms (GPU accelerated on all modern browsers)

## Customization

### Scatter Plot Points

1. **Hover Scale**: Adjust how much points grow on hover
   ```typescript
   const hoverRadius = isHovered ? baseRadius * 1.4 : baseRadius;
   // Change 1.4 to your preferred multiplier (e.g., 1.5 for 50% growth)
   ```

2. **Glow Ring Size**: Modify the glow ring radius
   ```typescript
   const glowRadius = hoverRadius + 6;
   // Change +6 to increase/decrease glow size
   ```

3. **Brightness**: Adjust the hover brightness
   ```typescript
   filter: isHovered ? 'brightness(1.3) drop-shadow(0 0 4px currentColor)' : 'none'
   // Change 1.3 to your preferred brightness (1.5 = 50% brighter)
   ```

4. **Pulse Speed**: Change the pulsing animation duration
   ```jsx
   <animate dur="1.5s" />
   // Change to "1s" for faster, "2s" for slower
   ```

### Time Plot Controls

5. **Duration**: Change timing in `.expandButton` transition
   ```css
   transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
   /* Change 0.3s to your preferred duration */
   ```

6. **Easing**: Modify the cubic-bezier values
   ```css
   cubic-bezier(0.4, 0, 0.2, 1) /* Current: ease-in-out feel */
   cubic-bezier(0.68, -0.55, 0.265, 1.55) /* Alternative: bounce */
   ```

7. **Scale amount**: Adjust hover scale
   ```css
   transform: scale(1.1); /* Change 1.1 to increase/decrease */
   ```

8. **Rotation**: Change rotation degrees
   ```css
   transform: rotate(90deg); /* Try 180deg for full flip */
   ```

## Testing

To test the animations:

1. Start the dev server: `npm run dev`

### Scatter Plot
2. Hover over any point in the left scatter plot
   - Point should scale up by 40%
   - Two glow rings should appear
   - Point should brighten
   - Inner highlight should appear
3. Move cursor away - all effects should smoothly reverse
4. Click to select a point - selection state should be maintained during hover

### Time Plot Controls
5. Select test points from the main plot
6. Hover over the expand/minimize buttons (▣/▪)
   - Button should scale up and lighten
   - Icon should rotate 90°
7. Click to expand/collapse plots
   - Icon should pulse and rotate 360°
   - Plot container should animate
8. Observe smooth transitions

## Animation Details by Component

### AnimatedDot Component Structure

The scatter plot points use a multi-layer SVG structure:

```
<g>                                    // Group container
  <circle />                          // Outer glow ring (pulsing)
  <circle />                          // Middle ring (static)
  <circle>                            // Main dot (interactive)
    <animate />                       // Radius animation
  </circle>
  <circle />                          // Inner highlight (shine effect)
</g>
```

**Layer breakdown:**
1. **Outer glow**: Animated ring that pulses outward (only on hover)
2. **Middle ring**: Semi-transparent ring for depth (only on hover)
3. **Main dot**: The clickable point with color and selection state
4. **Inner highlight**: White shine for 3D effect (only on hover)

### Event Handling
- `onMouseEnter`: Sets `isHovered` to true, triggers all hover effects
- `onMouseLeave`: Sets `isHovered` to false, reverses all effects
- `onClick`: Toggles selection state independent of hover

### State Management
- `displayColor`: Current point color (animated)
- `displayR`: Current radius (animated)
- `displayStroke`: Current stroke (white when selected)
- `isHovered`: Boolean controlling all hover effects

## Future Enhancements

Potential animation improvements:
- [ ] Stagger animations when multiple plots appear
- [ ] Spring physics for more natural movement
- [ ] Parallax effect on plot containers
- [ ] Smooth data line animations when new points are selected
- [ ] Toast notifications with slide-in animations
- [ ] Trail effect when dragging to select multiple points
- [ ] Sparkle effect on newly selected points
- [ ] Magnetic snap effect when hovering near points
