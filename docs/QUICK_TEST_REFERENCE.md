# Quick Test Reference

## Running Tests

### All tests
```bash
cd /home/cort/hblink4
source venv/bin/activate
python3 -m pytest tests/ -v
```

### Just routing optimization tests
```bash
python3 tests/test_routing_optimization.py
```

### Specific test
```bash
python3 -m pytest tests/test_routing_optimization.py::test_config_intersection -v
```

### With coverage
```bash
python3 -m pytest tests/ --cov=hblink4 --cov-report=html
```

---

## Test Status

| Test File | Tests | Status |
|-----------|-------|--------|
| test_routing_optimization.py | 8 | ✅ All Pass |
| test_access_control.py | 9 | ✅ All Pass |
| test_hang_time.py | 3 | ✅ All Pass |
| test_stream_tracking.py | 2 | ✅ All Pass |
| test_terminator_detection.py | 6 | ✅ All Pass |
| test_user_cache.py | 10 | ✅ All Pass |
| **TOTAL** | **38** | **✅ All Pass** |

---

## What's Tested

### Routing Optimization (NEW)
- ✅ Set-based TG lookups (O(1) performance)
- ✅ Routing cache (calculate once per stream)
- ✅ RPTO parsing (multiple formats)
- ✅ Config intersection (config is master)
- ✅ Rejected TG detection
- ✅ Stream start routing calculation
- ✅ Slot availability checks
- ✅ Performance improvement (83% reduction)

### Access Control
- ✅ Repeater ID matching
- ✅ Callsign matching
- ✅ Range matching
- ✅ Blacklist support
- ✅ Priority handling

### Stream Tracking
- ✅ Stream state lifecycle
- ✅ Timeout detection
- ✅ Hang time management
- ✅ Slot management

### Other
- ✅ Terminator detection
- ✅ User cache
- ✅ RSSI tracking

---

## Before Deploying

```bash
# 1. Syntax check
python3 -m py_compile hblink4/hblink.py

# 2. Run all tests
python3 -m pytest tests/ -v

# 3. Check for errors
echo "If all tests pass, ready to deploy!"
```

---

## Expected Output

```
============================================================
ROUTING OPTIMIZATION TEST SUITE
============================================================

=== Testing Set-Based TG Storage ===
✓ TG sets initialized correctly
  TS1: [1, 2, 3, 9]
  TS2: [4, 5, 6, 9]
✓ Set intersection works: {1000, 1, 2, 999} & config = {1, 2}
Set-Based TG Storage tests passed!

[... more tests ...]

============================================================
RESULTS: 8 passed, 0 failed
============================================================
```

---

## Troubleshooting

### Import errors
```bash
# Make sure you're in the right directory and venv is activated
cd /home/cort/hblink4
source venv/bin/activate
```

### Test failures
```bash
# Run with verbose output
python3 -m pytest tests/test_routing_optimization.py -vv

# Run single test
python3 -m pytest tests/test_routing_optimization.py::test_name -vv
```

### Performance verification
```bash
# Look for this in test output:
# "✓ Reduction: 83.0% fewer operations"
# "✓ Performance improvement validated"
```

---

## Files

- `tests/test_routing_optimization.py` - New optimization tests (8 tests)
- `docs/TEST_COVERAGE.md` - Detailed coverage documentation
- `docs/QUICK_TEST_REFERENCE.md` - This file

---

Last Updated: October 4, 2025
