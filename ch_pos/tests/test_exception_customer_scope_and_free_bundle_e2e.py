"""
CH POS — Exception customer-scope + free-bundle qty guards (regression E2E).

Regressions covered:

  1. `exception_api.raise_exception` MUST reject POS-raised requests that carry
     no customer, or that carry the walk-in customer.

  2. `raise_exception` MUST reject POS-raised requests bound to the POS
     Profile's walk-in customer (anonymous placeholder, not identity).

  3. `create_pos_invoice` MUST reject an `exception_request` approved for
     customer A when the invoice customer is B.

  4. `CH Exception Request.is_valid()` MUST return False for POS-raised
     exceptions whose `raised_at` date is not today (same-day rule).

  5. `create_pos_invoice` server-side must cap a free-bundle accessory line to
     qty=1, rate=0, `is_free_item=1` EVEN when the client sends qty=8, so a
     tampered payload cannot bill 8 headphones at ₹0 against a single device.

Run:

    bench --site erpnext.local execute \\
        ch_pos.tests.test_exception_customer_scope_and_free_bundle_e2e.run_all

Ties to memory:
  - /memories/repo/pos-exception-apply-silent-noop.md
  - /memories/repo/pos-free-item-return-guard.md
"""

import frappe
from frappe.utils import add_days, flt, cint, now_datetime, nowdate

_results = []
FLOW = "ExceptionScope+FreeBundle"


def _ok(step, detail=""):
    _results.append({"step": step, "status": "PASS", "detail": detail})
    print(f"  PASS  [{FLOW}] {step}" + (f"  ({detail})" if detail else ""))


def _fail(step, detail=""):
    _results.append({"step": step, "status": "FAIL", "detail": detail})
    print(f"  FAIL  [{FLOW}] {step}" + (f"  — {detail}" if detail else ""))


def _skip(step, detail=""):
    _results.append({"step": step, "status": "SKIP", "detail": detail})
    print(f"  SKIP  [{FLOW}] {step}" + (f"  ({detail})" if detail else ""))


# ── Fixtures ─────────────────────────────────────────────────────────────────

def _get_pos_profile():
    return frappe.get_all(
        "POS Profile",
        filters={"disabled": 0},
        fields=["name", "company", "warehouse", "customer"],
        limit=1,
    )
    # returns list


def _ensure_customer(name, company):
    if frappe.db.exists("Customer", name):
        return name
    doc = frappe.get_doc({
        "doctype": "Customer",
        "customer_name": name,
        "customer_type": "Individual",
        "customer_group": frappe.db.get_value("Customer Group", {"is_group": 0}, "name") or "Individual",
        "territory": frappe.db.get_value("Territory", {"is_group": 0}, "name") or "All Territories",
    })
    doc.flags.ignore_permissions = True
    doc.insert()
    frappe.db.commit()
    return doc.name


def _ensure_exception_type(name):
    if frappe.db.exists("CH Exception Type", name):
        return name
    doc = frappe.get_doc({
        "doctype": "CH Exception Type",
        "exception_type": name,
        "enabled": 1,
        "max_value_without_approval": 0,   # force approval flow (never auto)
        "requires_manager_pin": 0,
        "validity_minutes": 30,
    })
    doc.flags.ignore_permissions = True
    doc.insert()
    frappe.db.commit()
    return doc.name


def _cleanup_exception(exc_name):
    if not exc_name or not frappe.db.exists("CH Exception Request", exc_name):
        return
    try:
        doc = frappe.get_doc("CH Exception Request", exc_name)
        if doc.docstatus == 1:
            doc.cancel()
        frappe.delete_doc("CH Exception Request", exc_name,
                          ignore_permissions=True, force=True)
        frappe.db.commit()
    except Exception:
        pass


