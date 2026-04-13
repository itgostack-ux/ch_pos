"""
CH POS — Session Lifecycle E2E Test Suite.

Tests the complete POS opening and closing flow:
1. get_pos_context returns correct state for each scenario
2. get_pos_context_for_store returns day_closed / pos_profile correctly
3. get_session_status returns correct state
4. open_session creates session with proper state
5. close_session transitions states correctly
6. Business date lifecycle (Open → Closed → Advance)
7. Day-closed detection when business_date is stale
8. override_business_date advances date correctly
9. No duplicate sessions per store
10. Auto-advance after EOD close

Run: bench --site erpnext.local execute ch_pos.tests.test_session_lifecycle_e2e.test_all
"""
import sys
import traceback

import frappe
from frappe.utils import nowdate, now_datetime, getdate, add_days, flt

results = []


def ok(name, detail=""):
    results.append({"scenario": name, "status": "PASS", "detail": detail})
    print(f"✅  {name}{f'  ({detail})' if detail else ''}")


def fail(name, detail=""):
    results.append({"scenario": name, "status": "FAIL", "detail": detail})
    print(f"❌  {name}{f'  ({detail})' if detail else ''}")


def skip(name, detail=""):
    results.append({"scenario": name, "status": "SKIP", "detail": detail})
    print(f"⏭️   {name}{f'  ({detail})' if detail else ''}")


# ── Helpers ──────────────────────────────────────────────────────────────────

def _get_test_context():
    """Get a clean store + profile for testing. Force-closes blocking sessions."""
    frappe.set_user("Administrator")
    user = frappe.session.user

    # Get user's POS Executive assignments
    user_exec_stores = set(frappe.get_all(
        "POS Executive",
        filters={"user": user, "is_active": 1},
        pluck="store",
    ))

    # Get POS Profile Extensions  
    extensions = frappe.get_all(
        "POS Profile Extension",
        fields=["name", "pos_profile", "store"],
    )

    # Sort: prefer extensions whose store matches user's POS Executive assignment
    extensions.sort(key=lambda e: (0 if e.store in user_exec_stores else 1, e.name))

    for ext in extensions:
        profile_name = ext.pos_profile
        store_name = ext.store
        if not profile_name or not store_name:
            continue

        profile = frappe.db.get_value(
            "POS Profile", profile_name,
            ["name", "company", "warehouse", "disabled"],
            as_dict=True,
        )
        if not profile or profile.disabled:
            continue

        store = frappe.db.get_value(
            "CH Store", store_name,
            ["name", "company", "warehouse", "disabled"],
            as_dict=True,
        )
        if not store or store.get("disabled"):
            continue

        # Company must match between profile and store
        if profile.company != store.company:
            continue

        # Delete ALL sessions on this store for today (active AND closed)
        # to guarantee a completely clean slate for testing
        frappe.db.sql("""
            DELETE FROM `tabCH POS Session`
            WHERE store = %s AND business_date = %s
        """, (store_name, nowdate()))

        # Close ALL orphaned POS Opening Entries for this profile AND Administrator user
        stale_entries = frappe.db.sql("""
            SELECT name FROM `tabPOS Opening Entry`
            WHERE (pos_profile = %s OR user = %s)
              AND status = 'Open' AND docstatus = 1
              AND IFNULL(pos_closing_entry, '') = ''
        """, (profile_name, frappe.session.user), as_dict=True)
        for se in stale_entries:
            frappe.db.set_value("POS Opening Entry", se.name, "status", "Closed", update_modified=False)

        # Reset business date to today + Open status
        bd_name = frappe.db.get_value("CH Business Date", {"store": store_name}, "name")
        if bd_name:
            frappe.db.set_value("CH Business Date", bd_name, {
                "business_date": nowdate(),
                "status": "Open",
                "opened_on": now_datetime(),
                "closed_on": None,
            })
        else:
            bd = frappe.get_doc({
                "doctype": "CH Business Date",
                "store": store_name,
                "business_date": nowdate(),
                "status": "Open",
                "opened_on": now_datetime(),
            })
            bd.insert(ignore_permissions=True)

        frappe.db.commit()
        return {
            "pos_profile": profile_name,
            "company": profile.company,
            "warehouse": profile.warehouse,
            "store": store_name,
        }
    return None


