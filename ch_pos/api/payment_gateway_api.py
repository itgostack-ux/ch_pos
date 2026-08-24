import json
import hashlib
import hmac
import uuid
from datetime import datetime, timezone
from urllib.parse import urlsplit

import frappe
import requests
from frappe import _
from frappe.rate_limiter import rate_limit
from frappe.utils import cint, flt, get_url
from frappe.utils.password import get_decrypted_password

from ch_pos.api.scope_guard import assert_pos_profile_scope, get_pos_profile_anchors
from ch_pos.api.outbound_security import post_json_to_allowed_https
from ch_pos.config import (
    get_control_setting,
    is_privileged_user,
    require_authenticated_user,
    require_configured_roles)


def _bounded_callback_body() -> str:
    """Return the raw callback body after enforcing the configured byte cap."""
    limit = max(1024, min(cint(get_control_setting("guest_payload_max_bytes", 65536)), 1048576))
    raw = frappe.request.get_data(cache=True) or b""
    query = getattr(frappe.request, "query_string", b"") or b""
    if len(raw) + len(query) > limit:
        frappe.throw(_("Callback payload is too large."), frappe.ValidationError)
    try:
        return raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
    except UnicodeDecodeError:
        frappe.throw(_("Callback payload must be UTF-8."), frappe.ValidationError)


# ── Credential helpers ───────────────────────────────────────────────


def _safe_get_password(doctype, name, fieldname):
    """Return decrypted password or None — never raises."""
    try:
        return get_decrypted_password(doctype, name, fieldname, raise_exception=False)
    except Exception:
        return None


def _configured_gateway_hosts() -> set[str]:
    raw_hosts = str(get_control_setting("payment_gateway_allowed_hosts", "") or "")
    hosts = {
        host.strip().lower().rstrip(".")
        for host in raw_hosts.replace(",", "\n").splitlines()
        if host.strip()
    }
    invalid = [
        host
        for host in hosts
        if "://" in host
        or "/" in host
        or "@" in host
        or ":" in host
        or any(character.isspace() for character in host)
    ]
    if invalid:
        frappe.throw(
            _("Payment gateway host allowlist contains an invalid hostname."),
            frappe.ValidationError)
    if not hosts:
        frappe.throw(
            _("Configure at least one Payment Gateway Allowed Host in CH POS Control Settings."),
            frappe.ValidationError)
    return hosts


def _resolve_gateway_url(machine, fieldname: str) -> str:
    if fieldname not in {"api_base_url", "order_api_url"}:
        raise ValueError("Unsupported payment gateway endpoint field")

    endpoint = str(machine.get(fieldname) or "").strip()
    if not endpoint:
        label = "Authentication API URL" if fieldname == "api_base_url" else "Order API URL"
        frappe.throw(
            _("{0} is required on payment machine {1}.").format(
                label, machine.machine_name or machine.name
            ),
            frappe.ValidationError,
            title=_("Payment Gateway Not Configured"))
    if "\\" in endpoint or any(character.isspace() for character in endpoint):
        frappe.throw(_("Payment gateway URL is invalid."), frappe.ValidationError)

    try:
        parsed = urlsplit(endpoint)
        port = parsed.port
    except ValueError:
        frappe.throw(_("Payment gateway URL is invalid."), frappe.ValidationError)

    hostname = (parsed.hostname or "").lower().rstrip(".")
    if (
        parsed.scheme.lower() != "https"
        or not hostname
        or parsed.username
        or parsed.password
        or parsed.fragment
        or port not in (None, 443)
    ):
        frappe.throw(
            _("Payment gateway URLs must use HTTPS on port 443 without embedded credentials."),
            frappe.ValidationError)
    if hostname not in _configured_gateway_hosts():
        frappe.throw(
            _("Payment gateway host {0} is not allowlisted.").format(hostname),
            frappe.PermissionError)
    return endpoint


