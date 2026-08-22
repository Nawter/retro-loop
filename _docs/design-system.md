# Design system

Read before touching the UI. The frontend is one `index.html`, one `app.css`
and one `app.js`, served by the FastAPI app. No framework, no bundler, no CSS
library, no npm.

## Tokens

Every colour, space and type value is a CSS custom property on `:root` in
`app.css`. Nothing hard-codes a hex or a pixel margin anywhere else.

- `--bg`, `--surface`, `--text`, `--muted`, `--border`
- `--start`, `--stop`, `--continue` - one accent per column
- `--accent` - interactive elements
- `--space-1` .. `--space-4` on a 4px scale (4 / 8 / 16 / 24)
- `--radius`, `--font`

Dark mode is a single `@media (prefers-color-scheme: dark)` block that
redefines those tokens. Nothing else changes.

## Layout

- Three equal columns for Start / Stop / Continue, stacking to one column
  below 720px.
- A card is a bordered surface carrying its column's accent on the left edge.
- Persistent chrome - join code, current phase, "Next phase", connection
  status - lives in one header that every phase renders.
- Long content scrolls inside its column. The page never scrolls sideways.

## Behaviour

- Nothing renders optimistically. The UI changes when the server echoes the
  change back.
- State is the snapshot plus the deltas applied to it. The DOM is never the
  source of truth.

## Accessibility - not optional

- Real elements: `<button>` for actions, `<label>` for every input, no
  clickable `<div>`.
- Focus is always visible. Never remove an outline without replacing it.
- HTML5 drag-and-drop is pointer-only, so every card also needs a
  keyboard-reachable way to change cluster.
- Transient messages - "reconnecting", "no votes left", a rejected action - go
  into an `aria-live="polite"` region. Never `alert()`.
- Colour is never the only signal: column accents are paired with headings,
  vote counts are numbers rather than bar length alone.
