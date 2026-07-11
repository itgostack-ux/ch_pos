import frappe
from frappe.utils import flt, cint, now_datetime
from erpnext.accounts.doctype.sales_invoice.sales_invoice import SalesInvoice

from ch_item_master.ch_item_master.serial_utils import get_serial_nos_from_item as _get_serial_nos_from_item


def _ensure_lifecycle_exists(serial_no, item_code=None, company=None, warehouse=None):
    """Auto-create a CH Serial Lifecycle row if one does not exist.

    This closes the gap where serials entered the system outside the
    Purchase Receipt IMEI-tracking path and therefore never got a
    lifecycle document.  The row is created with status 'In Stock'
    so that the subsequent sale/return transition is valid.

    Advisory-locked to prevent duplicate creation under concurrent POS
    submissions for the same serial (e.g. family plan, dual-device sale).
    """
    if frappe.db.exists("CH Serial Lifecycle", serial_no):
        return

    if not item_code:
        item_code = frappe.db.get_value("Serial No", serial_no, "item_code")
    if not item_code:
        return  # cannot create without item_code

    lock_key = f"serial_create_{frappe.scrub(str(serial_no))}_lifecycle"
    got_lock = frappe.db.sql("SELECT GET_LOCK(%s, 10)", (lock_key,))[0][0]
    if not got_lock:
        frappe.log_error(
            f"Could not acquire lifecycle lock for {serial_no}",
            "POS Invoice Serial Lifecycle Auto-Create",
        )
        return
    try:
        if frappe.db.exists("CH Serial Lifecycle", serial_no):
            return  # raced with another worker — already created

        store_name = (
            frappe.db.get_value("CH Store", {"warehouse": warehouse}, "name") if warehouse else None
        )
        lc = frappe.new_doc("CH Serial Lifecycle")
        lc.serial_no = serial_no
        lc.item_code = item_code
        lc.lifecycle_status = "In Stock"
        lc.current_company = company
        lc.current_warehouse = warehouse
        lc.current_store = store_name or ""
        lc.append("lifecycle_log", {
            "log_timestamp": now_datetime(),
            "from_status": "",
            "to_status": "In Stock",
            "changed_by": frappe.session.user,
            "company": company,
            "warehouse": warehouse,
            "remarks": "Auto-created on sale — Serial No existed without lifecycle record",
        })
        lc.flags.ignore_permissions = True
        lc.flags.ignore_validate = True
        lc.insert()
    finally:
        frappe.db.sql("SELECT RELEASE_LOCK(%s)", (lock_key,))


def _update_serial_status(serial_no, new_status, company=None, warehouse=None, remarks=None, **kwargs):
    """Call the centralized CH Serial Lifecycle API for status transitions."""
    from ch_item_master.ch_item_master.doctype.ch_serial_lifecycle.ch_serial_lifecycle import (
        update_lifecycle_status,
    )
    return update_lifecycle_status(
        serial_no=serial_no,
        new_status=new_status,
        company=company,
        warehouse=warehouse,
        remarks=remarks,
        **kwargs,
    )


def _create_or_update_device(serial_no, customer, **kwargs):
    """Call the centralized CH Customer Device API for device registration."""
    from ch_item_master.ch_customer_master.doctype.ch_customer_device.ch_customer_device import (
        CHCustomerDevice,
    )
    return CHCustomerDevice.create_or_update_for_serial(serial_no, customer, **kwargs)