def _gateway_timeout_seconds() -> int:
    configured = cint(get_control_setting("payment_gateway_timeout_seconds", 30)) or 30
    return max(1, min(configured, 120))


def _gateway_response_max_bytes() -> int:
    configured = cint(get_control_setting("payment_gateway_response_max_bytes", 1048576)) or 1048576
    return max(1024, min(configured, 10485760))


def _verify_pine_callback_signature(machine_name: str | None, body: str, signature: str) -> str:
    """Authenticate a Pine Labs callback against a configured machine.

    An absent machine, client secret, or signature is an authentication
    failure.  Callback endpoints must never silently downgrade to unsigned
    mode because their URLs are public by design.
    """
    machine_name = (machine_name or "").strip()
    if not machine_name or not frappe.db.exists("CH Payment Machine", machine_name):
        frappe.throw(_("Unknown payment machine"), frappe.AuthenticationError)

    secret = _safe_get_password("CH Payment Machine", machine_name, "client_secret")
    if not secret or not signature:
        frappe.throw(_("Webhook signature is required"), frappe.AuthenticationError)

    expected = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature.strip()):
        frappe.log_error(
            title="Pine Labs Signature Mismatch",
            message=f"Signature validation failed for machine {machine_name}.")
        frappe.throw(_("Webhook signature validation failed"), frappe.AuthenticationError)
    return machine_name


def _machine_has_pine_credentials(machine):
    """All three are required to talk to Pine Labs."""
    if not (machine.client_id and machine.merchant_id):
        return False
    return bool(_safe_get_password("CH Payment Machine", machine.name, "client_secret"))


def _is_test_mode_machine(machine):
    """Test mode: explicit 'Other' provider, or UAT machine without configured creds.

    Lets QA stores test the full POS flow without real gateway secrets.
    """
    provider = (machine.provider or "").strip()
    if provider == "Other":
        return True
    env = (machine.environment or "UAT").upper()
    if env == "UAT" and provider == "Pine Labs" and not _machine_has_pine_credentials(machine):
        return True
    return False


def _utc_now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _normalize_mode(payment_mode):
    lc = (payment_mode or "").strip().lower()
    if any(token in lc for token in ("upi", "gpay", "phonepe", "paytm")):
        return "UPI"
    if any(token in lc for token in ("card", "credit", "debit", "edc")):
        return "CARD"
    if "wallet" in lc:
        return "WALLET"
    if "bank" in lc:
        return "NETBANKING"
    return (payment_mode or "").strip().upper()


def _machine_supported(machine, payment_mode):
    allowed = [m.strip().upper() for m in (machine.supported_payment_modes or "").replace("\n", ",").split(",") if m.strip()]
    if not allowed:
        return True
    return _normalize_mode(payment_mode) in allowed


def _sanitize_reference(value):
    base = "".join(ch for ch in (value or "") if ch.isalnum() or ch in ("-", "_"))
    return (base or f"POS_{uuid.uuid4().hex[:18]}")[:50]


def _get_machine(machine_name):
    machine = frappe.get_doc("CH Payment Machine", machine_name)
    if not cint(machine.enabled):
        frappe.throw(_("Payment machine {0} is disabled.").format(machine.machine_name or machine.name))
    return machine


def _user_store_company_scope():
    """Return ``(stores, companies)`` the current user is entitled to, or
    ``None`` for unrestricted access. Missing scope infrastructure fails closed."""
    require_authenticated_user()
    if is_privileged_user():
        return None
    try:
        from ch_erp15.ch_erp15.scope import get_user_scope
    except ImportError:
        return set(), set()
    scope = get_user_scope()
    if scope.get("bypass"):
        return None
    return (scope.get("stores") or set(), scope.get("companies") or set())


def _machine_matches_scope(machine, scoped) -> bool:
    if scoped is None:
        return True
    stores, companies = scoped
    return bool(
        machine.get("store")
        and machine.get("company")
        and machine.get("store") in stores
        and machine.get("company") in companies
    )


