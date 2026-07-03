---
name: excel
triggers: excel, xlsx, spreadsheet, workbook, sheet, csv to excel, pivot, financial model
priority: 5
---
# Excel playbook — build spreadsheets like a professional analyst

Use the create_excel tool. Aim for a workbook a consultant would hand to a
client, not a raw data dump.

## Structure
- First sheet = **Summary**: title, date, 3-6 key figures or takeaways.
  Detail sheets follow. Never make the reader hunt for the point.
- One table per sheet, starting at A1. No merged cells inside data.
- Sheet names: short, TitleCase, no "Sheet1" ("Revenue", "Assumptions").

## Every data table
- Header row: short, unambiguous names with units — "Revenue (EUR)",
  "Growth %", never bare "Value". The tool bolds and freezes it.
- One data type per column. Dates as YYYY-MM-DD. Numbers as numbers, never
  as text with symbols ("1200", not "€1.200,-").
- Sort by the most meaningful column (biggest first, or chronological).
- End numeric tables with a Total/Average row when it aids reading; label
  it "TOTAL" in the first column.

## Judgment
- 3-12 columns per table. If more, split into logical sheets.
- Derived columns (growth %, share %, variance) turn data into insight —
  add them when the question implies comparison.
- An Assumptions sheet whenever numbers are estimated: item, value, source.
- Round for humans: money to 2 decimals or thousands, percentages to 1.

## With uploaded/read data
- Read the source with read_file first, parse carefully, and preserve every
  row — never invent or drop values silently. If data looks malformed, say
  what you skipped in your final answer.
