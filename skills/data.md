---
name: data
triggers: csv, dataframe, pandas, dataset, data analysis, analyze data, clean data, sql, sqlite, query, json data, parse, aggregate, groupby, plot, chart
priority: 3
---
# Data playbook — trustworthy analysis, not confident nonsense

The bar: every number can be traced back to a line of code and reproduced.

## Look before you compute
- Load, then INSPECT before any analysis: shape, column names/types, and the
  first few rows. `df.head()`, `df.info()`, `df.describe()`. Never assume the
  schema — real data is messy.
- Check for the landmines that silently corrupt results: missing values
  (`df.isna().sum()`), duplicate rows, wrong dtypes (numbers read as strings,
  dates as objects), leading/trailing spaces, mixed units, inconsistent
  categories ("USA"/"U.S."/"us").
- State the row count before and after every filter — a `groupby` on dirty
  keys silently drops or splits rows.

## Load robustly
- CSV: `pd.read_csv(path)`. If it's big or numbers look wrong, set
  `dtype=`/`parse_dates=` explicitly; watch for thousands separators and a
  stray header/footer row. Bad encoding? try `encoding="utf-8-sig"` (Excel
  exports) or `latin-1`.
- JSON: `pd.json_normalize(data)` flattens nested records into columns.
- SQL/SQLite: query with parameters, never string-format user input into SQL
  (`cur.execute("... WHERE id = ?", (id,))`) — it's both an injection hole and
  a quoting bug. Pull into a DataFrame with `pd.read_sql(query, conn)`.

## Clean deliberately, and say what you did
- Handle missing data on purpose: drop, fill with a stated value, or
  interpolate — and say which and why. Never let NaN silently become 0 in a
  sum.
- Normalize types once, up front: `pd.to_numeric(col, errors="coerce")`,
  `pd.to_datetime(col)`, `.str.strip().str.lower()` for categorical keys.
- Deduplicate only with an explicit key: `df.drop_duplicates(subset=[...])`.

## Analyze
- `groupby(...).agg(...)` for summaries; name the outputs so columns are
  readable. Sort results so the reader sees the story (largest first).
- Vectorize — use column operations and `.map`/`.apply`, not Python `for`
  loops over rows (`iterrows` is slow and a code smell).
- Round for presentation only at the END; keep full precision while computing.
- Sanity-check every headline number by hand: do the totals add up? is the
  average in a plausible range? A number you can't explain is probably a bug.

## Deliver
- Report → hand back a clear table or a short written summary of the finding,
  with the numbers that back it.
- Spreadsheet requested → hand off to the excel playbook (summary sheet first,
  headers with units, totals row). Don't dump a raw CSV when they asked for a
  polished file.
- Chart → label both axes with units, title states the takeaway, start bar
  axes at zero, sort categories meaningfully.

## Verify
- Re-run the whole script top to bottom on the real file — analysis must be
  reproducible, not a REPL you can't rebuild. Confirm the row counts and the
  headline numbers match what you reported.