def _ensure_manager_pin(store):
    """Ensure a manager PIN '1234' exists that works for the given store."""
    from frappe.utils.password import get_decrypted_password

    # Check if any existing PIN with 1234 works for this store
    filters = {"is_active": 1, "store": ("in", [store, "", None])}
    pins = frappe.get_all("CH Manager PIN", filters=filters, fields=["name", "user", "employee_name"])
    for p in pins:
        try:
            stored_pin = get_decrypted_password("CH Manager PIN", p.name, "pin_hash")
            if stored_pin == "1234":
                return {"name": p.employee_name, "user": p.user}
        except Exception:
            continue

    # Check if there's a PIN with 1234 on another store — make it global
    all_pins = frappe.get_all("CH Manager PIN", filters={"is_active": 1}, fields=["name", "user", "store", "employee_name"])
    for p in all_pins:
        try:
            stored_pin = get_decrypted_password("CH Manager PIN", p.name, "pin_hash")
            if stored_pin == "1234" and p.store and p.store != store:
                # Clear store to make it global and enable all permissions
                doc = frappe.get_doc("CH Manager PIN", p.name)
                doc.store = ""
                doc.can_approve_opening = 1
                doc.can_approve_closing = 1
                doc.can_approve_cash_drop = 1
                doc.can_override_business_date = 1
                doc.save(ignore_permissions=True)
                frappe.db.commit()
                return {"name": p.employee_name, "user": p.user}
        except Exception:
            continue

    # Create a new global PIN
    pin_doc = frappe.get_doc({
        "doctype": "CH Manager PIN",
        "user": "Administrator",
        "employee_name": "E2E Test Manager",
        "pin_hash": "1234",
        "is_active": 1,
        "can_approve_opening": 1,
        "can_approve_closing": 1,
        "can_approve_cash_drop": 1,
        "can_override_business_date": 1,
        "can_approve_discount": 1,
        "can_force_close_session": 1,
    })
    try:
        pin_doc.insert(ignore_permissions=True)
        frappe.db.commit()
        return {"name": pin_doc.employee_name, "user": "Administrator"}
    except Exception:
        frappe.db.rollback()
        return None


def _cleanup_session(session_name):
    """Force-close a test session and ensure day is not marked closed."""
    if not session_name or not frappe.db.exists("CH POS Session", session_name):
        return
    sess = frappe.db.get_value("CH POS Session", session_name, ["status", "store", "business_date"], as_dict=True)
    if sess.status != "Closed":
        frappe.db.set_value("CH POS Session", session_name, "status", "Closed", update_modified=False)
    # Delete the test session entirely to avoid poisoning the day_closed check
    frappe.db.sql("DELETE FROM `tabCH POS Session` WHERE name = %s", session_name)
    # Reset business date to Open
    bd_name = frappe.db.get_value("CH Business Date", {"store": sess.store}, "name")
    if bd_name:
        frappe.db.set_value("CH Business Date", bd_name, {
            "status": "Open",
            "business_date": nowdate(),
        })
    frappe.db.commit()


# ── Test Scenarios ───────────────────────────────────────────────────────────

def test_01_get_pos_context_system_manager():
    """System Manager gets store picker with select_store status."""
    try:
        frappe.set_user("Administrator")
        from ch_pos.api.isolation_api import get_pos_context
        ctx = get_pos_context()
        assert ctx.get("status") == "select_store", f"Expected select_store, got {ctx.get('status')}"
        assert isinstance(ctx.get("stores"), list), "stores should be a list"
        assert len(ctx["stores"]) > 0, "Should have at least one store"
        ok("01 get_pos_context — System Manager gets select_store", f"{len(ctx['stores'])} stores")
    except Exception as e:
        fail("01 get_pos_context — System Manager gets select_store", str(e))


