import frappe
from frappe.utils import flt, cint
from erpnext.accounts.doctype.sales_invoice.sales_invoice import SalesInvoice


def _get_serial_nos_from_item(item, parent_doc=None):
    """Extract serial numbers from item row, checking serial_no field first,
    then falling back to Serial and Batch Bundle (ERPNext v15 clears serial_no
    on returns after bundle creation).

    If parent_doc has _cached_serial_nos (set during on_cancel before delink),
    use that instead — the bundle reference may already be cleared.
    """
    if parent_doc and hasattr(parent_doc, "_cached_serial_nos"):
        cached = parent_doc._cached_serial_nos.get(item.name, [])
        if cached:
            return cached

    serial_nos = (item.serial_no or "").strip()
    if serial_nos:
        return [s.strip() for s in serial_nos.split("\n") if s.strip()]

    if item.serial_and_batch_bundle:
        from erpnext.stock.serial_batch_bundle import get_serial_nos
        return get_serial_nos(item.serial_and_batch_bundle) or []

    return []


def _ensure_lifecycle_exists(serial_no, item_code=None, company=None, warehouse=None):
    """Auto-create a CH Serial Lifecycle row if one does not exist.

    This closes the gap where serials entered the system outside the
    Purchase Receipt IMEI-tracking path and therefore never got a
    lifecycle document.  The row is created with status 'In Stock'
    so that the subsequent sale/return transition is valid.
    """
    if frappe.db.exists("CH Serial Lifecycle", serial_no):
        return

    if not item_code:
        item_code = frappe.db.get_value("Serial No", serial_no, "item_code")
    if not item_code:
        return  # cannot create without item_code

    from frappe.utils import now_datetime
    lc = frappe.new_doc("CH Serial Lifecycle")
    lc.serial_no = serial_no
    lc.item_code = item_code
    lc.lifecycle_status = "In Stock"
    lc.current_company = company
    lc.current_warehouse = warehouse
    lc.append("lifecycle_log", {
        "log_timestamp": now_datetime(),
        "from_status": "",
        "to_status": "In Stock",
        "changed_by": frappe.session.user,
        "company": company,
        "warehouse": warehouse,
        "remarks": f"Auto-created on sale — Serial No existed without lifecycle record",
    })
    lc.flags.ignore_permissions = True
    lc.flags.ignore_validate = True
    lc.insert()


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

    def on_cancel(self):
        # Reimplements Sales Invoice on_cancel with SLE/GL reversal inserted
        # before serial bundle delinking to preserve bundle references.
        from erpnext.accounts.doctype.sales_invoice.sales_invoice import SalesInvoice

        self.ignore_linked_doctypes = (
            "GL Entry",
            "Stock Ledger Entry",
            "Repost Item Valuation",
            "Repost Payment Ledger",
            "Repost Payment Ledger Items",
            "Repost Accounting Ledger",
            "Repost Accounting Ledger Items",
            "Payment Ledger Entry",
            "Serial and Batch Bundle",
        )

        # SellingController chain (same pattern as POSInvoice.on_cancel)
        super(SalesInvoice, self).on_cancel()

        # Loyalty points cleanup (from Sales Invoice.on_cancel)
        # Policy: returns never created any LPE on submit, so only non-returns
        # need their LPE deleted on cancel.
        if not self.is_return and self.loyalty_program:
            self.delete_loyalty_point_entry()

        # Reverse SLE/GL entries BEFORE delinking serial bundles
        if self.update_stock == 1:
            self.update_stock_ledger()

        self.make_gl_entries_on_cancel()

        if self.update_stock == 1:
            self.repost_future_sle_and_gle()

        self.db_set("status", "Cancelled")

        coupon = getattr(self, "coupon_code", None) or getattr(self, "custom_coupon_code", None)
        if coupon:
            from erpnext.accounts.doctype.pricing_rule.utils import update_coupon_code_count

            update_coupon_code_count(coupon, "cancelled")

        # Cache serial numbers per item BEFORE delink clears the references.
        # Doc_events (reverse_serial_lifecycle) fire after on_cancel returns,
        # by which time serial_and_batch_bundle is already cleared.
        self._cached_serial_nos = {}
        for item in self.items:
            self._cached_serial_nos[item.name] = _get_serial_nos_from_item(item)

        # Delink serial bundles AFTER reversing stock/GL
        self.delink_serial_and_batch_bundle()

    def delink_serial_and_batch_bundle(self):
        """Override: skip cancel if the bundle was already cancelled by SLE reversal."""
        for row in self.items:
            if row.serial_and_batch_bundle:
                bundle_docstatus = frappe.db.get_value(
                    "Serial and Batch Bundle", row.serial_and_batch_bundle, "docstatus"
                )
                if not getattr(self, "is_consolidated", None):
                    frappe.db.set_value(
                        "Serial and Batch Bundle",
                        row.serial_and_batch_bundle,
                        {"is_cancelled": 1, "voucher_no": ""},
                    )
                if bundle_docstatus == 1:
                    frappe.get_doc("Serial and Batch Bundle", row.serial_and_batch_bundle).cancel()
                row.db_set("serial_and_batch_bundle", None)

    def make_discount_gl_entries(self, gl_entries):
        """Override: accounts_controller only handles Sales/Purchase Invoice.

        Sales Invoice is a selling document, so we read Selling Settings and
        then delegate to the parent implementation with doctype temporarily
        set so the if/elif branches match.
        """
        from erpnext.accounts.doctype.account.account import get_account_currency

        enable_discount_accounting = cint(
            frappe.db.get_single_value("Selling Settings", "enable_discount_accounting")
        )

        dr_or_cr = "debit"
        rev_dr_cr = "credit"
        supplier_or_customer = self.customer

        if enable_discount_accounting:
            for item in self.get("items"):
                if item.get("discount_amount") and item.get("discount_account"):
                    discount_amount = item.discount_amount * item.qty
                    income_or_expense_account = (
                        item.income_account
                        if (not item.get("enable_deferred_revenue") or self.is_return)
                        else item.get("deferred_revenue_account")
                    )

                    account_currency = get_account_currency(item.discount_account)
                    gl_entries.append(
                        self.get_gl_dict(
                            {
                                "account": item.discount_account,
                                "against": supplier_or_customer,
                                dr_or_cr: flt(
                                    discount_amount * self.get("conversion_rate"),
                                    item.precision("discount_amount"),
                                ),
                                dr_or_cr + "_in_transaction_currency": flt(
                                    discount_amount, item.precision("discount_amount")
                                ),
                                "cost_center": item.cost_center,
                                "project": item.project,
                            },
                            account_currency,
                            item=item,
                        )
                    )

                    account_currency = get_account_currency(income_or_expense_account)
                    gl_entries.append(
                        self.get_gl_dict(
                            {
                                "account": income_or_expense_account,
                                "against": supplier_or_customer,
                                rev_dr_cr: flt(
                                    discount_amount * self.get("conversion_rate"),
                                    item.precision("discount_amount"),
                                ),
                                rev_dr_cr + "_in_transaction_currency": flt(
                                    discount_amount, item.precision("discount_amount")
                                ),
                                "cost_center": item.cost_center,
                                "project": item.project or self.project,
                            },
                            account_currency,
                            item=item,
                        )
                    )

        if (
            (enable_discount_accounting or self.get("is_cash_or_non_trade_discount"))
            and self.get("additional_discount_account")
            and self.get("discount_amount")
        ):
            import erpnext

            gl_entries.append(
                self.get_gl_dict(
                    {
                        "account": self.additional_discount_account,
                        "against": supplier_or_customer,
                        dr_or_cr: self.base_discount_amount,
                        "cost_center": self.cost_center or erpnext.get_default_cost_center(self.company),
                    },
                    item=self,
                )
            )

    def get_gl_entries(self, warehouse_account=None):
        # accounts_controller.make_discount_gl_entries only handles
        # "Sales Invoice"/"Purchase Invoice" doctypes.  Override below
        # ensures Sales Invoice is handled like Sales Invoice for discounts.
        gl_entries = super().get_gl_entries(warehouse_account)
        if not cint(self.get("custom_is_margin_scheme")):
            return gl_entries

        # For mixed carts: reduce GST GL amounts by the margin-item GST saving
        margin_gst = flt(self.get("custom_margin_gst"))
        if margin_gst <= 0:
            return gl_entries

        # Calculate what GST SHOULD have been on margin items at full value
        total_margin_amount = sum(
            flt(item.amount) for item in self.items if cint(item.get("custom_is_margin_item"))
        )
        total_margin_taxable = sum(
            flt(item.get("custom_taxable_value")) for item in self.items
            if cint(item.get("custom_is_margin_item"))
        )
        # GST saving = GST that would have been charged on full amount minus actual margin GST
        gst_saving = 0
        for tax in self.taxes:
            if tax.charge_type != "On Net Total":
                continue
            full_margin_gst = (total_margin_amount * flt(tax.rate)) / 100
            actual_margin_gst = (total_margin_taxable * flt(tax.rate)) / 100
            gst_saving += full_margin_gst - actual_margin_gst

        if gst_saving <= 0:
            return gl_entries

        # Adjust GST GL entries downward by the saving amount
        adjusted = []
        for gl in gl_entries:
            if "gst" in (gl.account or "").lower():
                if flt(gl.debit) > 0:
                    gl.debit = max(0, flt(gl.debit) - gst_saving / 2)
                    gl.debit_in_account_currency = gl.debit
                if flt(gl.credit) > 0:
                    gl.credit = max(0, flt(gl.credit) - gst_saving / 2)
                    gl.credit_in_account_currency = gl.credit
                if flt(gl.debit) == 0 and flt(gl.credit) == 0:
                    continue
            adjusted.append(gl)
        return adjusted


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
            # that's a Link to CH Sold Plan which is created separately)
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
                    # Find rate from original invoice item
                    orig_items = frappe.db.get_all("Sales Invoice Item",
                        filters={"parent": orig_inv, "serial_no": ["like", f"%{sn}%"]},
                        fields=["rate"], limit=1)
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
    """Hook: on_submit — mark kiosk token as Converted if linked."""
    token = doc.get("custom_kiosk_token")
    if token:
        frappe.db.set_value("POS Kiosk Token", token, {
            "status": "Converted",
            "converted_invoice": doc.name,
        })


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