def _assert_machine_in_scope(machine):
    """Refuse a machine whose store/company is outside the caller's scope.

    A live gateway order must be bound to the operator's authority — an
    authenticated user cannot drive a terminal at a store they are not
    entitled to."""
    if frappe.session.user == "Guest":
        frappe.throw(_("You must be signed in to use a payment machine."), frappe.PermissionError)
    scoped = _user_store_company_scope()
    if _machine_matches_scope(machine, scoped):
        if machine.pos_profile:
            anchors = get_pos_profile_anchors(machine.pos_profile)
            if (
                anchors.get("company") != machine.company
                or anchors.get("store") != machine.store
            ):
                frappe.throw(
                    _("Payment machine {0} has inconsistent store configuration.").format(
                        machine.machine_name or machine.name
                    ),
                    frappe.PermissionError)
            assert_pos_profile_scope(machine.pos_profile)
        return
    frappe.throw(
        _("You are not entitled to operate payment machine {0}.").format(
            machine.machine_name or machine.name),
        frappe.PermissionError)


@frappe.whitelist()
def get_payment_machines(company=None, store=None, pos_profile=None, payment_mode=None):
    from ch_item_master.ch_core.shadow_live import manual_payment_entry

    if manual_payment_entry():
        # Shadow-live pilot: terminals (Paytm / Pine Labs) are unplugged —
        # staff key in the card RRN / UPI reference manually.
        return {"providers": [], "machines": [], "manual_only": True}

    frappe.has_permission("Sales Invoice", "create", throw=True)

    if pos_profile:
        anchors = get_pos_profile_anchors(pos_profile)
        assert_pos_profile_scope(pos_profile)
        if company and company != anchors.get("company"):
            frappe.throw(_("POS Profile belongs to another company."), frappe.PermissionError)
        if store and store != anchors.get("store"):
            frappe.throw(_("POS Profile belongs to another store."), frappe.PermissionError)
        company = anchors.get("company")
        store = anchors.get("store")

    filters = {"enabled": 1}
    if company:
        filters["company"] = company
    if store:
        filters["store"] = store

    machines = frappe.get_all(
        "CH Payment Machine",
        filters=filters,
        fields=[
            "name", "machine_id", "machine_name", "provider", "store",
            "company", "pos_profile", "supported_payment_modes", "terminal_id", "environment",
        ],
        order_by="provider asc, machine_name asc")

    if pos_profile:
        machines = [m for m in machines if not m.pos_profile or m.pos_profile == pos_profile]
    if payment_mode:
        machines = [m for m in machines if _machine_supported(frappe._dict(m), payment_mode)]

    # Store-scope gate: a user only sees machines at stores/companies they are
    # entitled to. Prevents enumerating another store's terminals.
    scoped = _user_store_company_scope()
    if scoped is not None:
        machines = [m for m in machines if _machine_matches_scope(m, scoped)]

    providers = []
    seen = set()
    for machine in machines:
        if machine.provider not in seen:
            seen.add(machine.provider)
            providers.append(machine.provider)

    return {
        "providers": providers,
        "machines": machines,
    }