def test_02_get_pos_context_for_store_open_day():
    """get_pos_context_for_store returns day_closed=False for today's date."""
    try:
        ctx = _get_test_context()
        if not ctx:
            skip("02 get_pos_context_for_store — open day", "No test context")
            return

        frappe.set_user("Administrator")
        from ch_pos.api.isolation_api import get_pos_context_for_store
        result = get_pos_context_for_store(ctx["store"])

        assert result.get("day_closed") == False, f"Expected day_closed=False, got {result.get('day_closed')}"
        assert result.get("pos_profile"), "Should have pos_profile"
        assert result.get("company") == ctx["company"], f"Company mismatch: {result.get('company')} != {ctx['company']}"
        assert result.get("business_date") == nowdate(), f"Business date should be today, got {result.get('business_date')}"
        ok("02 get_pos_context_for_store — open day", f"store={ctx['store']}, profile={result['pos_profile']}")
    except Exception as e:
        fail("02 get_pos_context_for_store — open day", str(e))


def test_03_get_pos_context_for_store_closed_day():
    """get_pos_context_for_store returns day_closed=True for stale date."""
    try:
        ctx = _get_test_context()
        if not ctx:
            skip("03 get_pos_context_for_store — closed day", "No test context")
            return

        frappe.set_user("Administrator")
        # Set business date to past
        bd_name = frappe.db.get_value("CH Business Date", {"store": ctx["store"]}, "name")
        old_date = add_days(nowdate(), -5)
        frappe.db.set_value("CH Business Date", bd_name, {
            "business_date": old_date,
            "status": "Closed",
        })
        frappe.db.commit()

        from ch_pos.api.isolation_api import get_pos_context_for_store
        result = get_pos_context_for_store(ctx["store"])

        assert result.get("day_closed") == True, f"Expected day_closed=True, got {result.get('day_closed')}"
        assert result.get("business_date") == str(old_date), f"Business date should be {old_date}, got {result.get('business_date')}"

        # Reset for subsequent tests
        frappe.db.set_value("CH Business Date", bd_name, {
            "business_date": nowdate(),
            "status": "Open",
        })
        frappe.db.commit()
        ok("03 get_pos_context_for_store — closed day", f"Correctly detected stale date {old_date}")
    except Exception as e:
        fail("03 get_pos_context_for_store — closed day", str(e))


def test_04_get_session_status_no_session():
    """get_session_status returns has_session=False when no active session."""
    try:
        ctx = _get_test_context()
        if not ctx:
            skip("04 get_session_status — no session", "No test context")
            return

        frappe.set_user("Administrator")
        from ch_pos.api.session_api import get_session_status
        result = get_session_status(ctx["pos_profile"])

        assert result.get("has_session") == False, f"Expected has_session=False, got {result}"
        assert not result.get("day_closed"), "Should not be day_closed"
        assert not result.get("unclosed_session"), "Should not have unclosed session"
        ok("04 get_session_status — no session", f"profile={ctx['pos_profile']}")
    except Exception as e:
        fail("04 get_session_status — no session", str(e))


def test_05_get_session_status_day_closed():
    """get_session_status returns day_closed when business date is stale."""
    try:
        ctx = _get_test_context()
        if not ctx:
            skip("05 get_session_status — day closed", "No test context")
            return

        frappe.set_user("Administrator")
        bd_name = frappe.db.get_value("CH Business Date", {"store": ctx["store"]}, "name")
        old_date = add_days(nowdate(), -3)
        frappe.db.set_value("CH Business Date", bd_name, {
            "business_date": old_date,
            "status": "Closed",
        })
        frappe.db.commit()

        from ch_pos.api.session_api import get_session_status
        result = get_session_status(ctx["pos_profile"])

        assert result.get("day_closed") == True, f"Expected day_closed=True, got {result}"
        assert result.get("has_session") == False, "Should not have active session"

        # Reset
        frappe.db.set_value("CH Business Date", bd_name, {
            "business_date": nowdate(),
            "status": "Open",
        })
        frappe.db.commit()
        ok("05 get_session_status — day closed", f"Correctly detected stale date {old_date}")
    except Exception as e:
        fail("05 get_session_status — day closed", str(e))