# ── Margin Scheme GST (selling side) ────────────────────────────


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

    Mirrors ch_erp15 purchase-side logic:
    - Taxable margin = selling price - incoming rate (purchase cost)
    - GST applies only on the margin, not the full selling price
    - Exempted value = full amount - margin - GST on margin
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

        # Incoming rate (purchase cost) from serial no or valuation
        incoming = _get_incoming_rate(item)
        margin_per_unit = max(0, rate - incoming)

        item.custom_taxable_value = margin_per_unit * qty
        item.custom_exempted_value = 0  # calculated after tax pass

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

    # Recalculate tax rows for margin items
    total_gst = 0
    for tax in doc.taxes:
        if tax.charge_type != "On Net Total":
            continue

        # Tax on margin items only
        margin_tax = (total_margin_taxable * flt(tax.rate)) / 100
        # Tax on non-margin items (normal)
        non_margin_tax = (total_non_margin * flt(tax.rate)) / 100

        combined = margin_tax + non_margin_tax
        tax.tax_amount = combined
        tax.tax_amount_after_discount_amount = combined
        tax.base_tax_amount = combined
        tax.base_tax_amount_after_discount_amount = combined

        total_gst += margin_tax

    # Calculate exempted value per margin item
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
        # Can only be negative when purchase_rate=0 (test data); cap at 0
        item.custom_exempted_value = max(0.0, exempted)
        total_exempted += item.custom_exempted_value

    # Populate header-level margin summary fields
    doc.custom_margin_taxable = total_margin_taxable
    doc.custom_margin_gst = total_gst
    doc.custom_margin_exempted = total_exempted