def _pine_generate_token(machine):
    env = (machine.environment or "UAT").upper()
    client_secret = _safe_get_password("CH Payment Machine", machine.name, "client_secret")
    if not (machine.client_id and client_secret):
        frappe.throw(
            _(
                "Pine Labs credentials are not configured on machine {0}. "
                "Set Client ID, Client Secret, and Merchant ID, or change provider to 'Other' for test mode."
            ).format(machine.machine_name or machine.name),
            title=_("Payment Machine Not Configured"))
    headers = {
        "Content-Type": "application/json",
        "accept": "application/json",
        "Request-Timestamp": _utc_now_iso(),
        "Request-ID": str(uuid.uuid4()),
    }
    payload = {
        "client_id": machine.client_id,
        "client_secret": client_secret,
        "grant_type": "client_credentials",
    }
    endpoint = _resolve_gateway_url(machine, "api_base_url")
    try:
        response = post_json_to_allowed_https(
            endpoint,
            allowed_hosts=_configured_gateway_hosts(),
            label=_("Payment gateway"),
            headers=headers,
            payload=payload,
            timeout=_gateway_timeout_seconds(),
            max_response_bytes=_gateway_response_max_bytes())
        response.raise_for_status()
        response_data = response.json()
        token = response_data.get("access_token") if isinstance(response_data, dict) else None
    except (requests.exceptions.RequestException, ValueError) as exc:
        frappe.log_error(
            title="Pine Labs token failed",
            message=f"machine={machine.name}\nerror={exc}")
        frappe.throw(
            _("Could not reach Pine Labs ({0}). Check network or credentials and retry.").format(env),
            title=_("Gateway Unavailable"))
    if not token:
        frappe.throw(
            _("Pine Labs did not return an access token. Verify Client ID / Secret on machine {0}.").format(
                machine.machine_name or machine.name
            ),
            title=_("Gateway Auth Failed"))
    return token


def _pine_create_order(machine, access_token, payload):
    env = (machine.environment or "UAT").upper()
    headers = {
        "Content-Type": "application/json",
        "accept": "application/json",
        "Authorization": f"Bearer {access_token}",
        "Request-Timestamp": _utc_now_iso(),
        "Request-ID": str(uuid.uuid4()),
    }
    endpoint = _resolve_gateway_url(machine, "order_api_url")
    try:
        response = post_json_to_allowed_https(
            endpoint,
            allowed_hosts=_configured_gateway_hosts(),
            label=_("Payment gateway"),
            headers=headers,
            payload=payload,
            timeout=_gateway_timeout_seconds(),
            max_response_bytes=_gateway_response_max_bytes())
        response.raise_for_status()
        response_data = response.json()
        if not isinstance(response_data, dict):
            raise ValueError("Gateway response must be a JSON object")
        return response_data
    except (requests.exceptions.RequestException, ValueError) as exc:
        frappe.log_error(
            title="Pine Labs order failed",
            message=f"machine={machine.name}\nerror={exc}")
        frappe.throw(
            _("Pine Labs order creation failed. Please retry or use cash."),
            title=_("Gateway Order Failed"))


def _build_test_order(machine, amount, payment_mode, merchant_order_reference, customer, customer_name):
    """Deterministic mock order for test-mode machines (Other provider or UAT without creds).

    Matches the shape of a real initiate_payment response so the POS UI flow is identical.
    """
    merchant_ref = _sanitize_reference(merchant_order_reference)
    order_id = f"TEST-{uuid.uuid4().hex[:18].upper()}"
    return {
        "provider": (machine.provider or "Other").strip() or "Other",
        "machine": machine.name,
        "machine_name": machine.machine_name,
        "status": "TEST_CREATED",
        "test_mode": True,
        "order_id": order_id,
        "merchant_order_reference": merchant_ref,
        "allowed_payment_methods": [_normalize_mode(payment_mode)],
        "callback_url": machine.callback_url or get_url("/api/method/ch_pos.api.payment_gateway_api.pine_labs_return"),
        "webhook_url": machine.webhook_url or "",
        "amount": round(flt(amount), 2),
        "currency": "INR",
        "customer": customer or "",
        "customer_name": customer_name or customer or "Customer",
        "raw": {
            "note": "Simulated order \u2014 no gateway call was made (test-mode machine).",
        },
    }


