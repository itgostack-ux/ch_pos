from __future__ import annotations

import pickle
import secrets

import frappe
from buyback.utils import validate_indian_phone
from frappe import _
from frappe.utils import cint, flt, now_datetime

from ch_pos.api.scope_guard import assert_pos_profile_scope
from ch_pos.config import get_control_setting, has_configured_roles, is_privileged_user


def _approval_ttl(fieldname: str, default: int) -> int:
    value = cint(get_control_setting(fieldname, default))
    return max(30, min(value, 3600))


def _bounded_ttl(value: int) -> int:
    return max(30, min(cint(value), 3600))


def _request_ttl() -> int:
    return _approval_ttl("manager_approval_request_ttl_seconds", 300)


def _grant_ttl() -> int:
    return _approval_ttl("manager_approval_grant_ttl_seconds", 600)


def _cache_key(kind: str, token: str) -> str:
    import hashlib

    digest = hashlib.sha256(str(token or "").encode()).hexdigest()
    return f"ch_pos_manager_approval:{kind}:{digest}"


def _phone_tail(value) -> str:
    digits = "".join(char for char in str(value or "") if char.isdigit())
    return digits[-10:]


def _resolve_manager_user(mobile_no: str, anchors: dict) -> dict:
    target = _phone_tail(mobile_no)
    matches = []
    phone_tail_sql = (
        "RIGHT(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE("
        "IFNULL({field}, ''), '+', ''), '-', ''), ' ', ''), '(', ''), ')', ''), 10)"
    )
    candidates = frappe.db.sql(
        f"""
        SELECT name, full_name, mobile_no, phone
          FROM `tabUser`
         WHERE enabled = 1
           AND user_type = 'System User'
           AND (
                {phone_tail_sql.format(field='mobile_no')} = %(phone)s
                OR {phone_tail_sql.format(field='phone')} = %(phone)s
           )
         ORDER BY name
         LIMIT 50
        """,
        {"phone": target},
        as_dict=True)
    for user in candidates:
        if not has_configured_roles(
            "discount_approval_roles",
            user=user.name):
            continue
        # Predicate, not throw-and-catch: a swallowed frappe.throw still leaves
        # its denial in frappe.local.message_log, so filtering N candidate
        # managers used to pop N "not entitled to access this store" messages
        # at the till — about other people's scope, not the operator's.
        from ch_pos.api.scope_guard import has_store_scope

        if not has_store_scope(
            store=anchors.get("store"),
            warehouse=anchors.get("warehouse"),
            company=anchors.get("company"),
            user=user.name):
            continue
        matches.append(user)

    if len(matches) != 1:
        frappe.throw(
            _("The mobile number is not uniquely assigned to an authorized manager for this store."),
            frappe.PermissionError,
            title=_("Manager Not Authorized"))
    return matches[0]


def request_approval(
    mobile_no,
    purpose,
    pos_profile,
    item_code=None,
    rate=None,
    qty=1,
    reference_doctype=None,
    reference_name=None) -> dict:
    frappe.has_permission("Sales Invoice", "create", throw=True)
    anchors = assert_pos_profile_scope(pos_profile)
    mobile_no = validate_indian_phone(mobile_no, "Manager Mobile Number")
    purpose = str(purpose or "").strip()
    if not purpose:
        frappe.throw(_("Approval purpose is required."), title=_("Missing Purpose"))

    manager = _resolve_manager_user(mobile_no, anchors)
    request_id = secrets.token_urlsafe(32)
    payload = {
        "requested_by": frappe.session.user,
        "manager_user": manager.name,
        "manager_name": manager.full_name or manager.name,
        "mobile_no": mobile_no,
        "purpose": purpose,
        "pos_profile": anchors["pos_profile"],
        "company": anchors.get("company"),
        "store": anchors.get("store"),
        "warehouse": anchors.get("warehouse"),
        "item_code": str(item_code or "").strip(),
        "rate": flt(rate),
        "qty": flt(qty) or 1,
        "reference_doctype": reference_doctype or "POS Profile",
        "reference_name": reference_name or anchors["pos_profile"],
        "requested_at": str(now_datetime()),
    }
    frappe.cache().set_value(
        _cache_key("request", request_id), payload, expires_in_sec=_request_ttl()
    )

    from ch_item_master.ch_core.doctype.ch_otp_log.ch_otp_log import CHOTPLog

    otp = CHOTPLog.generate_otp(
        mobile_no=mobile_no,
        purpose=purpose,
        reference_doctype=payload["reference_doctype"],
        reference_name=payload["reference_name"])
    channels = {}
    try:
        from buyback.buyback.whatsapp_notifications import send_otp

        channels = send_otp(
            mobile_no,
            otp,
            purpose,
            ref_doctype=payload["reference_doctype"],
            ref_name=payload["reference_name"])
    except Exception:
        frappe.log_error(frappe.get_traceback(), "Manager OTP delivery failed")

    return {
        "sent": True,
        "approval_request": request_id,
        "mobile": mobile_no[:3] + "****" + mobile_no[-3:],
        "channels": channels,
    }


