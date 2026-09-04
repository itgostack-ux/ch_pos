"""
CH Queue — Token System API
All public-facing endpoints for the kiosk and management dashboard.
"""

import hashlib
import re

import frappe
from ch_erp15.warranty import normalize as _normalize_warranty
from frappe import _
from frappe.model.naming import getseries
from frappe.rate_limiter import rate_limit
from frappe.utils import now_datetime, get_datetime, cint, add_to_date

from buyback.utils import normalize_indian_phone, validate_indian_phone
from ch_pos.api.scope_guard import (
    assert_pos_profile_scope,
    assert_store_scope,
    get_pos_profile_anchors)
from ch_pos.config import (
	get_control_setting,
	has_configured_roles,
	is_privileged_user,
	require_configured_roles)
from ch_pos.rate_limits import increment_fixed_window


def _configured_limit(fieldname: str, default: int, maximum: int) -> int:
    value = cint(get_control_setting(fieldname, default)) or default
    return max(1, min(value, maximum))


def _ensure_result_limit(rows, limit: int, label: str):
    if len(rows) > limit:
        frappe.throw(
            _("{0} exceeds the configured limit of {1} rows. Narrow the filters or raise the limit.").format(
                label, limit
            )
        )
    return rows


# ---------------------------------------------------------------------------
# Authority gate (Tier-1 — see ch_erp15.ch_erp15.auth.authority)
# ---------------------------------------------------------------------------

def _ensure_can_operate_token() -> None:
    """Use the standard DocType write permission for token operations."""
    if is_privileged_user():
        return
    if frappe.has_permission("POS Kiosk Token", ptype="write"):
        return
    frappe.throw(_("Not permitted"), frappe.PermissionError, title=_("API Error"))


def _ensure_can_view_tokens() -> None:
    frappe.has_permission("POS Kiosk Token", ptype="read", throw=True)


# ---------------------------------------------------------------------------
# Store-scope guards (queue / token reads + actions)
# ---------------------------------------------------------------------------

def _scoped_warehouses_companies():
    """Return ``(warehouses, companies, bypass)`` for the current user.

    ``bypass`` is True only for unrestricted users (System Manager or
    Administrator). Missing scope infrastructure and empty assignments both
    fail closed for every other caller.
    """
    if is_privileged_user():
        return None, None, True
    try:
        from ch_erp15.ch_erp15.scope import get_user_scope
    except ImportError:
        return set(), set(), False
    scope = get_user_scope()
    if scope.get("bypass"):
        return None, None, True
    return (scope.get("warehouses") or set()), (scope.get("companies") or set()), False


def _assert_pos_profile_scope(pos_profile: str) -> None:
    """Refuse a pos_profile whose warehouse/company is outside the caller's scope."""
    if not pos_profile:
        return
    profile = _resolve_pos_profile(pos_profile)
    if not profile:
        frappe.throw(_("Invalid POS Profile"), title=_("API Error"))
    assert_pos_profile_scope(profile.name)


def _apply_token_scope_filters(filters: dict) -> dict:
    """Constrain a POS Kiosk Token query to the caller's allowed stores.

    Used when no explicit pos_profile is supplied so a scoped manager cannot
    pull every store's queue (which carries customer name + phone). Fail
    CLOSED: an empty scope matches nothing.
    """
    warehouses, companies, bypass = _scoped_warehouses_companies()
    if bypass:
        return filters
    if warehouses:
        filters["store"] = ("in", list(warehouses))
    elif companies:
        filters["company"] = ("in", list(companies))
    else:
        filters["name"] = ("in", ["__none__"])
    return filters


def _assert_token_scope(token_name: str) -> None:
    """Refuse operating on a token whose store/company is outside scope.

    ``_ensure_can_operate_token`` proves the user MAY operate tokens; this
    proves they may operate THIS store's token.
    """
    if not token_name:
        return
    tok = frappe.db.get_value(
        "POS Kiosk Token", token_name, ["store", "company"], as_dict=True
    )
    if not tok:
        frappe.throw(_("Queue token was not found."), frappe.DoesNotExistError)
    assert_store_scope(warehouse=tok.store, company=tok.company)


def _assert_token_assignee(user: str, pos_profile: str) -> None:
    """Validate that an assigned user is active, suitably role-gated, and store-scoped."""
    user_row = frappe.db.get_value(
        "User", user, ["name", "enabled", "user_type"], as_dict=True
    )
    if not user_row or not user_row.enabled or user_row.user_type != "System User":
        frappe.throw(_("The selected assignee is not an active System User."), frappe.PermissionError)
    if not has_configured_roles(
        "token_assignee_roles",
        user=user):
        frappe.throw(_("The selected user cannot be assigned queue tokens."), frappe.PermissionError)
    anchors = get_pos_profile_anchors(pos_profile)
    assert_store_scope(
        store=anchors.get("store"),
        warehouse=anchors.get("warehouse"),
        company=anchors.get("company"),
        user=user)


def _assert_sales_executive(sales_executive: str, pos_profile: str) -> None:
    if not sales_executive:
        return
    executive = frappe.db.get_value(
        "POS Executive",
        sales_executive,
        ["name", "user", "company", "store", "is_active"],
        as_dict=True)
    if not executive or not executive.is_active:
        frappe.throw(_("The selected POS Executive is inactive or unavailable."), frappe.PermissionError)
    anchors = get_pos_profile_anchors(pos_profile)
    if executive.company != anchors.get("company") or (
        anchors.get("store") and executive.store != anchors.get("store")
    ):
        frappe.throw(_("The selected POS Executive belongs to a different store."), frappe.PermissionError)
    assert_store_scope(
        store=executive.store,
        warehouse=anchors.get("warehouse"),
        company=executive.company,
        user=executive.user)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_INDIAN_PHONE_RE = re.compile(r"^[6-9]\d{9}$")

def _check_rate_limit(key: str):
    """Raise if rate limit exceeded for this key (IP/store combo).

    Uses frappe.cache() (Redis) so limits are enforced across all Gunicorn workers.
    """
    request_limit = max(
        1, min(cint(get_control_setting("token_create_rate_limit_max", 10)), 1000)
    )
    window_seconds = max(
        60,
        min(
            cint(get_control_setting("token_create_rate_limit_window_seconds", 3600)),
            86400))
    hits = increment_fixed_window("kiosk-token-create", key, window_seconds)
    if hits > request_limit:
        frappe.throw(_("Too many requests. Please try again later."), frappe.RateLimitExceededError, title=_("API Error"))

def _normalize_phone(raw: str) -> str:
    """Alias for backward compatibility — delegates to shared utility."""
    return normalize_indian_phone(raw)

def _validate_indian_phone(raw: str) -> str:
    """Alias for backward compatibility — delegates to shared utility."""
    return validate_indian_phone(raw, "Phone number")

def _device_label(brand: str, model: str) -> str:
    """Return a clean device label, avoiding repeating the brand if model already starts with it."""
    brand = (brand or "").strip()
    model = (model or "").strip()
    if not model:
        return brand
    # If model already starts with brand name (case-insensitive), show model only
    if brand and model.lower().startswith(brand.lower()):
        return model
    return f"{brand} {model}".strip() if brand else model


def _get_store_code(pos_profile_name: str) -> tuple[str, bool]:
    """Return (code, is_canonical) identifying the store behind a POS Profile.

    Prefers ``CH Store.store_code``, which is the real per-store identifier
    ("GF-DOVETON", "GF-KELLYS"). Only if no CH Store maps to this profile does
    it fall back to shortening the profile name.

    The old shortener parsed the profile NAME, and on this bench every profile
    is called "POS - STO-GSPL-CHENNA-NNNN". Splitting on spaces made the lone
    "-" a word and then took "STO" from the shared legacy prefix, so every one
    of the 24 stores produced the same code "-STO" and tokens read
    "GF--STO-001" — identical at every counter, which is exactly what a store
    code must not be.
    """
    code = frappe.db.get_value(
        "CH Store", {"pos_profile": pos_profile_name, "disabled": 0}, "store_code"
    )
    if code:
        return code.strip().upper(), True

    # Fallback: shorten the profile name, ignoring fragments that carry no
    # letters so a separator can never become part of the code.
    parts = [p for p in pos_profile_name.upper().split() if re.search(r"[A-Z0-9]", p)]
    noise = {"POS", "QA", "THE", "AND", "&"}
    meaningful = [p for p in parts if p not in noise] or parts
    joined = "".join(re.sub(r"[^A-Z0-9]", "", p)[:3] for p in meaningful[:2])
    return (joined[:6] or "STORE"), False


def _next_daily_seq(pos_profile: str) -> int:
    """Return the next atomic per-profile sequence for the current date."""
    today = frappe.utils.today()
    existing_max = frappe.db.sql(
        """SELECT COALESCE(
                    MAX(CAST(SUBSTRING_INDEX(token_display, '-', -1) AS UNSIGNED)),
                    0
                  )
           FROM `tabPOS Kiosk Token`
           WHERE pos_profile = %s
             AND DATE(creation) = %s
        """,
        (pos_profile, today))[0][0] or 0
    issued_today = frappe.db.count(
        "POS Kiosk Token", {"pos_profile": pos_profile, "creation": (">=", today)}
    )
    profile_digest = hashlib.sha256(pos_profile.encode()).hexdigest()[:24]
    series_key = f"CH-POS-TOKEN-{today}-{profile_digest}"

    if not issued_today:
        # No token has actually been issued at this counter today, so anything
        # sitting in the allocator is burn: the number is consumed when the
        # display is generated, and a submission that then fails validation
        # never becomes a token. That is how this counter reached 100 on a day
        # with a single token. The first customer of the day must be 001, so
        # reset rather than carry the burn forward.
        frappe.db.sql(
            """
            INSERT INTO `tabSeries` (`name`, `current`) VALUES (%s, 0)
            ON DUPLICATE KEY UPDATE `current` = 0
            """,
            (series_key,))
    else:
        # Once real tokens exist the allocator must only ever move forward —
        # walking it back would re-issue a number a customer is already holding.
        frappe.db.sql(
            """
            INSERT INTO `tabSeries` (`name`, `current`)
            VALUES (%s, %s)
            ON DUPLICATE KEY UPDATE
                `current` = GREATEST(`current`, VALUES(`current`))
            """,
            (series_key, cint(existing_max)))
    return cint(getseries(series_key, 10))


def _generate_token_display(pos_profile: str, company_abbr: str) -> str:
    """Human-readable token, e.g. ``GF-DOVETON-001``.

    The sequence restarts at 001 each day for each store.
    """
    store_code, canonical = _get_store_code(pos_profile)
    seq = _next_daily_seq(pos_profile)
    if canonical:
        # A CH Store code already carries its company prefix ("GF-DOVETON"),
        # so prefixing the abbreviation again would read "GF-GF-DOVETON-001".
        return f"{store_code}-{seq:03d}"
    abbr = (company_abbr or "CH")[:4].upper()
    return f"{abbr}-{store_code}-{seq:03d}"


def _resolve_pos_profile(identifier: str) -> dict | None:
    """Resolve a store identifier to a POS Profile row.

    Accepts:
    - Exact POS Profile name
    - CH Store identifiers (name/store_code/store_name/linked pos_profile)
    - Fuzzy POS Profile match as last resort
    """
    if not identifier:
        return None

    # 1) Exact POS Profile name
    profile = frappe.db.get_value(
        "POS Profile",
        identifier,
        ["name", "company", "warehouse"],
        as_dict=True)
    if profile:
        return profile

    # 2) CH Store mapping (if available)
    if frappe.db.exists("DocType", "CH Store"):
        store_candidates = frappe.get_all(
            "CH Store",
            filters={"disabled": 0},
            or_filters=[
                ["name", "=", identifier],
                ["store_code", "=", identifier],
                ["store_name", "=", identifier],
                ["store_name", "like", f"%{identifier}%"],
                ["pos_profile", "=", identifier],
                ["warehouse", "=", identifier],
            ],
            fields=["pos_profile"],
            limit_page_length=5)
        mapped_profiles = sorted({(r.get("pos_profile") or "").strip() for r in store_candidates if r.get("pos_profile")})
        if len(mapped_profiles) == 1:
            profile = frappe.db.get_value(
                "POS Profile",
                mapped_profiles[0],
                ["name", "company", "warehouse"],
                as_dict=True)
            if profile:
                return profile

    # 3) Fuzzy POS Profile fallback (must be unambiguous)
    candidates = frappe.get_all(
        "POS Profile",
        filters={"name": ["like", f"%{identifier}%"]},
        fields=["name", "company", "warehouse"],
        limit_page_length=5)
    if len(candidates) == 1:
        return candidates[0]

    return None