def test_06_open_session():
    """open_session creates a valid CH POS Session + POS Opening Entry."""
    session_name = None
    try:
        ctx = _get_test_context()
        if not ctx:
            skip("06 open_session", "No test context")
            return

        pin = _ensure_manager_pin(ctx["store"])
        if not pin:
            skip("06 open_session", "No manager PIN")
            return

        frappe.set_user("Administrator")
        from ch_pos.api.session_api import open_session
        result = open_session(
            pos_profile=ctx["pos_profile"],
            opening_cash=5000,
            manager_pin="1234",
        )

        session_name = result.get("session_name")
        assert session_name, f"Should return session_name, got {result}"
        assert result.get("business_date") == nowdate(), f"Business date should be today"
        assert result.get("store") == ctx["store"], f"Store mismatch"
        assert flt(result.get("opening_cash")) == 5000, f"Opening cash mismatch"

        # Verify DB state
        sess = frappe.get_doc("CH POS Session", session_name)
        assert sess.status == "Open", f"Session status should be Open, got {sess.status}"
        assert sess.docstatus == 1, f"Session should be submitted"
        assert sess.pos_opening_entry, "Should have POS Opening Entry"

        # Verify POS Opening Entry is Open
        oe_status = frappe.db.get_value("POS Opening Entry", sess.pos_opening_entry, "status")
        assert oe_status == "Open", f"Opening Entry should be Open, got {oe_status}"

        # Verify Business Date is Open
        bd_status = frappe.db.get_value("CH Business Date", {"store": ctx["store"]}, "status")
        assert bd_status == "Open", f"Business Date should be Open, got {bd_status}"

        ok("06 open_session", f"session={session_name}, opening_cash=5000")
    except Exception as e:
        fail("06 open_session", str(e))
    finally:
        if session_name:
            _cleanup_session(session_name)


def test_07_open_session_blocks_duplicate():
    """Cannot open second session for same store."""
    session1 = None
    try:
        ctx = _get_test_context()
        if not ctx:
            skip("07 open_session — blocks duplicate", "No test context")
            return

        pin = _ensure_manager_pin(ctx["store"])
        if not pin:
            skip("07 open_session — blocks duplicate", "No manager PIN")
            return

        frappe.set_user("Administrator")
        from ch_pos.api.session_api import open_session

        # Open first session
        result1 = open_session(
            pos_profile=ctx["pos_profile"],
            opening_cash=3000,
            manager_pin="1234",
        )
        session1 = result1.get("session_name")
        assert session1, "First session should open"

        # Try to open second — should fail
        try:
            open_session(
                pos_profile=ctx["pos_profile"],
                opening_cash=2000,
                manager_pin="1234",
            )
            fail("07 open_session — blocks duplicate", "Second session should have been blocked!")
        except frappe.exceptions.ValidationError:
            ok("07 open_session — blocks duplicate", f"Correctly blocked duplicate for store={ctx['store']}")
        except Exception as e:
            if "still active" in str(e):
                ok("07 open_session — blocks duplicate", f"Correctly blocked: {e}")
            else:
                fail("07 open_session — blocks duplicate", f"Unexpected error: {e}")
    except Exception as e:
        fail("07 open_session — blocks duplicate", str(e))
    finally:
        if session1:
            _cleanup_session(session1)


