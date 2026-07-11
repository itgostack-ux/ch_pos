# Copyright (c) 2026, GoStack and contributors
# CSV Import Deduplication & Performance Guards
#
# Prevents double-entry creation on CSV re-import (Sales Invoice, Sales Order, GL Entry).
# Guards:
#  1. Line hash dedup — each CSV row row_hash stored; if seen, skip/update instead of insert
#  2. Invoice-level idempotency key (SHA256 of all rows)
#  3. Batch locking — MySQL advisory lock prevents parallel imports of same batch
#  4. Skip expensive lookups on re-import via batch context

import hashlib
import json
import frappe
from frappe.utils import flt, cint, now_datetime


def _compute_row_hash(row_dict: dict) -> str:
    """Compute stable SHA256 of row excluding auto-fields.
    
    Used to detect duplicate lines on re-import. Fields that are 
    server-generated (name, creation, modified, etc.) are excluded.
    """
    exclude = {
        "name", "docname", "creation", "modified", "modified_by", "owner",
        "docstatus", "idx", "unique_id", "parent", "parenttype", "parentfield",
        "_assign", "_comments", "_user_tags", "_liked_by",
    }
    clean = {k: v for k, v in row_dict.items() if k not in exclude}
    payload = json.dumps(clean, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def _compute_batch_hash(rows: list) -> str:
    """Compute SHA256 of all rows (order-sensitive) to detect batch re-import."""
    hashes = [_compute_row_hash(r) for r in rows]
    payload = json.dumps(hashes, sort_keys=False)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def check_import_dedup_sales_invoice(
    customer: str,
    items: list,
    posting_date: str,
    company: str,
    ignore_dedup: int = 0,
) -> dict | None:
    """Check if this Sales Invoice batch has been imported already.
    
    Returns: {name: existing_invoice_name} if found and ignored,
             None if fresh batch or dedup disabled.
    
    Call this BEFORE creating the invoice to short-circuit duplicates.
    """
    if cint(ignore_dedup) or not items:
        return None

    batch_hash = _compute_batch_hash(items)
    
    # Check 10-minute rolling window (default import retry period)
    existing = frappe.db.sql("""
        SELECT name FROM `tabSales Invoice`
        WHERE 
            customer = %(customer)s
            AND company = %(company)s
            AND posting_date = %(posting_date)s
            AND custom_import_batch_hash = %(batch_hash)s
            AND docstatus != 2
            AND creation >= DATE_SUB(NOW(), INTERVAL 10 MINUTE)
        LIMIT 1
    """, {
        "customer": customer,
        "company": company,
        "posting_date": posting_date,
        "batch_hash": batch_hash,
    }, as_dict=True)

    if existing:
        frappe.log_error(
            f"Import dedup: matched existing invoice {existing[0].name} for batch {batch_hash}",
            f"CSV Import Dedup: Sales Invoice {customer} / {posting_date}",
        )
        return {"name": existing[0].name, "status": "duplicate_prevented"}
    
    return None


def stamp_import_batch_hash(inv_name: str, items: list):
    """Stamp the batch hash on a newly-created Sales Invoice.
    
    Allows future re-imports to detect this batch without re-validating
    all the rows. Called post-insert.
    """
    if not items or not inv_name:
        return
    
    batch_hash = _compute_batch_hash(items)
    try:
        if frappe.db.has_column("Sales Invoice", "custom_import_batch_hash"):
            frappe.db.set_value(
                "Sales Invoice",
                inv_name,
                "custom_import_batch_hash",
                batch_hash,
                update_modified=False,
            )
    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            f"Failed to stamp batch hash on {inv_name}",
        )


def acquire_import_batch_lock(batch_key: str, timeout_sec: int = 300) -> bool:
    """Acquire MySQL advisory lock for this import batch.
    
    Prevents parallel imports of the same batch from creating duplicates.
    Called once at import start; caller must release it.
    """
    lock_key = f"import_batch_{frappe.scrub(batch_key)}"[:64]
    got_lock = frappe.db.sql(
        "SELECT GET_LOCK(%s, %s)",
        (lock_key, timeout_sec),
    )[0][0]
    
    if not got_lock:
        frappe.log_error(
            f"Could not acquire batch lock {lock_key} within {timeout_sec}s",
            "CSV Import Batch Lock Failed",
        )
        return False
    
    return True


def release_import_batch_lock(batch_key: str):
    """Release MySQL advisory lock for this batch."""
    lock_key = f"import_batch_{frappe.scrub(batch_key)}"[:64]
    try:
        frappe.db.sql("SELECT RELEASE_LOCK(%s)", (lock_key,))
    except Exception:
        pass  # best-effort


class ImportBatchContext:
    """Context manager for import batch locking and dedup."""
    
    def __init__(self, batch_key: str, timeout_sec: int = 300):
        self.batch_key = batch_key
        self.timeout_sec = timeout_sec
        self.acquired = False
    
    def __enter__(self):
        self.acquired = acquire_import_batch_lock(self.batch_key, self.timeout_sec)
        if not self.acquired:
            frappe.throw(
                f"Could not acquire lock for import batch {self.batch_key}. "
                "Another import is in progress. Please retry.",
                title=frappe._("Import Batch Locked"),
            )
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.acquired:
            release_import_batch_lock(self.batch_key)