def _with_pos_profile_lock(pos_profile: str, callback):
    lock_key = f"pos_billing_lock_{pos_profile}"
    locked = frappe.db.sql("SELECT GET_LOCK(%s, 30)", (lock_key))[0][0]
    if locked != 1:
        frappe.throw(_("Another billing request is being processed. Please try again."), title=_("Billing Busy"))
    try:
        return callback()
    finally:
        frappe.db.sql("SELECT RELEASE_LOCK(%s)", (lock_key))


def _active_billing_token(pos_profile: str, exclude_token: str = "") -> dict | None:
    filters = {
        "pos_profile": pos_profile,
        "status": "In Progress",
        "docstatus": 1,
    }
    if exclude_token:
        filters["name"] = ["!=", exclude_token]
    rows = frappe.get_all(
        "POS Kiosk Token",
        filters=filters,
        fields=["name", "token_display", "customer_name", "status"],
        order_by="modified desc",
        limit_page_length=1)
    return rows[0] if rows else None


def _release_held_tokens(pos_profile: str) -> list[str]:
    batch_limit = max(1, min(cint(get_control_setting("scheduler_batch_limit", 500)), 5000))
    hold_names = frappe.get_all(
        "POS Kiosk Token",
        filters={
            "pos_profile": pos_profile,
            "status": "Hold",
            "docstatus": 1,
        },
        pluck="name",
        order_by="modified asc, name asc",
        limit_page_length=batch_limit)
    if hold_names:
        frappe.db.set_value(
            "POS Kiosk Token",
            {"name": ("in", hold_names)},
            "status",
            "Waiting",
            update_modified=False)
    return hold_names


# ---------------------------------------------------------------------------
# Guest API — Kiosk
# ---------------------------------------------------------------------------

def _bounded_public_text(value, label: str, max_length: int, *, required: bool = False) -> str:
    text = str(value or "").strip()
    if required and not text:
        frappe.throw(_("{0} is required").format(label), title=_("API Error"))
    if len(text) > max_length:
        frappe.throw(
            _("{0} cannot exceed {1} characters").format(label, max_length),
            title=_("API Error"))
    return text

@frappe.whitelist(allow_guest=True)
@rate_limit(limit=120, seconds=60, ip_based=True)
def get_store_config(pos_profile: str) -> dict:
    """
    Returns store configuration for the kiosk page dropdowns.
    Called on kiosk load with ?store=<pos_profile>.
    """
    pos_profile = _bounded_public_text(pos_profile, _("Store"), 140, required=True)
    profile = _resolve_pos_profile(pos_profile)

    if not profile:
        frappe.throw(_("Store not found"), frappe.DoesNotExistError, title=_("API Error"))

    company = frappe.db.get_value("Company", profile.company, ["name", "abbr"], as_dict=True)

    # Device brands — pulled from Brand doctype, top-level only (no sub-brands)
    # Sub-brands have ch_parent_brand set (e.g. Galaxy → Samsung)
    # Some environments may not yet have custom Brand fields during rollout.
    # Fall back to a plain Brand list instead of failing with SQL 1054.
    if frappe.db.has_column("Brand", "ch_disabled") and frappe.db.has_column("Brand", "ch_parent_brand"):
        _raw_brands = frappe.db.sql(
            """SELECT name FROM `tabBrand`
               WHERE ch_disabled = 0
                 AND (ch_parent_brand IS NULL OR ch_parent_brand = '')
                 AND name != 'Test Brand'
               ORDER BY name ASC""",
            as_dict=True)
    else:
        _raw_brands = frappe.db.sql(
            """SELECT name FROM `tabBrand`
               WHERE name != 'Test Brand'
               ORDER BY name ASC""",
            as_dict=True)
    brands = [b.name for b in _raw_brands]
    if "Other" not in brands:
        brands.append("Other")

    # Issue categories
    issues = [
        {"key": "Screen Replacement", "icon": "📱"},
        {"key": "Screen Repair", "icon": "🔧"},
        {"key": "Battery Replacement", "icon": "🔋"},
        {"key": "Charging Port", "icon": "⚡"},
        {"key": "Camera Repair", "icon": "📷"},
        {"key": "Water Damage", "icon": "💧"},
        {"key": "Software Issue", "icon": "💾"},
        {"key": "Speaker / Mic", "icon": "🔊"},
        {"key": "Back Panel", "icon": "🪟"},
        {"key": "Other", "icon": "🛠️"},
    ]

    device_types = _device_choices()
    return {
        "store_name": profile.name,
        "company": profile.company,
        "company_abbr": company.abbr if company else "CH",
        "brands": brands,
        "device_types": device_types,
        "brands_by_category": {d["name"]: _brands_for_category(d["name"]) for d in device_types},
        "issues": issues,
    }


@frappe.whitelist(allow_guest=True)
@rate_limit(limit=120, seconds=60, ip_based=True)
def get_brand_models(brand: str, ch_category: str = "") -> list:
    """Models for a brand from the CH Model master, as ``[{value, label}]``.

    Used by the kiosk's model picker. Optionally narrowed to a device
    category so a Samsung phone customer is not offered Samsung laptops.
    """
    brand = _bounded_public_text(brand, _("Brand"), 140, required=True)
    ch_category = _bounded_public_text(ch_category, _("Device Category"), 140)
    return _models_for(brand, ch_category)


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(limit=20, seconds=300, methods=["POST"], ip_based=True)
def create_token(
    pos_profile: str,
    customer_name: str,
    customer_phone: str,
    device_type: str,
    device_brand: str,
    device_model: str,
    issue_category: str,
    issue_description: str = "") -> dict:
    """
    Create a new queue token. Called from the kiosk (no login required).
    Returns the token display number and doc name.
    """
    pos_profile = _bounded_public_text(pos_profile, _("Store"), 140, required=True)
    customer_name = _bounded_public_text(
        customer_name, _("Customer Name"), 140, required=True
    )
    customer_phone = _bounded_public_text(
        customer_phone, _("Customer Phone"), 20, required=True
    )
    device_type = _bounded_public_text(device_type, _("Device Type"), 140)
    device_brand = _bounded_public_text(device_brand, _("Device Brand"), 140)
    device_model = _bounded_public_text(device_model, _("Device Model"), 140)
    issue_category = _bounded_public_text(
        issue_category, _("Issue Category"), 140, required=True
    )
    issue_description = _bounded_public_text(
        issue_description, _("Issue Description"), 1000
    )

    # Rate limit by IP
    client_ip = frappe.local.request.remote_addr if hasattr(frappe.local, "request") and frappe.local.request else "unknown"
    _check_rate_limit(f"token_{client_ip}_{pos_profile}")

    # Input validation
    if not customer_name or not customer_phone:
        frappe.throw(_("Customer name and phone are required"), title=_("API Error"))
    if not pos_profile:
        frappe.throw(_("Store is required"), title=_("API Error"))
    customer_phone = _validate_indian_phone(customer_phone)  # normalize + validate

    profile = _resolve_pos_profile(pos_profile)
    if not profile:
        frappe.throw(_("Invalid POS Profile"), title=_("API Error"))

    company_abbr = frappe.db.get_value("Company", profile.company, "abbr") or "CH"
    device = _normalise_device(device_type, device_brand, device_model)

    token_display = _generate_token_display(pos_profile, company_abbr)
    doc = frappe.get_doc(
        {
            "doctype": "POS Kiosk Token",
            "pos_profile": pos_profile,
            "company": profile.company,
            "store": profile.warehouse,
            "status": "Waiting",
            "token_display": token_display,
            "customer_name": customer_name,
            "customer_phone": customer_phone,
            "device_type": device["device_type"],
            "device_brand": device["device_brand"],
            "device_model": device["device_model"],
            "other_device_hint": device["other_device_hint"],
            "issue_category": issue_category,
            "issue_description": issue_description,
            "visit_source": "Kiosk",
            "visit_purpose": "Repair",
            "expires_at": frappe.utils.add_days(now_datetime(), 1),
        }
    )
    doc.flags.ignore_permissions = True
    doc.insert()
    doc.submit()

    return {
        "token": token_display,
        "name": doc.name,
        "customer_name": doc.customer_name,
        "store": pos_profile,
        "device": device_model if device_model else device_brand,
        "issue": issue_category,
        "created_at": str(doc.creation),
    }


# ---------------------------------------------------------------------------
# Guest API — GoFix self check-in tablet (/gofix-token)
#
# The tablet used to write its own ``GoFix Token`` doctype, so a customer who
# checked in on the tablet and then walked to the counter became two tokens
# in two doctypes that never met. It now writes the same POS Kiosk Token the
# counter logs, so one queue carries the customer end to end. The GoFix
# masters (visit reasons, device types, symptoms) stay in gofix; only the
# token moved.
# ---------------------------------------------------------------------------

def _company_is_gofix_enabled(company: str | None) -> bool:
    """True only when the Company is explicitly flagged for GoFix self check-in."""
    if not company or not frappe.db.has_column("Company", "gofix_enabled"):
        return False
    return bool(frappe.db.get_value("Company", company, "gofix_enabled"))


def _gofix_int_setting(fieldname: str, default: int) -> int:
    """GoFix Settings integer when gofix is installed, else the default."""
    try:
        from gofix.config import get_int_setting
    except ImportError:
        return default
    try:
        return get_int_setting(fieldname, default)
    except Exception:
        return default


def _annotate_gofix_enabled(profiles: list) -> list:
    """Stamp ``gofix_enabled`` on profile rows so callers can pick the check-in URL."""
    if not profiles or not frappe.db.has_column("Company", "gofix_enabled"):
        for row in profiles or []:
            row["gofix_enabled"] = 0
        return profiles
    companies = sorted({row.get("company") for row in profiles if row.get("company")})
    enabled = set(
        frappe.get_all(
            "Company",
            filters={"name": ("in", companies), "gofix_enabled": 1},
            pluck="name",
            limit_page_length=len(companies) + 1,
        )
    ) if companies else set()
    for row in profiles:
        row["gofix_enabled"] = 1 if row.get("company") in enabled else 0
    return profiles


# ---------------------------------------------------------------------------
# Device taxonomy — the item master is the only source
#
# Device category = CH Category flagged "Repairable Device Category", brand =
# Brand, model = CH Model. Anything the customer types that is not in the
# master lands in ``other_device_hint`` instead of polluting a Link field.
# ---------------------------------------------------------------------------

OTHER_DEVICE = "Other"

# Labels the retired GoFix Device Type master (and the old kiosk tiles) used.
_LEGACY_DEVICE_TYPE_TO_CATEGORY = {
    "Mobile": "Smart Phones",
    "Tablet": "Tablets",
    "Laptop": "Laptops",
    "Smartwatch": "Watches",
    "Smart Watch": "Watches",
}


def _repairable_categories() -> list[dict]:
    """CH Categories flagged for repair intake, in kiosk order."""
    if not frappe.db.has_column("CH Category", "is_repairable_device"):
        return []
    rows = frappe.get_all(
        "CH Category",
        filters={"is_repairable_device": 1, "disabled": 0},
        fields=["name", "device_icon", "kiosk_display_order"],
        order_by="kiosk_display_order asc, name asc",
        limit_page_length=50)
    return [
        {"name": r.name, "icon": r.device_icon or "\U0001f527", "display_order": r.kiosk_display_order or 0}
        for r in rows
    ]


def _device_choices() -> list[dict]:
    """Repairable categories plus the one pseudo-choice, Other."""
    return _repairable_categories() + [
        {"name": OTHER_DEVICE, "icon": "\U0001f527", "display_order": 999, "is_other": True}
    ]


def _brands_for_category(category: str, limit: int = 60) -> list[str]:
    """Brands that have an active CH Model in the category, busiest first, then Other."""
    if not category or category == OTHER_DEVICE:
        return [OTHER_DEVICE]
    rows = frappe.db.sql(
        """
        SELECT m.brand, COUNT(*) AS n
        FROM `tabCH Model` m
        JOIN `tabCH Sub Category` sc ON sc.name = m.sub_category
        WHERE m.disabled = 0 AND m.brand IS NOT NULL AND m.brand <> ''
          AND sc.category = %s
        GROUP BY m.brand
        ORDER BY n DESC, m.brand ASC
        LIMIT %s
        """,
        (category, limit))
    return [r[0] for r in rows] + [OTHER_DEVICE]


