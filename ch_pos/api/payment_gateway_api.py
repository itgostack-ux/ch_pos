import json
import uuid
from datetime import datetime, timezone

import frappe
import requests
from frappe import _
from frappe.utils import cint, flt, get_url
from frappe.utils.password import get_decrypted_password


PINE_AUTH_URLS = {
    "UAT": "https://pluraluat.v2.pinepg.in/api/auth/v1/token",
    "PRODUCTION": "https://api.pluralpay.in/api/auth/v1/token",
}

PINE_ORDER_URLS = {
    "UAT": "https://pluraluat.v2.pinepg.in/api/pay/v1/orders",
    "PRODUCTION": "https://api.pluralpay.in/api/pay/v1/orders",
}


# ── Credential helpers ───────────────────────────────────────────────


def _safe_get_password(doctype, name, fieldname):
    """Return decrypted password or None — never raises."""
    try:
        return get_decrypted_password(doctype, name, fieldname, raise_exception=False)
    except Exception:
        return None


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


@frappe.whitelist()
def get_payment_machines(company=None, store=None, pos_profile=None, payment_mode=None):
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
        order_by="provider asc, machine_name asc",
    )

    if pos_profile:
        machines = [m for m in machines if not m.pos_profile or m.pos_profile == pos_profile]
    if payment_mode:
        machines = [m for m in machines if _machine_supported(frappe._dict(m), payment_mode)]

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
            title=_("Payment Machine Not Configured"),
        )
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
    try:
        response = requests.post(
            machine.api_base_url or PINE_AUTH_URLS[env],
            headers=headers,
            json=payload,
            timeout=20,
        )
        response.raise_for_status()
        token = response.json().get("access_token")
    except requests.exceptions.RequestException as exc:
        frappe.log_error(
            title="Pine Labs token failed",
            message=f"machine={machine.name}\nerror={exc}",
        )
        frappe.throw(
            _("Could not reach Pine Labs ({0}). Check network or credentials and retry.").format(env),
            title=_("Gateway Unavailable"),
        )
    if not token:
        frappe.throw(
            _("Pine Labs did not return an access token. Verify Client ID / Secret on machine {0}.").format(
                machine.machine_name or machine.name
            ),
            title=_("Gateway Auth Failed"),
        )
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
    try:
        response = requests.post(
            PINE_ORDER_URLS[env],
            headers=headers,
            json=payload,
            timeout=30,
        )
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as exc:
        frappe.log_error(
            title="Pine Labs order failed",
            message=f"machine={machine.name}\nerror={exc}",
        )
        frappe.throw(
            _("Pine Labs order creation failed. Please retry or use cash."),
            title=_("Gateway Order Failed"),
        )


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


@frappe.whitelist()
def initiate_payment(machine_name, amount, payment_mode, customer=None, customer_name=None,
        customer_email=None, customer_phone=None, merchant_order_reference=None, notes=None):
    machine = _get_machine(machine_name)
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
def pine_labs_return(**kwargs):
    frappe.logger("ch_pos_payment_gateway").info("Pine Labs return: %s", json.dumps(kwargs, default=str))
    return kwargs


@frappe.whitelist(allow_guest=True)
def pine_labs_webhook():
    body = frappe.request.get_data(as_text=True) or "{}"
    frappe.logger("ch_pos_payment_gateway").info("Pine Labs webhook: %s", body)
    return {"status": "ok"}