class CustomPOSInvoice(SalesInvoice):
    """Extends Sales Invoice for margin scheme GST on selling side.

    Custom fields on Sales Invoice (is_pos=1) replace the old POS Invoice flow.
    Each POS transaction is now a direct Sales Invoice — no consolidation needed.
    """
    # Sales Invoice DocType lacks this Sales Invoice field; accounts_controller
    # accesses it during make_precision_loss_gl_entry.
    use_company_roundoff_cost_center = 0
    # Sales Invoice DocType lacks this Sales Invoice field; used in
    # make_customer_gl_entry and make_tax_gl_entries for return invoices.
    update_outstanding_for_self = 0
    def validate(self):
        _hydrate_imported_sales_invoice_defaults(self)

        # When loyalty points are redeemed, paid_amount must include loyalty_amount
        # so ERPNext's validate_full_payment (paid_amount >= invoice_total) passes.
        # ERPNext's set_paid_amount() only sums payment rows, not loyalty — we fix that here.
        if self.redeem_loyalty_points and flt(self.loyalty_amount) > 0:
            payments_sum = sum(flt(p.amount) for p in self.get("payments", []))
            self.paid_amount = payments_sum + flt(self.loyalty_amount)
        super().validate()
        # Returns carry taxes from the original invoice already computed by
        # calculate_taxes_and_totals().  Replacing the tax template would zero
        # out base_tax_amount_after_discount_amount, leaving grand_total higher
        # than the sum of GL debits (margin scheme adjustments also don't apply
        # to negative-qty return rows).
        if not self.is_return:
            _ensure_gst_template(self)
            _apply_margin_scheme(self)
        else:
            # For returns ensure grand_total is consistent with the saved taxes
            # in case any validate hook modified items/amounts.
            self.run_method("calculate_taxes_and_totals")

    def on_submit(self):
        # POS-17 fix: Validate bundle item pricing is still active at submit time
        self._validate_bundle_pricing()
        # Standard SalesInvoice.on_submit() already calls update_stock_ledger(),
        # make_gl_entries(), and repost_future_sle_and_gle() when update_stock=1.
        # Do NOT duplicate those calls — that would create 2× SLE and 2× GL entries.
        #
        # Policy: returns and exchanges do NOT earn or deduct loyalty points.
        # ERPNext's on_submit would otherwise delete+recreate the original invoice's
        # LPE (proportional to returned amount).  We suppress that by temporarily
        # hiding loyalty_program from the SalesInvoice chain for return invoices.
        _saved_lp = None
        if self.is_return:
            _saved_lp = self.loyalty_program
            self.loyalty_program = None
        super().on_submit()
        if _saved_lp:
            self.loyalty_program = _saved_lp
            self.db_set("loyalty_program", _saved_lp)

        # Rolling expiry: each purchase resets the loyalty expiry clock so
        # active buyers never lose points mid-cycle (market-standard behaviour).
        if not self.is_return and self.customer and self.loyalty_program:
            try:
                from ch_item_master.ch_customer_master.loyalty import reset_loyalty_expiry_on_purchase
                reset_loyalty_expiry_on_purchase(self.customer, str(self.posting_date))
            except Exception:
                frappe.log_error(frappe.get_traceback(), "reset_loyalty_expiry_on_purchase failed")

    def _validate_bundle_pricing(self):
        """POS-17 fix: Validate that bundle/free items still have active pricing at submit time.
        Prevents stale bundle deals from being invoiced after pricing changes.
        """
        from ch_pos.api.pos_api import resolve_bundle_parent

        for item in self.items:
            if not item.get("custom_is_free_bundle_item"):
                continue
            # Check that the Product Bundle is still active. The frontend
            # stamps ``custom_bundle_parent`` with the scanned variant's
            # item_code; ``resolve_bundle_parent`` falls back to the
            # template so a bundle defined on ``Apple iPhone 13 Pro``
            # still validates when the invoice line is ``I02793`` etc.
            parent_item = item.get("custom_bundle_parent") or ""
            if parent_item and not resolve_bundle_parent(parent_item):
                frappe.throw(
                    _("Bundle offer for {0} is no longer active. "
                      "Please remove the free item {1} and re-add.").format(
                        parent_item, item.item_code),
                    title=_("Expired Bundle Offer"),
                )
            # Verify CH Item Price is still active for the bundle child
            if frappe.db.exists("DocType", "CH Item Price"):
                active_price = frappe.db.exists("CH Item Price", {
                    "item_code": item.item_code,
                    "status": "Active",
                })
                if not active_price and flt(item.rate) > 0:
                    frappe.msgprint(
                        _("Warning: No active price found for bundle item {0}. "
                          "Pricing may be outdated.").format(item.item_code),
                        indicator="orange",
                    )

    def before_cancel(self):
        _enforce_cancel_policy(self)

    def on_cancel(self):
        # Cache serial numbers BEFORE standard on_cancel delinks serial bundles.
        # Doc_events (reverse_serial_lifecycle, deactivate_customer_devices) fire
        # after on_cancel returns, by which time serial_and_batch_bundle is cleared.
        self._cached_serial_nos = {}
        for item in self.items:
            self._cached_serial_nos[item.name] = _get_serial_nos_from_item(item)

        # Delegate to standard SalesInvoice.on_cancel() which handles:
        #   - check_if_return_invoice_linked_with_payment_entry
        #   - super().on_cancel() (SellingController chain)
        #   - update_status_updater_args / update_prevdoc_status
        #   - update_billing_status_in_dn / update_billing_status_for_zero_amount_refdoc
        #   - SalesTaxWithholding
        #   - update_stock_ledger / make_gl_entries_on_cancel / repost_future_sle_and_gle
        #   - update_stock_reservation_entries
        #   - process_asset_depreciation
        #   - loyalty points cleanup
        #   - coupon code count
        #   - delete_auto_created_batches
        #   - delink serial bundles (implicit via SLE cancellation)
        super().on_cancel()

    def get_gl_entries(self, warehouse_account=None):
        # Margin-scheme GST is already correct in the GL by the time we get
        # here:  validate() → _apply_margin_scheme() rewrites each tax row's
        # charge_type to "Actual" and stores the *margin-reduced* amount in
        # tax_amount / tax_amount_after_discount_amount.  ERPNext's parent
        # get_gl_entries() reads those final amounts and emits a balanced
        # set of GL entries (Dr Debtor = Cr Sales + Cr Output GST).
        #
        # The previous override attempted to "reduce GST GL amounts by the
        # margin-item GST saving" a second time here, which double-deducted
        # the saving and produced the well-known "Debit and Credit not equal
        # in Sales Invoice — Difference X" submit error.  See the formula in
        # commit history; the math was:
        #
        #     gst_saving = Σ_tax_rows (full_amount × rate − margin_base × rate) / 100
        #     each "gst" GL.credit -= gst_saving / 2
        #
        # For an in-state cart this subtracted gst_saving across 2 GL rows
        # (CGST + SGST) → total deduction = gst_saving, leaving the GL short
        # by exactly that amount on the credit side.  For an out-state cart
        # it subtracted only gst_saving/2 from the single IGST row → still
        # unbalanced, just by half.  In every case the saving had ALREADY
        # been removed from the tax row by _apply_margin_scheme, so this
        # second deduction was always wrong.
        #
        # Keep the override intact (in case future logic needs a hook here),
        # but do not modify the entries.
        return super().get_gl_entries(warehouse_account)


# ── Cancel policy ────────────────────────────────────────────────


def _enforce_cancel_policy(doc):
    """Block cancellation of POS Sales Invoices outside the same open billing session.

    Policy (matches SAP/Oracle/Tally market standard):
    - Administrator is never blocked (emergency override).
    - Same-day invoice AND POS session still Open → allowed.
    - Invoice from a previous day → blocked. Use Sales Return.
    - Same-day invoice but session already Closed (EOD done) → blocked. Use Sales Return.

    Non-POS (ERP-direct) Sales Invoices are not blocked here; they fall
    under the standard ERPNext accounting period lock.
    """
    if frappe.flags.get("ignore_cancel_lock"):
        return
    if frappe.session.user == "Administrator":
        return
    if not cint(doc.get("is_pos")):
        return

    from frappe.utils import getdate, today as _today

    # Block if the invoice belongs to a previous day
    if doc.posting_date and getdate(doc.posting_date) < getdate(_today()):
        frappe.throw(
            frappe._(
                "Sales Invoice {0} is dated {1} and cannot be cancelled. "
                "The billing date has passed — please create a Sales Return instead."
            ).format(frappe.bold(doc.name), doc.posting_date),
            title=frappe._("Cancellation Not Allowed"),
        )

    # Block if today's POS session is already closed (EOD processed)
    if doc.pos_profile:
        try:
            from ch_pos.pos_core.doctype.ch_pos_session.ch_pos_session import get_active_session
            active = get_active_session(doc.pos_profile)
            if not active or active.get("status") != "Open":
                frappe.throw(
                    frappe._(
                        "The POS session for {0} is closed. "
                        "Sales Invoice {1} cannot be cancelled after End of Day — "
                        "please create a Sales Return instead."
                    ).format(frappe.bold(doc.pos_profile), frappe.bold(doc.name)),
                    title=frappe._("Cancellation Not Allowed"),
                )
        except frappe.ValidationError:
            raise
        except Exception:
            pass  # session check failure must not block cancel silently — let it proceed