def _models_for(brand: str, category: str = "", txt: str = "", limit: int = 100) -> list[dict]:
    """CH Models for a brand (optionally within a category) as {value, label}."""
    if not brand or brand == OTHER_DEVICE:
        return []
    conditions = ["m.disabled = 0", "m.brand = %(brand)s"]
    values = {"brand": brand, "limit": limit}
    if txt:
        conditions.append("m.model_name LIKE %(txt)s")
        values["txt"] = f"%{txt}%"
    if category and category != OTHER_DEVICE:
        conditions.append(
            "EXISTS (SELECT 1 FROM `tabCH Sub Category` sc WHERE sc.name = m.sub_category AND sc.category = %(category)s)")
        values["category"] = category
    rows = frappe.db.sql(
        f"""
        SELECT m.name, m.model_name FROM `tabCH Model` m
        WHERE {' AND '.join(conditions)}
        ORDER BY m.model_name ASC
        LIMIT %(limit)s
        """,
        values)
    return [{"value": name, "label": model_name} for name, model_name in rows]


def _resolve_model(model: str, brand: str = "", category: str = "") -> str:
    """CH Model docname for a docname or a readable model_name; "" when not confident.

    Permission-free on purpose: the kiosk and the tablet run as Guest.
    """
    model = (model or "").strip()
    if not model:
        return ""
    canonical = frappe.db.get_value("CH Model", {"name": model, "disabled": 0}, "name")
    if canonical:
        return canonical

    def _unique(filters):
        rows = frappe.get_all("CH Model", filters=filters, pluck="name", limit_page_length=2)
        return rows[0] if len(rows) == 1 else ""

    if brand:
        hit = _unique({"model_name": model, "brand": brand, "disabled": 0})
        if hit:
            return hit
    return _unique({"model_name": model, "disabled": 0})


def _normalise_device(device_type: str, device_brand: str, device_model: str, hint: str = "") -> dict:
    """Map device input onto item-master links; whatever does not match goes to the hint."""
    leftovers = []
    # get_value returns the master's own spelling, so "samsung" is stored as
    # "Samsung" rather than however the customer typed it.
    category = (device_type or "").strip()
    if category in ("", OTHER_DEVICE):
        category = ""
    else:
        canonical = frappe.db.get_value("CH Category", category, "name")
        if not canonical:
            mapped = _LEGACY_DEVICE_TYPE_TO_CATEGORY.get(category)
            canonical = frappe.db.get_value("CH Category", mapped, "name") if mapped else None
        if canonical:
            category = canonical
        else:
            leftovers.append(category)
            category = ""

    brand = (device_brand or "").strip()
    if brand in ("", OTHER_DEVICE):
        brand = ""
    else:
        canonical = frappe.db.get_value("Brand", brand, "name")
        if canonical:
            brand = canonical
        else:
            leftovers.append(brand)
            brand = ""

    model_text = (device_model or "").strip()
    model = _resolve_model(model_text, brand, category) if model_text else ""
    if model_text and not model:
        leftovers.append(model_text)

    hint = (hint or "").strip()
    extra = " ".join(x for x in leftovers if x)
    if extra:
        hint = f"{hint} ({extra})" if hint else extra
    return {
        "device_type": category,
        "device_brand": brand,
        "device_model": model,
        "other_device_hint": hint[:140],
    }


def _store_identity(profile) -> tuple[str, str]:
    """Return ``(store_code, store_name)`` for a resolved POS Profile row."""
    row = None
    if frappe.db.table_exists("CH Store"):
        row = frappe.db.get_value(
            "CH Store",
            {"pos_profile": profile.name, "disabled": 0},
            ["store_code", "store_name"],
            as_dict=True)
    code = ((row and row.get("store_code")) or "").strip().upper()
    name = (row and row.get("store_name")) or ""
    if not code:
        code, _canonical = _get_store_code(profile.name)
    if not name and profile.get("warehouse"):
        name = frappe.db.get_value("Warehouse", profile.warehouse, "warehouse_name") or profile.warehouse
    return code, name or profile.name


def _resolve_tablet_store(store: str):
    """Resolve the tablet's ``?store=`` to a POS Profile of a GoFix-enabled company."""
    store = _bounded_public_text(store, _("Store"), 140, required=True)
    profile = _resolve_pos_profile(store)
    if not profile or not _company_is_gofix_enabled(profile.company):
        # One message either way: never reveal whether the store exists on a
        # non-GoFix company.
        frappe.throw(_("Store {0} is not configured for GoFix self check-in.").format(store))
    return profile


@frappe.whitelist(allow_guest=True)
@rate_limit(limit=120, seconds=300, ip_based=True)
def get_tablet_config(store: str) -> dict:
    """Everything the self check-in tablet needs to render its wizard."""
    profile = _resolve_tablet_store(store)
    for master in ("GoFix Visit Reason", "GoFix Symptom"):
        if not frappe.db.table_exists(master):
            frappe.throw(_("GoFix intake masters are not installed on this site."))
    _check_rate_limit(f"tablet_config_{_client_ip()}_{profile.name}")
    limit = min(_gofix_int_setting("token_queue_limit", 200), 2000)

    device_types = _device_choices()
    visit_reasons = frappe.get_all(
        "GoFix Visit Reason",
        filters={"disabled": 0},
        fields=["reason_name", "is_repair", "display_order"],
        order_by="display_order asc, reason_name asc",
        limit_page_length=limit)
    symptom_rows = frappe.get_all(
        "GoFix Symptom",
        filters={"disabled": 0},
        fields=["device_category", "symptom_name", "is_expert_check", "is_other", "display_order"],
        order_by="device_category asc, display_order asc, symptom_name asc",
        limit_page_length=limit)

    brands_by_device: dict = {}
    for d in device_types:
        brands_by_device[d["name"]] = [
            {"name": b, "display_order": i * 10} for i, b in enumerate(_brands_for_category(d["name"]), start=1)
        ]
    symptoms_by_device: dict = {}
    for r in symptom_rows:
        symptoms_by_device.setdefault(r["device_category"] or OTHER_DEVICE, []).append({
            "name": r["symptom_name"],
            "is_expert_check": bool(r["is_expert_check"]),
            "is_other": bool(r["is_other"]),
            "display_order": r["display_order"],
        })

    store_code, store_name = _store_identity(profile)
    return {
        "store": {
            "code": store_code,
            "name": store_name,
            "company": profile.company,
            "warehouse": profile.warehouse,
            "pos_profile": profile.name,
        },
        "device_types": device_types,
        "visit_reasons": [
            {"name": v["reason_name"], "is_repair": bool(v["is_repair"]), "display_order": v["display_order"]}
            for v in visit_reasons
        ],
        "brands_by_device": brands_by_device,
        "symptoms_by_device": symptoms_by_device,
        "rules": {
            "max_issues": _gofix_int_setting("max_selected_issues", 3),
            "expert_check_exclusive": True,
            "other_notes_required": False,
            "phone_country_code": "+91",
            "phone_digits": 10,
        },
    }


def _client_ip() -> str:
    request = getattr(frappe.local, "request", None)
    return getattr(request, "remote_addr", None) or "unknown"


def _parse_symptoms(payload, resolved: dict) -> list[dict]:
    """Normalise the tablet's symptom payload into child rows.

    Accepts a JSON string or a list; items are plain names or dicts with
    ``name`` plus optional flag overrides. Unknown names are kept so ops can
    add symptoms on the tablet before the master catches up.
    """
    if not payload:
        return []
    if isinstance(payload, str):
        try:
            import json

            payload = json.loads(payload)
        except (ValueError, TypeError):
            payload = [s.strip() for s in payload.split(",") if s.strip()]
    rows: list[dict] = []
    for item in payload:
        if isinstance(item, dict):
            name = (item.get("name") or item.get("symptom_name") or "").strip()
            overrides = item
        else:
            name = str(item).strip()
            overrides = {}
        if not name:
            continue
        match = resolved.get(name)
        rows.append({
            "symptom_name": name[:140],
            "device_category": (match and match.get("device_category")) or overrides.get("device_category") or None,
            "is_expert_check": 1 if (overrides.get("is_expert_check") or (match and match.get("is_expert_check"))) else 0,
            "is_other": 1 if (overrides.get("is_other") or (match and match.get("is_other"))) else 0,
            "symptom_ref": match.get("name") if match else None,
        })
    return rows


def _first_backend_category(symptom_rows: list[dict]) -> str:
    """Issue Category behind the first mapped symptom, for the job-card handoff."""
    if not frappe.db.has_column("GoFix Symptom", "backend_category"):
        return ""
    for row in symptom_rows:
        if row.get("symptom_ref"):
            category = frappe.db.get_value("GoFix Symptom", row["symptom_ref"], "backend_category")
            if category:
                return category
    return ""


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(limit=20, seconds=300, methods=["POST"], ip_based=True)
def create_tablet_token(
    store: str,
    customer_name: str,
    customer_phone: str,
    visit_reason: str,
    device_type: str = "",
    device_brand: str = "",
    device_model: str = "",
    other_device_hint: str = "",
    selected_issues=None,
    additional_notes: str = "",
    customer_language: str = "") -> dict:
    """Create the walk-in token from the self check-in tablet.

    Same POS Kiosk Token the counter logs, so the customer keeps one token
    from tablet to counter to job card.
    """
    profile = _resolve_tablet_store(store)
    customer_name = _bounded_public_text(customer_name, _("Customer Name"), 140, required=True)
    customer_phone = _bounded_public_text(customer_phone, _("Customer Phone"), 20, required=True)
    visit_reason = _bounded_public_text(visit_reason, _("Visit Reason"), 140, required=True)
    device_type = _bounded_public_text(device_type, _("Device Type"), 140)
    device_brand = _bounded_public_text(device_brand, _("Device Brand"), 140)
    device_model = _bounded_public_text(device_model, _("Device Model"), 140)
    other_device_hint = _bounded_public_text(other_device_hint, _("Other Device"), 140)
    additional_notes = _bounded_public_text(additional_notes, _("Notes"), 1000)
    customer_language = _bounded_public_text(customer_language, _("Language"), 40) or "English"

    _check_rate_limit(f"tablet_{_client_ip()}_{profile.name}")
    customer_phone = _validate_indian_phone(customer_phone)

    visit_row = frappe.db.get_value(
        "GoFix Visit Reason", visit_reason, ["name", "is_repair", "disabled"], as_dict=True)
    if not visit_row or visit_row.get("disabled"):
        frappe.throw(_("Visit reason {0} is not available.").format(visit_reason))
    is_repair = bool(visit_row.get("is_repair"))

    device = _normalise_device(device_type, device_brand, device_model, other_device_hint) if is_repair else {
        "device_type": "", "device_brand": "", "device_model": "", "other_device_hint": ""}
    if is_repair and not device["device_type"] and not device["other_device_hint"]:
        frappe.throw(_("Pick a device type, or describe your device."))

    symptom_lookup: dict = {}
    if is_repair:
        symptom_filters = {"disabled": 0}
        if device["device_type"]:
            symptom_filters["device_category"] = device["device_type"]
        else:
            symptom_filters["device_category"] = ("is", "not set")
        for r in frappe.get_all(
            "GoFix Symptom",
            filters=symptom_filters,
            fields=["name", "symptom_name", "device_category", "is_expert_check", "is_other"],
            limit_page_length=500,
        ):
            symptom_lookup[r["symptom_name"]] = r
    symptoms = _parse_symptoms(selected_issues, symptom_lookup) if is_repair else []
    if is_repair and not symptoms:
        frappe.throw(_("Select at least one symptom for a repair visit."))

    company_abbr = frappe.db.get_value("Company", profile.company, "abbr") or "CH"
    token_display = _generate_token_display(profile.name, company_abbr)
    doc = frappe.get_doc({
        "doctype": "POS Kiosk Token",
        "pos_profile": profile.name,
        "company": profile.company,
        "store": profile.warehouse,
        "status": "Waiting",
        "token_display": token_display,
        "customer_name": customer_name,
        "customer_phone": customer_phone,
        "customer_language": customer_language if customer_language in ("English", "Hindi") else "English",
        "visit_source": "Kiosk",
        "visit_purpose": "Repair" if is_repair else "Enquiry",
        "visit_reason": visit_reason,
        "device_type": device["device_type"],
        "device_brand": device["device_brand"],
        "device_model": device["device_model"],
        "other_device_hint": device["other_device_hint"],
        "issue_category": _first_backend_category(symptoms) if is_repair else "",
        "issue_description": additional_notes,
        "symptoms": symptoms,
        "whatsapp_status": "Not Sent",
        "expires_at": frappe.utils.add_days(now_datetime(), 1),
    })
    doc.flags.ignore_permissions = True
    doc.insert()
    doc.submit()

    # WhatsApp confirmation is fire-and-forget; never block the token on it.
    if "gofix" in frappe.get_installed_apps():
        try:
            frappe.enqueue(
                "gofix.gofix_services.whatsapp_notifications.send_token_confirmation",
                queue="short",
                token_name=doc.name,
                enqueue_after_commit=True)
        except Exception:
            frappe.log_error(title="tablet token: whatsapp enqueue failed", message=frappe.get_traceback())

    store_code, store_name = _store_identity(profile)
    return {
        "name": doc.name,
        "token_number": token_display,
        "queue_position": _queue_position(doc.name, profile.name),
        "status": doc.status,
        "whatsapp_status": doc.whatsapp_status,
        "store_code": store_code,
        "store_name": store_name,
        "business_date": frappe.utils.today(),
    }