def verify_approval(approval_request, otp_code) -> dict:
    frappe.has_permission("Sales Invoice", "create", throw=True)
    request_id = str(approval_request or "").strip()
    payload = frappe.cache().get_value(
        _cache_key("request", request_id), use_local_cache=False
    )
    if not payload or (
        payload.get("requested_by") != frappe.session.user and not is_privileged_user()
    ):
        frappe.throw(
            _("Manager approval request is invalid or expired."),
            frappe.PermissionError,
            title=_("Approval Expired"))

    assert_pos_profile_scope(payload["pos_profile"])
    manager = _resolve_manager_user(payload["mobile_no"], payload)
    if manager.name != payload["manager_user"]:
        frappe.throw(_("Manager authorization changed; request a new approval."), frappe.PermissionError)

    from ch_item_master.ch_core.doctype.ch_otp_log.ch_otp_log import CHOTPLog

    result = CHOTPLog.verify_otp(
        mobile_no=payload["mobile_no"],
        purpose=payload["purpose"],
        otp_code=otp_code,
        reference_doctype=payload["reference_doctype"],
        reference_name=payload["reference_name"])
    if not result.get("valid"):
        return result
    if result.get("shadow_live"):
        frappe.throw(
            _("Shadow-live master OTP cannot authorize a manager override."),
            frappe.PermissionError,
            title=_("Manager Verification Required"))

    frappe.cache().delete_value(_cache_key("request", request_id))
    grant = secrets.token_urlsafe(48)
    grant_payload = {**payload, "verified_at": str(now_datetime())}
    grant_ttl = _grant_ttl()
    frappe.cache().set_value(
        _cache_key("grant", grant), grant_payload, expires_in_sec=grant_ttl
    )
    return {
        "valid": True,
        "message": _("Manager approval verified."),
        "approval_token": grant,
        "manager_user": payload["manager_user"],
        "manager_name": payload["manager_name"],
        "expires_in": grant_ttl,
    }


def _consume_cache_value(key: str):
    cache = frappe.cache()
    made_key = cache.make_key(key)
    try:
        raw = cache.getdel(made_key)
    except Exception:
        raw = cache.eval(
            "local value = redis.call('GET', KEYS[1]); "
            "if value then redis.call('DEL', KEYS[1]); end; return value",
            1,
            made_key)

    if hasattr(frappe.local, "cache"):
        frappe.local.cache.pop(made_key, None)
    return pickle.loads(raw) if raw is not None else None


def consume_approval_grant(token, pos_profile, item_code, rate, qty=1) -> dict:
    """Atomically consume a manager grant bound to one caller and cart row."""
    token = str(token or "").strip()
    if len(token) < 40:
        frappe.throw(_("A valid manager approval token is required."), frappe.PermissionError)

    payload = _consume_cache_value(_cache_key("grant", token))
    if not payload:
        frappe.throw(_("Manager approval is invalid, expired, or already used."), frappe.PermissionError)

    expected = {
        "requested_by": frappe.session.user,
        "pos_profile": str(pos_profile or ""),
        "item_code": str(item_code or "").strip(),
    }
    if any(payload.get(key) != value for key, value in expected.items()):
        frappe.throw(_("Manager approval does not match this transaction."), frappe.PermissionError)
    if abs(flt(payload.get("rate")) - flt(rate)) > 0.005:
        frappe.throw(_("Manager approval does not match the approved price."), frappe.PermissionError)
    if abs(flt(payload.get("qty")) - (flt(qty) or 1)) > 0.005:
        frappe.throw(_("Manager approval does not match the approved quantity."), frappe.PermissionError)

    return payload