# ── doc_event handlers (called via hooks.py) ─────────────────────


def validate_margin_scheme(doc, method=None):
    """Hook: validate — apply margin scheme GST calculation if applicable."""
    # Return invoices carry taxes from the original invoice.  Replacing the GST
    # template would reset base_tax_amount_after_discount_amount to 0, making GL
    # entries omit the tax reversal entry (→ "Debit and Credit not equal" -9900).
    if doc.get("is_return"):
        return
    _ensure_gst_template(doc)
    _apply_margin_scheme(doc)


def create_customer_device_records(doc, method=None):
    """Hook: on_submit — register sold devices under customer's account.

    Delegates to ch_item_master's CHCustomerDevice.create_or_update_for_serial()
    which handles deduplication, warranty syncing, and field population.
    """
    for item in doc.items:
        for sn in _get_serial_nos_from_item(item):

            device_kwargs = {
                "company": doc.company,
                "item_code": item.item_code,
                "item_name": item.item_name,
                "brand": item.brand or frappe.db.get_value("Item", item.item_code, "brand"),
                "purchase_date": doc.posting_date,
                # purchase_invoice links to Sales Invoice — Sales Invoice is a separate flow
                # Store the Sales Invoice name in a custom field if available, else skip
                "purchase_price": flt(item.rate),
                # purchase_store is a Link to Warehouse; use item.warehouse (already validated)
                "purchase_store": item.warehouse or frappe.db.get_value(
                    "POS Profile", doc.pos_profile, "warehouse"),
                "current_status": "Owned",
            }
            # Only set purchase_invoice if CH Customer Device has a pos_invoice field
            # (to avoid Link validation errors against tabSales Invoice)
            from ch_item_master.ch_customer_master.doctype.ch_customer_device.ch_customer_device import CHCustomerDevice
            cd_meta_fields = {f.fieldname for f in frappe.get_meta("CH Customer Device").fields}
            if "pos_invoice" in cd_meta_fields:
                device_kwargs["pos_invoice"] = doc.name
            elif "purchase_invoice" in cd_meta_fields:
                # Only set if the Link points to Sales Invoice (not Sales Invoice)
                pf = frappe.get_meta("CH Customer Device").get_field("purchase_invoice")
                if pf and pf.options == "Sales Invoice":
                    device_kwargs["purchase_invoice"] = doc.name

            # Attach warranty info if selected (don't set active_warranty_plan —
            # that's a Link to Active VAS Plans which is created separately)
            if item.get("custom_warranty_plan"):
                plan = frappe.get_cached_doc("CH Warranty Plan", item.custom_warranty_plan)
                device_kwargs.update({
                    "warranty_plan_name": plan.plan_name,
                    "warranty_months": plan.duration_months,
                    "warranty_expiry": frappe.utils.add_months(doc.posting_date, plan.duration_months),
                    "warranty_status": "In Warranty",
                })

            _create_or_update_device(sn, doc.customer, **device_kwargs)


def update_serial_lifecycle(doc, method=None):
    """Hook: on_submit — update CH Serial Lifecycle status.

    For sales: In Stock → Sold.
    For returns (is_return=1): Sold → Returned → In Stock.
    Delegates to ch_item_master's update_lifecycle_status() which enforces
    valid state transitions and writes audit log entries.
    """
    if doc.is_return:
        _return_serial_lifecycle(doc)
        return

    for item in doc.items:
        for sn in _get_serial_nos_from_item(item):
            wh = doc.get("set_warehouse") or item.warehouse
            _ensure_lifecycle_exists(sn, item_code=item.item_code,
                                     company=doc.company, warehouse=wh)

            # If the device is still in "Received" state (e.g. goods receipt
            # happened but no explicit check-in), auto-advance to "In Stock"
            # first so the subsequent "Sold" transition is valid.
            current_status = frappe.db.get_value("CH Serial Lifecycle", sn, "lifecycle_status")
            if current_status == "Received":
                _update_serial_status(
                    serial_no=sn,
                    new_status="In Stock",
                    company=doc.company,
                    warehouse=wh,
                    remarks=f"Auto-advanced Received → In Stock on sale via {doc.name}",
                )

            _update_serial_status(
                serial_no=sn,
                new_status="Sold",
                company=doc.company,
                warehouse=wh,
                remarks=f"Sold via Sales Invoice {doc.name}",
                sale_date=doc.posting_date,
                sale_document=doc.name,
                sale_rate=flt(item.rate),
                customer=doc.customer,
                customer_name=doc.customer_name,
            )


def _return_serial_lifecycle(doc):
    """Handle serial lifecycle for return invoices (is_return=1).

    Sold → Returned → In Stock (item returned to store).
    """
    for item in doc.items:
        for sn in _get_serial_nos_from_item(item):
            _ensure_lifecycle_exists(sn, item_code=item.item_code,
                                     company=doc.company, warehouse=item.warehouse)

            current_status = frappe.db.get_value("CH Serial Lifecycle", sn, "lifecycle_status")
            if current_status == "Sold":
                _update_serial_status(
                    serial_no=sn,
                    new_status="Returned",
                    company=doc.company,
                    remarks=f"Returned via Sales Invoice {doc.name} (return against {doc.return_against})",
                    sale_date=None,
                    sale_document=None,
                    sale_rate=0,
                    customer=None,
                    customer_name=None,
                )
                _update_serial_status(
                    serial_no=sn,
                    new_status="In Stock",
                    company=doc.company,
                    warehouse=item.warehouse,
                    remarks=f"Returned to stock — Sales Invoice {doc.name}",
                )


