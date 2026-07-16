---
name: frontend
triggers: html, css, website, web app, landing page, frontend, html page, web dashboard, javascript, react, tailwind, form, responsive
priority: 4
---
# Frontend playbook

## Hard rules — these beat the styling below
- NEVER invent DATA. Every number, name, category or date on the page must
  come from a file you read_file'd THIS turn. Not read it? Do not render
  it. Invent copy ONLY for chrome: nav, labels, buttons, empty states.
- ONE self-contained .html: inline ALL CSS + JS, no build step. NEVER a CDN
  <script>/<link> — GT and the client may be offline, so the page renders
  unstyled, and a client deliverable must not call third-party servers.
  Tailwind too: hand-write the CSS, never load the CDN.
- You CANNOT see the page: no browser or screenshot tool exists. `open
  file.html` exits 0 for a blank page too — that is NOT verification. Never
  claim it renders or looks right. Instead read_file what you wrote, check
  the markup is there, and report "written and re-read; open <path> to view".
- Escape user text before injecting it into HTML.
- Framework only if asked, then Vite. NEVER create-react-app — it times out.

## Design (CSS vars in :root)
- ONE accent + neutrals. Dark-on-light (#1a1a2e on #fafafa) or inverse.
- Spacing scale 4/8/12/16/24/32/48/64px — never eyeball.
- System font stack, 16px base, line-height 1.5. Radius 6-10px.
- Centered `max-width:1100px; margin-inline:auto; padding:24px`. Text ~70ch.
- `repeat(auto-fit, minmax(260px,1fr))` = responsive cards, no media query.
- Hover + :focus-visible on everything interactive. Semantic HTML, real
  <button>, labels on inputs.
