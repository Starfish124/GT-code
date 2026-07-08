---
name: testing
triggers: test, tests, testing, unit test, pytest, jest, vitest, coverage, tdd, assert, mock, fixture, test suite, spec
priority: 3
---
# Testing playbook — tests that catch real bugs, not busywork

The bar: a stranger runs one command, sees green, and trusts the code.

## Pick the standard runner, don't invent one
- Python: `pytest`. Files `test_*.py`, functions `test_*`, plain `assert`.
  Run: `.venv/bin/pytest -q` (call the venv binary directly — activation
  doesn't persist between commands).
- JS/TS: `vitest` for Vite/ESM projects, `jest` for older Node. Files
  `*.test.js` / `*.spec.ts`. Run: `npx vitest run` (the `run` avoids watch
  mode, which never exits and would time out).
- Match whatever the project already uses — check package.json / pyproject
  before adding a second framework.

## What to actually test (in priority order)
1. The happy path of each public function — the thing it's for.
2. Edge cases that break naive code: empty input, zero, negative, one
   element, missing keys, None/null, unicode, very large.
3. Error paths: it raises/returns the RIGHT error on bad input — assert the
   specific exception, not just "it raises".
4. The bug you just fixed: write the failing test FIRST, watch it fail, then
   fix — that proves the fix and locks it in (regression test).
Don't test the language, getters, or trivial glue. Coverage is a hint, not
a goal — one sharp test beats ten that assert `True`.

## Structure: Arrange–Act–Assert
```python
def test_split_bill_rounds_up_the_remainder():
    bill = Bill(total=100, people=3)          # arrange
    shares = bill.split()                      # act
    assert shares == [33.34, 33.33, 33.33]     # assert — exact, not "> 0"
```
- One behavior per test. The name is a sentence: `test_<thing>_<condition>_<result>`.
- Assert exact expected values, not just truthiness. `assert x == 5`, not
  `assert x`.
- Independent + deterministic: no shared mutable state, no reliance on order,
  no real clock/network/randomness — inject or freeze them.

## Fixtures & mocks — sparingly
- `pytest` fixtures (or `beforeEach`) for shared setup like a temp dir or a
  seeded in-memory DB. Use `tmp_path` for files, never write to the repo.
- Mock only what you don't own or what's slow/nondeterministic: network,
  time, filesystem, paid APIs. Never mock the thing under test — that just
  tests the mock.
- Prefer a real in-memory SQLite over a mocked DB; fakes drift from reality.

## Run it and read the output
- Always run the suite after writing tests and after any fix — never claim
  passing without seeing green. On failure, read the assertion diff, fix the
  CODE (or the test if the expectation was wrong), re-run.
- Add the run command to the README so the user can reproduce it.
- A test you can't make pass is telling you the code is wrong — listen to it,
  don't delete the test.

## Verify
- `pytest -q` / `npx vitest run` exits 0 with every test green, and the count
  is what you expect (a suite that "passes" with 0 collected tests is broken —
  check the file names and paths).