def reverse_serial_lifecycle(doc, method=None):
    """Hook: on_cancel — revert serial lifecycle status.

    For sales: Sold → Returned → In Stock.
    For returns: In Stock → Sold (re-mark as sold since the return is voided).
    """
    # Require a cancellation reason from non-system users
    if frappe.session.user != "Administrator":
        if not (doc.get("custom_cancel_reason") or "").strip():
            frappe.throw(
                frappe._("A cancellation reason is required to cancel Sales Invoice {0}").format(doc.name)
            )

    # Emit audit log for the cancellation
    try:
        from ch_pos.audit import log_business_event
        log_business_event(
            event_type="Sales Invoice Cancelled",
            ref_doctype="Sales Invoice",
            ref_name=doc.name,
            before="Submitted",
            after="Cancelled",
            remarks=doc.get("custom_cancel_reason") or "",
            company=doc.company,
        )
    except Exception:
        frappe.log_error(frappe.get_traceback(), "Audit log failed on Sales Invoice cancel")

    if doc.is_return:
        _cancel_return_serial_lifecycle(doc)
        return

    for item in doc.items:
        for sn in _get_serial_nos_from_item(item, parent_doc=doc):
            _ensure_lifecycle_exists(sn, item_code=item.item_code,
                                     company=doc.company, warehouse=item.warehouse)

            current_status = frappe.db.get_value("CH Serial Lifecycle", sn, "lifecycle_status")
            # Sold → Returned is a valid transition; then Returned → In Stock
            if current_status == "Sold":
                _update_serial_status(
                    serial_no=sn,
                    new_status="Returned",
                    company=doc.company,
                    remarks=f"Sales Invoice {doc.name} cancelled",
                    sale_date=None,
                    sale_document=None,
                    sale_rate=0,
                    customer=None,
                    customer_name=None,
                )
                # Returned → In Stock
                _update_serial_status(
                    serial_no=sn,
                    new_status="In Stock",
                    company=doc.company,
                    warehouse=item.warehouse,
                    remarks=f"Returned to stock — Sales Invoice {doc.name} cancelled",
                )


def _cancel_return_serial_lifecycle(doc):
    """Handle serial lifecycle when a return invoice is cancelled.

    Cancelling a return means the original sale stands — re-mark as Sold.
    In Stock → Sold (with original sale details from return_against).
    """
    orig_inv = doc.return_against
    for item in doc.items:
        for sn in _get_serial_nos_from_item(item, parent_doc=doc):
            _ensure_lifecycle_exists(sn, item_code=item.item_code,
                                     company=doc.company, warehouse=item.warehouse)

            current_status = frappe.db.get_value("CH Serial Lifecycle", sn, "lifecycle_status")
            if current_status == "In Stock":
                # Look up original sale details
                sale_date = None
                sale_rate = 0
                customer = doc.customer
                customer_name = doc.customer_name
                if orig_inv:
                    sale_date = frappe.db.get_value("Sales Invoice", orig_inv, "posting_date")
                    # Find rate from original invoice item — check serial_no text or bundle
                    orig_items = frappe.db.get_all("Sales Invoice Item",
                        filters={"parent": orig_inv, "serial_no": ["like", f"%{sn}%"]},
                        fields=["rate"], limit=1)
                    if not orig_items:
                        # v16: serial may be in Serial and Batch Bundle instead
                        orig_items = frappe.db.sql("""
                            SELECT si_item.rate
                            FROM `tabSales Invoice Item` si_item
                            JOIN `tabSerial and Batch Entry` sbe
                                ON sbe.parent = si_item.serial_and_batch_bundle
                            WHERE si_item.parent = %s AND sbe.serial_no = %s
                            LIMIT 1
                        """, (orig_inv, sn), as_dict=True)
                    if orig_items:
                        sale_rate = flt(orig_items[0].rate)
                _update_serial_status(
                    serial_no=sn,
                    new_status="Sold",
                    company=doc.company,
                    remarks=f"Return {doc.name} cancelled — sale {orig_inv} reinstated",
                    sale_date=sale_date,
                    sale_document=orig_inv,
                    sale_rate=sale_rate,
                    customer=customer,
                    customer_name=customer_name,
                )


def update_kiosk_token_status(doc, method=None):
    """Hook: on_submit — mark kiosk token as Converted if linked, record exit time."""
    token = doc.get("custom_kiosk_token")
    if not token:
        # Auto-create token if POS invoice submitted without one (enforcement fallback)
        if cint(doc.get("is_pos")) and doc.get("pos_profile"):
            token = _auto_create_token_for_invoice(doc)
            if token:
                frappe.db.set_value("Sales Invoice", doc.name, "custom_kiosk_token", token)
    if token:
        frappe.db.set_value("POS Kiosk Token", token, {
            "status": "Converted",
            "converted_invoice": doc.name,
            "exit_at": now_datetime(),
        })
        try:
            from ch_pos.api.token_api import release_pos_billing
            release_pos_billing(token_name=token, pos_profile=doc.pos_profile, revert_current=0)
        except Exception:
            frappe.log_error(frappe.get_traceback(), f"Failed to release held queue tokens after invoice {doc.name}")