def _queue_position(token_name: str, pos_profile: str) -> int:
    """1-based place among today's Waiting tokens at this counter; 0 once called."""
    status = frappe.db.get_value("POS Kiosk Token", token_name, "status")
    if status != "Waiting":
        return 0
    ahead = frappe.db.sql(
        """
        SELECT COUNT(*) FROM `tabPOS Kiosk Token`
        WHERE pos_profile = %s AND status = 'Waiting' AND docstatus < 2
          AND DATE(creation) = %s
          AND creation < (SELECT creation FROM `tabPOS Kiosk Token` WHERE name = %s)
        """,
        (pos_profile, frappe.utils.today(), token_name))[0][0]
    return int(ahead or 0) + 1


@frappe.whitelist(allow_guest=True)
@rate_limit(limit=120, seconds=300, ip_based=True)
def get_queue_position(token_number: str, store: str) -> dict:
    """Polled by the tablet's confirmation screen."""
    profile = _resolve_tablet_store(store)
    token_number = _bounded_public_text(token_number, _("Token"), 40, required=True)
    _check_rate_limit(f"tablet_position_{_client_ip()}_{profile.name}")
    row = frappe.db.get_value(
        "POS Kiosk Token",
        {"token_display": token_number, "pos_profile": profile.name,
         "creation": (">=", frappe.utils.today() + " 00:00:00"), "docstatus": ("<", 2)},
        ["name", "status", "whatsapp_status"],
        as_dict=True)
    if not row:
        frappe.throw(_("Token {0} not found for today.").format(token_number))
    return {
        "token_number": token_number,
        "status": row["status"],
        "queue_position": _queue_position(row["name"], profile.name),
        "whatsapp_status": row["whatsapp_status"],
    }


@frappe.whitelist(allow_guest=True)
@rate_limit(limit=240, seconds=300, ip_based=True)
def get_tablet_models(store: str, brand: str, category: str = "", txt: str = "") -> list[dict]:
    """CH Models for the tablet's model picker: ``[{value, label}]``."""
    profile = _resolve_tablet_store(store)
    brand = _bounded_public_text(brand, _("Brand"), 140, required=True)
    category = _bounded_public_text(category, _("Device Category"), 140)
    txt = _bounded_public_text(txt, _("Search"), 80)
    _check_rate_limit(f"tablet_models_{_client_ip()}_{profile.name}")
    return _models_for(brand, category, txt)


# ---------------------------------------------------------------------------
# Authenticated API — Management Dashboard
# ---------------------------------------------------------------------------

@frappe.whitelist()
def get_queue(pos_profile: str = None, status: str = None, date_filter: str = "today") -> dict:
    """
    Returns token queue for manager/admin view.
    date_filter: today | yesterday | this_week | all
    """
    _ensure_can_view_tokens()
    filters = {}
    if pos_profile:
        _assert_pos_profile_scope(pos_profile)
        filters["pos_profile"] = pos_profile
    else:
        # No explicit store → narrow to the caller's allowed stores so a
        # scoped manager can't pull every store's queue (customer PII).
        _apply_token_scope_filters(filters)
    if status and status != "All":
        filters["status"] = status

    now = frappe.utils.now_datetime()
    if date_filter == "today":
        filters["creation"] = [">=", frappe.utils.today() + " 00:00:00"]
    elif date_filter == "yesterday":
        yesterday = frappe.utils.add_days(frappe.utils.today(), -1)
        filters["creation"] = ["between", [
            yesterday + " 00:00:00",
            yesterday + " 23:59:59",
        ]]
    elif date_filter == "this_week":
        week_start = frappe.utils.add_days(frappe.utils.today(), -6)
        filters["creation"] = [">=", week_start + " 00:00:00"]
    # date_filter == "all" → no date filter applied

    result_limit = _configured_limit("token_queue_result_limit", 200, 2000)
    tokens = frappe.get_all(
        "POS Kiosk Token",
        filters=filters,
        fields=[
            "name", "token_display", "creation", "status",
            "customer_name", "customer_phone",
            "device_type", "device_brand", "device_model", "device_model_name", "other_device_hint",
            "issue_category", "issue_description",
            "technician", "assigned_at", "started_at", "completed_at",
            "pos_profile", "company", "linked_service_request",
        ],
        order_by="creation desc",
        limit_page_length=result_limit + 1)
    _ensure_result_limit(tokens, result_limit, _("Queue tokens"))

    technician_ids = sorted({t.technician for t in tokens if t.get("technician")})
    technician_names = {}
    if technician_ids:
        user_rows = frappe.get_all(
            "User",
            filters={"name": ("in", technician_ids)},
            fields=["name", "full_name"],
            limit_page_length=result_limit + 1)
        _ensure_result_limit(user_rows, result_limit, _("Queue technicians"))
        technician_names = {row.name: row.full_name or row.name for row in user_rows}

    for t in tokens:
        t["device"] = _device_label(t.get('device_brand', ''), t.get('device_model_name') or t.get('other_device_hint') or '')
        # Compute wait time in minutes
        created = get_datetime(t["creation"])
        end_time = get_datetime(t["completed_at"]) if t.get("completed_at") else now
        delta_minutes = int((end_time - created).total_seconds() / 60)
        t["wait_minutes"] = delta_minutes
        t["technician_name"] = technician_names.get(t.get("technician"))

    return tokens


@frappe.whitelist()
def get_store_users(pos_profile: str = None, role: str = None) -> dict:
    """
    Return users mapped to a store via CH Store.store_users child table.
    Filtered by pos_profile (looks up CH Store via pos_profile field).
    Optionally filtered by role (Technician / Store Executive / Store Manager).
    Falls back to all enabled non-guest users if no mapping exists.
    """
    _ensure_can_view_tokens()
    users = []
    result_limit = _configured_limit("token_profile_result_limit", 1000, 5000)
    if pos_profile:
        _assert_pos_profile_scope(pos_profile)
        # Find the CH Store linked to this POS Profile
        store_name = frappe.db.get_value("CH Store", {"pos_profile": pos_profile, "disabled": 0}, "name")
        if store_name:
            filters = {"parent": store_name, "parenttype": "CH Store"}
            if role:
                filters["role"] = role
            # CH Store User retired into CH User Scope (ch_erp15 patch v34).
            from ch_erp15.ch_erp15.scope import get_store_users

            rows = [
                # _dict, not a plain dict: the attribute access below (r.user /
                # r.full_name) would raise AttributeError otherwise.
                frappe._dict({"user": _r.get("user"), "full_name": _r.get("full_name"),
                              "role": _r.get("role_profile")})
                for _r in get_store_users(store_name, role=role, limit=result_limit + 1)
            ]
            _ensure_result_limit(rows, result_limit, _("Store users"))
            missing_users = sorted({r.user for r in rows if r.user and not r.full_name})
            live_names = {}
            if missing_users:
                live_rows = frappe.get_all(
                    "User",
                    filters={"name": ("in", missing_users)},
                    fields=["name", "full_name"],
                    limit_page_length=result_limit + 1)
                _ensure_result_limit(live_rows, result_limit, _("Store user details"))
                live_names = {r.name: r.full_name or r.name for r in live_rows}
            for r in rows:
                if not r.full_name:
                    r.full_name = live_names.get(r.user, r.user)
            users = rows

    return users


@frappe.whitelist(methods=["POST"])
def assign_token(token_name: str, technician: str) -> dict:
    """Assign a technician to a token. Status → In Progress."""
    _ensure_can_operate_token()
    _assert_token_scope(token_name)
    doc = frappe.get_doc("POS Kiosk Token", token_name)
    _assert_token_assignee(technician, doc.pos_profile)
    updates = {"technician": technician, "assigned_at": now_datetime()}
    if doc.status == "Waiting":
        updates["status"] = "In Progress"
    frappe.db.set_value("POS Kiosk Token", token_name, updates)
    return {"status": "ok", "token_status": updates.get("status", doc.status)}


@frappe.whitelist(methods=["POST"])
def start_token(token_name: str) -> dict:
    """Mark service as started."""
    _ensure_can_operate_token()
    _assert_token_scope(token_name)
    frappe.db.set_value("POS Kiosk Token", token_name, {
        "started_at": now_datetime(),
        "status": "In Progress",
    })
    return {"status": "ok"}


@frappe.whitelist(methods=["POST"])
def complete_token(token_name: str) -> dict:
    """Mark token as completed."""
    _ensure_can_operate_token()
    _assert_token_scope(token_name)
    frappe.db.set_value("POS Kiosk Token", token_name, {
        "completed_at": now_datetime(),
        "status": "Completed",
    })
    return {"status": "ok"}


@frappe.whitelist(methods=["POST"])
def cancel_token(token_name: str) -> dict:
    """Cancel a token (typically Waiting status)."""
    _ensure_can_operate_token()
    _assert_token_scope(token_name)
    doc = frappe.get_doc("POS Kiosk Token", token_name)
    if doc.status in ("Completed", "Cancelled", "Converted"):
        frappe.throw(_("Cannot cancel a {0} token").format(doc.status), title=_("API Error"))
    frappe.db.set_value("POS Kiosk Token", token_name, "status", "Cancelled")
    return {"status": "ok"}


@frappe.whitelist(methods=["POST"])
def drop_token(token_name: str, drop_reason: str = "", drop_sub_reason: str = "", drop_remarks: str = "") -> dict:
    """Withdraw a token (customer left / no-show) with mandatory reason + remarks capture.

    Status remains "Dropped" in the database for backward compatibility with reports;
    the UI surfaces the action as "Withdraw" / "Withdrawn" (Salesforce/Dynamics 365
    nomenclature for lost-opportunity tracking).
    """
    _ensure_can_operate_token()
    _assert_token_scope(token_name)
    doc = frappe.get_doc("POS Kiosk Token", token_name)
    if doc.status in ("Completed", "Cancelled", "Converted", "Dropped"):
        frappe.throw(_("Cannot withdraw a {0} token").format(doc.status), title=_("API Error"))
    if not drop_reason:
        frappe.throw(_("Withdrawal reason is mandatory"), title=_("API Error"))
    if not (drop_remarks or "").strip():
        frappe.throw(_("Remarks are mandatory when withdrawing a token (audit requirement)"), title=_("API Error"))
    frappe.db.set_value("POS Kiosk Token", token_name, {
        "status": "Dropped",
        "drop_reason": drop_reason,
        "drop_sub_reason": drop_sub_reason,
        "drop_remarks": drop_remarks,
        "exit_at": now_datetime(),
    })
    return {"status": "ok", "drop_reason": drop_reason}


@frappe.whitelist(methods=["POST"])
def engage_token(token_name: str, sales_executive: str = "") -> dict:
    """Mark a token as Engaged — staff has started interacting with the customer."""
    _ensure_can_operate_token()
    _assert_token_scope(token_name)
    doc = frappe.get_doc("POS Kiosk Token", token_name)
    if doc.status not in ("Waiting"):
        frappe.throw(_("Can only engage a Waiting token, current status is {0}").format(doc.status), title=_("API Error"))
    updates = {
        "status": "Engaged",
        "engaged_at": now_datetime(),
    }
    if sales_executive:
        _assert_sales_executive(sales_executive, doc.pos_profile)
        updates["sales_executive"] = sales_executive
    elif not doc.technician:
        updates["technician"] = frappe.session.user
    frappe.db.set_value("POS Kiosk Token", token_name, updates)
    return {"status": "ok", "token_status": "Engaged"}


