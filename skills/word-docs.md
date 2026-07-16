---
name: word-docs
triggers: word, docx, memo, letter, proposal, report
priority: 4
---
# Word-document playbook

## Grounding — do this first, every time
- Every number, name, date and total must come from a file you called
  read_file on THIS turn. If you did not read it, do not write it.
- The request names a file? Call read_file on it BEFORE create_word.
  Never guess column names, categories or figures.
- A figure not in the source: write "not available in the source data".
  Never estimate, never invent a total.
- Never say the document is "ready to share". Say what you read and
  what you left out.

## The tool — create_word, nothing else
- Block types are ONLY: heading (text, integer level 1-9), paragraph
  (text), bullets (items). Any other type is silently discarded — a
  "numbered" or "table" block writes an EMPTY paragraph and loses its
  content.
- Need a numbered list or a table? Use bullets, and say so in your
  final answer.
- Plain text only. `**bold**` prints literal asterisks in Word.
- NEVER run_command, NEVER pip, NEVER python3, NEVER pandas. If
  create_word cannot do it, deliver what it can and say so.

## Structure (report/memo default)
1. Title — heading level 1, specific.
2. Executive summary — 3-5 sentences: situation, finding, recommendation.
3. Body — level-2 headings, one topic each.
4. Recommendations — bullets, each starting with a verb.

## Style
- Short sentences, active voice. Concrete numbers over vague claims —
  but only numbers you read this turn.
- Bullets for parallel facts; paragraphs for narrative. No filler.