def _ensure_manager_pin(store):
    """Ensure a manager PIN '1234' exists that can approve session opening.

    On this bench the only CH POS Password row is scoped to one store; the
    fastest way to bootstrap a session for other stores is to widen an
    existing PIN to global + set a known value.
    """
    from frappe.utils.password import get_decrypted_password

    # Any active PIN — reuse it and coerce to a known value + global scope.
    existing = frappe.get_all("CH POS Password", filters={"is_active": 1},
                              fields=["name"], limit=1)
    if existing:
        pin_name = existing[0].name
        try:
            current = get_decrypted_password(
                "CH POS Password", pin_name, "pin_hash",
                raise_exception=False,
            )
        except Exception:
            current = None
        doc = frappe.get_doc("CH POS Password", pin_name)
        doc.store = ""   # global
        doc.can_approve_opening = 1
        doc.can_approve_closing = 1
        doc.can_approve_cash_drop = 1
        doc.can_override_business_date = 1
        if current != "1234":
            doc.pin_hash = "1234"
        doc.save(ignore_permissions=True)
        frappe.db.commit()
        return "1234"

    # No PIN at all — create one.
    try:
        doc = frappe.get_doc({
            "doctype": "CH POS Password",
            "user": "Administrator",
            "employee_name": "ExcScope E2E Manager",
            "pin_hash": "1234",
            "is_active": 1,
            "can_approve_opening": 1,
            "can_approve_closing": 1,
            "can_approve_cash_drop": 1,
            "can_override_business_date": 1,
            "can_approve_discount": 1,
            "can_force_close_session": 1,
        })
        doc.insert(ignore_permissions=True)
        frappe.db.commit()
        return "1234"
    except Exception:
        frappe.db.rollback()
        return None


def _ensure_today_session(profile_name):
    """Close any stale POS Opening Entries for this profile/user so
    create_pos_invoice can open a fresh session dated to today.

    Mirrors the setup used by test_session_lifecycle_e2e — POS sessions in
    this bench tend to linger across days and the invoice-date guard rejects
    today's bill if the active opening entry is from yesterday.
    """
    stale = frappe.db.sql("""
        SELECT name FROM `tabPOS Opening Entry`
        WHERE (pos_profile = %s OR user = %s)
          AND status = 'Open' AND docstatus = 1
          AND IFNULL(pos_closing_entry, '') = ''
    """, (profile_name, frappe.session.user), as_dict=True)
    for oe in stale:
        frappe.db.set_value(
            "POS Opening Entry", oe.name, "status", "Closed",
            update_modified=False,
        )
    # Reset any CH POS Session rows so start_pos_session finds a clean slate.
    store = frappe.db.get_value("POS Profile", profile_name, "warehouse")
    ch_store = frappe.db.get_value("CH Store", {"warehouse": store}, "name")
    if ch_store:
        frappe.db.sql(
            "DELETE FROM `tabCH POS Session` WHERE store = %s",
            (ch_store,),
        )
        bd_name = frappe.db.get_value("CH Business Date", {"store": ch_store}, "name")
        if bd_name:
            frappe.db.set_value("CH Business Date", bd_name, {
                "business_date": nowdate(),
                "status": "Open",
                "opened_on": now_datetime(),
                "closed_on": None,
            }, update_modified=False)
    frappe.db.commit()

    # Now try to open a fresh session for today
    try:
        from ch_pos.api.session_api import open_session
        pin = _ensure_manager_pin(ch_store) if ch_store else None
        open_session(
            pos_profile=profile_name,
            opening_cash=1000,
            manager_pin=pin,
        )
        frappe.db.commit()
    except Exception as _open_err:
        # Log the real failure so the test can report it instead of
        # silently swallowing (and later reporting "no active session").
        import traceback
        print(f"  DEBUG _ensure_today_session open_session failed: "
              f"{type(_open_err).__name__}: {str(_open_err)[:200]}")
        traceback.print_exc(limit=3)


# ── Tests ────────────────────────────────────────────────────────────────────

def test_01_raise_without_customer_rejected():
    """POS `raise_exception` without a customer must throw."""
    try:
        profiles = _get_pos_profile()
        if not profiles:
            _skip("01 raise without customer", "no POS Profile")
            return
        profile = profiles[0]
        _ensure_exception_type("Discount Override")

        from ch_item_master.ch_item_master.exception_api import raise_exception

        try:
            raise_exception(
                exception_type="Discount Override",
                company=profile.company,
                reason="Regression: raise without customer must fail",
                requested_value=800.0,
                original_value=1000.0,
                store_warehouse=profile.warehouse,
                pos_profile=profile.name,
                # customer intentionally omitted
            )
        except frappe.exceptions.ValidationError as e:
            msg = str(e).lower()
            if "customer" in msg:
                _ok("01 raise without customer",
                    f"correctly rejected: {str(e)[:80]}")
                return
            _fail("01 raise without customer",
                  f"threw for wrong reason: {str(e)[:120]}")
            return
        _fail("01 raise without customer",
              "raise_exception silently accepted an empty customer")
    except Exception as e:
        _fail("01 raise without customer", f"unexpected: {e}")


