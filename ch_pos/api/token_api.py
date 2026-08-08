"""
CH Queue — Token System API
All public-facing endpoints for the kiosk and management dashboard.
"""

import hashlib
import re

import frappe
from frappe import _
from frappe.model.naming import getseries
from frappe.rate_limiter import rate_limit
from frappe.utils import now_datetime, get_datetime, cint, add_to_date

from buyback.utils import normalize_indian_phone, validate_indian_phone
from ch_pos.api.scope_guard import (
    assert_pos_profile_scope,
    assert_store_scope,
    get_pos_profile_anchors,
)
from ch_pos.config import (
	get_control_setting,
	has_configured_roles,
	is_privileged_user,
	require_configured_roles,
)
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
    require_configured_roles(
        "token_view_roles",
        defaults=("POS User", "POS Manager", "Store Manager", "Technician"),
        action=_("view queue tokens"),
    )


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
        ("Technician", "POS User", "POS Manager", "Store Manager"),
        user=user,
    ):
        frappe.throw(_("The selected user cannot be assigned queue tokens."), frappe.PermissionError)
    anchors = get_pos_profile_anchors(pos_profile)
    assert_store_scope(
        store=anchors.get("store"),
        warehouse=anchors.get("warehouse"),
        company=anchors.get("company"),
        user=user,
    )


def _assert_sales_executive(sales_executive: str, pos_profile: str) -> None:
    if not sales_executive:
        return
    executive = frappe.db.get_value(
        "POS Executive",
        sales_executive,
        ["name", "user", "company", "store", "is_active"],
        as_dict=True,
    )
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
        user=executive.user,
    )


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
            86400,
        ),
    )
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


def _get_store_code(pos_profile_name: str) -> str:
    """Generate a short store code from the POS Profile name."""
    # e.g. "QA Velachery POS" → "VELPOS", "T Nagar" → "TNAGAR"
    parts = pos_profile_name.upper().split()
    # Drop common noise words
    noise = {"POS", "QA", "THE", "AND", "&"}
    meaningful = [p for p in parts if p not in noise] or parts
    code = "".join(p[:3] for p in meaningful[:2])
    return code[:6]


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
        (pos_profile, today),
    )[0][0] or 0
    profile_digest = hashlib.sha256(pos_profile.encode()).hexdigest()[:24]
    series_key = f"CH-POS-TOKEN-{today}-{profile_digest}"
    frappe.db.sql(
        """
        INSERT INTO `tabSeries` (`name`, `current`)
        VALUES (%s, %s)
        ON DUPLICATE KEY UPDATE
            `current` = GREATEST(`current`, VALUES(`current`))
        """,
        (series_key, cint(existing_max)),
    )
    return cint(getseries(series_key, 10))


def _generate_token_display(pos_profile: str, company_abbr: str) -> str:
    """Generate human-readable token like  GGR-VEL-001."""
    store_code = _get_store_code(pos_profile)
    seq = _next_daily_seq(pos_profile)
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
        as_dict=True,
    )
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
            ],
            fields=["pos_profile"],
            limit_page_length=5,
        )
        mapped_profiles = sorted({(r.get("pos_profile") or "").strip() for r in store_candidates if r.get("pos_profile")})
        if len(mapped_profiles) == 1:
            profile = frappe.db.get_value(
                "POS Profile",
                mapped_profiles[0],
                ["name", "company", "warehouse"],
                as_dict=True,
            )
            if profile:
                return profile

    # 3) Fuzzy POS Profile fallback (must be unambiguous)
    candidates = frappe.get_all(
        "POS Profile",
        filters={"name": ["like", f"%{identifier}%"]},
        fields=["name", "company", "warehouse"],
        limit_page_length=5,
    )
    if len(candidates) == 1:
        return candidates[0]

    return None


def _with_pos_profile_lock(pos_profile: str, callback):
    lock_key = f"pos_billing_lock_{pos_profile}"
    locked = frappe.db.sql("SELECT GET_LOCK(%s, 30)", (lock_key,))[0][0]
    if locked != 1:
        frappe.throw(_("Another billing request is being processed. Please try again."), title=_("Billing Busy"))
    try:
        return callback()
    finally:
        frappe.db.sql("SELECT RELEASE_LOCK(%s)", (lock_key,))


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
        limit_page_length=1,
    )
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
        limit_page_length=batch_limit,
    )
    if hold_names:
        frappe.db.set_value(
            "POS Kiosk Token",
            {"name": ("in", hold_names)},
            "status",
            "Waiting",
            update_modified=False,
        )
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
            title=_("API Error"),
        )
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
            as_dict=True,
        )
    else:
        _raw_brands = frappe.db.sql(
            """SELECT name FROM `tabBrand`
               WHERE name != 'Test Brand'
               ORDER BY name ASC""",
            as_dict=True,
        )
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

    return {
        "store_name": profile.name,
        "company": profile.company,
        "company_abbr": company.abbr if company else "CH",
        "brands": brands,
        "issues": issues,
    }