def validate_eod_lock(doc, method=None):
    """Hook: validate — block Sales Invoice creation/amendment after session close.

    Once a CH POS Session for this pos_profile + business_date is Closed,
    no new invoices (or amendments) are allowed for that profile + date.

    Set frappe.flags.ignore_eod_lock = True before insert to bypass (tests only).
    """
    if frappe.flags.get("ignore_eod_lock"):
        return
    if not doc.pos_profile or not doc.posting_date:
        return

    closed = frappe.db.exists(
        "CH POS Session",
        {
            "pos_profile": doc.pos_profile,
            "business_date": doc.posting_date,
            "status": "Closed",
            "docstatus": 1,
        },
    )
    if closed:
        frappe.throw(
            frappe._(
                "POS Session for {0} on {1} is already closed. "
                "No new invoices can be created. Contact your manager to reopen."
            ).format(doc.pos_profile, doc.posting_date)
        )


def _get_incoming_rate(item):
    """Get purchase cost (incoming rate) for a Sales Invoice item.

    Returns the purchase_rate from CH Serial Lifecycle if the item has a serial
    number — treating 0 as a valid cost (e.g. refurb sourced for free).
    Falls back to 0 so the full selling price becomes the taxable margin.
    item.incoming_rate is intentionally ignored: ERPNext populates it from the
    current valuation rate which can equal the selling price and is unreliable.
    """
    serial = (item.serial_no or "").strip().split("\n")[0].strip()
    if serial:
        rate = frappe.db.get_value("CH Serial Lifecycle", {"serial_no": serial}, "purchase_rate")
        # Explicitly check for None — 0 is a valid purchase cost (free source)
        if rate is not None:
            return flt(rate)

    # No serial / no lifecycle record → treat cost as 0 (full margin)
    return 0.0
