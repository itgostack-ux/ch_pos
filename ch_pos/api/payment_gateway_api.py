import json
import uuid
from datetime import datetime, timezone

import frappe
import requests
from frappe import _
from frappe.utils import cint, flt, get_url


PINE_AUTH_URLS = {
    "UAT": "https://pluraluat.v2.pinepg.in/api/auth/v1/token",
    "PRODUCTION": "https://api.pluralpay.in/api/auth/v1/token",
}

PINE_ORDER_URLS = {
    "UAT": "https://pluraluat.v2.pinepg.in/api/pay/v1/orders",
    "PRODUCTION": "https://api.pluralpay.in/api/pay/v1/orders",
}


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
    headers = {
        "Content-Type": "application/json",
        "accept": "application/json",
        "Request-Timestamp": _utc_now_iso(),
        "Request-ID": str(uuid.uuid4()),
    }
    payload = {
        "client_id": machine.client_id,
        "client_secret": machine.get_password("client_secret"),
        "grant_type": "client_credentials",
    }
    response = requests.post(
        machine.api_base_url or PINE_AUTH_URLS[env],
        headers=headers,
        json=payload,
        timeout=20,
    )
    response.raise_for_status()
    return response.json()["access_token"]


def _pine_create_order(machine, access_token, payload):
    env = (machine.environment or "UAT").upper()
    headers = {
        "Content-Type": "application/json",
        "accept": "application/json",
        "Authorization": f"Bearer {access_token}",
        "Request-Timestamp": _utc_now_iso(),
        "Request-ID": str(uuid.uuid4()),
    }
    response = requests.post(
        PINE_ORDER_URLS[env],
        headers=headers,
        json=payload,
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


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