@frappe.whitelist(allow_guest=True)
@rate_limit(limit=120, seconds=60, ip_based=True)
def get_brand_models(brand: str) -> dict:
    """
    Return distinct device model names for a given brand from Item Master.
    Uses Brand doctype as single source of truth:
    - Includes items tagged with the brand itself
    - Includes items tagged with sub-brands (where ch_parent_brand = brand)
    """
    brand = _bounded_public_text(brand, _("Brand"), 140, required=True)
    # Single source of truth: query Brand doctype for the brand + all sub-brands.
    # Some environments may not yet have ch_parent_brand custom field.
    if frappe.db.has_column("Brand", "ch_parent_brand"):
        brand_rows = frappe.db.sql(
            "SELECT name FROM `tabBrand` WHERE name = %s OR ch_parent_brand = %s",
            (brand, brand),
            as_dict=True,
        )
    else:
        brand_rows = frappe.db.sql(
            "SELECT name FROM `tabBrand` WHERE name = %s",
            (brand,),
            as_dict=True,
        )
    db_brands = [r.name for r in brand_rows] or [brand]

    rows = frappe.get_all(
        "Item",
        filters={"brand": ("in", db_brands), "disabled": 0},
        fields=["item_name"],
        order_by="item_name asc",
        limit_page_length=200,
    )

    seen = set()
    models = []
    for r in rows:
        name = r.item_name.strip()
        if name not in seen:
            seen.add(name)
            models.append(name)

    return models


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
    issue_description: str = "",
) -> dict:
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
            "device_type": device_type,
            "device_brand": device_brand,
            "device_model": device_model,
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
            "device_type", "device_brand", "device_model",
            "issue_category", "issue_description",
            "technician", "assigned_at", "started_at", "completed_at",
            "pos_profile", "company",
        ],
        order_by="creation desc",
        limit_page_length=result_limit + 1,
    )
    _ensure_result_limit(tokens, result_limit, _("Queue tokens"))

    technician_ids = sorted({t.technician for t in tokens if t.get("technician")})
    technician_names = {}
    if technician_ids:
        user_rows = frappe.get_all(
            "User",
            filters={"name": ("in", technician_ids)},
            fields=["name", "full_name"],
            limit_page_length=result_limit + 1,
        )
        _ensure_result_limit(user_rows, result_limit, _("Queue technicians"))
        technician_names = {row.name: row.full_name or row.name for row in user_rows}

    for t in tokens:
        t["device"] = _device_label(t.get('device_brand', ''), t.get('device_model', ''))
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
                {"user": _r.get("user"), "full_name": _r.get("full_name"),
                 "role": _r.get("role_profile")}
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
                    limit_page_length=result_limit + 1,
                )
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
    if doc.status not in ("Waiting",):
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
        ),
    )

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
                limit_page_length=len(token_names),
            )
            if not current_names:
                return
            frappe.db.set_value(
                "POS Kiosk Token",
                {"name": ("in", current_names)},
                "status",
                "Waiting",
                update_modified=False,
            )
            recovered.extend(current_names)
            released_holds.extend(_release_held_tokens(pos_profile))

        try:
            _with_pos_profile_lock(pos_profile, _recover_profile)
        except Exception:
            frappe.log_error(
                frappe.get_traceback(),
                f"Failed stale POS billing recovery for profile {pos_profile}",
            )

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
    sales_executive: str = "",
) -> dict:
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
                    limit_page_length=profile_limit + 1,
                )
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
        limit_page_length=report_limit + 1,
    )
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
        ),
    )
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
        limit_page_length=report_limit + 1,
    )
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
                        limit_page_length=2,
                    )
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
        as_dict=True,
    )
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
            as_dict=True,
        )
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
        as_dict=True,
    )
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
            as_dict=True,
        )
        if contact_tail:
            return contact_tail[0].link_name
    return None


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
    device_brand: str = "",
    device_model: str = "",
    item_code: str = "",
) -> dict:
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

    # Validate item_code if provided — silently drop bad references rather
    # than failing the whole walk-in log (interest capture is best-effort).
    item_code = (item_code or "").strip()
    if item_code and not frappe.db.exists("Item", item_code):
        item_code = ""

    token_display = _generate_token_display(pos_profile, company_abbr)
    device_brand = (device_brand or "").strip()
    device_model = (device_model or "").strip()
    token_payload = {
        "doctype": "POS Kiosk Token",
        "pos_profile": pos_profile,
        "company": profile.company,
        "store": profile.warehouse,
        "status": "In Progress",
        "token_display": token_display,
        "customer_name": customer_name.strip() or "Walk-in",
        "customer_phone": customer_phone.strip() or "",
        "visit_source": "Counter",
        "visit_purpose": visit_purpose,
        "issue_description": remarks,
        "device_brand": device_brand,
        "device_model": device_model,
        "started_at": now_datetime(),
        "technician": frappe.session.user,
        "expires_at": frappe.utils.add_days(now_datetime(), 1),
    }
    if visit_purpose in ("Sales", "Enquiry") and device_brand:
        token_payload["brand_interest"] = device_brand

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
        limit_page_length=report_limit + 1,
    )
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
        require_configured_roles(
            "token_assignment_roles",
            defaults=("POS Manager", "Store Manager"),
            action=_("view another technician's queue"),
        )
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
            "device_type", "device_brand", "device_model",
            "issue_category", "assigned_at", "started_at", "completed_at",
            "pos_profile",
        ],
        order_by="creation desc",
        limit_page_length=result_limit + 1,
    )
    _ensure_result_limit(tokens, result_limit, _("Technician tokens"))

    for t in tokens:
        t["device"] = _device_label(t.get('device_brand', ''), t.get('device_model', ''))

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
        limit_page_length=report_limit + 1,
    )
    _ensure_result_limit(all_tokens, report_limit, _("Token report rows"))

    technician_ids = sorted({t.technician for t in all_tokens if t.technician})
    technician_names = {}
    if technician_ids:
        user_rows = frappe.get_all(
            "User",
            filters={"name": ("in", technician_ids)},
            fields=["name", "full_name"],
            limit_page_length=report_limit + 1,
        )
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

    Result: a list of ``{name, company, warehouse}`` dicts, ordered by
    ``name asc``, filtered to the entitled subset. Never raises when the
    user has no scope; simply returns an empty list (fail-closed).
    """
    if frappe.session.user == "Guest":
        frappe.throw(
            frappe._("You must be signed in to list POS Profiles."),
            frappe.PermissionError,
        )

    result_limit = _configured_limit("token_profile_result_limit", 1000, 5000)
    if is_privileged_user():
        all_profiles = frappe.get_all(
            "POS Profile",
            filters={"disabled": 0},
            fields=["name", "company", "warehouse"],
            order_by="name asc",
            limit_page_length=result_limit + 1,
        )
        return _ensure_result_limit(all_profiles, result_limit, _("POS profiles"))

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
            limit_page_length=result_limit + 1,
        )
        return _ensure_result_limit(all_profiles, result_limit, _("POS profiles"))

    stores = scope.get("stores") or set()
    if not stores:
        # Fail-closed: an authenticated non-bypass user with no scope sees
        # nothing. Admins provision access via CH User Scope.
        return []

    profile_names = frappe.get_all(
        "CH Store",
        filters={"name": ("in", list(stores))},
        pluck="pos_profile",
        limit_page_length=result_limit + 1,
    )
    _ensure_result_limit(profile_names, result_limit, _("Scoped store profiles"))
    entitled_profiles = {name for name in profile_names if name}
    if not entitled_profiles:
        return []

    profiles = frappe.get_all(
        "POS Profile",
        filters={"disabled": 0, "name": ("in", list(entitled_profiles))},
        fields=["name", "company", "warehouse"],
        order_by="name asc",
        limit_page_length=result_limit + 1,
    )
    return _ensure_result_limit(profiles, result_limit, _("Scoped POS profiles"))


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
                  budget_range, sales_executive, engaged_at,
                  technician, creation
           FROM `tabPOS Kiosk Token`
           WHERE pos_profile = %s
                         AND status IN ('Waiting', 'Hold', 'Engaged', 'In Progress')
             AND DATE(creation) = %s
                     ORDER BY FIELD(status, 'In Progress', 'Hold', 'Waiting', 'Engaged'), creation ASC
                     LIMIT %s""",
        (pos_profile, today, result_limit + 1),
        as_dict=True,
    )
    return _ensure_result_limit(tokens, result_limit, _("Waiting POS tokens"))


