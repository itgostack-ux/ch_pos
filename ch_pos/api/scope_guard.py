"""Import-safe wrapper around ch_erp15's central store-scope guard.

Whitelisted POS / queue / stock APIs call ``assert_store_scope(...)`` to refuse
a caller acting on a store / warehouse / company outside their CH User Scope.

Only the *import* of ch_erp15 is guarded — when ch_erp15 is not installed
(standalone unit tests) the guard is a no-op, mirroring the resilience pattern
already used in ``payment_gateway_api._user_store_company_scope``. The scope
check itself runs outside the try/except so a real ``PermissionError``
propagates to the caller.
"""

from __future__ import annotations


def assert_store_scope(store=None, company=None, warehouse=None, user=None, msg=None):
    """Raise ``frappe.PermissionError`` if the user is outside scope.

    No-op when ch_erp15 (the scope authority) is unavailable.
    """
    try:
        from ch_erp15.ch_erp15.scope import assert_user_has_store_scope
    except ImportError:
        return
    assert_user_has_store_scope(
        store=store, company=company, warehouse=warehouse, user=user, msg=msg
    )