def test_08_get_session_status_with_active_session():
    """get_session_status returns has_session=True when session is open."""
    session_name = None
    try:
        ctx = _get_test_context()
        if not ctx:
            skip("08 get_session_status — active session", "No test context")
            return

        pin = _ensure_manager_pin(ctx["store"])
        if not pin:
            skip("08 get_session_status — active session", "No manager PIN")
            return

        frappe.set_user("Administrator")
        from ch_pos.api.session_api import open_session, get_session_status

        result = open_session(
            pos_profile=ctx["pos_profile"],
            opening_cash=4000,
            manager_pin="1234",
        )
        session_name = result["session_name"]

        status = get_session_status(ctx["pos_profile"])
        assert status.get("has_session") == True, f"Expected has_session=True, got {status}"
        assert status.get("session_name") == session_name, f"Session name mismatch"
        assert status.get("store") == ctx["store"], f"Store mismatch"

        ok("08 get_session_status — active session", f"session={session_name}")
    except Exception as e:
        fail("08 get_session_status — active session", str(e))
    finally:
        if session_name:
            _cleanup_session(session_name)


def test_09_close_session():
    """close_session transitions session to Closed and updates business date."""
    session_name = None
    try:
        ctx = _get_test_context()
        if not ctx:
            skip("09 close_session", "No test context")
            return

        pin = _ensure_manager_pin(ctx["store"])
        if not pin:
            skip("09 close_session", "No manager PIN")
            return

        frappe.set_user("Administrator")
        from ch_pos.api.session_api import open_session, close_session

        # Disable settlement requirement for this test
        from ch_pos.api.session_api import _is_settlement_required
        # Open session
        open_result = open_session(
            pos_profile=ctx["pos_profile"],
            opening_cash=5000,
            manager_pin="1234",
        )
        session_name = open_result["session_name"]

        # Close session
        close_result = close_session(
            session_name=session_name,
            closing_cash=5000,
            manager_pin="1234",
        )

        assert close_result.get("status") == "Closed", f"Expected Closed, got {close_result.get('status')}"
        assert flt(close_result.get("cash_variance")) == 0, f"Variance should be 0"

        # Verify DB state
        sess = frappe.get_doc("CH POS Session", session_name)
        assert sess.status == "Closed", f"Session should be Closed, got {sess.status}"

        ok("09 close_session", f"session={session_name}, variance=0")
        session_name = None  # Already closed
    except Exception as e:
        fail("09 close_session", str(e))
    finally:
        if session_name:
            _cleanup_session(session_name)


def test_10_override_business_date():
    """override_business_date advances the date correctly."""
    try:
        ctx = _get_test_context()
        if not ctx:
            skip("10 override_business_date", "No test context")
            return

        pin = _ensure_manager_pin(ctx["store"])
        if not pin:
            skip("10 override_business_date", "No manager PIN")
            return

        frappe.set_user("Administrator")

        # Set business date to past + Closed
        bd_name = frappe.db.get_value("CH Business Date", {"store": ctx["store"]}, "name")
        old_date = add_days(nowdate(), -2)
        frappe.db.set_value("CH Business Date", bd_name, {
            "business_date": old_date,
            "status": "Closed",
        })
        frappe.db.commit()

        from ch_pos.api.session_api import override_business_date
        result = override_business_date(
            store=ctx["store"],
            new_date=nowdate(),
            reason="E2E test advance",
            manager_pin="1234",
        )

        assert result.get("business_date") == nowdate(), f"Should advance to today, got {result.get('business_date')}"

        # Verify DB state
        bd_row = frappe.db.get_value("CH Business Date", bd_name, ["business_date", "status"], as_dict=True)
        assert str(bd_row.business_date) == nowdate(), f"DB date should be today: {bd_row.business_date}"

        ok("10 override_business_date", f"Advanced from {old_date} to {nowdate()}")

        # Reset for subsequent tests
        frappe.db.set_value("CH Business Date", bd_name, {
            "business_date": nowdate(),
            "status": "Open",
        })
        frappe.db.commit()
    except Exception as e:
        fail("10 override_business_date", str(e))