def _auto_create_token_for_invoice(doc):
    """Create a retroactive token for a POS invoice submitted without one."""
    try:
        from ch_pos.api.token_api import _generate_token_display
        company_abbr = frappe.db.get_value("Company", doc.company, "abbr") or "CH"
        token_display = _generate_token_display(doc.pos_profile, company_abbr)
        token_doc = frappe.get_doc({
            "doctype": "POS Kiosk Token",
            "pos_profile": doc.pos_profile,
            "company": doc.company,
            "store": doc.get("set_warehouse") or "",
            "status": "Converted",
            "token_display": token_display,
            "customer_name": doc.customer_name or "Walk-in",
            "visit_source": "Counter",
            "visit_purpose": "Sales",
            "engaged_at": doc.posting_date,
            "exit_at": now_datetime(),
            "converted_invoice": doc.name,
            "sales_executive": doc.get("custom_sales_executive") or "",
            "expires_at": now_datetime(),
        })
        token_doc.flags.ignore_permissions = True
        token_doc.insert()
        return token_doc.name
    except Exception:
        frappe.log_error(frappe.get_traceback(), "Auto-create token for POS invoice failed")
        return None


def deactivate_customer_devices(doc, method=None):
    """Hook: on_cancel — deactivate customer device records created by this invoice."""
    for item in doc.items:
        for sn in _get_serial_nos_from_item(item, parent_doc=doc):

            device = frappe.db.get_value(
                "CH Customer Device",
                {"serial_no": sn, "customer": doc.customer, "purchase_invoice": doc.name},
                "name",
            )
            if device:
                frappe.db.set_value("CH Customer Device", device, "current_status", "Inactive")


def revert_kiosk_token_status(doc, method=None):
    """Hook: on_cancel — revert kiosk token from Converted back to Expired.

    We set 'Expired' rather than 'Active' since the token has likely passed its
    expiry time by now. If the user needs to re-use it, they should create a new token.
    """
    token = doc.get("custom_kiosk_token")
    if token:
        frappe.db.set_value("POS Kiosk Token", token, {
            "status": "Expired",
            "converted_invoice": None,
        })
        try:
            from ch_pos.api.token_api import release_pos_billing
            release_pos_billing(token_name=token, pos_profile=doc.pos_profile, revert_current=0)
        except Exception:
            frappe.log_error(frappe.get_traceback(), f"Failed to release held queue tokens after cancel of invoice {doc.name}")


# ── Coupon usage counter (Pricing Rule integration) ─────────────────


def increment_coupon_usage(doc, method=None):
    """Hook: on_submit — increment the `used` counter on the linked Coupon Code.

    In standard ERPNext, Sales Invoice has a `coupon_code` field that auto-fires
    `update_coupon_code_count` on submit/cancel. In this site we store the coupon
    on `custom_coupon_code` (the standard field does not exist in v15), so the
    standard hook never fires and `maximum_use` would never be enforced.

    This hook bridges the gap by delegating to the standard ERPNext utility,
    preserving the original max-use validation semantics.
    """
    coupon_name = doc.get("custom_coupon_code")
    if not coupon_name:
        return
    try:
        from erpnext.accounts.doctype.pricing_rule.utils import update_coupon_code_count
        update_coupon_code_count(coupon_name, "used")
    except Exception:
        # Standard util raises on max-use exhausted; let it bubble.
        # Other failures (missing coupon, perms) must not block invoice submit.
        if frappe.flags.get("in_test"):
            raise
        frappe.log_error(
            title="increment_coupon_usage failed",
            message=f"Invoice {doc.name} coupon {coupon_name}: " + frappe.get_traceback(),
        )


def decrement_coupon_usage(doc, method=None):
    """Hook: on_cancel — decrement the `used` counter when an SI is cancelled."""
    coupon_name = doc.get("custom_coupon_code")
    if not coupon_name:
        return
    try:
        from erpnext.accounts.doctype.pricing_rule.utils import update_coupon_code_count
        update_coupon_code_count(coupon_name, "cancelled")
    except Exception:
        if frappe.flags.get("in_test"):
            raise
        frappe.log_error(
            title="decrement_coupon_usage failed",
            message=f"Invoice {doc.name} coupon {coupon_name}: " + frappe.get_traceback(),
        )


# ── Margin Scheme GST (selling side) ────────────────────────────


def _is_data_import_context(doc) -> bool:
    return bool(
        getattr(frappe.flags, "in_import", False)
        or getattr(frappe.flags, "in_data_import", False)
        or getattr(getattr(doc, "flags", None), "in_import", False)
    )


def _hydrate_imported_sales_invoice_defaults(doc):
    """Fill server-side defaults that Data Import does not fetch from the form UI."""
    if not _is_data_import_context(doc):
        return

    # Historic data import must reflect the imported file EXACTLY. Pricing
    # Rules — in particular "free item" product-discount rules — otherwise
    # fire during validate() and inject an EXTRA item line (e.g. a free
    # accessory). With update_stock=1 that extra line posts its own Stock
    # Ledger Entry, so a single imported invoice line shows up as TWO stock
    # movements. Suppress rule application on import so lines/amounts stay as
    # supplied. Import-only (guarded by _is_data_import_context above) — live
    # POS sales still apply pricing rules normally. Set here, before
    # super().validate() runs the pricing-rule engine.
    if doc.meta.has_field("ignore_pricing_rule"):
        doc.ignore_pricing_rule = 1

    _hydrate_imported_item_prices(doc)
    _guard_imported_line_has_price(doc)
    _hydrate_imported_tax_rows(doc)


def _guard_imported_line_has_price(doc):
    """Fail loud when an imported sale line carries no price.

    An import must use the price from the uploaded file exactly — we never
    fabricate a price from the Item master / CH Item Price (historical sales
    need the ORIGINAL price, not a current one). So a line that still has
    neither rate nor amount after file hydration means the CSV was missing the
    price for that row. Surface it (naming the row) rather than silently booking
    a zero-value, zero-tax invoice that posts nothing to the GL. Free /
    free-bundle lines are exempt — rate 0 is legitimate there.

    Data Import runs each row in its own savepoint and records the raised
    message against that row, so the rest of the file still imports.
    """
    for item in doc.get("items") or []:
        if cint(item.get("is_free_item")) or cint(item.get("custom_is_free_bundle_item")):
            continue
        if flt(item.get("rate")) or flt(item.get("amount")):
            continue
        frappe.throw(
            frappe._(
                "Import row for item {0} has no price. Provide the Rate (or Amount) "
                "in the uploaded file — imported sales must use the price from the "
                "file, so the line, tax and ledger postings match the original sale."
            ).format(frappe.bold(item.get("item_code") or item.get("idx"))),
            title=frappe._("Missing Price in Import"),
        )


