---
name: excel
triggers: excel, xlsx, spreadsheet, workbook, sheet, csv to excel, financial model
priority: 5
---
# Excel playbook

## Rules — obey before anything else
- If the user names a file, call read_file on it BEFORE create_excel. Never
  write a number you did not read. Inventing rows is the worst failure here.
- Every figure must trace to a cell you actually read. Organise what the
  source says; never add figures it does not contain. Never estimate.
- create_excel is the ONLY way to write .xlsx. Never pip install, never
  import pandas or openpyxl yourself, never run python3 -c. Aggregate by
  grouping the rows read_file returned, in your own reasoning.
- create_excel CAN add a simple chart: put "chart" on the sheet, e.g.
  {"type":"bar","title":"...","categories":"Department","values":"Amount (EUR)"}
  — categories/values name header columns. Pivots, formulas and number formats
  stay unsupported; pre-aggregate rows yourself, never reach for a library.
- If a read shows "[truncated at 20000 chars]" the file is bigger than what
  you saw. Build only from rows you got and say so. Never infer the rest.

## Shape
- Sheet names: short TitleCase ("Revenue"), never "Sheet1". One table per
  sheet, from A1, no merged cells.
- Headers carry units — "Revenue (EUR)", never bare "Value". One data type
  per column. Dates YYYY-MM-DD. Numbers as numbers (1200, not "EUR 1.200").
- Sort by the most meaningful column. TOTAL row for numeric tables.
- A Summary sheet is optional: only figures computed from rows you read.