@frappe.whitelist(methods=["POST"])
def start_pos_billing(token_name: str, pos_profile: str, sales_executive: str = "") -> dict:
    """Claim the exclusive active billing slot for this POS profile.

    If another token is already being billed, the requested token is parked in
    Hold and must wait until the active billing completes or is released.
    """
    _ensure_can_operate_token()
    _assert_token_scope(token_name)
    _assert_pos_profile_scope(pos_profile)

    def _claim_slot():
        doc = frappe.get_doc("POS Kiosk Token", token_name)
        if doc.pos_profile != pos_profile:
            frappe.throw(_("Token does not belong to POS Profile {0}").format(pos_profile), title=_("API Error"))
        if doc.status in ("Completed", "Cancelled", "Converted", "Dropped", "Expired"):
            frappe.throw(_("Cannot bill a {0} token").format(doc.status), title=_("API Error"))

        active = _active_billing_token(pos_profile, exclude_token=token_name)
        if active:
            if doc.status != "Hold":
                frappe.db.set_value("POS Kiosk Token", token_name, "status", "Hold")
            return {
                "status": "ok",
                "action": "held",
                "token_status": "Hold",
                "active_token": active,
            }

        updates = {
            "status": "In Progress",
        }
        if not doc.engaged_at:
            updates["engaged_at"] = now_datetime()
        if sales_executive:
            _assert_sales_executive(sales_executive, pos_profile)
            updates["sales_executive"] = sales_executive
        elif not doc.technician:
            updates["technician"] = frappe.session.user
        frappe.db.set_value("POS Kiosk Token", token_name, updates)
        return {
            "status": "ok",
            "action": "started",
            "token_status": "In Progress",
        }

    return _with_pos_profile_lock(pos_profile, _claim_slot)


@frappe.whitelist(methods=["POST"])
def release_pos_billing(token_name: str = "", pos_profile: str = "", revert_current: int = 0) -> dict:
    """Release the active billing slot and return held customers to Waiting."""
    _ensure_can_operate_token()
    _assert_token_scope(token_name)

    if token_name and not pos_profile:
        pos_profile = frappe.db.get_value("POS Kiosk Token", token_name, "pos_profile")
    if not pos_profile:
        frappe.throw(_("POS Profile is required to release billing"), title=_("API Error"))
    _assert_pos_profile_scope(pos_profile)

    def _release():
        released_current = None
        if token_name and cint(revert_current):
            current_status = frappe.db.get_value("POS Kiosk Token", token_name, "status")
            if current_status == "In Progress":
                frappe.db.set_value("POS Kiosk Token", token_name, "status", "Waiting")
                released_current = token_name

        hold_names = _release_held_tokens(pos_profile)
        return {
            "status": "ok",
            "released_current": released_current,
            "released_holds": hold_names,
        }

    return _with_pos_profile_lock(pos_profile, _release)


def recover_stale_pos_billing(timeout_minutes: int | None = None) -> dict:
    """Recover abandoned retail billing tokens and release any held queue.

    Since the POS billing session is browser-local, abandoned carts can leave a
    token stuck in ``In Progress``. This scheduled recovery uses document
    inactivity (``modified`` timestamp) as the stale signal.
    """
    configured_timeout = cint(get_control_setting("stale_billing_timeout_minutes", 45)) or 45
    timeout = max(5, min(cint(timeout_minutes or configured_timeout), 1440))
    cutoff = add_to_date(now_datetime(), minutes=-timeout)

    stale = frappe.get_all(
        "POS Kiosk Token",
        filters={
            "status": "In Progress",
            "docstatus": 1,
            "modified": ("<", cutoff),
        },
        fields=["name", "pos_profile"],
        order_by="modified asc",
        limit_page_length=max(
            1, min(cint(get_control_setting("scheduler_batch_limit", 500)), 5000)
        ))

    recovered: list[str] = []
    released_holds: list[str] = []

    tokens_by_profile: dict[str, list[str]] = {}
    for row in stale:
        if row.get("pos_profile") and row.get("name"):
            tokens_by_profile.setdefault(row.pos_profile, []).append(row.name)

    for pos_profile, token_names in tokens_by_profile.items():
        def _recover_profile():
            current_names = frappe.get_all(
                "POS Kiosk Token",
                filters={"name": ("in", token_names), "status": "In Progress", "docstatus": 1},
                pluck="name",
                order_by="modified asc, name asc",
                limit_page_length=len(token_names))
            if not current_names:
                return
            frappe.db.set_value(
                "POS Kiosk Token",
                {"name": ("in", current_names)},
                "status",
                "Waiting",
                update_modified=False)
            recovered.extend(current_names)
            released_holds.extend(_release_held_tokens(pos_profile))

        try:
            _with_pos_profile_lock(pos_profile, _recover_profile)
        except Exception:
            frappe.log_error(
                frappe.get_traceback(),
                f"Failed stale POS billing recovery for profile {pos_profile}")

    return {
        "timeout_minutes": timeout,
        "recovered_tokens": recovered,
        "released_holds": released_holds,
    }


@frappe.whitelist(methods=["POST"])
def quick_walkin(
    pos_profile: str,
    visit_purpose: str = "Sales",
    category_interest: str = "",
    brand_interest: str = "",
    budget_range: str = "",
    customer_name: str = "",
    customer_phone: str = "",
    sales_executive: str = "") -> dict:
    """
    2-second retail walk-in entry — button-driven, no typing needed.
    Creates a token already in Engaged state with retail interest fields populated.
    """
    _ensure_can_operate_token()
    _assert_pos_profile_scope(pos_profile)
    profile = frappe.db.get_value(
        "POS Profile", pos_profile, ["name", "company", "warehouse"], as_dict=True
    )
    if not profile:
        frappe.throw(_("Invalid POS Profile"), title=_("API Error"))
    _assert_sales_executive(sales_executive, pos_profile)

    company_abbr = frappe.db.get_value("Company", profile.company, "abbr") or "CH"

    if customer_phone and customer_phone.strip():
        customer_phone = validate_indian_phone(customer_phone.strip(), "Phone number")

    token_display = _generate_token_display(pos_profile, company_abbr)
    doc = frappe.get_doc({
        "doctype": "POS Kiosk Token",
        "pos_profile": pos_profile,
        "company": profile.company,
        "store": profile.warehouse,
        "status": "Engaged",
        "token_display": token_display,
        "customer_name": customer_name.strip() or "Walk-in",
        "customer_phone": customer_phone.strip() or "",
        "visit_source": "Counter",
        "visit_purpose": visit_purpose,
        "category_interest": category_interest,
        "brand_interest": brand_interest,
        "budget_range": budget_range,
        "sales_executive": sales_executive or "",
        "engaged_at": now_datetime(),
        "technician": frappe.session.user,
        "expires_at": frappe.utils.add_days(now_datetime(), 1),
    })
    doc.insert()
    doc.submit()

    return {
        "status": "ok",
        "token": token_display,
        "name": doc.name,
        "visit_purpose": visit_purpose,
    }


@frappe.whitelist()
def audit_orphan_invoices(pos_profile: str = "", date: str = "") -> dict:
    """
    Daily audit: find POS invoices that have no linked kiosk token.
    Returns list of orphan invoices for compliance review.
    """
    _ensure_can_view_tokens()
    frappe.has_permission("Sales Invoice", "read", throw=True)
    target_date = date or frappe.utils.today()
    filters = {
        "is_pos": 1,
        "docstatus": 1,
        "posting_date": target_date,
        "custom_kiosk_token": ("in", ["", None]),
    }
    if pos_profile:
        _assert_pos_profile_scope(pos_profile)
        filters["pos_profile"] = pos_profile
    else:
        warehouses, companies, bypass = _scoped_warehouses_companies()
        if not bypass:
            if warehouses:
                profile_limit = _configured_limit("token_profile_result_limit", 1000, 5000)
                profiles = frappe.get_all(
                    "POS Profile",
                    filters={"warehouse": ("in", list(warehouses))},
                    pluck="name",
                    limit_page_length=profile_limit + 1)
                _ensure_result_limit(profiles, profile_limit, _("Scoped POS profiles"))
                filters["pos_profile"] = ("in", profiles or ["__none__"])
            elif companies:
                filters["company"] = ("in", list(companies))
            else:
                filters["name"] = ("in", ["__none__"])

    report_limit = _configured_limit("token_report_row_limit", 5000, 20000)
    orphans = frappe.get_all(
        "Sales Invoice",
        filters=filters,
        fields=["name", "pos_profile", "customer_name", "grand_total", "posting_date", "owner"],
        order_by="creation asc",
        limit_page_length=report_limit + 1)
    _ensure_result_limit(orphans, report_limit, _("Orphan invoices"))
    return {
        "date": target_date,
        "total_orphans": len(orphans),
        "invoices": orphans,
    }