@frappe.whitelist(methods=["POST"])
def convert_token_to_gofix(token_name: str, pos_profile: str,
                            customer: str = None, device_item: str = None,
                            device_condition: str = "Good",
                            accessories: str = "",
                            warranty_status: str = "Out of Warranty",
                            data_disclaimer: int = 0) -> dict:
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

    # Resolve issue category — must match GoFix Issue Category doctype
    issue_cat = None
    if token.issue_category:
        if frappe.db.exists("Issue Category", token.issue_category):
            issue_cat = token.issue_category
        else:
            # Try a case-insensitive match
            match = frappe.db.get_value(
                "Issue Category", {"category_name": token.issue_category}, "name"
            )
            issue_cat = match

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

    sr = frappe.get_doc({
        "doctype": "Service Request",
        "customer": customer or None,
        "customer_name": token.customer_name,
        "contact_number": token.customer_phone,
        "company": profile.company,
        "source_warehouse": profile.warehouse,
        "walkin_source": walkin_source,
        "product_condition_desc": product_condition_desc,
        "backup_info": backup_info,
        "decision": "Accepted",        # Customer is present — accepting the device
        "device_item": device_item or None,
        "device_item_name": _device_label(token.device_brand, token.device_model) if not device_item else None,
        "brand": token.device_brand,
        "device_condition": device_condition,
        "accessories_received": accessories,
        "warranty_status": warranty_status,
        "issue_category": issue_cat,
        "issue_description": token.issue_description or token.issue_category,
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