def _hydrate_imported_item_prices(doc):
    if not doc.get("items"):
        return

    _ensure_import_price_list_defaults(doc)

    from erpnext.stock.get_item_details import get_item_details
    from erpnext.stock.get_item_details import ItemDetailsCtx

    parent_dict = {}
    for fieldname in doc.meta.get_valid_columns():
        parent_dict[fieldname] = doc.get(fieldname)

    parent_dict.update({
        "document_type": "Sales Invoice Item",
        "price_list": doc.get("selling_price_list"),
    })

    for item in doc.get("items"):
        if not item.get("item_code"):
            continue
        if cint(item.get("is_free_item")) or cint(item.get("custom_is_free_bundle_item")):
            continue

        ctx = ItemDetailsCtx(parent_dict.copy())
        ctx.update(item.as_dict())
        ctx.update(
            {
                "doctype": doc.doctype,
                "name": doc.name,
                "child_doctype": item.doctype,
                "child_docname": item.name,
                "ignore_pricing_rule": doc.get("ignore_pricing_rule") or 0,
            }
        )
        if not ctx.transaction_date:
            ctx.transaction_date = ctx.posting_date

        details = get_item_details(ctx, doc, for_validate=True, overwrite_warehouse=False)
        _apply_import_item_details(doc, item, details)


def _ensure_import_price_list_defaults(doc):
    if not doc.get("selling_price_list"):
        doc.selling_price_list = (
            frappe.db.get_single_value("Selling Settings", "selling_price_list")
            or frappe.db.get_value("Price List", {"selling": 1, "enabled": 1}, "name")
        )

    if doc.get("selling_price_list") and not doc.get("price_list_currency"):
        doc.price_list_currency = frappe.db.get_value(
            "Price List", doc.selling_price_list, "currency", cache=True
        )

    if not flt(doc.get("plc_conversion_rate")):
        doc.plc_conversion_rate = 1
    if not flt(doc.get("conversion_rate")):
        doc.conversion_rate = 1


def _apply_import_item_details(doc, item, details):
    for fieldname in (
        "item_name",
        "description",
        "uom",
        "stock_uom",
        "conversion_factor",
        "income_account",
        "cost_center",
        "warehouse",
        "item_tax_template",
    ):
        value = details.get(fieldname)
        if value is not None and item.meta.get_field(fieldname) and not item.get(fieldname):
            item.set(fieldname, value)

    price_list_rate = flt(item.get("price_list_rate")) or flt(details.get("price_list_rate"))
    imported_amount = flt(item.get("amount"))
    rate = flt(item.get("rate")) or flt(details.get("rate")) or price_list_rate
    qty = flt(item.get("qty"))

    # The uploaded file's own price is authoritative: the file's rate column
    # wins (resolved above), else the line amount / qty. We deliberately do NOT
    # fabricate a POS price from CH Item Price here — historical sales must keep
    # the ORIGINAL price from the file, not a current one. (`details.rate` /
    # `price_list_rate` may still fill from the configured selling price list via
    # get_item_details — pre-existing behaviour — but when the file gives neither
    # rate nor amount and no price list applies, the line stays 0 and
    # _guard_imported_line_has_price() rejects the row instead of booking ₹0.)
    if not rate and qty and imported_amount:
        rate = flt(imported_amount / qty, item.precision("rate"))

    if price_list_rate and item.meta.get_field("price_list_rate") and not flt(item.get("price_list_rate")):
        item.price_list_rate = price_list_rate
    if rate and item.meta.get_field("rate") and not flt(item.get("rate")):
        item.rate = rate

    if rate and qty and item.meta.get_field("amount") and not flt(item.get("amount")):
        item.amount = qty * rate

    conversion_rate = flt(doc.get("conversion_rate")) or 1
    if rate and item.meta.get_field("base_rate") and not flt(item.get("base_rate")):
        item.base_rate = rate * conversion_rate
    if price_list_rate and item.meta.get_field("base_price_list_rate") and not flt(item.get("base_price_list_rate")):
        item.base_price_list_rate = price_list_rate * conversion_rate
    if flt(item.get("amount")) and item.meta.get_field("base_amount") and not flt(item.get("base_amount")):
        item.base_amount = flt(item.amount) * conversion_rate

    if rate and item.meta.get_field("net_rate") and not flt(item.get("net_rate")):
        item.net_rate = rate
    if flt(item.get("amount")) and item.meta.get_field("net_amount") and not flt(item.get("net_amount")):
        item.net_amount = flt(item.amount)
    if rate and item.meta.get_field("base_net_rate") and not flt(item.get("base_net_rate")):
        item.base_net_rate = rate * conversion_rate
    if flt(item.get("amount")) and item.meta.get_field("base_net_amount") and not flt(item.get("base_net_amount")):
        item.base_net_amount = flt(item.amount) * conversion_rate


def _hydrate_imported_tax_rows(doc):
    if not doc.meta.get_field("taxes") or doc.get("taxes"):
        return

    if doc.get("taxes_and_charges"):
        tax_master_doctype = doc.meta.get_field("taxes_and_charges").options
        doc.append_taxes_from_master(tax_master_doctype)
        return

    doc.set_taxes()


def _is_margin_scheme_item(item):
    """Return True when the item should follow margin-scheme GST rules."""
    if cint(item.get("custom_is_margin_item")):
        return True

    item_code = item.get("item_code")
    if not item_code:
        return False

    return frappe.db.get_value("Item", item_code, "ch_item_type") in ("Refurbished", "Pre-Owned")


# Company GSTIN state code is derived once from the Company record.
# In India, GST state codes are the first 2 digits of the GSTIN.
_COMPANY_STATE_CODE_CACHE: dict = {}


