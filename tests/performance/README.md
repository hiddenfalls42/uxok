# Performance Tests

Run separately: `pytest tests/performance/ -v`

Requirements:
- Run on dedicated machine (minimal background processes)
- Ubuntu tested, should work on any Linux/macOS
- Baseline metrics vary by hardware
- Performance tests are marked with `@pytest.mark.performance` and excluded by default