@frappe.whitelist()
def get_walkin_insights(pos_profile: str = "", days: int = 30) -> dict:
    """
    AI-style insights derived from token data — actionable observations for store managers.
    Returns structured insights with severity and recommendations.
    """
    _ensure_can_view_tokens()
    from frappe.utils import getdate, add_days
    max_days = max(1, min(cint(get_control_setting("walkin_insight_max_days", 366)), 730))
    days = max(1, min(cint(days or 30), max_days))
    thresholds = frappe._dict(
        low_conversion=max(
            0.0, min(frappe.utils.flt(get_control_setting("walkin_insight_low_conversion_pct", 30)), 100.0)
        ),
        high_conversion=max(
            0.0, min(frappe.utils.flt(get_control_setting("walkin_insight_high_conversion_pct", 50)), 100.0)
        ),
        unengaged=max(
            0.0, min(frappe.utils.flt(get_control_setting("walkin_insight_unengaged_pct", 15)), 100.0)
        ),
        missed_brand_min=max(
            1, min(cint(get_control_setting("walkin_insight_missed_brand_min_requests", 3)), 1000)
        ),
        staff_min_tokens=max(
            1, min(cint(get_control_setting("walkin_insight_staff_min_tokens", 5)), 1000)
        ),
        staff_variance=max(
            0.0, min(frappe.utils.flt(get_control_setting("walkin_insight_staff_variance_pct", 20)), 100.0)
        ))
    end_date = frappe.utils.today()
    start_date = str(add_days(getdate(end_date), -(int(days) - 1)))

    base_filters = {"creation": [">=", start_date + " 00:00:00"]}
    if pos_profile:
        _assert_pos_profile_scope(pos_profile)
        base_filters["pos_profile"] = pos_profile
    else:
        _apply_token_scope_filters(base_filters)

    report_limit = _configured_limit("token_report_row_limit", 5000, 20000)
    tokens = frappe.get_all(
        "POS Kiosk Token",
        filters=base_filters,
        fields=[
            "name", "status", "visit_purpose", "visit_source",
            "category_interest", "brand_interest", "budget_range",
            "drop_reason", "sales_executive", "handling_duration",
            "creation", "engaged_at", "exit_at", "converted_invoice",
            "pos_profile",
        ],
        limit_page_length=report_limit + 1)
    _ensure_result_limit(tokens, report_limit, _("Walk-in insight tokens"))

    if not tokens:
        return {"insights": [], "summary": "No token data for the selected period."}

    total = len(tokens)
    converted = [t for t in tokens if t.status == "Converted"]
    dropped = [t for t in tokens if t.status == "Dropped"]
    waiting = [t for t in tokens if t.status in ("Waiting", "Expired")]

    conversion_rate = round(len(converted) / total * 100, 1) if total else 0
    drop_rate = round(len(dropped) / total * 100, 1) if total else 0

    insights = []

    # 1. Conversion rate alert
    if conversion_rate < thresholds.low_conversion:
        insights.append({
            "type": "warning",
            "title": "Low Conversion Rate",
            "metric": f"{conversion_rate}%",
            "detail": f"Only {len(converted)} of {total} walk-ins converted to sales, below the configured {thresholds.low_conversion:g}% threshold.",
            "action": "Review drop reasons and staff training. Check if high-demand products are in stock.",
        })
    elif conversion_rate > thresholds.high_conversion:
        insights.append({
            "type": "success",
            "title": "Strong Conversion",
            "metric": f"{conversion_rate}%",
            "detail": f"{len(converted)} of {total} walk-ins converted — above benchmark.",
            "action": "Maintain momentum. Consider upsell training to increase basket size.",
        })

    # 2. Top drop reasons
    drop_reasons = {}
    for t in dropped:
        r = t.drop_reason or "Not Specified"
        drop_reasons[r] = drop_reasons.get(r, 0) + 1
    if drop_reasons:
        top_reason = max(drop_reasons, key=drop_reasons.get)
        top_count = drop_reasons[top_reason]
        insights.append({
            "type": "info",
            "title": "Top Drop Reason",
            "metric": f"{top_reason} ({top_count}x)",
            "detail": f"'{top_reason}' is the #1 reason customers leave without buying ({round(top_count / len(dropped) * 100)}% of drops).",
            "action": {
                "Price Too High": "Review pricing vs. competitors. Push finance/EMI options.",
                "Product Not Available": "Check stock availability for requested items. Improve procurement.",
                "Competitor Better Deal": "Activate price match or bundle offers.",
                "Just Browsing": "Train staff on engagement techniques to convert browsers.",
            }.get(top_reason, "Investigate and address the root cause."),
        })

    # 3. Unengaged visitors (went from Waiting to Expired without engagement)
    unengaged = [t for t in waiting if not t.engaged_at]
    if unengaged and (len(unengaged) / total * 100) > thresholds.unengaged:
        insights.append({
            "type": "warning",
            "title": "High Unengaged Walk-ins",
            "metric": f"{len(unengaged)} ({round(len(unengaged) / total * 100)}%)",
            "detail": f"{len(unengaged)} customers left without any staff interaction.",
            "action": "Ensure adequate floor staff during peak hours. Consider greeting protocol within 60 seconds.",
        })

    # 4. Brand demand without sales
    brand_demand = {}
    brand_converted = set()
    for t in tokens:
        if t.brand_interest:
            brand_demand[t.brand_interest] = brand_demand.get(t.brand_interest, 0) + 1
        if t.status == "Converted" and t.brand_interest:
            brand_converted.add(t.brand_interest)
    missed_brands = {
        brand: count
        for brand, count in brand_demand.items()
        if brand not in brand_converted and count >= thresholds.missed_brand_min
    }
    if missed_brands:
        top_missed = max(missed_brands, key=missed_brands.get)
        insights.append({
            "type": "opportunity",
            "title": "Missed Brand Opportunity",
            "metric": f"{top_missed} ({missed_brands[top_missed]} requests, 0 sales)",
            "detail": f"Customers asked for {top_missed} {missed_brands[top_missed]} times but none converted.",
            "action": f"Check {top_missed} stock levels and pricing. Consider adding models if not stocked.",
        })

    # 5. Staff performance variance
    exec_data = {}
    for t in tokens:
        ex = t.sales_executive or t.get("technician") or ""
        if not ex:
            continue
        if ex not in exec_data:
            exec_data[ex] = {"total": 0, "converted": 0}
        exec_data[ex]["total"] += 1
        if t.status == "Converted":
            exec_data[ex]["converted"] += 1
    if len(exec_data) >= 2:
        rates = {
            executive: round(data["converted"] / data["total"] * 100, 1)
            for executive, data in exec_data.items()
            if data["total"] >= thresholds.staff_min_tokens
        }
        if rates:
            best = max(rates, key=rates.get)
            worst = min(rates, key=rates.get)
            if rates[best] - rates[worst] > thresholds.staff_variance:
                names = {
                    row.name: row.full_name or row.name
                    for row in frappe.get_all(
                        "User",
                        filters={"name": ("in", [best, worst])},
                        fields=["name", "full_name"],
                        limit_page_length=2)
                }
                best_name = names.get(best, best)
                worst_name = names.get(worst, worst)
                insights.append({
                    "type": "info",
                    "title": "Staff Conversion Gap",
                    "metric": f"{rates[best]}% vs {rates[worst]}%",
                    "detail": f"{best_name} converts at {rates[best]}% while {worst_name} is at {rates[worst]}%.",
                    "action": "Pair low-performers with high-performers for shadowing. Review approach differences.",
                })

    # 6. Budget range analysis
    budget_counts = {}
    for t in tokens:
        if t.budget_range:
            budget_counts[t.budget_range] = budget_counts.get(t.budget_range, 0) + 1
    if budget_counts:
        top_budget = max(budget_counts, key=budget_counts.get)
        insights.append({
            "type": "info",
            "title": "Most Requested Budget Segment",
            "metric": top_budget,
            "detail": f"{budget_counts[top_budget]} walk-ins asked for {top_budget} range ({round(budget_counts[top_budget] / total * 100)}%).",
            "action": f"Ensure strong assortment and display in the {top_budget} range.",
        })

    return {
        "insights": insights,
        "summary": {
            "period_days": days,
            "total_footfall": total,
            "converted": len(converted),
            "dropped": len(dropped),
            "conversion_rate": conversion_rate,
            "drop_rate": drop_rate,
            "drop_reasons": drop_reasons,
            "top_categories": dict(sorted(
                {t.category_interest: 0 for t in tokens if t.category_interest}.items()
            )),
        },
    }


# ---------------------------------------------------------------------------
# Customer Lookup by Phone
# ---------------------------------------------------------------------------

@frappe.whitelist()
def find_customer_by_phone(phone: str, pos_profile: str = None) -> dict:
    """Return the ERPNext Customer name matching this phone number, or None."""
    _ensure_can_view_tokens()
    frappe.has_permission("Customer", "read", throw=True)
    if pos_profile:
        _assert_pos_profile_scope(pos_profile)
    elif not is_privileged_user():
        frappe.throw(_("POS Profile is required for customer lookup."), frappe.PermissionError)
    if not phone or not phone.strip():
        return None
    phone = normalize_indian_phone(phone.strip())
    tail10 = (phone or "")[-10:]
    # Reject clearly invalid numbers: must be 10 digits starting with 6-9 (Indian mobile).
    # This prevents test/dummy numbers like 0000000000 from matching test-data contacts.
    if not tail10 or len(tail10) != 10 or tail10[0] not in "6789":
        return None
    # Try mobile_no on Customer directly
    customer_scope_clause = ""
    contact_scope_clause = ""
    profile_params = []
    if pos_profile:
        customer_scope_clause = (
            " AND EXISTS (SELECT 1 FROM `tabSales Invoice` si "
            "WHERE si.customer = c.name AND si.docstatus = 1 AND si.pos_profile = %s)"
        )
        contact_scope_clause = (
            " AND EXISTS (SELECT 1 FROM `tabSales Invoice` si "
            "WHERE si.customer = dl.link_name AND si.docstatus = 1 AND si.pos_profile = %s)"
        )
        profile_params.append(pos_profile)
    direct = frappe.db.sql(
        f"SELECT c.name FROM `tabCustomer` c WHERE c.mobile_no = %s {customer_scope_clause} LIMIT 1",
        [phone, *profile_params],
        as_dict=True)
    name = direct[0].name if direct else None
    if name:
        return name
    # Try mobile variants (country code / separators) using last 10 digits
    if tail10 and len(tail10) == 10:
        by_mobile_tail = frappe.db.sql(
            """
            SELECT c.name
            FROM `tabCustomer` c
            WHERE (
                RIGHT(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(IFNULL(c.mobile_no, ''), '+', ''), '-', ''), ' ', ''), '(', ''), ')', ''), 10) = %s
                OR RIGHT(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(IFNULL(c.ch_alternate_phone, ''), '+', ''), '-', ''), ' ', ''), '(', ''), ')', ''), 10) = %s
            )
              {customer_scope_clause}
            LIMIT 1
            """.format(customer_scope_clause=customer_scope_clause),
            [tail10, tail10, *profile_params],
            as_dict=True)
        if by_mobile_tail:
            return by_mobile_tail[0].name
    # Try Dynamic Link on Contact
    contact = frappe.db.sql(
        """SELECT dl.link_name
           FROM `tabContact Phone` cp
           JOIN `tabDynamic Link` dl ON dl.parent = cp.parent AND dl.parenttype = 'Contact'
           WHERE cp.phone = %s AND dl.link_doctype = 'Customer'
             {contact_scope_clause}
           LIMIT 1""".format(contact_scope_clause=contact_scope_clause),
        [phone, *profile_params],
        as_dict=True)
    if contact:
        return contact[0].link_name
    if tail10 and len(tail10) == 10:
        contact_tail = frappe.db.sql(
            """SELECT dl.link_name
               FROM `tabContact Phone` cp
               JOIN `tabDynamic Link` dl ON dl.parent = cp.parent AND dl.parenttype = 'Contact'
               WHERE dl.link_doctype = 'Customer'
                 AND RIGHT(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(IFNULL(cp.phone, ''), '+', ''), '-', ''), ' ', ''), '(', ''), ')', ''), 10) = %s
                 {contact_scope_clause}
               LIMIT 1""".format(contact_scope_clause=contact_scope_clause),
            [tail10, *profile_params],
            as_dict=True)
        if contact_tail:
            return contact_tail[0].link_name
    return None


@frappe.whitelist()
@frappe.read_only()
def lookup_walkin_customer(phone: str, pos_profile: str = None) -> dict:
    """Identify a returning walk-in from the phone number the counter typed.

    The point is to capture ``linked_customer`` on the token AT INTAKE. Without
    it every downstream step -- the GoFix conversion, the buyback, the bill --
    has to re-derive the customer from a phone string, and a second Customer
    record gets created the moment somebody types the name slightly differently.

    Visibility follows the rule POS already uses for customers
    (``_assert_customer_pos_access``): this store sees a customer it has
    actually transacted with; anyone else needs the override role. When a
    number belongs to a customer this store may not open, the operator is told
    that the record EXISTS but is shown no details -- enough to stop them
    creating a duplicate, not enough to read another branch's book.
    """
    _ensure_can_view_tokens()
    frappe.has_permission("Customer", "read", throw=True)
    if pos_profile:
        _assert_pos_profile_scope(pos_profile)

    out = {
        "found": False, "restricted": False, "customer": None,
        "customer_name": None, "mobile_no": None, "email_id": None,
        "visits": 0, "last_visit": None, "last_store": None,
    }

    phone = (phone or "").strip()
    if not phone:
        return out

    # Same normalisation/validation the token uses, so a half-typed number
    # never triggers a lookup and 0000000000 never matches test contacts.
    normalized = normalize_indian_phone(phone)
    tail10 = (normalized or "")[-10:]
    if len(tail10) != 10 or tail10[0] not in "6789":
        return out

    # Scoped match first -- this is the one the store is entitled to see.
    customer = find_customer_by_phone(phone, pos_profile=pos_profile)

    if not customer:
        # Unscoped match: does this number belong to anyone at all?
        unscoped = find_customer_by_phone(phone, pos_profile=None) \
            if (pos_profile and is_privileged_user()) else None
        if not unscoped and pos_profile:
            unscoped = frappe.db.sql(
                """
                SELECT c.name FROM `tabCustomer` c
                WHERE REPLACE(REPLACE(REPLACE(IFNULL(c.mobile_no,''),' ',''),'-',''),'+','')
                      LIKE %(tail)s
                LIMIT 1
                """,
                {"tail": f"%{tail10}"},
            )
            unscoped = unscoped[0][0] if unscoped else None
        if not unscoped:
            return out
        # Imported lazily: pos_api imports from this module.
        from ch_pos.api.pos_api import _assert_customer_pos_access

        try:
            _assert_customer_pos_access(unscoped, pos_profile, action=_("look up"))
            customer = unscoped
        except frappe.PermissionError:
            # Exists, but not this store's to open.
            return {**out, "found": True, "restricted": True}

    row = frappe.db.get_value(
        "Customer", customer,
        ["name", "customer_name", "mobile_no", "email_id", "customer_group", "territory"],
        as_dict=True,
    ) or {}

    history = frappe.db.sql(
        """
        SELECT COUNT(*) AS visits, MAX(si.posting_date) AS last_visit,
               SUBSTRING_INDEX(GROUP_CONCAT(si.pos_profile ORDER BY si.posting_date DESC), ',', 1) AS last_store
        FROM `tabSales Invoice` si
        WHERE si.customer = %(c)s AND si.docstatus = 1
        """,
        {"c": customer},
        as_dict=True,
    )
    hist = history[0] if history else {}

    return {
        "found": True,
        "restricted": False,
        "customer": row.get("name"),
        "customer_name": row.get("customer_name"),
        "mobile_no": row.get("mobile_no"),
        "email_id": row.get("email_id"),
        "customer_group": row.get("customer_group"),
        "territory": row.get("territory"),
        "visits": cint(hist.get("visits")),
        "last_visit": str(hist.get("last_visit")) if hist.get("last_visit") else None,
        "last_store": hist.get("last_store"),
    }


# ---------------------------------------------------------------------------
# Counter Walk-in — creates a lightweight token from POS app
# ---------------------------------------------------------------------------