def issue_action_grant(kind: str, payload: dict, expires_in: int | None = None) -> str:
    """Issue a short-lived server grant for a non-item manager action."""
    grant = secrets.token_urlsafe(48)
    expires_in = _bounded_ttl(expires_in) if expires_in is not None else _grant_ttl()
    frappe.cache().set_value(
        _cache_key(f"action:{kind}", grant),
        {**payload, "requested_by": frappe.session.user, "verified_at": str(now_datetime())},
        expires_in_sec=expires_in)
    return grant


def consume_action_grant(
    kind: str, token, expected: dict, *, restore_on_rollback: bool = False
) -> dict:
    """Atomically consume an action grant and validate its transaction anchors."""
    token = str(token or "").strip()
    if len(token) < 40:
        frappe.throw(_("A valid manager approval token is required."), frappe.PermissionError)
    payload = _consume_cache_value(_cache_key(f"action:{kind}", token))
    if not payload:
        frappe.throw(_("Manager approval is invalid, expired, or already used."), frappe.PermissionError)
    required = {"requested_by": frappe.session.user, **expected}
    if any(str(payload.get(key) or "") != str(value or "") for key, value in required.items()):
        frappe.throw(_("Manager approval does not match this transaction."), frappe.PermissionError)

    if restore_on_rollback:
        # Redis is outside the SQL transaction. If a later insert fails, put the
        # already-validated one-time grant back so the user can safely retry.
        key = _cache_key(f"action:{kind}", token)

        def restore_grant():
            frappe.cache().set_value(key, payload, expires_in_sec=_grant_ttl())

        frappe.db.after_rollback.add(restore_grant)
    return payload


# ── Session open: an OTP to the opener's own inbox ───────────────────────────
#
# A manager PIN proves that *a* manager approved, never *who* — PINs get shared,
# and this estate once had every active PIN row using one 5-digit code. A code
# delivered to the opener's own mailbox proves the person, so the session can
# record who actually started it.
#
# CH OTP Log already provides 6 digits, a 5-minute expiry, attempt lockout,
# 5-per-hour rate limiting per identity and single-use semantics, so nothing
# here re-implements any of that.

_SESSION_OTP_PURPOSE = "POS Session Open"


def _mask_email(address: str) -> str:
    """j***e@example.com — enough to confirm the inbox without printing it."""
    address = str(address or "").strip()
    if "@" not in address:
        return "your registered email"
    local, _, domain = address.partition("@")
    if len(local) <= 2:
        masked = local[:1] + "*"
    else:
        masked = f"{local[0]}{'*' * (len(local) - 2)}{local[-1]}"
    return f"{masked}@{domain}"


def request_session_open_otp(pos_profile: str) -> dict:
    """Email the caller a code that lets them open this profile's till."""
    from ch_item_master.ch_core.doctype.ch_otp_log.ch_otp_log import CHOTPLog

    from ch_pos.api.scope_guard import assert_pos_executive

    anchors = assert_pos_profile_scope(pos_profile)
    store = anchors.get("store")
    if not store:
        frappe.throw(_("This POS Profile is not mapped to a store."))

    # Check entitlement BEFORE sending: never mail a code to someone who could
    # not open this till anyway, since that alone would leak which stores exist.
    assert_pos_executive(store)

    user = frappe.session.user
    email = frappe.db.get_value("User", user, "email") or user
    if "@" not in str(email):
        frappe.throw(
            _("Your account has no email address, so a code cannot be sent. Ask an administrator to add one."))

    otp = CHOTPLog.generate_otp(email=email, purpose=_SESSION_OTP_PURPOSE)

    frappe.sendmail(
        recipients=[email],
        subject=_("Your POS session code: {0}").format(otp),
        message=_(
            "<p>Use this code to open the till at <b>{store}</b>.</p>"
            "<p style='font-size:28px;letter-spacing:6px;font-weight:700'>{otp}</p>"
            "<p>It expires in 5 minutes and can be used once. "
            "If you did not ask to open a till, tell your manager — someone has your login.</p>"
        ).format(store=frappe.utils.escape_html(store), otp=otp),
        now=True,
    )

    return {
        "sent": True,
        "sent_to": _mask_email(email),
        "expires_in": 300,
        "message": _("We sent a 6-digit code to {0}.").format(_mask_email(email)),
    }


