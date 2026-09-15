"""POS Profile — golden dataset export / import.

Mirrors ``ch_item_master.ch_core.location_hierarchy_seed`` for the till
configuration layer, which had no seed path of its own: POS Profiles are
created by hand, so a rebuilt environment (DR, staging, a new tenant) came
up with none of them and no record of what production actually runs.

Portability
-----------
A POS Profile is stitched to company-suffixed records — ``warehouse``,
``income_account``, ``cost_center`` all end in ``" - <abbr>"``. Those are
exported as base names and re-suffixed against the target company on import,
the same way the location seeder handles warehouses, so one baseline can seed
any environment.

What is deliberately NOT exported
---------------------------------
``applicable_for_users`` is derived state, not configuration:
``ch_erp15.ch_erp15.pos_profile_sync`` recomputes it from CH User Scope on
every scope save and explicitly owns those rows. Seeding its 2,000-odd rows
would fight the sync and re-grant till access that scope had revoked.

Usage
-----
    bench --site erpnext.local execute \
        ch_pos.pos_core.pos_profile_seed.export_to_file \
        --kwargs "{'out_path': '/tmp/pos.json'}"

    bench --site erpnext.local execute \
        ch_pos.pos_core.pos_profile_seed.import_from_file \
        --kwargs "{'in_path': '/tmp/pos.json', 'apply': False}"
"""

import json
import os

import frappe
from ch_item_master.ch_core.location_hierarchy_seed import (
    _company_abbr,
    _company_from_abbr,
    _warehouse_base_name,
    _warehouse_target_name,
)
from frappe.utils import now

SEED_SCHEMA_VERSION = 1
BASELINE_RELATIVE_PATH = os.path.join("data", "seed", "pos_profile_baseline.json")

#: Plain settings copied verbatim — no company-specific naming in them.
_PLAIN_FIELDS = (
    "customer", "country", "disabled", "currency", "selling_price_list",
    "write_off_limit", "taxes_and_charges", "tax_category",
    "disable_rounded_total", "apply_discount_on", "allow_partial_payment",
    "action_on_new_invoice", "validate_stock_on_save", "update_stock",
    "ignore_pricing_rule", "print_receipt_on_order_complete", "hide_images",
    "hide_unavailable_items", "auto_add_item_to_cart", "allow_rate_change",
    "allow_discount_change", "print_format", "letter_head", "tc_name",
    "select_print_heading", "set_grand_total_to_default_mop",
    "custom_pos_mode", "custom_return_auto_approve_limit",
    "custom_return_window_days", "ch_cutoff_time", "ch_cutoff_override_role",
    "ch_petty_cash_daily_limit", "ch_petty_cash_auto_categories",
)

#: Fields whose value carries a trailing " - <abbr>" and must be rebased.
_SUFFIXED_FIELDS = (
    "warehouse", "write_off_account", "write_off_cost_center",
    "income_account", "expense_account", "account_for_change_amount",
    "cost_center",
)

_EXTENSION_FIELDS = (
    "pos_mode", "disabled", "enable_guided_selling", "enable_ai_comparison",
    "enable_ai_upsell", "enable_repair_intake", "enable_buyback_intake",
    "require_token_linkage", "max_comparison_items", "show_cost_price",
    "allow_manual_discount", "allow_rate_change", "receipt_template",
    "invoice_autoclose_seconds", "kiosk_idle_timeout_sec", "default_float",
    "idle_timeout_minutes",
)


# ─────────────────────────────────────────────────────────── export ──

def export_pos_profiles(company: str | None = None) -> dict:
    """Snapshot every POS Profile as a portable, company-agnostic payload."""
    filters = {"company": company} if company else {}
    names = frappe.get_all("POS Profile", filters=filters, pluck="name", order_by="name")

    profiles = []
    for name in names:
        doc = frappe.get_doc("POS Profile", name)
        abbr = _company_abbr(doc.company)

        entry = {
            "profile_name": doc.name,
            "company_abbr": abbr,
            "company_name": doc.company,
        }
        for field in _PLAIN_FIELDS:
            entry[field] = doc.get(field)
        for field in _SUFFIXED_FIELDS:
            entry[field] = _warehouse_base_name(doc.get(field) or "", abbr) or None

        entry["payments"] = [
            {
                "mode_of_payment": row.mode_of_payment,
                "default": row.default,
                "allow_in_returns": row.get("allow_in_returns"),
            }
            for row in (doc.get("payments") or [])
        ]

        ext_name = frappe.db.get_value("POS Profile Extension", {"pos_profile": doc.name}, "name")
        if ext_name:
            ext = frappe.get_doc("POS Profile Extension", ext_name)
            entry["extension"] = {f: ext.get(f) for f in _EXTENSION_FIELDS}
            entry["extension"]["store"] = ext.get("store")
        else:
            entry["extension"] = None

        profiles.append(entry)

    # A POS Profile with no payment method cannot be inserted — ERPNext's own
    # validate() rejects it. Twelve such rows exist in production (created
    # before that validation landed), so they are recorded here rather than
    # seeded: any environment rebuilt from this baseline would otherwise log
    # the same twelve failures on every migrate. Fix is to add a payment
    # method at source, after which the next export picks them up normally.
    seedable = [p for p in profiles if p["payments"]]
    unseedable = [
        {"profile_name": p["profile_name"], "company_abbr": p["company_abbr"],
         "reason": "no payment methods — POS Profile.validate() would reject it"}
        for p in profiles if not p["payments"]
    ]

    return {
        "schema_version": SEED_SCHEMA_VERSION,
        "exported_at": now(),
        "source_site": frappe.local.site,
        "filter_company": company,
        "counts": {
            "profiles": len(seedable),
            "with_extension": sum(1 for p in seedable if p["extension"]),
            "excluded_no_payment_methods": len(unseedable),
        },
        "profiles": seedable,
        "excluded_no_payment_methods": unseedable,
    }