@frappe.whitelist(methods=["POST"])
def log_counter_walkin(
    pos_profile: str,
    visit_purpose: str = "Enquiry",
    customer_name: str = "",
    customer_phone: str = "",
    remarks: str = "",
    ch_category: str = "",
    device_brand: str = "",
    device_model: str = "",
    item_code: str = "",
    linked_customer: str = None) -> dict:
    """
    Create a minimal POS Kiosk Token for a direct-counter walk-in.
    This replaces the old log_walkin counter-only approach.

    ``device_brand`` / ``device_model`` capture what the customer asked
    for (used by repair-intake follow-up and the Walkin Conversion Report).
    For Sales / Enquiry purposes the same values also feed
    ``brand_interest`` so the retail-interest section is populated.

    ``item_code`` (optional) — when supplied, links a catalogue item the
    customer enquired about; appended to the token's items table with
    qty=0 so reports can correlate footfall to specific SKUs without
    affecting stock.

    Returns token name and display number.
    """
    _ensure_can_operate_token()
    _assert_pos_profile_scope(pos_profile)
    profile = frappe.db.get_value(
        "POS Profile", pos_profile, ["name", "company", "warehouse"], as_dict=True
    )
    if not profile:
        frappe.throw(_("Invalid POS Profile"), title=_("API Error"))

    company_abbr = frappe.db.get_value("Company", profile.company, "abbr") or "CH"

    # Validate phone if provided (walk-ins don't always have a phone)
    if customer_phone and customer_phone.strip():
        customer_phone = validate_indian_phone(customer_phone.strip(), "Phone number")

    # Tie the walk-in to an existing Customer at intake. The client passes the
    # one the operator confirmed; if it did not, resolve from the phone anyway,
    # so the link is captured even when the lookup never ran (offline, stale
    # bundle, a token created by another caller). Never trust the client's
    # choice blindly -- re-check that this store may use that customer.
    linked_customer = (linked_customer or "").strip() or None
    if linked_customer:
        if not frappe.db.exists("Customer", linked_customer):
            linked_customer = None
        else:
            from ch_pos.api.pos_api import _assert_customer_pos_access

            try:
                _assert_customer_pos_access(linked_customer, pos_profile, action=_("link"))
            except frappe.PermissionError:
                linked_customer = None
    if not linked_customer and customer_phone:
        try:
            # Same resolution the dialog showed the operator, so the promise
            # "this walk-in will be linked to X" holds even when the token is
            # created by something other than that dialog. find_customer_by_phone
            # alone is narrower -- it needs a prior invoice AT this profile --
            # and would silently drop a customer the UI had just identified.
            hit = lookup_walkin_customer(customer_phone, pos_profile=pos_profile)
            linked_customer = hit.get("customer") if hit.get("found") else None
        except Exception:
            # Identification is a convenience -- never fail a walk-in over it.
            linked_customer = None

    # Validate item_code if provided — silently drop bad references rather
    # than failing the whole walk-in log (interest capture is best-effort).
    item_code = (item_code or "").strip()
    if item_code and not frappe.db.exists("Item", item_code):
        item_code = ""

    token_display = _generate_token_display(pos_profile, company_abbr)
    device_brand = (device_brand or "").strip()
    device_model = (device_model or "").strip()
    # The dialog sends a Brand name and either a CH Model docname or its
    # readable model_name; both land as item-master links here.
    device = _normalise_device(ch_category if frappe.db.exists("CH Category", ch_category or "") else "", device_brand, device_model)
    token_payload = {
        "doctype": "POS Kiosk Token",
        "pos_profile": pos_profile,
        "company": profile.company,
        "store": profile.warehouse,
        "status": "In Progress",
        "token_display": token_display,
        "customer_name": customer_name.strip() or "Walk-in",
        "customer_phone": customer_phone.strip() or "",
        "linked_customer": linked_customer,
        "visit_source": "Counter",
        "visit_purpose": visit_purpose,
        "issue_description": remarks,
        "device_type": device["device_type"],
        "device_brand": device["device_brand"],
        "device_model": device["device_model"],
        "other_device_hint": device["other_device_hint"],
        "started_at": now_datetime(),
        "technician": frappe.session.user,
        "expires_at": frappe.utils.add_days(now_datetime(), 1),
    }
    if visit_purpose in ("Sales", "Enquiry") and device_brand:
        token_payload["brand_interest"] = device_brand

    # Stored even when brand and model were never narrowed down: "asked about
    # laptops, we had nothing to show" is a complete and useful record, and the
    # category is the one thing the counter almost always captures.
    if ch_category and frappe.db.exists("CH Category", ch_category):
        token_payload["category_interest"] = ch_category

    if item_code:
        token_payload["items"] = [{
            "item_code": item_code,
            "qty": 1,
        }]

    doc = frappe.get_doc(token_payload)
    doc.insert()
    doc.submit()

    return {
        "status": "ok",
        "token": token_display,
        "name": doc.name,
        "visit_purpose": visit_purpose,
        "linked_customer": linked_customer,
    }


@frappe.whitelist()
def get_dashboard_stats(pos_profile: str = None, date_filter: str = "today") -> dict:
    """
    Returns aggregate metrics for the dashboard cards.
    """
    _ensure_can_view_tokens()
    filters = {}
    if pos_profile:
        _assert_pos_profile_scope(pos_profile)
        filters["pos_profile"] = pos_profile
    else:
        _apply_token_scope_filters(filters)

    today = frappe.utils.today()
    if date_filter == "today":
        filters["creation"] = [">=", today + " 00:00:00"]
    elif date_filter == "yesterday":
        yesterday = frappe.utils.add_days(today, -1)
        filters["creation"] = ["between", [yesterday + " 00:00:00", yesterday + " 23:59:59"]]
    elif date_filter == "this_week":
        filters["creation"] = [">=", frappe.utils.add_days(today, -6) + " 00:00:00"]
    # date_filter == "all" → no date filter

    report_limit = _configured_limit("token_report_row_limit", 5000, 20000)
    all_tokens = frappe.get_all(
        "POS Kiosk Token",
        filters=filters,
        fields=["status", "creation", "completed_at", "pos_profile"],
        limit_page_length=report_limit + 1)
    _ensure_result_limit(all_tokens, report_limit, _("Dashboard tokens"))

    total = len(all_tokens)
    waiting = sum(1 for t in all_tokens if t.status == "Waiting")
    in_progress = sum(1 for t in all_tokens if t.status == "In Progress")
    completed = sum(1 for t in all_tokens if t.status == "Completed")
    cancelled = sum(1 for t in all_tokens if t.status in ("Cancelled", "Expired"))
    dropped = sum(1 for t in all_tokens if t.status == "Dropped")

    # Completion rate: completed / (completed + waiting + in_progress) — excludes cancelled
    serviceable = completed + waiting + in_progress
    completion_rate = round(completed / serviceable * 100) if serviceable else 0

    # Average wait time (creation → completed_at) for completed tokens
    completed_tokens = [t for t in all_tokens if t.status == "Completed" and t.completed_at]
    if completed_tokens:
        total_mins = sum(
            int((get_datetime(t["completed_at"]) - get_datetime(t["creation"])).total_seconds() / 60)
            for t in completed_tokens
        )
        avg_wait = round(total_mins / len(completed_tokens))
    else:
        avg_wait = 0

    # Per-store breakdown (for admin)
    store_breakdown = {}
    for t in all_tokens:
        p = t.pos_profile or "Unknown"
        if p not in store_breakdown:
            store_breakdown[p] = {"store": p, "total": 0, "waiting": 0, "in_progress": 0, "completed": 0}
        store_breakdown[p]["total"] += 1
        status_key = t.status.lower().replace(" ", "_")
        if status_key in store_breakdown[p]:
            store_breakdown[p][status_key] += 1

    return {
        "total": total,
        "waiting": waiting,
        "in_progress": in_progress,
        "completed": completed,
        "cancelled": cancelled,
        "dropped": dropped,
        "avg_wait_minutes": avg_wait,
        "completion_rate": completion_rate,
        "store_breakdown": list(store_breakdown.values()),
    }


@frappe.whitelist()
def get_technician_tokens(technician: str = None) -> dict:
    """
    Returns tokens assigned to a specific technician (defaults to logged-in user).
    """
    _ensure_can_view_tokens()
    tech = technician or frappe.session.user
    if tech != frappe.session.user and not is_privileged_user():
        frappe.has_permission("POS Kiosk Token", ptype="write", throw=True)
    today = frappe.utils.today()

    token_filters = {
        "technician": tech,
        "creation": [">=", today + " 00:00:00"],
    }
    _apply_token_scope_filters(token_filters)
    result_limit = _configured_limit("token_queue_result_limit", 200, 2000)
    tokens = frappe.get_all(
        "POS Kiosk Token",
        filters=token_filters,
        fields=[
            "name", "token_display", "creation", "status",
            "customer_name", "customer_phone",
            "device_type", "device_brand", "device_model", "device_model_name", "other_device_hint",
            "issue_category", "assigned_at", "started_at", "completed_at",
            "pos_profile",
        ],
        order_by="creation desc",
        limit_page_length=result_limit + 1)
    _ensure_result_limit(tokens, result_limit, _("Technician tokens"))

    for t in tokens:
        t["device"] = _device_label(t.get('device_brand', ''), t.get('device_model_name') or t.get('other_device_hint') or '')

    return tokens


@frappe.whitelist()
def get_reports(pos_profile: str = None, days: int = 7) -> dict:
    """
    Returns daily breakdown and technician performance for the manager Reports tab.
    """
    _ensure_can_view_tokens()
    max_days = _configured_limit("token_report_max_days", 366, 730)
    days = max(1, min(cint(days or 7), max_days))
    today = frappe.utils.today()
    start_date = frappe.utils.add_days(today, -(days - 1))

    base_filters = {"creation": [">=", start_date + " 00:00:00"]}
    if pos_profile:
        _assert_pos_profile_scope(pos_profile)
        base_filters["pos_profile"] = pos_profile
    else:
        _apply_token_scope_filters(base_filters)

    report_limit = _configured_limit("token_report_row_limit", 5000, 20000)
    all_tokens = frappe.get_all(
        "POS Kiosk Token",
        filters=base_filters,
        fields=["name", "status", "creation", "completed_at", "technician"],
        limit_page_length=report_limit + 1)
    _ensure_result_limit(all_tokens, report_limit, _("Token report rows"))

    technician_ids = sorted({t.technician for t in all_tokens if t.technician})
    technician_names = {}
    if technician_ids:
        user_rows = frappe.get_all(
            "User",
            filters={"name": ("in", technician_ids)},
            fields=["name", "full_name"],
            limit_page_length=report_limit + 1)
        _ensure_result_limit(user_rows, report_limit, _("Report technicians"))
        technician_names = {row.name: row.full_name or row.name for row in user_rows}

    # Daily breakdown
    daily_map = {}
    for t in all_tokens:
        day = str(get_datetime(t["creation"]).date())
        if day not in daily_map:
            daily_map[day] = {"date": day, "created": 0, "completed": 0, "cancelled": 0, "wait_sum": 0, "wait_count": 0}
        daily_map[day]["created"] += 1
        if t.status == "Completed":
            daily_map[day]["completed"] += 1
            if t.completed_at:
                mins = int((get_datetime(t["completed_at"]) - get_datetime(t["creation"])).total_seconds() / 60)
                daily_map[day]["wait_sum"] += mins
                daily_map[day]["wait_count"] += 1
        elif t.status == "Cancelled":
            daily_map[day]["cancelled"] += 1

    daily_breakdown = []
    for day, data in sorted(daily_map.items(), reverse=True):
        avg = round(data["wait_sum"] / data["wait_count"]) if data["wait_count"] else 0
        daily_breakdown.append({
            "date": data["date"],
            "created": data["created"],
            "completed": data["completed"],
            "cancelled": data["cancelled"],
            "avg_wait": avg,
        })

    # Technician performance
    tech_map = {}
    for t in all_tokens:
        if not t.technician:
            continue
        tech = t.technician
        if tech not in tech_map:
            tech_map[tech] = {"technician": tech, "name": technician_names.get(tech, tech),
                              "total": 0, "completed": 0, "time_sum": 0, "time_count": 0}
        tech_map[tech]["total"] += 1
        if t.status == "Completed":
            tech_map[tech]["completed"] += 1
            if t.completed_at:
                mins = int((get_datetime(t["completed_at"]) - get_datetime(t["creation"])).total_seconds() / 60)
                tech_map[tech]["time_sum"] += mins
                tech_map[tech]["time_count"] += 1

    tech_performance = []
    for tech_data in sorted(tech_map.values(), key=lambda x: x["completed"], reverse=True):
        avg = round(tech_data["time_sum"] / tech_data["time_count"]) if tech_data["time_count"] else 0
        tech_performance.append({
            "technician": tech_data["technician"],
            "name": tech_data["name"],
            "total": tech_data["total"],
            "completed": tech_data["completed"],
            "avg_time": avg,
        })

    return {
        "daily_breakdown": daily_breakdown,
        "tech_performance": tech_performance,
    }