@frappe.whitelist(methods=["POST"])
def initiate_payment(machine_name, amount, payment_mode, customer=None, customer_name=None,
        customer_email=None, customer_phone=None, merchant_order_reference=None, notes=None):
    from ch_item_master.ch_core.shadow_live import manual_payment_entry

    if manual_payment_entry():
        frappe.throw(
            _("Shadow live mode — payment machines are disabled. "
              "Enter the card / UPI reference manually and save the payment."),
            title=_("Payment Machines Disabled"))

    frappe.has_permission("Sales Invoice", "create", throw=True)

    machine = _get_machine(machine_name)
    # Bind the live gateway order to the operator's store authority.
    _assert_machine_in_scope(machine)
    provider = (machine.provider or "").strip()
    amount = flt(amount)
    if amount <= 0:
        frappe.throw(_("Amount must be greater than zero."))
    if not _machine_supported(machine, payment_mode):
        frappe.throw(_("Machine {0} does not support {1} payments.").format(machine.machine_name, payment_mode))

    # Test-mode shortcut: 'Other' provider, or UAT Pine Labs machine without configured credentials.
    if _is_test_mode_machine(machine):
        return _build_test_order(machine, amount, payment_mode, merchant_order_reference, customer, customer_name)

    if provider == "Pine Labs":
        access_token = _pine_generate_token(machine)
        merchant_ref = _sanitize_reference(merchant_order_reference)
        callback_url = machine.callback_url or get_url("/api/method/ch_pos.api.payment_gateway_api.pine_labs_return")
        failure_callback_url = machine.failure_callback_url or callback_url
        payload = {
            "merchant_order_reference": merchant_ref,
            "order_amount": {
                "value": round(amount, 2),
                "currency": "INR",
            },
            "pre_auth": False,
            "allowed_payment_methods": [_normalize_mode(payment_mode)],
            "notes": notes or f"POS payment via {machine.machine_name}",
            "callback_url": callback_url,
            "failure_callback_url": failure_callback_url,
            "purchase_details": {
                "customer": {
                    "customer_id": customer or "",
                    "first_name": (customer_name or customer or "Customer")[:50],
                    "email_id": customer_email or "",
                    "mobile_number": customer_phone or "",
                    "country_code": "91" if customer_phone else "",
                }
            },
        }
        order = _pine_create_order(machine, access_token, payload)
        data = order.get("data") or {}
        return {
            "provider": provider,
            "machine": machine.name,
            "machine_name": machine.machine_name,
            "status": data.get("status") or "CREATED",
            "order_id": data.get("order_id"),
            "merchant_order_reference": data.get("merchant_order_reference") or merchant_ref,
            "allowed_payment_methods": data.get("allowed_payment_methods") or [_normalize_mode(payment_mode)],
            "callback_url": callback_url,
            "webhook_url": machine.webhook_url or get_url("/api/method/ch_pos.api.payment_gateway_api.pine_labs_webhook"),
            "raw": data,
        }

    frappe.throw(_("Provider {0} is not implemented yet.").format(provider))


@frappe.whitelist(allow_guest=True)
@rate_limit(limit=60, seconds=300, methods=["GET", "POST"], ip_based=True)
def pine_labs_return(**kwargs):
    """Pine Labs return callback — HMAC validation required (H17)."""
    body = _bounded_callback_body() or "{}"
    sig_header = frappe.get_request_header("X-PINELABS-SIGNATURE") or ""

    machine_param = frappe.form_dict.get("machine", "")
    machine_param = _verify_pine_callback_signature(machine_param, body, sig_header)
    frappe.logger("ch_pos_payment_gateway").info(
        "Verified Pine Labs return callback for machine %s", machine_param
    )
    return kwargs


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(limit=120, seconds=300, methods=["POST"], ip_based=True)
def pine_labs_webhook():
    """Pine Labs webhook callback — HMAC validation required (H17)."""
    body = _bounded_callback_body() or "{}"
    sig_header = frappe.get_request_header("X-PINELABS-SIGNATURE") or ""

    # Attempt to extract machine name from payload to get the right secret
    payload = json.loads(body) if body else {}
    machine_name = payload.get("machine") or frappe.form_dict.get("machine")

    machine_name = _verify_pine_callback_signature(machine_name, body, sig_header)
    frappe.logger("ch_pos_payment_gateway").info(
        "Verified Pine Labs webhook callback for machine %s", machine_name
    )
    return {"status": "ok"}
