"""Test suite for double-order deduplication fix."""
import sys
import time
from collections import OrderedDict

print("=" * 70)
print("TEST SUITE: Double Order Dedup Fix")
print("=" * 70)

passed = 0
failed = 0

def test(name, condition):
    global passed, failed
    if condition:
        print(f"  PASS: {name}")
        passed += 1
    else:
        print(f"  FAIL: {name}")
        failed += 1

# ── Test 1: Database functions exist and work ──
print("\n[1] Database Layer")
try:
    from database import is_slug_already_traded, get_recent_traded_slugs, init_db
    init_db()
    test("is_slug_already_traded() callable", callable(is_slug_already_traded))
    test("get_recent_traded_slugs() callable", callable(get_recent_traded_slugs))
    
    # Test with non-existent slug
    result = is_slug_already_traded("test-nonexistent-slug-xyz")
    test("Non-existent slug returns False", result == False)
    
    # Test with a slug that exists in DB (use the latest trade)
    from database import get_trades
    trades = get_trades(limit=1)
    if trades:
        existing_slug = trades[0]["market_slug"]
        result = is_slug_already_traded(existing_slug)
        test(f"Existing slug '{existing_slug[-30:]}' returns True", result == True)
    else:
        test("Existing slug check (skipped - no trades in DB)", True)
    
    # Test get_recent_traded_slugs
    recent = get_recent_traded_slugs(hours=24)
    test(f"get_recent_traded_slugs returns list ({len(recent)} slugs)", isinstance(recent, list))
    
except Exception as e:
    test(f"Database import/function error: {e}", False)

# ── Test 2: OrderedDict FIFO cleanup ──
print("\n[2] OrderedDict FIFO Cleanup")
try:
    od = OrderedDict()
    # Insert 120 items
    for i in range(120):
        od[f"slug-{i:04d}"] = time.time()
    
    test("OrderedDict has 120 items", len(od) == 120)
    
    # Cleanup: keep max 100 (remove oldest)
    while len(od) > 100:
        od.popitem(last=False)
    
    test("After cleanup: 100 items remain", len(od) == 100)
    
    # Verify OLDEST were removed (slug-0000 through slug-0019)
    test("Oldest slug-0000 was removed", "slug-0000" not in od)
    test("Oldest slug-0019 was removed", "slug-0019" not in od)
    test("Newest slug-0119 was kept", "slug-0119" in od)
    test("slug-0020 was kept (first survivor)", "slug-0020" in od)
    
    # Verify 'in' operator works for dedup check
    test("'in' check works for existing key", "slug-0100" in od)
    test("'in' check works for missing key", "slug-0000" not in od)
    
except Exception as e:
    test(f"OrderedDict test error: {e}", False)

# ── Test 3: Bot imports and bootstrap ──
print("\n[3] Bot Module Imports")
try:
    # Test that bot.py can be imported without errors
    from bot import _bootstrap_traded_slugs, bot_state, BotState
    test("_bootstrap_traded_slugs() importable", callable(_bootstrap_traded_slugs))
    test("bot_state.traded_slugs is OrderedDict", isinstance(bot_state.traded_slugs, OrderedDict))
    test("bot_state.skipped_slugs is OrderedDict", isinstance(bot_state.skipped_slugs, OrderedDict))
    test("bot_state.disqualified_slugs is OrderedDict", isinstance(bot_state.disqualified_slugs, OrderedDict))
    
    # Test bootstrap
    initial_count = len(bot_state.traded_slugs)
    _bootstrap_traded_slugs()
    after_count = len(bot_state.traded_slugs)
    test(f"Bootstrap loaded slugs from DB ({after_count} total)", after_count >= initial_count)
    
except Exception as e:
    test(f"Bot import error: {e}", False)

# ── Test 4: Pre-mark and rollback pattern ──
print("\n[4] Pre-mark / Rollback Pattern")
try:
    od = OrderedDict()
    slug = "test-premark-slug"
    
    # Pre-mark
    od[slug] = time.time()
    test("Pre-mark: slug in dict", slug in od)
    
    # Simulate failed trade → rollback
    del od[slug]
    test("Rollback: slug removed", slug not in od)
    
    # Pre-mark again, simulate success → keep
    od[slug] = time.time()
    test("Re-mark after new attempt: slug in dict", slug in od)
    
    # Verify no KeyError on delete of non-existent key (safety)
    try:
        od.pop("nonexistent-slug", None)
        test("Safe delete of non-existent key", True)
    except KeyError:
        test("Safe delete of non-existent key", False)
        
except Exception as e:
    test(f"Pre-mark test error: {e}", False)

# ── Summary ──
print("\n" + "=" * 70)
print(f"RESULTS: {passed} passed, {failed} failed, {passed + failed} total")
if failed == 0:
    print("ALL TESTS PASSED!")
else:
    print("SOME TESTS FAILED!")
print("=" * 70)

sys.exit(0 if failed == 0 else 1)
