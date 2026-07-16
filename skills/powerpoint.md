---
name: powerpoint
triggers: powerpoint, pptx, slides, deck, presentation, pitch, slideshow
priority: 5
---
# PowerPoint playbook — decks a client can be shown

## Never invent slide content
- Every number, name and date on a slide must come from a file you called
  read_file on THIS TURN. No source file? Say so and ask — never invent
  illustrative figures. A fabricated deck gets presented to a client.
- Summarizing a document? read_file it FIRST, then extract its argument.

## Use the native tool
- Call create_powerpoint. No install, no pandas.
- slides = [{"title": "...", "bullets": ["...", ...], "notes": "..."}].
  Bullets are PLAIN STRINGS, never dicts.
- It CANNOT draw charts, images or tables. Asked for a chart? Put the real
  numbers in bullets and tell the user the deck has no chart. Never pip
  install, never shell out, never reach for matplotlib.

## Structure
A deck is an argument: situation → insight → evidence → so-what → next steps.
- Title slide, then an executive summary that leads with the answer.
- Body: ONE idea per slide. If a slide needs "and", split it.
- Last slide: next steps or the decision asked — never "Thanks".
- One slide per real finding. Never add a slide to hit a number.

## Slides
- Title = the takeaway: "Cloud costs doubled in Q3" beats "Cost overview" —
  but only if you read that. Can't trace it? Use the plain topic title.
- Bullets: max 5 per slide, ~12 words, fragments, parallel grammar.
- Notes per slide: what the presenter says — detail, caveats, and the file
  path the numbers came from.