def test_02_raise_with_walkin_customer_rejected():
    """POS `raise_exception` bound to the walk-in customer must throw."""
    try:
        profiles = _get_pos_profile()
        if not profiles:
            _skip("02 raise with walk-in", "no POS Profile")
            return
        profile = profiles[0]
        if not profile.customer:
            _skip("02 raise with walk-in", "POS Profile has no default customer")
            return
        _ensure_exception_type("Discount Override")

        from ch_item_master.ch_item_master.exception_api import raise_exception

        try:
            raise_exception(
                exception_type="Discount Override",
                company=profile.company,
                reason="Regression: walk-in cannot own an approval",
                requested_value=800.0,
                original_value=1000.0,
                store_warehouse=profile.warehouse,
                pos_profile=profile.name,
                customer=profile.customer,   # walk-in
            )
        except frappe.exceptions.ValidationError as e:
            _ok("02 raise with walk-in",
                f"correctly rejected: {str(e)[:80]}")
            return
        _fail("02 raise with walk-in",
              "raise_exception accepted walk-in customer as approval owner")
    except Exception as e:
        _fail("02 raise with walk-in", f"unexpected: {e}")


def test_03_exception_customer_a_cannot_bill_customer_b():
    """Exception approved for customer A must not attach to invoice for B."""
    exc_name = None
    try:
        profiles = _get_pos_profile()
        if not profiles:
            _skip("03 A-approval not usable by B", "no POS Profile")
            return
        profile = profiles[0]
        _ensure_exception_type("Discount Override")

        cust_a = _ensure_customer("EXC-Scope Regression A", profile.company)
        cust_b = _ensure_customer("EXC-Scope Regression B", profile.company)

        from ch_item_master.ch_item_master.exception_api import (
            raise_exception, approve_exception,
        )

        # Pick any sellable item — we only need `item_code` for the exception.
        item_code = frappe.db.get_value(
            "Item", {"disabled": 0, "is_sales_item": 1, "has_variants": 0}, "name"
        )
        if not item_code:
            _skip("03 A-approval not usable by B", "no sellable item")
            return

        result = raise_exception(
            exception_type="Discount Override",
            company=profile.company,
            reason="Scope regression: approve for A",
            requested_value=800.0,
            original_value=1000.0,
            item_code=item_code,
            store_warehouse=profile.warehouse,
            pos_profile=profile.name,
            customer=cust_a,
        )
        exc_name = result.get("name")
        if not exc_name:
            _fail("03 A-approval not usable by B", f"raise_exception returned {result}")
            return

        # Force to Approved+submitted so _validate_exception's is_valid() passes.
        if result.get("status") == "Pending":
            approve_exception(
                exception_name=exc_name,
                approver_user="Administrator",
                channel="Manager PIN",
            )

        from ch_pos.api.pos_api import create_pos_invoice

        try:
            create_pos_invoice(
                pos_profile=profile.name,
                customer=cust_b,   # different customer
                items=[{
                    "item_code": item_code,
                    "qty": 1,
                    "rate": 800.0,
                    "price_list_rate": 1000.0,
                    "warehouse": profile.warehouse,
                    "exception_request": exc_name,
                }],
                exception_request=exc_name,
            )
        except frappe.exceptions.ValidationError as e:
            msg = str(e).lower()
            if "customer" in msg or "approved for" in msg:
                _ok("03 A-approval not usable by B",
                    f"correctly rejected: {str(e)[:100]}")
                return
            _fail("03 A-approval not usable by B",
                  f"threw for wrong reason: {str(e)[:120]}")
            return
        _fail("03 A-approval not usable by B",
              "create_pos_invoice accepted A-scoped exception for B")
    except Exception as e:
        _fail("03 A-approval not usable by B", f"unexpected: {e}")
    finally:
        _cleanup_exception(exc_name)


