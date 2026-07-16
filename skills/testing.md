---
name: testing
triggers: test, tests, testing, unit test, pytest, jest, vitest, coverage, tdd, assert, mock, fixture, test suite, spec
priority: 3
---
# Testing playbook

NEVER say a test passes unless a run_command observation THIS turn shows
`exit code: 0`. No run, no claim — say what you did not verify.
"0 tests collected" is a broken suite, not a pass.

## Run the suite with GT's own python
- Python: run_command `"<GT python>" -m pytest -q` (GT python = the venv
  interpreter running GT; on Windows `...\Scripts\python.exe`). If it says
  `No module named pytest`, use `"<GT python>" -m unittest discover -q` —
  stdlib, always there.
- NEVER `pip install` a runner, NEVER bare `python3`/`brew`.
- JS/TS: `npx --no-install vitest run` (`run` avoids watch mode; both hang
  until killed otherwise). If vitest is absent, run the script package.json
  already declares, e.g. `npm test`. Never add a second framework.

## Write the assert
Assert exact values, never truthiness: `assert x == 5`, not `assert x`.
One behavior per test; name it as a sentence.
```python
def test_split_bill_rounds_up_the_remainder():
    bill = Bill(total=100, people=3)        # arrange
    shares = bill.split()                   # act
    assert shares == [33.34, 33.33, 33.33]  # assert — exact
```
Deterministic: no real clock, network, randomness. Write files to
`tmp_path`, never into the repo.

## What to test
Happy path first. Then edges: empty, zero, negative, one element, missing
key, None. Then error paths — assert the specific exception. For a bug fix,
write the failing test first, watch it fail, fix the CODE, re-run.