@frappe.whitelist()
def get_pos_profiles() -> list:
    """Returns active POS Profiles the caller is entitled to see.

    Filter rules (Tier 1 CH User Scope hardening, SAP/Oracle parity):

      1. **System Manager / Administrator**: full list (bypass).
      2. Any other authenticated user: only profiles whose ``name`` appears
         in the resolved store set of the user's ``CH User Scope``. This
         set is kept in lock-step with ``POS Profile.applicable_for_users``
         by ``ch_erp15.ch_erp15.pos_profile_sync``, but we compute here from
         the scope directly so a user with a fresh scope-save sees the
         update immediately (no need to wait for the async gate sync).
      3. Guest is refused. ``allow_guest`` was removed intentionally —
         kiosks running the queue page anonymously must switch to a
         service-account session (SAP dedicated dialog user pattern).

    Result: a list of ``{name, company, warehouse, gofix_enabled}`` dicts,
    ordered by ``name asc``, filtered to the entitled subset. Never raises when the
    user has no scope; simply returns an empty list (fail-closed).
    """
    if frappe.session.user == "Guest":
        frappe.throw(
            frappe._("You must be signed in to list POS Profiles."),
            frappe.PermissionError)

    result_limit = _configured_limit("token_profile_result_limit", 1000, 5000)
    if is_privileged_user():
        all_profiles = frappe.get_all(
            "POS Profile",
            filters={"disabled": 0},
            fields=["name", "company", "warehouse"],
            order_by="name asc",
            limit_page_length=result_limit + 1)
        return _annotate_gofix_enabled(
            _ensure_result_limit(all_profiles, result_limit, _("POS profiles")))

    try:
        from ch_erp15.ch_erp15.scope import get_user_scope
    except ImportError:
        return []

    scope = get_user_scope()
    if scope.get("bypass"):
        all_profiles = frappe.get_all(
            "POS Profile",
            filters={"disabled": 0},
            fields=["name", "company", "warehouse"],
            order_by="name asc",
            limit_page_length=result_limit + 1)
        return _annotate_gofix_enabled(
            _ensure_result_limit(all_profiles, result_limit, _("POS profiles")))

    stores = scope.get("stores") or set()
    if not stores:
        # Fail-closed: an authenticated non-bypass user with no scope sees
        # nothing. Admins provision access via CH User Scope.
        return []

    profile_names = frappe.get_all(
        "CH Store",
        filters={"name": ("in", list(stores))},
        pluck="pos_profile",
        limit_page_length=result_limit + 1)
    _ensure_result_limit(profile_names, result_limit, _("Scoped store profiles"))
    entitled_profiles = {name for name in profile_names if name}
    if not entitled_profiles:
        return []

    profiles = frappe.get_all(
        "POS Profile",
        filters={"disabled": 0, "name": ("in", list(entitled_profiles))},
        fields=["name", "company", "warehouse"],
        order_by="name asc",
        limit_page_length=result_limit + 1)
    return _annotate_gofix_enabled(
        _ensure_result_limit(profiles, result_limit, _("Scoped POS profiles")))


# ---------------------------------------------------------------------------
# POS Integration APIs
# ---------------------------------------------------------------------------

@frappe.whitelist()
def get_pos_waiting_tokens(pos_profile: str) -> dict:
    """
    Returns waiting/in-progress tokens for the given POS store.
    Called by the POS Queue panel on load and after each action.
    """
    _ensure_can_view_tokens()
    _assert_pos_profile_scope(pos_profile)
    today = frappe.utils.today()
    result_limit = _configured_limit("token_queue_result_limit", 200, 2000)
    tokens = frappe.db.sql(
        """SELECT name, token_display, customer_name, customer_phone,
                  device_type, device_brand, device_model,
                  issue_category, issue_description, status,
                  visit_purpose, category_interest, brand_interest,
                  linked_customer,
                  budget_range, sales_executive, engaged_at,
                  technician, creation
           FROM `tabPOS Kiosk Token`
           WHERE pos_profile = %s
                         AND status IN ('Waiting', 'Hold', 'Engaged', 'In Progress')
             AND DATE(creation) = %s
                     ORDER BY FIELD(status, 'In Progress', 'Hold', 'Waiting', 'Engaged'), creation ASC
                     LIMIT %s""",
        (pos_profile, today, result_limit + 1),
        as_dict=True)
    return _ensure_result_limit(tokens, result_limit, _("Waiting POS tokens"))


def _resolve_token_customer(token, explicit, pos_profile, profile=None):
    """The ERPNext Customer a converted token belongs to.

    Service Request.customer is mandatory, so this must return something or
    explain why it cannot. Priority, most specific first:

      1. what the operator picked in the dialog
      2. the Customer already linked to the token
      3. a Customer matching the walk-in phone number
      4. the POS Profile's default (walk-in) Customer

    Mirrors the client-side _resolve_customer() in queue_workspace.js, but
    server-side so the guarantee does not depend on the client.
    """
    if explicit and frappe.db.exists("Customer", explicit):
        return explicit

    linked = getattr(token, "linked_customer", None)
    if linked and frappe.db.exists("Customer", linked):
        return linked

    phone = (token.customer_phone or "").strip()
    if phone:
        match = _customer_name_for_phone(phone)
        if match:
            return match

    default_customer = None
    if profile is not None:
        default_customer = profile.get("customer")
    if not default_customer and pos_profile:
        default_customer = frappe.db.get_value("POS Profile", pos_profile, "customer")
    if default_customer and frappe.db.exists("Customer", default_customer):
        return default_customer

    frappe.throw(
        _("No customer could be resolved for token {0}. Pick an ERPNext Customer "
          "in the dialog, or set a default Customer on POS Profile {1}.").format(
            frappe.bold(token.token_display or token.name), frappe.bold(pos_profile)
        ),
        title=_("Customer Required"),
    )


def _customer_name_for_phone(phone):
    """Customer whose mobile matches `phone`, by last 10 digits.

    Deliberately does NOT go through find_customer_by_phone(): that is a
    whitelisted endpoint with its own permission gates, and this runs inside an
    already-authorised conversion.
    """
    tail10 = "".join(ch for ch in str(phone) if ch.isdigit())[-10:]
    if len(tail10) != 10:
        return None
    row = frappe.db.sql(
        """
        SELECT name FROM `tabCustomer`
        WHERE REPLACE(REPLACE(REPLACE(IFNULL(mobile_no, ''), ' ', ''), '-', ''), '+', '')
              LIKE %(tail)s
        ORDER BY creation LIMIT 1
        """,
        {"tail": f"%{tail10}"},
    )
    return row[0][0] if row else None


@frappe.whitelist(methods=["POST"])
def link_token_to_service_request(token_name: str, service_request: str) -> dict:
    """Close a queue token against the Service Request that was raised from it.

    The Service Request itself is built by the POS intake form
    (``ch_pos.api.repair.create_service_intake_from_pos``); this only records
    the outcome on the token. Keeping the two apart is what lets the queue and
    the counter share ONE intake form instead of each maintaining its own
    conversion with its own subset of fields — which is how the queue dialog
    ended up unable to capture a technician, a promised time or a serial.
    """
    _ensure_can_operate_token()
    _assert_token_scope(token_name)

    token = frappe.get_doc("POS Kiosk Token", token_name)
    if token.status in ("Converted", "Cancelled", "Expired"):
        frappe.throw(
            _("Token {0} is already {1}.").format(
                token.token_display or token_name, _(token.status)),
            title=_("Token Not Open"))
    if not frappe.db.exists("Service Request", service_request):
        frappe.throw(_("Service Request {0} does not exist.").format(service_request))

    frappe.db.set_value("POS Kiosk Token", token_name, {
        "status": "Converted",
        "technician": frappe.session.user_fullname or frappe.session.user,
        "linked_service_request": service_request,
    })
    return {"token": token.token_display or token_name, "service_request": service_request}


@frappe.whitelist(methods=["POST"])
def convert_token_to_gofix(token_name: str, pos_profile: str,
                            customer: str = None, device_item: str = None,
                            device_condition: str = "Good",
                            accessories: str = "",
                            warranty_status: str = "Out of Warranty",
                            data_disclaimer: int = 0,
                            issue_category: str = None,
                            issue_description: str = None) -> dict:
    """
    Convert a POS Kiosk Token into a GoFix Service Request.
    - Pulls all device/issue info from the token
    - Creates the Service Request doc
    - Marks the token as Converted with a link back
    Returns the new Service Request name.
    """
    _ensure_can_operate_token()
    _assert_token_scope(token_name)
    _assert_pos_profile_scope(pos_profile)
    token = frappe.get_doc("POS Kiosk Token", token_name)
    if token.pos_profile != pos_profile:
        frappe.throw(_("Token does not belong to this POS Profile."), frappe.PermissionError)
    if token.status == "Converted":
        frappe.throw(_("This token has already been converted to a GoFix request."), title=_("API Error"))

    profile = frappe.db.get_value(
        "POS Profile", pos_profile,
        ["company", "warehouse"], as_dict=True
    )
    if not profile:
        frappe.throw(_("Invalid POS Profile"), title=_("API Error"))

    # Resolve issue category — must match GoFix Issue Category doctype.
    # The operator's choice in the dialog wins over whatever the kiosk token
    # captured; the token is a hint, the counter is the record.
    issue_cat = None
    for candidate in (issue_category, token.issue_category):
        if not candidate:
            continue
        if frappe.db.exists("Issue Category", candidate):
            issue_cat = candidate
            break
        # Try a case-insensitive match
        match = frappe.db.get_value(
            "Issue Category", {"category_name": candidate}, "name"
        )
        if match:
            issue_cat = match
            break

    from ch_pos.api.repair import build_condition_and_backup

    product_condition_desc, backup_info = build_condition_and_backup(
        device_condition, accessories, data_disclaimer
    )

    walkin_source = None
    if frappe.db.table_exists("Walkin Source"):
        walkin_source = (
            frappe.db.get_value("Walkin Source", "POS Counter", "name")
            or frappe.db.get_value("Walkin Source", {}, "name")
        )

    # Service Request.customer is mandatory. The dialog offered it as
    # "optional", so a blank field aborted the whole conversion with
    # "Value missing for Service Request: Customer". Resolve it here instead,
    # server-side, so the answer cannot depend on what the client sent.
    customer = _resolve_token_customer(token, customer, pos_profile, profile)

    # Likewise issue_description (labelled "Issue Specified By Customer") is
    # mandatory. Prefer the operator's text, then the token's, then the issue
    # category, and only then a plain statement of fact -- never None.
    description = (
        (issue_description or "").strip()
        or (token.issue_description or "").strip()
        or (issue_cat or "")
        or (token.issue_category or "")
        or _("Walk-in device handed in at the counter; issue to be diagnosed.")
    )

    from gofix.constants.device_condition import (
        DEFAULT_DEVICE_CONDITION,
        normalize_device_condition,
    )

    device_condition = normalize_device_condition(device_condition) or DEFAULT_DEVICE_CONDITION

    sr = frappe.get_doc({
        "doctype": "Service Request",
        "customer": customer,
        "customer_name": token.customer_name,
        "contact_number": token.customer_phone,
        "company": profile.company,
        "source_warehouse": profile.warehouse,
        "walkin_source": walkin_source,
        "product_condition_desc": product_condition_desc,
        "backup_info": backup_info,
        "decision": "Accepted",        # Customer is present — accepting the device
        "device_item": device_item or None,
        "device_item_name": _device_label(token.device_brand, token.device_model_name or token.other_device_hint) if not device_item else None,
        "brand": token.device_brand,
        # Item-master taxonomy travels with the customer from token to job card.
        "device_category": token.device_type,
        "device_brand": token.device_brand,
        "device_model": token.device_model,
        "device_condition": device_condition,
        "accessories_received": accessories,
        "warranty_status": _normalize_warranty(warranty_status),
        "issue_category": issue_cat,
        "issue_description": description,
        "data_backup_disclaimer": data_disclaimer,
        "mode_of_service": "Walk-in",
        "priority": "Medium",
        "internal_remarks": f"Created from CH Queue token {token.token_display}",
        # Store back-reference
        "referral_code": token.name,
    })
    sr.insert()
    sr.submit()

    # Mark token as Converted and link back to SR
    frappe.db.set_value("POS Kiosk Token", token_name, {
        "status": "Converted",
        "technician": frappe.session.user_fullname or frappe.session.user,
        "linked_service_request": sr.name,
    })

    return {
        "service_request": sr.name,
        "token": token.token_display,
        "customer_name": token.customer_name,
    }
