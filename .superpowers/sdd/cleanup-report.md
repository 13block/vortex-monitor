# Cleanup Report — consolidated minor review findings

## Changes

| # | File | Line(s) | Change |
|---|------|---------|--------|
| 1 | `detector/classifier.py` | 22 | `cfg=DevConfig()` → `cfg=None`; added `if cfg is None: cfg = DevConfig()` as first body line (mutable default arg fix) |
| 2 | `detector/classifier.py` | 28–36 | Split five `score += …; reasons.append(…)` one-liners into two lines each (PEP8) |
| 3 | `tests/test_classifier.py` | 34 | Added `self.assertEqual(v.reasons, [])` to `test_untraded_scores_zero` |
| 4 | `detector/detector.py` | 1 | `from dataclasses import dataclass, field` → `from dataclasses import dataclass` (unused `field` removed) |
| 5 | `detector/detector.py` | 51–55 | Removed docstring from `_cov`; body is now the two-line minimal form |
| 6 | `tests/test_detector.py` | 63–64 | Added `self.assertIn(DEV, ws)` and `self.assertEqual(len(ws), 2)` to `test_window_returns_distinct_buyers_in_span` |
| 7 | `detector/jobs.py` | 22, 25 | Both `return self._state[ca]` → `return dict(self._state[ca])` (return a copy, matching `status()`) |
| 8 | `tests/test_jobs.py` | 37–52 | Added `test_resubmit_after_done_starts_new_job` — submits same CA twice, asserts counter increments on second run |
| 9 | `monitor.py` | 5 | Hoisted `from urllib.parse import urlparse, parse_qs` to top import block |
| 9 | `monitor.py` | 308, 315 | Removed duplicated inline `from urllib.parse import urlparse, parse_qs` inside `/analyze` and `/detection` handlers |
| 10 | `monitor.py` | 53–54 | Wrapped `RECORDS.get(ca)` in `with LOCK:` inside `_run_detection` |

## Full suite

Command:
```
python -m unittest discover -s tests -v
```

Output (abbreviated):
```
Ran 25 tests in 0.241s
OK
```

All 25 tests passed, pristine output (no errors, no failures, no warnings).

## Import smoke

Command:
```
python -c "import monitor"
```

Result: no output, exit 0. Import succeeds without `HELIUS_KEY`.
