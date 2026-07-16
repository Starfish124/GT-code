---
name: data
triggers: csv, dataset, data analysis, analyze data, clean data, sql, sqlite, query, json data, parse, aggregate, groupby, plot, chart
priority: 3
---
# Data playbook

NEVER invent a number, column name, category or total. Every figure you write
must come from a file you read THIS turn with read_file. Not read it? Read it
first. If read_file says truncated, you have NOT seen the whole file — compute
the numbers in a script, never from the visible snippet.

Do NOT use pandas, numpy or matplotlib. They are NOT installed and installing
them burns the whole turn. Never run pip, brew, or venv. Never call `python3`.

## Read a CSV
write_file a real .py script, then run_command:
"/Users/sarveshsingh/GT-code/.venv/bin/python script.py"
Never `python3 -c` one-liners — chained compound statements are a SyntaxError.

    import csv
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    print(len(rows), list(rows[0].keys()))

Print the row count and the REAL column names before you compute anything, and
use those exact names and units (e.g. amount_eur, not USD). Aggregate with
collections.defaultdict or Counter — a groupby is a dict, not a library.
Print every headline number, and the row count before and after each filter.

## Deliver
Pass the aggregated rows straight to create_excel — it takes plain lists/dicts.
Sort largest first, put units in the headers, add a totals row.

create_excel CAN chart: add "chart" to the sheet, e.g. {"type":"bar",
"categories":"<header>","values":"<header>"}. Never install a plotting library.