def export_to_file(out_path: str, company: str | None = None) -> dict:
    payload = export_pos_profiles(company=company)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=1, sort_keys=True, ensure_ascii=False, default=str)
    return payload["counts"]


# ─────────────────────────────────────────────────────────── import ──

def import_pos_profiles(data, *, dry_run: bool = True, company_map: dict | None = None) -> dict:
    """Create any POS Profile named in ``data`` that this site is missing.

    Existing profiles are left completely alone. The point is to restore what
    a rebuilt site lacks, never to overwrite tuning someone did on a live till.
    """
    if isinstance(data, str):
        with open(data, "r", encoding="utf-8") as fh:
            data = json.load(fh)

    version = data.get("schema_version")
    if version and version > SEED_SCHEMA_VERSION:
        frappe.throw(
            f"POS Profile seed schema_version={version} is newer than this "
            f"build ({SEED_SCHEMA_VERSION}). Upgrade ch_pos before importing."
        )

    plan = {"dry_run": dry_run, "created": [], "skipped": [], "errors": []}

    for entry in data.get("profiles") or []:
        name = entry.get("profile_name")
        if not name:
            plan["errors"].append({"reason": "no profile_name", "entry": entry})
            continue

        if frappe.db.exists("POS Profile", name):
            plan["skipped"].append({"name": name, "reason": "already exists"})
            continue

        company = (company_map or {}).get(entry.get("company_abbr")) \
            or _company_from_abbr(entry.get("company_abbr")) \
            or entry.get("company_name")
        if not company or not frappe.db.exists("Company", company):
            plan["skipped"].append({"name": name, "reason": f"company unresolved ({entry.get('company_abbr')})"})
            continue

        if dry_run:
            plan["created"].append({"name": name, "company": company})
            continue

        try:
            _create_profile(entry, company)
            plan["created"].append({"name": name, "company": company})
        except Exception as exc:  # noqa: BLE001 — one bad profile must not abort the seed
            plan["errors"].append({"name": name, "error": f"{type(exc).__name__}: {exc}"})

    return plan


def _create_profile(entry: dict, company: str) -> None:
    doc = frappe.new_doc("POS Profile")
    doc.name = entry["profile_name"]
    doc.company = company

    for field in _PLAIN_FIELDS:
        if entry.get(field) is not None:
            doc.set(field, entry[field])

    # Re-suffix company-scoped links onto the target company, and drop any
    # that do not exist here rather than failing the whole profile.
    for field in _SUFFIXED_FIELDS:
        base = entry.get(field)
        if not base:
            continue
        target = _warehouse_target_name(base, company)
        meta = doc.meta.get_field(field)
        if meta and meta.options and frappe.db.exists(meta.options, target):
            doc.set(field, target)

    for row in entry.get("payments") or []:
        if frappe.db.exists("Mode of Payment", row.get("mode_of_payment")):
            doc.append("payments", {
                "mode_of_payment": row["mode_of_payment"],
                "default": row.get("default") or 0,
                "allow_in_returns": row.get("allow_in_returns") or 0,
            })

    doc.flags.ignore_permissions = True
    doc.flags.ignore_mandatory = True
    doc.insert(ignore_permissions=True)

    ext = entry.get("extension")
    if not ext:
        return
    ext_doc = frappe.new_doc("POS Profile Extension")
    ext_doc.pos_profile = doc.name
    for field in _EXTENSION_FIELDS:
        if ext.get(field) is not None:
            ext_doc.set(field, ext[field])
    store = ext.get("store")
    if store and frappe.db.exists("CH Store", store):
        ext_doc.store = store
    ext_doc.flags.ignore_permissions = True
    ext_doc.flags.ignore_mandatory = True
    ext_doc.insert(ignore_permissions=True)


def import_from_file(in_path: str, apply: bool = False, company_map_json: str | None = None) -> dict:
    company_map = json.loads(company_map_json) if company_map_json else None
    return import_pos_profiles(in_path, dry_run=not apply, company_map=company_map)


# ───────────────────────────────────────────────────────── baseline ──

def baseline_seed_path() -> str | None:
    candidate = os.path.join(frappe.get_app_path("ch_pos"), BASELINE_RELATIVE_PATH)
    return candidate if os.path.exists(candidate) else None


def seed_baseline_pos_profiles() -> dict:
    """after_migrate hook — restore any POS Profile this site is missing."""
    path = baseline_seed_path()
    if not path:
        print(f"seed_baseline_pos_profiles: no baseline at ch_pos/{BASELINE_RELATIVE_PATH}; skipping")
        return {"skipped": "no baseline"}

    plan = import_pos_profiles(path, dry_run=False)
    print(
        "seed_baseline_pos_profiles: "
        f"created={len(plan['created'])} skipped={len(plan['skipped'])} errors={len(plan['errors'])}"
    )
    for err in plan["errors"][:5]:
        print(f"  ! {err}")
    return plan