def _get_company_state_code(company: str) -> str:
    if company not in _COMPANY_STATE_CODE_CACHE:
        gstin = frappe.db.get_value("Company", company, "gstin") or ""
        _COMPANY_STATE_CODE_CACHE[company] = gstin[:2]
    return _COMPANY_STATE_CODE_CACHE[company]


def _ensure_gst_template(doc):
    """Auto-select in-state (CGST+SGST) or out-state (IGST) tax template.

    Rules:
    - Customer state == Company state  → Output GST In-state
    - Customer state != Company state  → Output GST Out-state
    - If place_of_supply is missing or templates don't exist, does nothing.

    Also applies the selected template's tax rows onto the invoice so that
    _apply_margin_scheme() can recalculate them correctly on margin.
    """
    company = doc.company
    if not company:
        return

    company_state = _get_company_state_code(company)

    # place_of_supply format: "33-Tamil Nadu" or just "33"
    pos = (doc.place_of_supply or "").strip()
    customer_state = pos[:2] if pos else ""

    # Determine which template to use
    if company_state and customer_state:
        if customer_state == company_state:
            desired_template = f"Output GST In-state - {frappe.db.get_value('Company', company, 'abbr')}"
        else:
            desired_template = f"Output GST Out-state - {frappe.db.get_value('Company', company, 'abbr')}"
    else:
        # Fall back to whatever is already on the invoice
        desired_template = doc.taxes_and_charges

    if not desired_template or not frappe.db.exists("Sales Taxes and Charges Template", desired_template):
        return

    # If template already applied and matches, skip
    if doc.taxes_and_charges == desired_template and doc.taxes:
        return

    # Load template rows onto the invoice
    doc.taxes_and_charges = desired_template
    template = frappe.get_doc("Sales Taxes and Charges Template", desired_template)
    doc.set("taxes", [])
    for row in template.taxes:
        doc.append("taxes", {
            "charge_type":        row.charge_type,
            "account_head":       row.account_head,
            "description":        row.description or row.account_head,
            "rate":               row.rate,
            "tax_amount":         0,
            "base_tax_amount":    0,
            "tax_amount_after_discount_amount": 0,
            "base_tax_amount_after_discount_amount": 0,
        })
    # Recompute totals after replacing the tax template so that grand_total
    # and base_tax_amount_after_discount_amount stay in sync.  Without this the
    # GL entries would use the old grand_total while tax amounts are 0.
    doc.run_method("calculate_taxes_and_totals")


def _apply_margin_scheme(doc):
    """Apply margin scheme GST calculation for used/refurbished items.

    Uses the same formula as ch_erp15 delivery_note.full_recalculation:
    1. Get exempted value per serial from Purchase Receipt Item
       (falls back to purchase_rate from CH Serial Lifecycle)
    2. taxable_per_unit = (selling_rate - exempt_per_unit) / 1.18
    3. tax = total_taxable × rate%
    4. exempted_value = item_amount - taxable - item_gst
    """
    has_margin = False

    for item in doc.items:
        is_margin_item = _is_margin_scheme_item(item)
        item.custom_is_margin_item = 1 if is_margin_item else 0

        if not is_margin_item:
            item.custom_taxable_value = 0
            item.custom_exempted_value = 0
            continue

        has_margin = True
        qty = flt(item.qty)
        rate = flt(item.rate)
        if qty <= 0 or rate <= 0:
            continue

        # Get exempted value per serial — same source as ch_erp15
        # delivery_note.get_exempted_value_from_serial()
        total_exempt = _get_exempted_value(item)
        exempt_per_unit = (total_exempt / qty) if qty else 0

        base_value = rate - exempt_per_unit
        if base_value < 0:
            # P2-20: below-cost sale — selling price under exempted (purchase) value.
            # Margin scheme cannot create a negative base; clamp to zero, but log to
            # audit so finance can review unusual clearance pricing rather than the
            # silent zero-tax behaviour the previous code had.
            try:
                from ch_pos.audit import log_business_event
                log_business_event(
                    event_type="Margin Below Cost",
                    ref_doctype="Sales Invoice",
                    ref_name=getattr(doc, "name", "") or "new",
                    before=f"exempt_per_unit={exempt_per_unit:.2f}",
                    after=f"selling_rate={rate:.2f}",
                    remarks=(
                        f"Item {item.item_code}: selling rate \u20b9{rate:.2f} below "
                        f"purchase exempt value \u20b9{exempt_per_unit:.2f}; margin base "
                        "clamped to zero."
                    ),
                    company=getattr(doc, "company", "") or "",
                )
            except Exception:
                # Audit logger is best-effort; never block billing because of it.
                frappe.log_error(
                    frappe.get_traceback(),
                    f"Below-cost margin audit log failed for {item.item_code}",
                )
            base_value = 0

        # POS-15 fix: Dynamic GST rate lookup instead of hardcoded 1.18
        gst_rate = _get_margin_gst_rate(item.item_code, doc) or 18
        taxable_per_unit = base_value / (1 + gst_rate / 100)

        item.custom_taxable_value = taxable_per_unit * qty
        item.custom_exempted_value = total_exempt  # will be recalculated after tax pass

    if not has_margin:
        doc.custom_is_margin_scheme = 0
        doc.custom_margin_taxable = 0
        doc.custom_margin_gst = 0
        doc.custom_margin_exempted = 0
        return

    doc.custom_is_margin_scheme = 1

    # Sum up margin taxable across all margin items
    total_margin_taxable = sum(
        flt(item.custom_taxable_value) for item in doc.items if item.get("custom_is_margin_item")
    )
    total_non_margin = sum(
        flt(item.amount) for item in doc.items if not item.get("custom_is_margin_item")
    )

    # Only modify taxes if they are still "On Net Total" (first validate pass).
    # After the first pass we switch them to "Actual" so that subsequent calls
    # to calculate_taxes_and_totals (e.g. during submit) respect the pre-set
    # amounts and keep grand_total / paid_amount consistent with margin taxes.
    has_on_net_total = any(t.charge_type == "On Net Total" for t in doc.taxes)
    if not has_on_net_total:
        # Taxes are already "Actual" from a previous validate pass.
        # Summary fields (custom_margin_*) were already set; nothing to do.
        return

    # Recalculate tax rows — same pattern as ch_erp15
    total_gst = 0
    for tax in doc.taxes:
        if tax.charge_type != "On Net Total":
            continue

        # Tax on margin items (reduced taxable base)
        margin_tax = (total_margin_taxable * flt(tax.rate)) / 100
        # Tax on non-margin items (normal full-amount base)
        non_margin_tax = (total_non_margin * flt(tax.rate)) / 100

        combined = margin_tax + non_margin_tax

        # Switch to "Actual" so ERPNext's calculate_taxes_and_totals won't
        # recompute these amounts from the rate on subsequent validate calls.
        tax.charge_type = "Actual"
        tax.tax_amount = combined
        tax.tax_amount_after_discount_amount = combined
        tax.base_tax_amount = combined
        tax.base_tax_amount_after_discount_amount = combined

        total_gst += margin_tax

    # Recompute totals now that taxes are "Actual" with margin-only amounts.
    doc.run_method("calculate_taxes_and_totals")

    # Calculate exempted value per margin item — same as ch_erp15
    total_exempted = 0
    for item in doc.items:
        if not item.get("custom_is_margin_item"):
            continue

        qty = flt(item.qty)
        rate = flt(item.rate)
        item_amount = rate * qty
        margin_taxable = flt(item.custom_taxable_value)

        # Proportional GST for this item
        item_gst = 0
        if total_margin_taxable > 0:
            item_gst = (margin_taxable / total_margin_taxable) * total_gst

        exempted = item_amount - margin_taxable - item_gst
        item.custom_exempted_value = max(0.0, exempted)
        total_exempted += item.custom_exempted_value

    # Populate header-level margin summary fields
    doc.custom_margin_taxable = total_margin_taxable
    doc.custom_margin_gst = total_gst
    doc.custom_margin_exempted = total_exempted


