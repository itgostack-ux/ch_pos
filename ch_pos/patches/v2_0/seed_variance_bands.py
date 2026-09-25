"""Seed the over/short tolerance bands and blind close; retire the flat Rs 100.

The single ``variance_approval_threshold`` of Rs 100 was applied to every till
regardless of turnover — a 0.1% tolerance on a till that took Rs 92,400 in a
day. It is now the *floor* of the approval band rather than the whole rule
(see ch_pos.pos_core.variance_policy), so the stored value has to move with it.

Only a value still sitting on the old default is touched. An operator who has
deliberately chosen a different number keeps it.
"""

import frappe
from frappe.utils import flt

OLD_DEFAULT_THRESHOLD = 100.0
NEW_APPROVAL_FLOOR = 1000.0

#: Field -> value to seed when the site has never set it. Blind close and the
#: percentage bands are new, so on an existing site they are simply absent.
SEEDS = {
    "variance_accept_amount": 200.0,
    "variance_accept_percent": 0.5,
    "variance_approval_percent": 2.0,
    "blind_close": 1,
    # Off, deliberately. This is the gate that stranded every store on this
    # estate behind an acquirer reconciliation that cannot exist at close time.
    "block_business_date_on_edc_mismatch": 0,
}


def execute():
    if not frappe.db.exists("DocType", "CH POS Control Settings"):
        return

    settings = frappe.get_single("CH POS Control Settings")
    meta = frappe.get_meta("CH POS Control Settings")
    changed = []

    for fieldname, value in SEEDS.items():
        if not meta.get_field(fieldname):
            # Schema has not synced yet; a later migrate will pick it up.
            continue
        current = frappe.db.get_single_value("CH POS Control Settings", fieldname)
        # A falsy value counts as unset here, not as a deliberate zero. The
        # DocType schema syncs *before* post_model_sync patches run, and that
        # sync materialises every new field in tabSingles as 0 — so by the time
        # this patch looks, "never configured" already reads as 0, not None.
        # Treating only None as unset left blind_close stored as 0, which
        # silently shipped blind close turned off. These fields are new in this
        # release, so nobody can have chosen a zero for them beforehand.
        if not current:
            settings.set(fieldname, value)
            changed.append(f"{fieldname}={value}")

    if meta.get_field("variance_approval_threshold"):
        threshold = flt(
            frappe.db.get_single_value("CH POS Control Settings", "variance_approval_threshold")
        )
        # 0 means "never configured" for a Currency whose docfield declares a
        # non-zero default, which is why get_control_setting treats it as unset.
        if threshold in (0.0, OLD_DEFAULT_THRESHOLD):
            settings.variance_approval_threshold = NEW_APPROVAL_FLOOR
            changed.append(f"variance_approval_threshold={NEW_APPROVAL_FLOOR}")

    if not changed:
        return

    settings.flags.ignore_permissions = True
    settings.flags.ignore_mandatory = True
    settings.save()
    frappe.db.commit()
    print(f"ch_pos: seeded POS variance bands -> {', '.join(changed)}")
