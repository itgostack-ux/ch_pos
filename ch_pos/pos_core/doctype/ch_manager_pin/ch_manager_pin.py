"""CH POS Password — scoped, rate-limited approval authentication."""

import hashlib
import hmac

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint
from frappe.utils.password import get_decrypted_password

from ch_pos.config import get_configured_roles, get_control_setting, is_privileged_user
from ch_pos.rate_limits import clear_fixed_window, increment_fixed_window


class CHPOSPassword(Document):
    def validate(self):
        pin = self.get_password("pin_hash") or ""
        if not pin.isdigit() or len(pin) < 4 or len(pin) > 6:
            frappe.throw(_("PIN must be 4-6 digits"), title=_("CH POS Password Error"))


def _pin_attempt_key(store=None) -> str:
    request_ip = getattr(frappe.local, "request_ip", None) or "unknown"
    user = getattr(getattr(frappe.local, "session", None), "user", None) or "Guest"
    digest = hashlib.sha256(f"{request_ip}:{user}:{store or 'no-store'}".encode()).hexdigest()
    return f"ch_pos_pin_attempts:{digest}"


def _check_pin_rate_limit(store=None) -> str:
    key = _pin_attempt_key(store)
    attempt_limit = max(
        1, min(cint(get_control_setting("manager_pin_attempt_limit", 5)), 100)
    )
    lockout_seconds = max(
        30, min(cint(get_control_setting("manager_pin_lockout_seconds", 900)), 86400)
    )
    attempts = increment_fixed_window("manager-pin", key, lockout_seconds)
    if attempts > attempt_limit:
        frappe.throw(
            _("Too many invalid approval attempts. Try again later."),
            frappe.RateLimitExceededError,
            title=_("Approval Temporarily Locked"))
    return key


def _clear_pin_failures(key: str) -> None:
    clear_fixed_window("manager-pin", key)


def _read_pin_quietly(password_name):
    """Return ``(pin, readable)`` without leaking decryption noise to the till.

    ``frappe.decrypt`` **msgprints before it throws** — it emits "Failed to
    decrypt key …", "Encryption key is invalid! Please check site_config.json"
    and a link about restoring sites. Catching the exception does not remove
    those from ``frappe.local.message_log``, so a cashier entering a manager PIN
    was shown the site's encryption internals once per unreadable row.

    Snapshotting the log and truncating it back is the only way to undo a
    msgprint that has already happened. The failure is not swallowed: the caller
    collects the names and writes a single Error Log for administrators.
    """
    log = getattr(frappe.local, "message_log", None)
    mark = len(log) if isinstance(log, list) else None
    try:
        value = get_decrypted_password(
            "CH POS Password", password_name, "pin_hash", raise_exception=True
        )
        return value, True
    except Exception:
        return None, False
    finally:
        if mark is not None and isinstance(frappe.local.message_log, list):
            del frappe.local.message_log[mark:]


