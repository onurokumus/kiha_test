# Project development instructions

## Development workflow

Follow [DEVELOPMENT_WORKFLOW.md](DEVELOPMENT_WORKFLOW.md) for milestone selection, implementation, verification, and checkpoints. Read [IMPLEMENTATION.md](IMPLEMENTATION.md) for the current handoff, then reconcile it with the actual code and working tree before continuing.

## Target devices and input

- This application is intended exclusively for desktop/laptop computers using a keyboard and mouse. Phones, tablets, and touchscreen interaction are out of scope unless the user explicitly changes this requirement.
- Design and validate interactions for desktop use. Do not add mobile layouts, touch gestures, long-press alternatives, or touch-specific controls solely for device compatibility.
- Continue to support different desktop window sizes, resizing, maximized plots, browser zoom, and keyboard accessibility. Desktop-only does not imply a fixed viewport size.
- Custom right-click menus are permitted, and secondary actions may be available only there. Keep core analysis and export actions discoverable through visible controls or a compact menu button; reuse the same action handlers across entry points.
