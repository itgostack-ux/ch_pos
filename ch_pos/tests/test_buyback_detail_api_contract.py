"""
Regression guard for the POS buyback detail API contract.

Run:
  cd /home/palla/erpnext-bench
  bench --site erpnext.local execute ch_pos.tests.test_buyback_detail_api_contract.run
"""

import frappe


METHOD = "ch_pos.api.pos_api.get_pos_buyback_detail"


def run():
    frappe.set_user("Administrator")

    method = frappe.get_attr(METHOD)
    assert method in frappe.whitelisted, f"{METHOD} is not registered as whitelisted"

    allowed_methods = set(frappe.allowed_http_methods_for_whitelisted_func.get(method) or [])
    assert {"GET", "POST"}.issubset(allowed_methods), (
        f"{METHOD} should allow GET and POST, got {sorted(allowed_methods)}"
    )
    assert "PUT" not in allowed_methods and "DELETE" not in allowed_methods, (
        f"{METHOD} is a read endpoint and should not allow mutating HTTP verbs"
    )
    assert method not in frappe.guest_methods, (
        f"{METHOD} exposes buyback order detail and must remain login-only"
    )

    frappe.set_user("Guest")
    try:
        frappe.is_whitelisted(method)
    except frappe.PermissionError:
        pass
    else:
        raise AssertionError(f"{METHOD} should not be callable as Guest")
    finally:
        frappe.set_user("Administrator")

    assessment_name = frappe.db.get_value(
        "Buyback Assessment",
        {"docstatus": ["!=", 2]},
        "name",
        order_by="modified desc",
    )
    if assessment_name:
        detail = method(assessment_name)
        assert detail.get("name") == assessment_name, "Detail response assessment mismatch"
        assert "order" in detail, "Detail response should include order key"

    print(
        {
            "method": METHOD,
            "whitelisted": True,
            "allowed_methods": sorted(allowed_methods),
            "guest_blocked": True,
            "detail_checked": bool(assessment_name),
        }
    )