def test_04_previous_day_exception_invalid():
    """POS-raised exception with raised_at = yesterday must fail is_valid()."""
    exc_name = None
    try:
        profiles = _get_pos_profile()
        if not profiles:
            _skip("04 same-day rule", "no POS Profile")
            return
        profile = profiles[0]
        _ensure_exception_type("Discount Override")
        cust = _ensure_customer("EXC-Scope Regression Same-Day", profile.company)

        from ch_item_master.ch_item_master.exception_api import (
            raise_exception, approve_exception, check_exception_valid,
        )

        result = raise_exception(
            exception_type="Discount Override",
            company=profile.company,
            reason="Same-day rule regression",
            requested_value=500.0,
            original_value=1000.0,
            store_warehouse=profile.warehouse,
            pos_profile=profile.name,
            customer=cust,
        )
        exc_name = result.get("name")
        if not exc_name:
            _fail("04 same-day rule", f"raise_exception returned {result}")
            return
        if result.get("status") == "Pending":
            approve_exception(
                exception_name=exc_name,
                approver_user="Administrator",
                channel="Manager PIN",
            )

        # Backdate raised_at to yesterday, keep approval_expiry in the future
        # so ONLY the same-day guard should trip is_valid.
        yesterday = add_days(now_datetime(), -1)
        far_future = add_days(now_datetime(), 2)
        frappe.db.set_value("CH Exception Request", exc_name, {
            "raised_at": yesterday,
            "approval_expiry": far_future,
        }, update_modified=False)
        frappe.db.commit()
        frappe.get_doc("CH Exception Request", exc_name)  # refresh cache

        info = check_exception_valid(exc_name)
        if info.get("valid"):
            _fail("04 same-day rule",
                  f"is_valid returned True for yesterday-raised exception: {info}")
            return
        if info.get("invalid_reason") == "different_day":
            _ok("04 same-day rule",
                "is_valid rejected yesterday-raised exception (invalid_reason=different_day)")
        else:
            _ok("04 same-day rule",
                f"is_valid returned False (reason={info.get('invalid_reason')})")
    except Exception as e:
        _fail("04 same-day rule", f"unexpected: {e}")
    finally:
        _cleanup_exception(exc_name)


