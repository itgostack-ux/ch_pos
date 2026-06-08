"""TC_044 regression guard: Bin Manager must preserve exact IMEI on Damaged move.

Run:
  bench --site erpnext.local execute ch_pos.tests.test_tc044_bin_manager_imei_transfer_guard.run
"""

from __future__ import annotations

import frappe


def _pick_candidate_serial() -> tuple[str | None, str | None]:
    rows = frappe.db.sql(
        """
        SELECT sn.name AS serial_no, w.ch_store AS store
        FROM `tabSerial No` sn
        JOIN `tabWarehouse` w ON w.name = sn.warehouse
        WHERE IFNULL(w.ch_store, '') != ''
          AND IFNULL(w.ch_bin_type, '') = 'Sellable'
          AND IFNULL(sn.status, 'Active') = 'Active'
          AND sn.name LIKE %(needle)s
        ORDER BY sn.modified DESC
        LIMIT 1
        """,
        {"needle": "%1021%"},
        as_dict=True,
    )
    if rows:
        return rows[0].serial_no, rows[0].store

    # Fallback for non-QA datasets: still verify exact-serial preservation logic.
    rows = frappe.db.sql(
        """
        SELECT sn.name AS serial_no, w.ch_store AS store
        FROM `tabSerial No` sn
        JOIN `tabWarehouse` w ON w.name = sn.warehouse
        WHERE IFNULL(w.ch_store, '') != ''
          AND IFNULL(w.ch_bin_type, '') = 'Sellable'
          AND IFNULL(sn.status, 'Active') = 'Active'
        ORDER BY sn.modified DESC
        LIMIT 1
        """,
        as_dict=True,
    )
    if rows:
        return rows[0].serial_no, rows[0].store

    return None, None


def run() -> dict:
    from ch_item_master.ch_core.bin_transfer import (
        get_bin_transfer_reasons,
        get_serial_bin_context,
        get_store_bin_serials,
        pos_bin_transfer,
    )

    before = None
    try:
        serial_no, store = _pick_candidate_serial()
        if not serial_no or not store:
            print("SKIP: TC_044 (no sellable serial/store candidate)")
            return {"pass": 0, "fail": 0, "skip": 1}

        before = get_serial_bin_context(serial_no=serial_no, store=store)
        if not before:
            raise AssertionError("TC_044: serial context not found")

        reasons = get_bin_transfer_reasons(target_bin_type="Damaged")
        reason = next((r for r in reasons if (r.get("source_bin_type") or "") == before.get("bin_type")), None)
        reason = reason or (reasons[0] if reasons else None)
        if not reason:
            raise AssertionError("TC_044: no Damaged-bin reason configured")

        res = pos_bin_transfer(
            item_code=before.get("item_code"),
            qty=1,
            reason=reason.get("name"),
            from_bin_type=before.get("bin_type"),
            to_bin_type="Damaged",
            store=store,
            serial_no=serial_no,
        )
        if not res.get("stock_entry"):
            raise AssertionError("TC_044: transfer did not create stock entry")

        after = get_serial_bin_context(serial_no=serial_no, store=store)
        if (after or {}).get("bin_type") != "Damaged":
            raise AssertionError(f"TC_044: expected Damaged, got {(after or {}).get('bin_type')}")

        damaged_rows = get_store_bin_serials(bin_type="Damaged", store=store, search_text=serial_no, limit=50)
        serials = {r.get("serial_no") for r in (damaged_rows or {}).get("serials", [])}
        if serial_no not in serials:
            raise AssertionError("TC_044: moved serial not present in Damaged bin list")

        # UI list loads unfiltered rows; the transferred serial must still appear
        # in the regular Damaged-bin dataset (not just when explicitly searched).
        damaged_rows_unfiltered = get_store_bin_serials(bin_type="Damaged", store=store, limit=50)
        serials_unfiltered = {r.get("serial_no") for r in (damaged_rows_unfiltered or {}).get("serials", [])}
        if serial_no not in serials_unfiltered:
            raise AssertionError("TC_044: moved serial missing from unfiltered Damaged bin listing")

        print(f"PASS: TC_044 exact serial tracked in Damaged bin ({serial_no})")
        return {"pass": 1, "fail": 0}
    finally:
        # pos_bin_transfer commits internally; use compensating transfer cleanup.
        try:
            if before and serial_no and store:
                current = get_serial_bin_context(serial_no=serial_no, store=store) or {}
                current_bin = current.get("bin_type")
                original_bin = before.get("bin_type")
                if current_bin and original_bin and current_bin != original_bin:
                    reasons = get_bin_transfer_reasons(target_bin_type=original_bin)
                    reason = next((r for r in reasons if (r.get("source_bin_type") or "") == current_bin), None)
                    reason = reason or (reasons[0] if reasons else None)
                    if reason:
                        pos_bin_transfer(
                            item_code=before.get("item_code"),
                            qty=1,
                            reason=reason.get("name"),
                            from_bin_type=current_bin,
                            to_bin_type=original_bin,
                            store=store,
                            serial_no=serial_no,
                        )
        except Exception:
            pass