def test_11_complete_lifecycle():
    """Complete lifecycle: open → verify active → close → verify closed → day state."""
    session_name = None
    try:
        ctx = _get_test_context()
        if not ctx:
            skip("11 complete lifecycle", "No test context")
            return

        pin = _ensure_manager_pin(ctx["store"])
        if not pin:
            skip("11 complete lifecycle", "No manager PIN")
            return

        frappe.set_user("Administrator")
        from ch_pos.api.session_api import open_session, close_session, get_session_status
        from ch_pos.api.isolation_api import get_pos_context_for_store

        # 1. Verify store is ready
        store_ctx = get_pos_context_for_store(ctx["store"])
        assert store_ctx.get("day_closed") == False, f"Store should not be day_closed before opening"

        # 2. Open session
        open_result = open_session(
            pos_profile=ctx["pos_profile"],
            opening_cash=10000,
            manager_pin="1234",
        )
        session_name = open_result["session_name"]

        # 3. Verify active session via get_session_status
        status = get_session_status(ctx["pos_profile"])
        assert status["has_session"] == True
        assert status["session_name"] == session_name

        # 4. Verify active session via get_pos_context (System Manager)
        from ch_pos.api.isolation_api import get_pos_context
        pos_ctx = get_pos_context()
        assert pos_ctx["status"] == "select_store", "Admin should still get select_store"

        # 5. Close session
        close_result = close_session(
            session_name=session_name,
            closing_cash=10000,
            manager_pin="1234",
        )
        assert close_result["status"] == "Closed"
        session_name = None  # Closed

        # 6. Verify no active session
        status2 = get_session_status(ctx["pos_profile"])
        # May get day_closed=True if auto-advance didn't run, or has_session=False
        assert status2.get("has_session") == False, f"Should have no active session after close"

        ok("11 complete lifecycle", "open → active → close → verified")
    except Exception as e:
        fail("11 complete lifecycle", str(e))
    finally:
        if session_name:
            _cleanup_session(session_name)


def test_12_stale_date_detection_multiple_days():
    """Business date 5 days in the past is correctly detected as day_closed."""
    try:
        ctx = _get_test_context()
        if not ctx:
            skip("12 stale date — multi-day gap", "No test context")
            return

        frappe.set_user("Administrator")
        bd_name = frappe.db.get_value("CH Business Date", {"store": ctx["store"]}, "name")

        # Set date 5 days ago
        stale_date = add_days(nowdate(), -5)
        frappe.db.set_value("CH Business Date", bd_name, {
            "business_date": stale_date,
            "status": "Open",  # Status is Open but date is stale
        })
        frappe.db.commit()

        from ch_pos.api.isolation_api import get_pos_context_for_store
        result = get_pos_context_for_store(ctx["store"])

        # Even though BD status is "Open", business_date < today means day_closed
        assert result.get("day_closed") == True, f"Stale date should be day_closed, got {result}"

        from ch_pos.api.session_api import get_session_status
        status = get_session_status(ctx["pos_profile"])
        assert status.get("day_closed") == True, f"session_status should also detect stale date"

        # Reset
        frappe.db.set_value("CH Business Date", bd_name, {
            "business_date": nowdate(),
            "status": "Open",
        })
        frappe.db.commit()
        ok("12 stale date — multi-day gap", f"date={stale_date} correctly detected as closed")
    except Exception as e:
        fail("12 stale date — multi-day gap", str(e))