def validate_eod_lock(doc, method=None):
    """
    Hook: validate — allow billing only when an active POS session exists.

    - If an active CH POS Session exists → allow billing
    - If no active session → block billing
    - Closed sessions are ignored if an active session exists
    """

    # Skip for testing / overrides
    if frappe.flags.get("ignore_eod_lock"):
        return

    if not doc.pos_profile:
        return

    from ch_pos.pos_core.doctype.ch_pos_session.ch_pos_session import get_active_session

    # Get active session
    active = get_active_session(doc.pos_profile)

    if not active:
        frappe.throw(
            frappe._(
                "No active POS Session for {0}. Open a session before billing."
            ).format(doc.pos_profile)
        )

    if active.get("status") != "Open":
        frappe.throw(
            frappe._(
                "POS Session {0} is not open. Current status: {1}"
            ).format(active.get("name"), active.get("status"))
        )

    if doc.posting_date and active.get("business_date"):
        if str(active.get("business_date")) != str(doc.posting_date):
            frappe.throw(
                frappe._(
                    "Active session {0} is for {1}, but invoice date is {2}."
                ).format(
                    active.get("name"),
                    active.get("business_date"),
                    doc.posting_date,
                )
            )

def _get_margin_gst_rate(item_code, doc):
    """POS-15 fix: Get GST rate for margin scheme calculation.
    Uses same logic as ch_erp15 _get_item_gst_rate — item tax template first,
    then document tax rows, then defaults to 18%.
    """
    from frappe.utils import flt as _flt
    # Try item-level tax template first (via Item Tax child table)
    if item_code:
        item_tax_template = frappe.db.get_value("Item Tax", {"parent": item_code}, "item_tax_template")
        if item_tax_template:
            rates = frappe.get_all(
                "Item Tax Template Detail",
                filters={"parent": item_tax_template},
                fields=["tax_rate"],
            )
            if rates:
                return sum(_flt(r.tax_rate) for r in rates)
    # Fall back to document tax rows — sum all "On Net Total" rates
    # (CGST 9% + SGST 9% = 18%, not just the first 9%)
    total_rate = sum(
        _flt(tax.get("rate"))
        for tax in (doc.get("taxes") or [])
        if _flt(tax.get("rate")) > 0
    )
    return total_rate if total_rate > 0 else 18


def _get_exempted_value(item):
    """Get total exempted value for a Sales Invoice item's serials.

    Uses the same lookup as ch_erp15 delivery_note.get_exempted_value_from_serial:
    Purchase Receipt Item → custom_exempted_value (weighted by qty via serial).

    Falls back to computing exempted from CH Serial Lifecycle purchase_rate
    when no Purchase Receipt data exists (e.g. items entered without PRs).
    Fallback formula mirrors ch_erp15 Purchase Receipt validate():
        exempted = purchase_amount - 0 - 0 = purchase_amount
    (i.e. when custom_unit_taxable_value is not set, the full purchase
    cost is treated as exempted).
    """
    serials = []
    serial_str = (item.serial_no or "").strip()
    if serial_str:
        serials = [s.strip() for s in serial_str.split("\n") if s.strip()]

    if not serials:
        return 0.0

    total_exempt = 0.0
    for serial in serials:
        # Primary: same SQL as ch_erp15 get_exempted_value_from_serial
        result = frappe.db.sql("""
            SELECT
                SUM(pri.custom_exempted_value) / NULLIF(SUM(pri.qty), 0)
            FROM `tabSerial No` sn
            JOIN `tabPurchase Receipt Item` pri
                ON sn.reference_name = pri.parent
                AND sn.item_code = pri.item_code
            WHERE sn.name = %s
              AND sn.reference_doctype = 'Purchase Receipt'
        """, (serial,), as_list=True)

        exempt = flt(result[0][0]) if result and result[0] and result[0][0] else None

        if exempt is not None:
            total_exempt += exempt
        else:
            # Fallback: use purchase_rate from CH Serial Lifecycle as exempted
            rate = frappe.db.get_value(
                "CH Serial Lifecycle", {"serial_no": serial}, "purchase_rate"
            )
            if rate is not None:
                total_exempt += flt(rate)

    return total_exempt
