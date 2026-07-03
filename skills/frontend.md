---
name: frontend
triggers: html, css, website, web app, landing page, frontend, ui, page, dashboard, javascript, react, tailwind, form, responsive
priority: 4
---
# Frontend playbook — pages that look professionally designed

The bar: someone opens the file and assumes a designer was involved.

## Design system first (define as CSS custom properties in :root)
- ONE accent color + neutrals. Dark text on light bg (#1a1a2e-ish on
  #fafafa-ish) or the inverse — never pure black on pure white.
- Spacing scale: 4/8/12/16/24/32/48/64px. Pick from it, never eyeball.
- Font: system stack `-apple-system, "Segoe UI", Roboto, sans-serif`;
  16px base, line-height 1.5-1.6; headings ~1.25 ratio, weight 600-700.
- Border-radius consistent (6-10px). Shadows subtle:
  `0 1px 3px rgba(0,0,0,.08)`, more blur than offset.

## Layout
- Content in a centered container: `max-width: 1100px; margin-inline: auto;
  padding-inline: 24px`. Text columns max ~70ch.
- Flexbox/Grid only — no floats, no absolute-position layouts.
- Responsive by default: `grid-template-columns: repeat(auto-fit,
  minmax(260px, 1fr))` handles most card layouts with zero media queries.
- Generous whitespace: sections 48-96px apart. Cramped = amateur.

## Details that sell it
- Hover/focus states on EVERYTHING interactive (color shift + subtle
  transform, `transition: all .15s ease`). Visible :focus-visible outline.
- Semantic HTML: header/nav/main/section/footer, real <button>, labels
  tied to inputs. Alt text on images.
- Empty states, not blank divs ("No projects yet — create one").
- No lorem ipsum: write plausible real copy for the actual purpose.

## Single-file rule
- Default to ONE self-contained .html (inline CSS + JS, no build step, no
  CDN) so it opens with a double-click. Use a framework only if asked or
  the project already has one.
- Vanilla JS: addEventListener, fetch, template literals. Escape any
  user-provided text before injecting into HTML.

## Verify
- Open it (run_command with the file path / a static server) and confirm
  it renders; check what it does at narrow width before declaring done.