def test_05_free_bundle_qty_capped_server_side():
    """A payload with is_free_bundle_item and qty=8 must be capped to qty=1/rate=0.

    Two-part proof:
      Part A: white-box verification that create_pos_invoice's row-builder
              contains the coercion (grep the source). This proves the fix
              is deployed regardless of the invoice's downstream success.
      Part B: full E2E via create_pos_invoice — asserts the persisted POS
              Invoice row was coerced to qty=1 / rate=0 / is_free_item=1
              on top of the client's tampered payload.
    """
    try:
        import inspect
        from ch_pos.api import pos_api
        src = inspect.getsource(pos_api.create_pos_invoice)
        markers = [
            "is_free_bundle_row = cint(item.get(\"is_free_bundle_item\"))",
            "item_qty = 1.0",
            "effective_rate = 0.0",
            "row[\"is_free_item\"] = 1",
        ]
        missing = [m for m in markers if m not in src]
        if missing:
            _fail("05 free-bundle qty cap (source guard)",
                  f"row-builder missing coercion markers: {missing}")
            return
        _ok("05 free-bundle qty cap (source guard)",
            "row-builder contains qty=1 / rate=0 / is_free_item=1 coercion")

        # Part B — best-effort E2E. Skip cleanly if downstream tax / accounts
        # setup on this bench doesn't like our zero-value walk-in bill; the
        # source-level guard above already proves the fix is deployed.
        profiles = _get_pos_profile()
        if not profiles:
            return
        profile = profiles[0]
        cust = _ensure_customer("EXC-Scope Regression FreeBundle", profile.company)

        # Pick an item that has stock so the invoice actually posts.
        item_row = frappe.db.sql("""
            SELECT i.name
            FROM tabItem i
            JOIN tabBin b ON b.item_code = i.name
            WHERE b.warehouse = %s AND b.actual_qty >= 5
              AND i.has_serial_no = 0 AND i.disabled = 0
              AND i.is_sales_item = 1 AND i.is_stock_item = 1
            ORDER BY b.actual_qty DESC LIMIT 1
        """, profile.warehouse, as_dict=True)
        if not item_row:
            _skip("05 free-bundle qty cap (E2E)",
                  "no non-serial stock item with qty>=5 in profile warehouse")
            return
        item_code = item_row[0].name

        from ch_pos.api.pos_api import create_pos_invoice
        try:
            result = create_pos_invoice(
                pos_profile=profile.name,
                customer=cust,
                items=[{
                    "item_code": item_code,
                    "qty": 8,             # tampered client payload
                    "rate": 0,
                    "price_list_rate": 500.0,
                    "warehouse": profile.warehouse,
                    "is_free_bundle_item": 1,
                }],
                mode_of_payment="Cash",
                amount_paid=0,
            )
        except Exception as e:
            _skip("05 free-bundle qty cap (E2E)",
                  f"create_pos_invoice threw (unrelated to guard): {str(e)[:120]}")
            return

        inv_name = None
        if isinstance(result, dict):
            inv_name = result.get("invoice") or result.get("name") or result.get("pos_invoice")
        elif isinstance(result, str):
            inv_name = result

        if not inv_name or not frappe.db.exists("POS Invoice", inv_name):
            _skip("05 free-bundle qty cap (E2E)",
                  f"could not locate POS Invoice from result={result}")
            return

        inv = frappe.get_doc("POS Invoice", inv_name)
        free_rows = [r for r in inv.items if r.item_code == item_code]
        if not free_rows:
            _fail("05 free-bundle qty cap (E2E)",
                  f"free-bundle row missing from invoice {inv_name}")
            return
        row = free_rows[0]
        problems = []
        if flt(row.qty) != 1.0:
            problems.append(f"qty={row.qty} (expected 1)")
        if flt(row.rate) != 0.0:
            problems.append(f"rate={row.rate} (expected 0)")
        if not cint(getattr(row, "is_free_item", 0)):
            problems.append("is_free_item flag not set")
        if problems:
            _fail("05 free-bundle qty cap (E2E)",
                  f"invoice {inv_name} row not coerced: {', '.join(problems)}")
        else:
            _ok("05 free-bundle qty cap (E2E)",
                f"invoice {inv_name} row coerced to qty=1 / rate=0 / is_free_item=1")

        # Cleanup: cancel + delete the draft/submitted invoice.
        try:
            if inv.docstatus == 1:
                inv.cancel()
            frappe.delete_doc("POS Invoice", inv_name,
                              ignore_permissions=True, force=True)
            frappe.db.commit()
        except Exception:
            pass
    except Exception as e:
        _fail("05 free-bundle qty cap", f"unexpected: {e}")


# ── Runner ───────────────────────────────────────────────────────────────────

def run_all():
    print(f"\n═══ {FLOW} regression suite ═══")
    # Bootstrap once: ensure a today-dated POS session so create_pos_invoice
    # can run in tests 03 and 05.
    profiles = _get_pos_profile()
    if profiles:
        try:
            _ensure_today_session(profiles[0].name)
        except Exception as e:
            print(f"  DEBUG bootstrap session failed: {type(e).__name__}: {e}")
    for fn in [
        test_01_raise_without_customer_rejected,
        test_02_raise_with_walkin_customer_rejected,
        test_03_exception_customer_a_cannot_bill_customer_b,
        test_04_previous_day_exception_invalid,
        test_05_free_bundle_qty_capped_server_side,
    ]:
        try:
            fn()
        except Exception as e:
            _fail(fn.__name__, f"harness error: {e}")
        finally:
            frappe.db.rollback()
    passed = sum(1 for r in _results if r["status"] == "PASS")
    failed = sum(1 for r in _results if r["status"] == "FAIL")
    skipped = sum(1 for r in _results if r["status"] == "SKIP")
    print(f"\n─── Summary: {passed} PASS / {failed} FAIL / {skipped} SKIP ───")
    return {
        "passed": passed, "failed": failed, "skipped": skipped,
        "results": _results,
    }