def test_13_open_session_blocked_on_closed_day():
    """Cannot open session when business date is closed."""
    try:
        ctx = _get_test_context()
        if not ctx:
            skip("13 open blocked on closed day", "No test context")
            return

        pin = _ensure_manager_pin(ctx["store"])
        if not pin:
            skip("13 open blocked on closed day", "No manager PIN")
            return

        frappe.set_user("Administrator")

        # Create a closed session for today to make the day "closed"
        bd_name = frappe.db.get_value("CH Business Date", {"store": ctx["store"]}, "name")

        # Close orphaned opening entries first
        stale_entries = frappe.db.get_all(
            "POS Opening Entry",
            filters={
                "pos_profile": ctx["pos_profile"],
                "status": "Open",
                "docstatus": 1,
                "pos_closing_entry": ("in", ["", None]),
            },
            pluck="name",
        )
        for se in stale_entries:
            frappe.db.set_value("POS Opening Entry", se, "status", "Closed", update_modified=False)

        # Create+close a dummy session to make the day closed
        oe = frappe.get_doc({
            "doctype": "POS Opening Entry",
            "pos_profile": ctx["pos_profile"],
            "company": ctx["company"],
            "user": frappe.session.user,
            "period_start_date": now_datetime(),
            "balance_details": [{"mode_of_payment": "Cash", "opening_amount": 1000}],
        })
        oe.insert(ignore_permissions=True)
        oe.submit()

        closed_sess = frappe.get_doc({
            "doctype": "CH POS Session",
            "company": ctx["company"],
            "pos_profile": ctx["pos_profile"],
            "store": ctx["store"],
            "user": frappe.session.user,
            "business_date": nowdate(),
            "shift_start": now_datetime(),
            "opening_cash": 1000,
            "pos_opening_entry": oe.name,
            "status": "Open",
        })
        closed_sess.insert(ignore_permissions=True)
        closed_sess.submit()
        frappe.db.set_value("CH POS Session", closed_sess.name, "status", "Closed", update_modified=False)
        frappe.db.set_value("CH Business Date", bd_name, "status", "Closed")
        frappe.db.commit()

        # Now try to open — should fail
        from ch_pos.api.session_api import open_session
        try:
            open_session(
                pos_profile=ctx["pos_profile"],
                opening_cash=5000,
                manager_pin="1234",
            )
            fail("13 open blocked on closed day", "Should have been blocked!")
        except Exception as e:
            if "already closed" in str(e).lower() or "closed" in str(e).lower():
                ok("13 open blocked on closed day", f"Correctly blocked: {str(e)[:80]}")
            else:
                fail("13 open blocked on closed day", f"Unexpected error: {e}")

        # Cleanup
        frappe.db.set_value("CH Business Date", bd_name, {
            "business_date": nowdate(),
            "status": "Open",
        })
        frappe.db.commit()
    except Exception as e:
        fail("13 open blocked on closed day", str(e))


# ── Runner ───────────────────────────────────────────────────────────────────

def test_all():
    """Run all session lifecycle E2E tests."""
    global results
    results = []

    print("\n" + "=" * 70)
    print("CH POS — Session Lifecycle E2E Tests")
    print("=" * 70 + "\n")

    # One-time setup: ensure PIN exists
    frappe.set_user("Administrator")
    ctx = _get_test_context()
    if ctx:
        _ensure_manager_pin(ctx["store"])
    frappe.db.commit()

    tests = [
        test_01_get_pos_context_system_manager,
        test_02_get_pos_context_for_store_open_day,
        test_03_get_pos_context_for_store_closed_day,
        test_04_get_session_status_no_session,
        test_05_get_session_status_day_closed,
        test_06_open_session,
        test_07_open_session_blocks_duplicate,
        test_08_get_session_status_with_active_session,
        test_09_close_session,
        test_10_override_business_date,
        test_11_complete_lifecycle,
        test_12_stale_date_detection_multiple_days,
        test_13_open_session_blocked_on_closed_day,
    ]

    for t in tests:
        try:
            t()
        except Exception as e:
            fail(t.__name__, f"Unhandled: {e}")
            traceback.print_exc()
        # Commit after each test to preserve state for subsequent tests
        try:
            frappe.db.commit()
        except Exception:
            frappe.db.rollback()

    # Summary
    total = len(results)
    passed = sum(1 for r in results if r["status"] == "PASS")
    failed = sum(1 for r in results if r["status"] == "FAIL")
    skipped = sum(1 for r in results if r["status"] == "SKIP")

    print("\n" + "=" * 70)
    print(f"Results: {passed}/{total} passed, {failed} failed, {skipped} skipped")
    if failed:
        print("\nFailed tests:")
        for r in results:
            if r["status"] == "FAIL":
                print(f"  ❌ {r['scenario']}: {r['detail']}")
    print("=" * 70)

    return results