def verify_manager_pin(pin, store=None, permission=None):
    """Verify a manager PIN and return the manager's user if valid.

    Args:
        pin: The plain-text PIN entered by the manager
        store: Optional store to restrict to
        permission: Optional permission field to check (e.g. "can_approve_closing")

    Returns:
        dict: {"valid": True, "user": "manager@example.com", "name": "Manager Name"}
        or {"valid": False, "message": "..."}
    """
    if not pin or not pin.strip().isdigit():
        return {"valid": False, "message": _("Invalid PIN format")}

    attempt_key = _check_pin_rate_limit(store)
    permission_fields = {
        "can_approve_opening",
        "can_approve_closing",
        "can_approve_cash_drop",
        "can_override_business_date",
        "can_approve_discount",
        "can_approve_return",
        "can_force_close_session",
    }
    if permission and permission not in permission_fields:
        frappe.throw(_("Unsupported manager approval permission."), frappe.ValidationError)

    has_store_field = frappe.db.has_column("CH POS Password", "store")
    candidate_limit = max(
        1, min(cint(get_control_setting("manager_pin_candidate_limit", 100)), 1000)
    )
    allowed_roles = sorted(
        get_configured_roles(
            "manager_pin_roles")
        | {"System Manager"}
    )
    values = {"roles": allowed_roles, "limit": candidate_limit + 1}
    store_select = "p.store" if has_store_field else "NULL AS store"
    store_condition = ""
    if has_store_field and store:
        store_condition = "AND (IFNULL(p.store, '') = '' OR p.store = %(store)s)"
        values["store"] = store
    elif has_store_field:
        store_condition = "AND (u.name = 'Administrator' OR system_role.parent IS NOT NULL)"

    managers = frappe.db.sql(
        f"""
        SELECT p.name, p.user, p.employee_name, {store_select},
               {f'p.`{permission}`' if permission else '1'} AS requested_permission
          FROM `tabCH POS Password` p
          JOIN `tabUser` u ON u.name = p.user AND u.enabled = 1
          LEFT JOIN `tabHas Role` system_role
            ON system_role.parent = u.name
           AND system_role.parenttype = 'User'
           AND system_role.role = 'System Manager'
         WHERE p.is_active = 1
           {store_condition}
           AND (
                u.name = 'Administrator'
                OR system_role.parent IS NOT NULL
                OR EXISTS (
                    SELECT 1
                      FROM `tabHas Role` allowed_role
                     WHERE allowed_role.parent = u.name
                       AND allowed_role.parenttype = 'User'
                       AND allowed_role.role IN %(roles)s
                )
           )
         ORDER BY p.name
         LIMIT %(limit)s
        """,
        values,
        as_dict=True)
    if len(managers) > candidate_limit:
        frappe.throw(
            _("Manager PIN candidates exceed the configured store limit. Assign each manager to a store or raise the limit."),
            frappe.PermissionError)

    matches = []
    undecryptable = []
    for mgr in managers:
        manager_is_privileged = is_privileged_user(mgr.user)
        manager_store = mgr.get("store") if has_store_field else None
        if not manager_is_privileged:
            if not store or (manager_store and manager_store != store):
                continue
            # Predicate, not throw-and-catch: `frappe.throw` also appends to
            # frappe.local.message_log, and catching the exception does not
            # remove it. The old form showed the cashier one "You are not
            # entitled to access this store / location." popup per skipped
            # manager — noise about other people's permissions.
            from ch_pos.api.scope_guard import has_store_scope

            if not has_store_scope(store=store, user=mgr.user):
                continue
        if permission and not cint(mgr.requested_permission):
            continue

        # One unreadable PIN must not deny the till to every other manager.
        # Decryption fails for every row encrypted under a different
        # site_config `encryption_key` — the usual cause is a database restored
        # from another site without its key.
        stored_pin, readable = _read_pin_quietly(mgr.name)
        if not readable:
            undecryptable.append(mgr.name)
            continue
        if not hmac.compare_digest(str(stored_pin or ""), str(pin)):
            continue

        matches.append((mgr, manager_store))

    if len(matches) > 1 and store:
        # Most-specific assignment wins (SAP org-level parity): a PIN row
        # explicitly bound to THIS store beats floating (store-NULL) rows.
        # Shared PINs across floating rows remain ambiguous and are refused
        # below — the durable fix is one distinct PIN per manager.
        store_bound = [m for m in matches if (m[1] or "") == store]
        if len(store_bound) == 1:
            matches = store_bound

    if len(matches) == 1:
        mgr, manager_store = matches[0]
        _clear_pin_failures(attempt_key)
        return {
            "valid": True,
            "user": mgr.user,
            "name": mgr.employee_name,
            "store": manager_store,
        }

    if len(matches) > 1:
        return {"valid": False, "message": _(
            "This PIN belongs to more than one authorized manager here. "
            "Assign each manager a distinct PIN (or bind their PIN to a store)."
        )}

    if undecryptable:
        # Loud in the server log (an admin must fix the key or reset the PINs),
        # and honest at the till: without this the operator sees a bare
        # "Invalid PIN" for a PIN that is very likely correct.
        frappe.log_error(
            "Manager PINs stored under a different encryption key and cannot be "
            "read on this site. Restore the original site_config.json "
            "encryption_key, or have these managers set a new PIN:\n"
            + "\n".join(undecryptable),
            "CH POS Password decryption failed")
        if not managers or len(undecryptable) == len(managers):
            return {"valid": False, "message": _(
                "Manager PINs cannot be read on this site — none are stored "
                "under the current encryption key. Ask an administrator to "
                "restore the original encryption key or reset the PINs."
            )}

    return {"valid": False, "message": _("Invalid PIN")}