def verify_session_open_otp(pos_profile: str, otp: str) -> dict:
    """Exchange a correct code for a single-use grant that opens the session."""
    from ch_item_master.ch_core.doctype.ch_otp_log.ch_otp_log import CHOTPLog

    from ch_pos.api.scope_guard import assert_pos_executive

    anchors = assert_pos_profile_scope(pos_profile)
    store = anchors.get("store")
    if not store:
        frappe.throw(_("This POS Profile is not mapped to a store."))

    # Re-assert: entitlement can have been withdrawn between send and verify.
    assert_pos_executive(store)

    user = frappe.session.user
    email = frappe.db.get_value("User", user, "email") or user
    result = CHOTPLog.verify_otp(email=email, purpose=_SESSION_OTP_PURPOSE, otp_code=otp)
    if not result.get("valid"):
        frappe.throw(result.get("message") or _("That code is not valid."), frappe.PermissionError)

    grant = issue_action_grant(
        "session_open",
        {"user": user, "store": store, "pos_profile": str(pos_profile or "")},
    )
    return {
        "verified": True,
        "session_grant": grant,
        "expires_in": _grant_ttl(),
        "message": _("Code accepted. Opening the session."),
    }

# ── A code to the logged-in user, for actions a PIN used to gate ─────────────
#
# Generalised from the session-open flow. The manager PIN answers "did someone
# with authority approve this"; where the action is operational rather than a
# concession — rolling the business date, for instance — what actually matters
# is proving who is standing at the till. An executive has no approver role by
# design, so a PIN can only ever tell them "Invalid PIN".

_ACTION_PURPOSE = {
    "business_date_override": "POS Business Date Override",
}


def request_action_otp(kind: str, store: str) -> dict:
    """Email the caller a code authorising `kind` at `store`."""
    from ch_item_master.ch_core.doctype.ch_otp_log.ch_otp_log import CHOTPLog
    from ch_pos.api.scope_guard import assert_pos_executive, assert_store_scope

    purpose = _ACTION_PURPOSE.get(kind)
    if not purpose:
        frappe.throw(_("Unsupported verification action."), frappe.PermissionError)
    if not store:
        frappe.throw(_("Store is required."))

    company = frappe.db.get_value("CH Store", store, "company")
    assert_store_scope(store=store, company=company)
    # Entitlement before delivery: never mail a code to someone who could not
    # work this till anyway.
    assert_pos_executive(store)

    user = frappe.session.user
    email = frappe.db.get_value("User", user, "email") or user
    if "@" not in str(email):
        frappe.throw(_("Your account has no email address, so a code cannot be sent."))

    otp = CHOTPLog.generate_otp(email=email, purpose=purpose)
    frappe.sendmail(
        recipients=[email],
        subject=_("Your POS verification code: {0}").format(otp),
        message=_(
            "<p>Use this code to continue at <b>{store}</b>.</p>"
            "<p style='font-size:28px;letter-spacing:6px;font-weight:700'>{otp}</p>"
            "<p>It expires in 5 minutes and can be used once. If you did not ask "
            "for it, tell your manager — someone has your login.</p>"
        ).format(store=frappe.utils.escape_html(store), otp=otp),
        now=True,
    )
    return {"sent": True, "sent_to": _mask_email(email), "expires_in": 300,
            "message": _("We sent a 6-digit code to {0}.").format(_mask_email(email))}


def verify_action_otp(kind: str, store: str, otp: str) -> dict:
    """Exchange a correct code for a single-use grant bound to caller+store."""
    from ch_item_master.ch_core.doctype.ch_otp_log.ch_otp_log import CHOTPLog
    from ch_pos.api.scope_guard import assert_pos_executive

    purpose = _ACTION_PURPOSE.get(kind)
    if not purpose:
        frappe.throw(_("Unsupported verification action."), frappe.PermissionError)
    assert_pos_executive(store)   # entitlement can lapse between send and verify

    user = frappe.session.user
    email = frappe.db.get_value("User", user, "email") or user
    result = CHOTPLog.verify_otp(email=email, purpose=purpose, otp_code=otp)
    if not result.get("valid"):
        frappe.throw(result.get("message") or _("That code is not valid."),
                     frappe.PermissionError)

    grant = issue_action_grant(kind, {"user": user, "store": store})
    return {"verified": True, "action_grant": grant, "expires_in": _grant_ttl(),
            "message": _("Code accepted.")}
