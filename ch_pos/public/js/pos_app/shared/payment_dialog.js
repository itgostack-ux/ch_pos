/**
 * CH POS — Payment Screen v3
 *
 * Comprehensive payment overlay supporting 10 payment types:
 *  1. Full (Cash/UPI/Card)  2. Split  3. EMI/Finance
 *  4. Credit Sales  5. Loyalty  6. Voucher  7. Exchange
 *  8. Free Sales  9. Advance Adjustment  10. Refunds
 *
 *  Left  : Itemized bill, serial numbers, all discounts, tax note
 *  Right : Split payment rows with type-specific fields,
 *          sale mode toggles (Credit/Free), advance adjustment,
 *          bank offers, loyalty, quick-cash, success screen
 */
import { PosState, EventBus } from "../state.js";
import { format_number } from "./helpers.js";
import { pos_warning, pos_info } from "./toast.js";

// ── MOP icon lookup ────────────────────────────────────────────────────────
function _mop_icon(mop) {
	const lc = (mop || "").toLowerCase();
	if (lc.includes("upi") || lc.includes("gpay") || lc.includes("phonepe") || lc.includes("paytm"))
		return `<i class="fa fa-mobile" style="color:#4f46e5"></i>`;
	if (lc.includes("card") || lc.includes("credit") || lc.includes("debit") || lc.includes("edc"))
		return `<i class="fa fa-credit-card" style="color:#0ea5e9"></i>`;
	if (lc.includes("cash"))
		return `<i class="fa fa-money" style="color:#16a34a"></i>`;
	if (lc.includes("bank") || lc.includes("neft") || lc.includes("rtgs") || lc.includes("transfer"))
		return `<i class="fa fa-university" style="color:#6366f1"></i>`;
	if (lc.includes("finance") || lc.includes("emi") || lc.includes("bajaj") || lc.includes("hdfc") || lc.includes("tvs"))
		return `<i class="fa fa-calculator" style="color:#7c3aed"></i>`;
	if (lc.includes("voucher") || lc.includes("gift"))
		return `<i class="fa fa-gift" style="color:#f59e0b"></i>`;
	return `<i class="fa fa-exchange" style="color:#64748b"></i>`;
}

export class PaymentDialog {
	constructor() {
		this._overlay = null;
		this._payments = [];    // [{mode, amount, upi_transaction_id, card_reference, card_last_four, finance_provider, finance_tenure, finance_approval_id, finance_down_payment}]
		this._loyalty_amount = 0;
		this._redeem_loyalty = false;
		this._submitting = false;

		// Credit Sale state
		this._is_credit_sale = false;
		this._credit_days = 30;
		this._credit_terms = "Net 30";
		this._credit_reference = "";
		this._credit_notes = "";
		this._credit_interest_rate = 0;
		this._credit_grace_period = 0;
		this._credit_partial_payment = 0;
		this._credit_approved_by = "";
		this._credit_approved = false;  // true when credit limit OK or manager approved

		// Free Sale state
		this._is_free_sale = false;
		this._free_sale_reason = "";
		this._free_sale_approved_by = "";
		this._free_sale_approved_at = null;

		// Advance Adjustment
		this._advance_amount = 0;
		this._customer_advances = [];  // [{name, amount, balance}]

		// ── Discount & Coupon (moved from cart panel) ────────────────────────
		this._disc_pct     = 0;
		this._disc_amt     = 0;
		this._disc_reason  = "";
		this._disc_reasons = [];
		this._dlg_coupon_code      = "";
		this._dlg_coupon_discount  = 0;
		this._bank_offer           = null;  // { name, offer_name, value_type, value, discount }
		this._dlg_voucher_code     = "";
		this._dlg_voucher_amount   = 0;
		this._dlg_voucher_name     = "";
		this._dlg_voucher_balance  = 0;

		// Sale types, finance partners, and payment machines (loaded async on overlay mount)
		this._sale_types = [];
		this._finance_partners = [];
		this._payment_machine_data = { providers: [], machines: [] };
		this._last_grand_total = null;

		this._bind_events();
	}

	_bind_events() {
		EventBus.on("payment:open", () => this.show());
	}

	show() {
		if (!PosState.cart.length) {
			frappe.show_alert({ message: __("Cart is empty"), indicator: "orange" });
			return;
		}
		if (!PosState.customer) {
			frappe.show_alert({ message: __("Please select a customer"), indicator: "orange" });
			return;
		}

		// Restore or reset per-transaction payment state
		const saved = PosState._payment_state;
		this._submitting = false;
		if (saved) {
			this._payments = saved.payments || [];
			this._loyalty_amount = saved.loyalty_amount || 0;
			this._redeem_loyalty = saved.redeem_loyalty || false;
			this._is_credit_sale = saved.is_credit_sale || false;
			this._credit_days = saved.credit_days || 30;
			this._credit_terms = saved.credit_terms || "Net 30";
			this._credit_interest_rate = saved.credit_interest_rate || 0;
			this._credit_grace_period = saved.credit_grace_period || 0;
			this._credit_partial_payment = saved.credit_partial_payment || 0;
			this._credit_approved_by = saved.credit_approved_by || "";
			this._is_free_sale = saved.is_free_sale || false;
			this._free_sale_reason = saved.free_sale_reason || "";
			this._free_sale_approved_by = saved.free_sale_approved_by || "";
			this._free_sale_approved_at = saved.free_sale_approved_at || null;
			this._free_sale_approval_name = saved.free_sale_approval_name || null;
			this._required_managers = saved.required_managers || [];
			this._advance_amount = saved.advance_amount || 0;
			this._customer_advances = saved.customer_advances || [];
			this._disc_pct    = saved.disc_pct    || 0;
			this._disc_amt    = saved.disc_amt    || 0;
			this._disc_reason = saved.disc_reason || "";
			this._dlg_coupon_code     = saved.dlg_coupon_code     || "";
			this._dlg_coupon_discount = saved.dlg_coupon_discount || 0;
			this._dlg_voucher_code    = saved.dlg_voucher_code    || "";
			this._dlg_voucher_amount  = saved.dlg_voucher_amount  || 0;
			this._dlg_voucher_name    = saved.dlg_voucher_name    || "";
			this._dlg_voucher_balance = saved.dlg_voucher_balance || 0;
		} else {
			this._payments = [];
			this._loyalty_amount = 0;
			this._redeem_loyalty = false;
			this._is_credit_sale = false;
			this._credit_days = 30;
			this._credit_terms = "Net 30";
			this._credit_interest_rate = 0;
			this._credit_grace_period = 0;
			this._credit_partial_payment = 0;
			this._credit_approved_by = "";
			this._is_free_sale = false;
			this._free_sale_reason = "";
			this._free_sale_approved_by = "";
			this._free_sale_approved_at = null;
			this._free_sale_approval_name = null;
			this._required_managers = [];
			this._advance_amount = 0;
			this._customer_advances = [];
			this._disc_pct    = 0;
			this._disc_amt    = 0;
			this._disc_reason = "";
			this._dlg_coupon_code     = "";
			this._dlg_coupon_discount = 0;
			this._bank_offer          = null;
			PosState.bank_offer_discount = 0;
			this._dlg_voucher_code    = "";
			this._dlg_voucher_amount  = 0;
			this._dlg_voucher_name    = "";
			this._dlg_voucher_balance = 0;
			// Sync any stale PosState discount to zero for fresh transaction
			PosState.additional_discount_pct = 0;
			PosState.additional_discount_amt = 0;
			PosState.discount_reason = "";
			PosState.coupon_code = null;
			PosState.coupon_discount = 0;
			PosState.voucher_code = null;
			PosState.voucher_amount = 0;

			// Pre-seed with default MOP covering full amount
			const total = this._calc_grand_total();
			const def_mop = PosState.payment_modes.find(p => p.default) || PosState.payment_modes[0];
			if (def_mop) {
				this._payments = [{ mode: def_mop.mode_of_payment, amount: total, upi_transaction_id: "", card_reference: "", card_last_four: "", finance_provider: "", finance_tenure: "", finance_approval_id: "", finance_down_payment: 0 }];
			}
		}

		// Load customer advances if not walk-in
		if (PosState.customer && PosState.customer !== "Walk-in Customer") {
			this._load_customer_advances();
		}

		this._mount_overlay();
	}

	// ─────────────────────────────────────────── Overlay shell ──

	_mount_overlay() {
		$("#ch-pos-payment-overlay").remove();
		$("body").append(this._build_overlay_html());
		this._overlay = $("#ch-pos-payment-overlay");
		requestAnimationFrame(() => this._overlay.addClass("ch-pay-visible"));
		this._bind_overlay();
		this._render_payments();
		this._render_mop_buttons();   // ensure MOP buttons are populated even if build-time modes were empty
		this._update_totals();
		if (this._payments[0]) this._load_bank_offers(this._payments[0].mode);
		this._load_sale_types();
		this._restore_payment_ui();
		this._load_disc_reasons();
		this._load_finance_partners();
		this._load_payment_machines();
	}

	/** Re-render the Add Payment buttons from PosState.payment_modes.
	 *  Called after mount so the live (already-loaded) modes are always reflected. */
	_render_mop_buttons() {
		if (!this._overlay) return;
		const container = this._overlay.find(".ch-pay-mop-btns");
		if (!container.length) return;
		const modes = PosState.payment_modes || [];
		if (!modes.length) return;   // no change — template value stays
		container.html(modes.map(p => `
			<button class="ch-pay-mop-btn" data-mop="${frappe.utils.escape_html(p.mode_of_payment)}">
				${_mop_icon(p.mode_of_payment)}
				<span>${frappe.utils.escape_html(p.mode_of_payment)}</span>
			</button>`).join(""));
	}

	/** Restore saved payment UI state (checkboxes, sections) after overlay mounts */
	_restore_payment_ui() {
		if (!this._overlay) return;
		const ov = this._overlay;

		if (this._is_credit_sale) {
			ov.find("#ch-pay-credit-chk").prop("checked", true);
			ov.find("#ch-pay-credit-section").show();
			ov.find("#ch-pay-credit-days").val(this._credit_days);
			ov.find("#ch-pay-credit-interest").val(this._credit_interest_rate || 0);
			ov.find("#ch-pay-credit-grace").val(this._credit_grace_period || 0);
			ov.find("#ch-pay-credit-partial").val(this._credit_partial_payment || 0);
			ov.find("#ch-pay-credit-approved-by").val(this._credit_approved_by || "");
			// Highlight active term pill
			if (this._credit_terms) {
				ov.find(".ch-pay-credit-term-btn")
					.filter((_, el) => $(el).data("term") === this._credit_terms)
					.addClass("active");
			}
		}
		if (this._is_free_sale) {
			ov.find("#ch-pay-free-chk").prop("checked", true);
			ov.find("#ch-pay-free-section").show();
			ov.find("#ch-pay-mop-section").hide();
			ov.find("#ch-pay-rows").hide();
			ov.find("#ch-pay-quick-cash").hide();
			ov.find("#ch-pay-free-reason").val(this._free_sale_reason);
			// Restore approval status
			if (this._free_sale_approval_name && this._required_managers.length) {
				ov.find("#ch-pay-free-request-btn").hide();
				ov.find("#ch-pay-free-reason").prop("readonly", true);
				this._render_approval_status(this._required_managers);
				// Resume polling if not yet approved
				if (!this._free_sale_approved_by) {
					this._start_approval_polling();
				}
			} else {
				this._load_category_managers();
				ov.find("#ch-pay-free-request-btn").prop("disabled", !this._free_sale_reason);
			}
		}
	}

	_build_overlay_html() {
		const total = this._calc_grand_total();
		const loyalty_pts = flt(PosState.loyalty_points) || 0;
		const has_loyalty = loyalty_pts > 0 && PosState.loyalty_program;
		const max_loyalty = has_loyalty ? flt(loyalty_pts * (PosState.conversion_factor || 0)) : 0;
		const is_walkin = !PosState.customer || PosState.customer === "Walk-in Customer";

		const mop_btns = (PosState.payment_modes || []).map(p => `
			<button class="ch-pay-mop-btn" data-mop="${frappe.utils.escape_html(p.mode_of_payment)}">
				${_mop_icon(p.mode_of_payment)}
				<span>${frappe.utils.escape_html(p.mode_of_payment)}</span>
			</button>`).join("");

		const loyalty_html = has_loyalty ? `
			<div class="ch-pay-loyalty-row">
				<label class="ch-pay-loyalty-toggle">
					<input type="checkbox" id="ch-pay-loyalty-chk">
					<span>${__("Loyalty")} (${format_number(loyalty_pts)} pts ≈ ₹${format_number(max_loyalty)})</span>
				</label>
				<div class="ch-pay-loyalty-input" id="ch-pay-loyalty-input" style="display:none">
					<div class="input-group input-group-sm">
						<span class="input-group-addon">₹</span>
						<input type="number" class="form-control" id="ch-pay-loyalty-amt"
							value="${Math.min(max_loyalty, total).toFixed(2)}" min="0" max="${max_loyalty}" step="0.01">
						<span class="input-group-addon" id="ch-pay-loyalty-max">${__("max")} ₹${format_number(Math.min(max_loyalty, total))}</span>
					</div>
				</div>
			</div>` : "";

		// Sale mode toggles — hidden, driven by sale type pills
		const sale_modes_html = !is_walkin ? `
			<div class="ch-pay-sale-modes" style="display:none">
				<label class="ch-pay-mode-toggle">
					<input type="checkbox" id="ch-pay-credit-chk">
				</label>
				<label class="ch-pay-mode-toggle">
					<input type="checkbox" id="ch-pay-free-chk">
				</label>
			</div>` : "";

		// Credit sale details (hidden by default)
		const credit_html = !is_walkin ? `
			<div id="ch-pay-credit-section" class="ch-pay-credit-section" style="display:none">
				<div class="ch-pay-credit-header">
					<i class="fa fa-handshake-o"></i> ${__("Credit Sale")}
					<span id="ch-pay-credit-limit-badge" class="ch-pay-credit-limit-badge"></span>
				</div>
				<div class="ch-pay-credit-body">
					<!-- Credit limit / outstanding info (loaded async, shown as compact badge row) -->
					<div class="ch-pay-credit-info" id="ch-pay-credit-info"></div>
					<div id="ch-pay-credit-history" class="ch-pay-credit-history" style="display:none"></div>

					<!-- Essential row: Terms + Reference inline -->
					<div class="ch-pay-credit-essentials">
						<div class="ch-pay-credit-terms-pills" id="ch-pay-credit-terms-pills">
							${["Net 15","Net 30","Net 45","Net 60"].map(t =>
								`<button class="ch-pay-credit-term-btn" data-days="${t.split(' ')[1]}" data-term="${t}">${t}</button>`
							).join("")}
							<button class="ch-pay-credit-term-btn" data-days="custom" data-term="Custom">${__("Custom")}</button>
						</div>
						<div class="ch-pay-credit-due-row">
							<span class="ch-pay-credit-due-label">${__("Due")}</span>
							<div id="ch-pay-credit-due-date" class="ch-pay-credit-due-val"></div>
						</div>
					</div>

					<!-- Advanced fields (collapsible) -->
					<details class="ch-pay-credit-advanced" id="ch-pay-credit-advanced">
						<summary>${__("More options")}</summary>
						<div class="ch-pay-credit-fields">
							<div class="ch-pay-credit-grid">
								<div class="ch-pay-credit-field-item">
									<label>${__("Credit Days")}</label>
									<input type="number" class="form-control form-control-sm" id="ch-pay-credit-days"
										value="30" min="1" max="365" step="1">
								</div>
								<div class="ch-pay-credit-field-item">
									<label>${__("Down Payment")}</label>
									<div class="input-group input-group-sm">
										<span class="input-group-addon">₹</span>
										<input type="number" class="form-control" id="ch-pay-credit-partial"
											value="0" min="0" step="1" placeholder="0">
									</div>
								</div>
								<div class="ch-pay-credit-field-item">
									<label>${__("Interest % p.a.")}</label>
									<div class="input-group input-group-sm">
										<input type="number" class="form-control" id="ch-pay-credit-interest"
											value="0" min="0" max="48" step="0.5" placeholder="0">
										<span class="input-group-addon">%</span>
									</div>
								</div>
								<div class="ch-pay-credit-field-item">
									<label>${__("Grace Period (days)")}</label>
									<input type="number" class="form-control form-control-sm" id="ch-pay-credit-grace"
										value="0" min="0" max="30" step="1">
								</div>
								<div class="ch-pay-credit-field-item">
									<label>${__("Ref / PO No")}</label>
									<input type="text" class="form-control form-control-sm" id="ch-pay-credit-ref"
										placeholder="${__("PO-2026-001")}">
								</div>
								<div class="ch-pay-credit-field-item">
									<label>${__("Reminder Date")}</label>
									<div id="ch-pay-credit-reminder" class="form-control form-control-sm ch-pay-credit-readonly"></div>
								</div>
								<div class="ch-pay-credit-field-item ch-pay-credit-field-full">
									<label>${__("Notes")}</label>
									<input type="text" class="form-control form-control-sm" id="ch-pay-credit-notes"
										placeholder="${__("e.g. Payment by NEFT on or before due date")}">
								</div>
								<div class="ch-pay-credit-field-item ch-pay-credit-field-full">
									<label>${__("Approved By")}</label>
									<input type="text" class="form-control form-control-sm ch-pay-credit-readonly"
										id="ch-pay-credit-approved-by" readonly
										placeholder="${__("Auto-filled on manager override")}">
								</div>
							</div>
						</div>
					</details>
					<div id="ch-pay-credit-approval" class="ch-pay-credit-approval" style="display:none"></div>
				</div>
			</div>` : "";

		// Free sale details (hidden by default) — category manager approval
		const free_html = !is_walkin ? `
			<div id="ch-pay-free-section" class="ch-pay-free-section" style="display:none">
				<div class="ch-pay-free-header">
					<i class="fa fa-gift"></i> ${__("FREE SALE — CATEGORY MANAGER APPROVAL REQUIRED")}
				</div>
				<div class="ch-pay-free-body">
					<div class="ch-pay-free-field">
						<label>${__("Reason")} <span class="text-danger">*</span></label>
						<input type="text" class="form-control form-control-sm" id="ch-pay-free-reason"
							placeholder="${__("e.g. Warranty replacement, Display damage compensation")}">
					</div>
					<div id="ch-pay-free-managers" class="ch-pay-free-managers"></div>
					<button class="btn btn-sm btn-primary ch-pay-free-request-btn" id="ch-pay-free-request-btn" style="margin-top:8px" disabled>
						<i class="fa fa-paper-plane"></i> ${__("Request Approval")}
					</button>
					<div id="ch-pay-free-status" class="ch-pay-free-status" style="display:none"></div>
					<!-- Approval timestamp (shown after approval) -->
					<div id="ch-pay-free-approved-at" style="display:none;margin-top:6px;font-size:11px;color:#6b7280">
						<i class="fa fa-clock-o"></i> <span id="ch-pay-free-approved-at-txt"></span>
					</div>
				</div>
			</div>` : "";

		// Advance adjustment section
		const advance_html = !is_walkin ? `
			<div id="ch-pay-advance-section" class="ch-pay-advance-section" style="display:none">
				<div class="ch-pay-advance-header">
					<i class="fa fa-history"></i> ${__("Customer Advances")}
				</div>
				<div id="ch-pay-advance-list" class="ch-pay-advance-list"></div>
			</div>
			<div id="ch-pay-ss-advance-section" class="ch-pay-ss-advance-section" style="display:none">
				<div class="ch-pay-ss-advance-row">
					<label class="ch-pay-ss-advance-label"><i class="fa fa-money"></i> ${__("Advance Paid")}</label>
					<div class="ch-pay-fin-down-inp">
						<span>₹</span>
						<input type="number" id="ch-pay-ss-advance-amt" placeholder="0.00" min="0" step="0.01">
					</div>
				</div>
			</div>` : "";

		// Sale type selector (DS, CS, FS, FREE, SS)
		const sale_type_html = `
			<div class="ch-pay-sale-type-section" id="ch-pay-sale-type-section">
				<div class="ch-pay-sale-type-label">${__("Sale Type")}</div>
				<div class="ch-pay-sale-type-pills" id="ch-pay-sale-type-pills"></div>
				<div class="ch-pay-sale-sub-row" id="ch-pay-sale-sub-row" style="display:none">
					<select class="form-control form-control-sm" id="ch-pay-sale-sub-select"></select>
					<select class="form-control form-control-sm" id="ch-pay-sale-fin-tenure" style="display:none;max-width:140px"></select>
					<input type="text" class="form-control form-control-sm" id="ch-pay-sale-ref-input"
						placeholder="${__("Reference No...")}" style="display:none;max-width:180px">
				</div>
			</div>`;

		// ── Discount / Coupon controls in setup zone ──────────────────────────
		const disc_html = `
<div class="ch-pay-section-block" id="ch-pay-disc-block">
<button class="ch-pay-section-hdr" type="button" data-target="ch-pay-disc-body">
<span><i class="fa fa-percent"></i> ${__("Discount")}</span>
<i class="fa fa-chevron-right ch-pay-toggle-icon"></i>
</button>
<div id="ch-pay-disc-body" class="ch-pay-section-body" style="display:none">
<div class="ch-pay-field-row">
<select class="form-control form-control-sm" id="ch-pay-disc-reason">
<option value="">${__("Reason (required)...")}</option>
</select>
</div>
<div id="ch-pay-disc-manual" style="display:none">
<div class="ch-pay-disc-split">
<div class="input-group input-group-sm">
<input type="number" class="form-control" id="ch-pay-disc-pct"
placeholder="%" min="0" max="100" step="0.5">
<span class="input-group-addon">%</span>
</div>
<span class="ch-pay-disc-or">${__("or")}</span>
<div class="input-group input-group-sm">
<span class="input-group-addon">₹</span>
<input type="number" class="form-control" id="ch-pay-disc-amt"
placeholder="0.00" min="0" step="1">
</div>
</div>
</div>
<div id="ch-pay-disc-info" class="ch-pay-field-hint" style="display:none"></div>
</div>
</div>`;
const coupon_html = `
<div class="ch-pay-section-block" id="ch-pay-coupon-block">
<button class="ch-pay-section-hdr" type="button" data-target="ch-pay-coupon-body">
<span><i class="fa fa-ticket"></i> ${__("Coupon / Voucher")}</span>
<i class="fa fa-chevron-right ch-pay-toggle-icon"></i>
</button>
<div id="ch-pay-coupon-body" class="ch-pay-section-body" style="display:none">
<div class="ch-pay-coupon-row">
<input type="text" class="form-control form-control-sm" id="ch-pay-coupon-code"
placeholder="${__("Enter code...")}">
<button class="btn btn-sm btn-outline-primary" id="ch-pay-coupon-apply">${__("Apply")}</button>
</div>
<div id="ch-pay-coupon-msg" class="ch-pay-field-hint"></div>
</div>
</div>`;
		const commercial_controls_html = `
			<div class="ch-pay-commercials" id="ch-pay-commercials">
				<div class="ch-pay-commercials-head">
					<div class="ch-pay-commercials-title">${__("Commercial Controls")}</div>
					<div class="ch-pay-commercials-copy">${__("Apply pricing decisions before choosing payment instruments")}</div>
				</div>
				<div class="ch-pay-commercials-grid">
					${disc_html}
					${coupon_html}
				</div>
			</div>`;


		return `
		<div id="ch-pos-payment-overlay" class="ch-pay-overlay">
			<div class="ch-pay-screen">
				<!-- ── LEFT: Bill Summary + Commercial Controls ────────────────── -->
				<div class="ch-pay-left">
					<div class="ch-pay-left-header">
						<button class="ch-pay-close" title="${__("Back to cart")}">
							<i class="fa fa-arrow-left"></i> ${__("Back")}
						</button>
						<div class="ch-pay-cust-info">
							<span class="ch-pay-cust-name">
								<i class="fa fa-user-circle"></i>
								${frappe.utils.escape_html(PosState.customer || __("Walk-in"))}
							</span>
							${PosState.sales_executive_name
								? `<span class="ch-pay-exec-tag"><i class="fa fa-id-badge"></i> ${frappe.utils.escape_html(PosState.sales_executive_name)}</span>`
								: ""}
						</div>
					</div>

					<div class="ch-pay-items-scroll">
						${this._build_items_html()}
					</div>

					<div class="ch-pay-totals-block">
						${this._build_totals_html()}
					</div>

					<!-- Loyalty: redeem points above commercial controls -->
					${loyalty_html}

					<!-- Commercial Controls pinned at bottom of left panel -->
					${commercial_controls_html}

				<!-- Grand Total after all commercial deductions -->
				<div class="ch-pay-commercials-grand" id="ch-pay-commercials-grand">
					<span class="ch-pay-commercials-grand-label">${__('Grand Total')}</span>
					<span class="ch-pay-commercials-grand-value" id="ch-pay-commercials-grand-value">₹0</span>
				</div>
				</div><!-- /.ch-pay-left -->

			<!-- ── RIGHT: Payment Panel ─────────────────────────────── -->
				<div class="ch-pay-right">
					<!-- Hero amount -->
					<div class="ch-pay-right-header">
						<span class="ch-pay-right-label">${__("Amount Due")}</span>
						<span class="ch-pay-grand-display" id="ch-pay-amount-due">
							₹${format_number(total)}
						</span>
					</div>

					<!-- Everything scrollable -->
					<div class="ch-pay-right-scroll">

						<!-- Hidden sale mode checkboxes (driven by pills) -->
						${sale_modes_html}

						<!-- ─ Step 1: Sale Type ─ -->
						${sale_type_html}

						<!-- ─ Step 2: Sale-mode specific details ─ -->
						${credit_html}
						${free_html}

						<!-- ─ Step 3: Payment Instruments ─ -->
						<div class="ch-pay-instruments-zone">
							<div class="ch-pay-zone-label">${__("Payment")}</div>

							<!-- MOP quick-add buttons -->
							<div class="ch-pay-mop-section" id="ch-pay-mop-section">
								<div class="ch-pay-mop-btns">${mop_btns}</div>
							</div>

							<!-- Bank / card offers (loaded dynamically) -->
							<div id="ch-pay-bank-offers" class="ch-pay-bank-offers"></div>

							<!-- Payment rows -->
							<div id="ch-pay-rows" class="ch-pay-rows"></div>

							<!-- Quick cash amounts -->
							<div id="ch-pay-quick-cash" class="ch-pay-quick-cash" style="display:none">
								<div class="ch-pay-quick-label">${__("Quick Cash")}</div>
								<div id="ch-pay-quick-btns" class="ch-pay-quick-btns"></div>
							</div>

							<!-- Advance adjustment (walk-in hidden) -->
							${advance_html}
						</div>

					</div><!-- /.ch-pay-right-scroll -->

					<!-- Balance bar — sticky above submit -->
					<div class="ch-pay-balance-bar">
						<div class="ch-pay-bal-row">
							<span>${__("Total Paid")}</span>
							<b id="ch-pay-total-paid">₹0</b>
						</div>
						<div class="ch-pay-bal-row" id="ch-pay-loyalty-bal-row" style="display:none">
							<span>🎁 ${__("Loyalty Applied")}</span>
							<b id="ch-pay-loyalty-applied" style="color:#7c3aed">₹0</b>
						</div>
						<div class="ch-pay-bal-row" id="ch-pay-advance-bal-row" style="display:none">
							<span>${__("Advance Applied")}</span>
							<b id="ch-pay-advance-applied" style="color:#7c3aed">₹0</b>
						</div>
						<div class="ch-pay-bal-row ch-pay-bal-due-row">
							<span id="ch-pay-balance-label">${__("Remaining")}</span>
							<b id="ch-pay-balance-due" class="ch-pay-bal-positive">₹${format_number(total)}</b>
						</div>
						<div class="ch-pay-bal-row ch-pay-change-row" id="ch-pay-change-row" style="display:none">
							<span>${__("Change to Return")}</span>
							<b id="ch-pay-change" class="ch-pay-change-val">₹0</b>
						</div>
					</div>

					<!-- Submit -->
					<button class="btn ch-pay-submit-btn btn-default" id="ch-pay-submit" disabled>
						<i class="fa fa-check-circle"></i>
						<span id="ch-pay-submit-label">${__("Confirm Payment")}</span>
						<span class="ch-pay-kbd-hint">↵ Enter</span>
					</button>
				</div>
			</div>
		</div>`;
	}

	_build_items_html() {
		let html = `<table class="ch-pay-item-table"><thead><tr>
			<th>${__("Item")}</th><th class="text-center">${__("Qty")}</th><th class="text-right">${__("Amt")}</th>
		</tr></thead><tbody>`;
		PosState.cart.forEach(c => {
			const amt = flt(c.qty) * flt(c.rate);
			const cls = c.is_warranty ? " ch-pay-row-warranty" : c.is_vas ? " ch-pay-row-vas" : "";
			const serial_tag = c.serial_no
				? `<div class="ch-pay-serial-tag"><i class="fa fa-barcode"></i> ${frappe.utils.escape_html(c.serial_no)}</div>`
				: "";
			html += `<tr class="ch-pay-item-row${cls}">
				<td>${frappe.utils.escape_html(c.item_name)}${serial_tag}</td>
				<td class="text-center">${c.qty}</td>
				<td class="text-right">₹${format_number(amt)}</td>
			</tr>`;
			if (flt(c.discount_amount) > 0) {
				html += `<tr class="ch-pay-offer-row">
					<td colspan="2"><i class="fa fa-tag"></i> ${frappe.utils.escape_html(c.applied_offer ? (c.applied_offer.offer_name || __("Offer")) : __("Discount"))}</td>
					<td class="text-right text-success">-₹${format_number(flt(c.discount_amount) * flt(c.qty))}</td>
				</tr>`;
			}
		});
		html += "</tbody></table>";
		return html;
	}

	_build_totals_html() {
		let subtotal = 0, disc_total = 0;
		PosState.cart.forEach(c => {
			subtotal += flt(c.qty) * flt(c.rate);
			disc_total += flt(c.discount_amount || 0) * flt(c.qty);
		});
		const net = subtotal - disc_total;
		let add_disc = 0;
		if (PosState.additional_discount_pct) add_disc = net * PosState.additional_discount_pct / 100;
		else if (PosState.additional_discount_amt) add_disc = flt(PosState.additional_discount_amt);
		const coupon   = flt(PosState.coupon_discount);
		const voucher  = flt(PosState.voucher_amount);
		const exchange = flt(PosState.exchange_amount);
		const pe_cr    = flt(PosState.product_exchange_credit);
		const offer_d  = flt(this._bank_offer ? this._bank_offer.discount : 0);
		const grand    = Math.max(0, net - add_disc - coupon - voucher - exchange - pe_cr - offer_d);

		let rows = `<div class="ch-pay-total-row"><span>${__("Subtotal")}</span><span>₹${format_number(subtotal)}</span></div>`;
		if (disc_total > 0)  rows += `<div class="ch-pay-total-row ch-pay-deduct"><span>${__("Item Discounts")}</span><span>-₹${format_number(disc_total)}</span></div>`;
		if (add_disc > 0)    rows += `<div class="ch-pay-total-row ch-pay-deduct"><span>${PosState.additional_discount_pct ? PosState.additional_discount_pct + "% " : ""}${__("Additional Discount")}</span><span>-₹${format_number(add_disc)}</span></div>`;
		if (coupon > 0)      rows += `<div class="ch-pay-total-row ch-pay-deduct"><span>🏷️ ${__("Coupon")} (${frappe.utils.escape_html(PosState.coupon_code || "")})</span><span>-₹${format_number(coupon)}</span></div>`;
		if (voucher > 0)     rows += `<div class="ch-pay-total-row ch-pay-deduct"><span>🎟️ ${__("Voucher")}</span><span>-₹${format_number(voucher)}</span></div>`;
		if (exchange > 0)    rows += `<div class="ch-pay-total-row ch-pay-deduct" style="color:var(--pos-success,#16a34a)"><span><i class="fa fa-exchange"></i> ${__("Exchange Credit")}</span><span>-₹${format_number(exchange)}</span></div>`;
		if (pe_cr > 0)       rows += `<div class="ch-pay-total-row ch-pay-deduct" style="color:var(--pos-success,#16a34a)"><span><i class="fa fa-retweet"></i> ${__("Swap Credit")}</span><span>-₹${format_number(pe_cr)}</span></div>`;
		if (offer_d > 0)     rows += `<div class="ch-pay-total-row ch-pay-deduct"><span>🏦 ${__("Bank Offer")}</span><span>-₹${format_number(offer_d)}</span></div>`;
		// Loyalty redemption shown as a deduction (market standard: negative line in red)
		const loyalty_redeemed = this._redeem_loyalty ? Math.min(this._loyalty_amount || 0, grand) : 0;
		if (loyalty_redeemed > 0) {
			rows += `<div class="ch-pay-total-row ch-pay-deduct ch-pay-loyalty-deduct"><span>🎁 ${__("Loyalty Redeemed")}</span><span>-₹${format_number(loyalty_redeemed)}</span></div>`;
		}
		rows += `<div class="ch-pay-tax-note"><i class="fa fa-info-circle"></i> ${__("GST auto-applied per POS Profile")}</div>`;
		// Update the Grand Total bar below commercial controls too
		const net_after_loyalty = Math.max(0, grand - loyalty_redeemed);
		this._overlay?.find("#ch-pay-commercials-grand-value").text(`₹${format_number(net_after_loyalty)}`);
		return rows;
	}

	// ───────────────────────────────────────── Bind overlay ──

	_bind_overlay() {
		const ov = this._overlay;

		ov.on("click", ".ch-pay-close", () => this._close());

		// MOP button → switch (single-mode) or add/top-up (split)
		ov.on("click", ".ch-pay-mop-btn", e => {
			const mop = $(e.currentTarget).data("mop");
			const is_finance_mode = this._is_finance_sale_type(PosState.sale_type);

			if (is_finance_mode) {
				// In Finance mode: MOP buttons add/top-up DOWN PAYMENT rows (never touch Finance row)
				const due = this._calc_balance_due();
				const idx = this._payments.findIndex(p => p.mode === mop && this._mop_type(p.mode) !== "finance");
				if (idx >= 0) {
					// Already have this MOP as down payment — top-up with remaining due
					this._payments[idx].amount = flt(this._payments[idx].amount) + Math.max(0, due);
					this._payments[idx].is_down_payment = true;
				} else {
					// Add new down payment row before the Finance row
					const finance_idx = this._payments.findIndex(p => this._mop_type(p.mode) === "finance");
					const new_row = { mode: mop, amount: Math.max(0, due), upi_transaction_id: "", card_reference: "", card_last_four: "", finance_provider: "", finance_tenure: "", finance_approval_id: "", finance_down_payment: 0, is_down_payment: true };
					if (finance_idx >= 0) {
						this._payments.splice(finance_idx, 0, new_row);
					} else {
						this._payments.push(new_row);
					}
				}
				this._sync_finance_payments();
				this._render_payments();
				this._update_totals();
				this._load_bank_offers(mop);
				return;
			}

			const due = this._calc_balance_due();
			const idx = this._payments.findIndex(p => p.mode === mop);
			if (idx >= 0) {
				// Already present — top-up with remaining balance
				this._payments[idx].amount = flt(this._payments[idx].amount) + Math.max(0, due);
			} else if (this._payments.length === 1 && due === 0) {
				// Single row covering full amount, not manually split yet — switch mode entirely
				const total = this._payments[0].amount;
				this._payments = [{ mode: mop, amount: total, upi_transaction_id: "", card_reference: "", card_last_four: "", finance_provider: "", finance_tenure: "", finance_approval_id: "", finance_down_payment: 0 }];
			} else {
				// Partial/split — add new row for remaining due
				this._payments.push({ mode: mop, amount: Math.max(0, due), upi_transaction_id: "", card_reference: "", card_last_four: "", finance_provider: "", finance_tenure: "", finance_approval_id: "", finance_down_payment: 0 });
			}
			this._render_payments();
			this._update_totals();
			this._load_bank_offers(mop);
		});

		// Remove a row (Finance row cannot be removed)
		ov.on("click", ".ch-pay-row-remove", e => {
			const idx = parseInt($(e.currentTarget).data("idx"));
			if (this._mop_type(this._payments[idx]?.mode) === "finance") return;
			this._payments.splice(idx, 1);
			if (this._is_finance_sale_type(PosState.sale_type)) {
				this._sync_finance_payments();
			}
			this._render_payments();
			this._update_totals();
		});

		// Amount edit (not for Finance row — it is read-only/auto-calculated)
		ov.on("input", ".ch-pay-row-amount", e => {
			const idx = parseInt($(e.currentTarget).data("idx"));
			if (this._mop_type(this._payments[idx]?.mode) === "finance") return; // read-only
			this._payments[idx].amount = flt($(e.currentTarget).val()) || 0;
			if (this._is_finance_sale_type(PosState.sale_type)) {
				this._sync_finance_payments();
			}
			this._update_totals();
		});

		// UPI UTR
		ov.on("input", ".ch-pay-row-utr", e => {
			this._payments[parseInt($(e.currentTarget).data("idx"))].upi_transaction_id = $(e.currentTarget).val().trim();
			this._update_totals();
		});
		// Card RRN
		ov.on("input", ".ch-pay-row-rrn", e => {
			this._payments[parseInt($(e.currentTarget).data("idx"))].card_reference = $(e.currentTarget).val().trim();
			this._update_totals();
		});
		// Card last 4
		ov.on("input", ".ch-pay-row-card4", e => {
			this._payments[parseInt($(e.currentTarget).data("idx"))].card_last_four = $(e.currentTarget).val().trim();
		});
		ov.on("change", ".ch-pay-row-gateway-provider", e => {
			const idx = parseInt($(e.currentTarget).data("idx"));
			this._payments[idx].gateway_provider = $(e.currentTarget).val();
			this._payments[idx].payment_machine = "";
			this._render_payments();
			this._update_totals();
		});
		ov.on("change", ".ch-pay-row-machine", e => {
			const idx = parseInt($(e.currentTarget).data("idx"));
			this._payments[idx].payment_machine = $(e.currentTarget).val();
		});
		ov.on("click", ".ch-pay-row-paynow", e => {
			this._initiate_gateway_payment(parseInt($(e.currentTarget).data("idx")));
		});

		// Finance/EMI fields
		ov.on("change", ".ch-pay-row-fin-provider", e => {
			const idx = parseInt($(e.currentTarget).data("idx"));
			const partner_name = $(e.currentTarget).val();
			this._payments[idx].finance_provider = partner_name;
			// Populate tenure dropdown based on selected partner
			const partner = (this._finance_partners || []).find(fp => fp.partner_name === partner_name);
			const tenures = partner ? partner.tenures : [];
			const $tenure = this._overlay.find(`.ch-pay-row-fin-tenure[data-idx="${idx}"]`);
			$tenure.empty().append(`<option value="">${__('Select Tenure')}</option>`);
			tenures.forEach(t => $tenure.append(`<option value="${t}">${t} Months</option>`));
			this._payments[idx].finance_tenure = "";
			this._update_totals();
		});
		ov.on("change", ".ch-pay-row-fin-tenure", e => {
			this._payments[parseInt($(e.currentTarget).data("idx"))].finance_tenure = $(e.currentTarget).val();
		});
		ov.on("input", ".ch-pay-row-fin-approval", e => {
			this._payments[parseInt($(e.currentTarget).data("idx"))].finance_approval_id = $(e.currentTarget).val().trim();
			this._update_totals();
		});

		// ── Credit Sale toggle ────────────────────────
		ov.on("change", "#ch-pay-credit-chk", e => {
			this._is_credit_sale = $(e.currentTarget).is(":checked");
			ov.find("#ch-pay-credit-section").toggle(this._is_credit_sale);
			if (this._is_credit_sale) {
				// Uncheck free sale — mutually exclusive
				this._is_free_sale = false;
				ov.find("#ch-pay-free-chk").prop("checked", false);
				ov.find("#ch-pay-free-section").hide();
				// Default to Net 30 and activate pill
				this._credit_terms = "Net 30";
				this._credit_days = 30;
				ov.find("#ch-pay-credit-days").val(30);
				ov.find(".ch-pay-credit-term-btn[data-term='Net 30']").addClass("active");
				this._load_credit_info();
				this._update_credit_due_date();
			}
			if (this._is_finance_sale_type(PosState.sale_type)) {
				this._sync_finance_payments();
				this._render_payments();
			}
			this._update_totals();
		});

		// ── Credit Terms pills ────────────────────────
		ov.on("click", ".ch-pay-credit-term-btn", e => {
			const btn = $(e.currentTarget);
			const days = btn.data("days");
			const term = btn.data("term");
			ov.find(".ch-pay-credit-term-btn").removeClass("active");
			btn.addClass("active");
			this._credit_terms = term;
			if (days !== "custom") {
				this._credit_days = parseInt(days) || 30;
				ov.find("#ch-pay-credit-days").val(this._credit_days);
				this._update_credit_due_date();
			} else {
				// Focus days input for manual entry
				ov.find("#ch-pay-credit-days").focus().select();
			}
		});

		ov.on("input", "#ch-pay-credit-days", e => {
			this._credit_days = parseInt($(e.currentTarget).val()) || 30;
			// If days don't match a preset, switch to Custom
			const preset_map = { 15: "Net 15", 30: "Net 30", 45: "Net 45", 60: "Net 60", 90: "Net 90" };
			const matched_term = preset_map[this._credit_days];
			if (matched_term) {
				this._credit_terms = matched_term;
				ov.find(".ch-pay-credit-term-btn").removeClass("active");
				ov.find(`.ch-pay-credit-term-btn[data-term='${matched_term}']`).addClass("active");
			} else {
				this._credit_terms = "Custom";
				ov.find(".ch-pay-credit-term-btn").removeClass("active");
				ov.find(".ch-pay-credit-term-btn[data-term='Custom']").addClass("active");
			}
			this._update_credit_due_date();
		});
		ov.on("input", "#ch-pay-credit-interest", e => {
			this._credit_interest_rate = parseFloat($(e.currentTarget).val()) || 0;
		});
		ov.on("input", "#ch-pay-credit-grace", e => {
			this._credit_grace_period = parseInt($(e.currentTarget).val()) || 0;
		});
		ov.on("input", "#ch-pay-credit-partial", e => {
			this._credit_partial_payment = parseFloat($(e.currentTarget).val()) || 0;
			this._update_totals();
		});
		ov.on("input", "#ch-pay-credit-ref", e => {
			this._credit_reference = $(e.currentTarget).val().trim();
		});
		ov.on("input", "#ch-pay-credit-notes", e => {
			this._credit_notes = $(e.currentTarget).val().trim();
		});

		// ── Free Sale toggle ──────────────────────────
		ov.on("change", "#ch-pay-free-chk", e => {
			this._is_free_sale = $(e.currentTarget).is(":checked");
			ov.find("#ch-pay-free-section").toggle(this._is_free_sale);
			if (this._is_free_sale) {
				// Uncheck credit sale — mutually exclusive
				this._is_credit_sale = false;
				ov.find("#ch-pay-credit-chk").prop("checked", false);
				ov.find("#ch-pay-credit-section").hide();
				// Hide MOP section and payment rows for free sale
				ov.find("#ch-pay-mop-section").hide();
				ov.find("#ch-pay-rows").hide();
				ov.find("#ch-pay-quick-cash").hide();
				// Load category managers for items in cart
				this._load_category_managers();
			} else {
				ov.find("#ch-pay-mop-section").show();
				ov.find("#ch-pay-rows").show();
				this._free_sale_approval_name = null;
				this._free_sale_approved_by = "";
				clearInterval(this._approval_poll);
			}
			this._update_totals();
		});
		ov.on("input", "#ch-pay-free-reason", e => {
			this._free_sale_reason = $(e.currentTarget).val().trim();
			ov.find("#ch-pay-free-request-btn").prop("disabled", !this._free_sale_reason);
			this._update_totals();
		});
		ov.on("click", "#ch-pay-free-request-btn", () => {
			this._request_free_sale_approval();
		});

		// ── Advance adjustment toggle ─────────────────
		ov.on("change", ".ch-pay-advance-chk", e => {
			const $row = $(e.currentTarget).closest(".ch-pay-advance-item");
			const advName = $row.data("advance");
			const advBal = flt($row.data("balance"));
			if ($(e.currentTarget).is(":checked")) {
				this._advance_amount += advBal;
			} else {
				this._advance_amount = Math.max(0, this._advance_amount - advBal);
			}
			this._update_totals();
		});

		// ── SS manual advance input ───────────────────
		ov.on("input", "#ch-pay-ss-advance-amt", e => {
			const val = flt($(e.currentTarget).val()) || 0;
			const grand = this._calc_grand_total();
			this._advance_amount = Math.min(val, grand);
			this._set_cash_amount(this._calc_balance_due());
			this._update_totals();
		});

		// Loyalty
		ov.on("change", "#ch-pay-loyalty-chk", e => {
			this._redeem_loyalty = $(e.currentTarget).is(":checked");
			ov.find("#ch-pay-loyalty-input").toggle(this._redeem_loyalty);
			this._loyalty_amount = this._redeem_loyalty ? flt(ov.find("#ch-pay-loyalty-amt").val()) : 0;
			// Finance mode: don't auto-adjust down payment rows — just recalc Finance row
			if (!this._is_finance_sale_type(PosState.sale_type)) {
				this._set_cash_amount(this._calc_balance_due());
			}
			this._update_totals();
		});
		ov.on("input", "#ch-pay-loyalty-amt", e => {
			const max = flt(PosState.loyalty_points) * flt(PosState.conversion_factor);
			const max_usable = Math.min(max, this._calc_grand_total());
			this._loyalty_amount = Math.min(flt($(e.currentTarget).val()) || 0, max_usable);
			// Finance mode: don't auto-adjust down payment rows — just recalc Finance row
			if (!this._is_finance_sale_type(PosState.sale_type)) {
				this._set_cash_amount(this._calc_balance_due());
			}
			this._update_totals();
		});

		// Quick cash exact
		ov.on("click", "#ch-pay-exact-btn", () => {
			this._set_cash_amount(this._calc_balance_due());
		});
		// Quick cash rounded
		ov.on("click", ".ch-pay-quick-btn:not(#ch-pay-exact-btn)", e => {
			this._set_cash_amount(flt($(e.currentTarget).data("amt")));
		});

		// Submit
		ov.on("click", "#ch-pay-submit", () => this._submit_invoice());

		// Sale type pills — unified with credit/free checkboxes
		ov.on("click", ".ch-pay-saletype-btn", e => {
			const btn = $(e.currentTarget);
			const type = btn.data("type");
			ov.find(".ch-pay-saletype-btn").removeClass("active");
			btn.addClass("active");
			PosState.sale_type = type;
			PosState.sale_sub_type = null;
			PosState.sale_reference = null;
			this._update_sale_sub_type(type);
			EventBus.emit("sale_type:changed", type);

			// Sync checkboxes based on sale type properties
			const st = this._sale_types.find(t => t.sale_type_name === type);
			if (st) {
				// requires_payment may come back as 0 (int) or false (bool)
				const is_free   = !st.requires_payment;
				// FS (Finance Sale) is NOT a credit sale — it has its own finance flow
				const is_credit = !!(st.triggers_credit_sale ||
					["CS"].includes((st.code || "").toUpperCase())) &&
					!this._is_finance_sale_type(type);

				// Toggle free sale (only if changed — avoids re-triggering)
				if (is_free !== this._is_free_sale) {
					ov.find("#ch-pay-free-chk").prop("checked", is_free).trigger("change");
				}
				// Toggle credit sale
				if (is_credit !== this._is_credit_sale) {
					ov.find("#ch-pay-credit-chk").prop("checked", is_credit).trigger("change");
				}
				// If neither, ensure both are off
				if (!is_free && !is_credit) {
					if (this._is_free_sale) ov.find("#ch-pay-free-chk").prop("checked", false).trigger("change");
					if (this._is_credit_sale) ov.find("#ch-pay-credit-chk").prop("checked", false).trigger("change");
				}
			}

			// ── Always reconcile payment rows after type switch ──
			const is_finance = this._is_finance_sale_type(type);
			const had_finance = this._payments.some(p => this._mop_type(p.mode) === "finance");
			if (is_finance) {
				// Switching TO finance:
				// Strip any stale is_down_payment flags from previous session
				this._payments.forEach(p => { delete p.is_down_payment; });
				// Remove existing finance rows first (to let _sync re-add correctly)
				this._payments = this._payments.filter(p => this._mop_type(p.mode) !== "finance");
				// If no rows remain, seed an empty Cash row as the down payment placeholder
				if (!this._payments.length) {
					this._payments = [{ mode: "Cash", amount: 0, upi_transaction_id: "", card_reference: "", card_last_four: "", finance_provider: "", finance_tenure: "", finance_approval_id: "", finance_down_payment: 0, is_down_payment: true }];
				} else {
					// Existing rows become down-payment rows — zero out amounts so Finance carries full
					this._payments.forEach(p => {
						p.amount = 0;
						p.is_down_payment = true;
					});
				}
				this._sync_finance_payments();
			} else if (had_finance) {
				// Switching AWAY from finance — remove Finance row, restore single Cash row
				const grand = this._calc_grand_total();
				this._payments = this._payments.filter(p => this._mop_type(p.mode) !== "finance");
				// Clean up down payment flags
				this._payments.forEach(p => { delete p.is_down_payment; });
				// Ensure at least one row covering full grand total
				if (!this._payments.length) {
					this._payments = [{ mode: "Cash", amount: grand, upi_transaction_id: "", card_reference: "", card_last_four: "", finance_provider: "", finance_tenure: "", finance_approval_id: "", finance_down_payment: 0 }];
				} else if (this._payments.length === 1) {
					// Re-seed to full amount
					this._payments[0].amount = grand;
				}
			}
			// Always re-render rows and ensure #ch-pay-rows / #ch-pay-mop-section are visible
			ov.find("#ch-pay-mop-section").show();
			ov.find("#ch-pay-rows").show();
			this._render_payments();

			// Show/hide SS advance section
			const st_code = (this._sale_types.find(t => t.sale_type_name === type)?.code || "").toUpperCase();
			if (st_code === "SS") {
				ov.find("#ch-pay-ss-advance-section").show();
				ov.find("#ch-pay-ss-advance-amt").val("");
				this._advance_amount = 0;
			} else {
				ov.find("#ch-pay-ss-advance-section").hide();
				if (!this._payments.some(() => true) || st_code !== "SS") {
					// Reset SS advance when leaving SS type
					if (!ov.find(".ch-pay-advance-chk:checked").length) {
						this._advance_amount = 0;
					}
				}
			}

			this._update_totals();
		});
		ov.on("change", "#ch-pay-sale-sub-select", e => {
			const val = $(e.currentTarget).val();
			PosState.sale_sub_type = val || null;
			PosState.sale_reference = null;
			const ref = ov.find("#ch-pay-sale-ref-input");
			const tenure_sel = ov.find("#ch-pay-sale-fin-tenure");

			// Finance sale type — show tenure dropdown and reference input from partner
			if (this._is_finance_sale_type(PosState.sale_type)) {
				const partner = (this._finance_partners || []).find(fp => fp.partner_name === val);
				if (partner && partner.tenures && partner.tenures.length) {
					let topts = `<option value="">${__("Tenure...")}</option>`;
					partner.tenures.forEach(t => topts += `<option value="${t}">${t} Months</option>`);
					tenure_sel.html(topts).show();
				} else {
					tenure_sel.hide();
				}
				PosState.finance_tenure = null;
				if (val) { ref.attr("placeholder", __("Approval / Loan ID")).show().val(""); } else { ref.hide().val(""); }
				// Auto-populate payment row finance fields
				this._sync_finance_to_payment(val, null, null);
				this._sync_finance_payments();
				this._render_payments();
				this._update_totals();
				return;
			}

			tenure_sel.hide();
			const opt = $(e.currentTarget).find(":selected");
			if (opt.data("ref")) { ref.attr("placeholder", __("Reference No...")).show().val(""); } else { ref.hide().val(""); }
		});
		ov.on("change", "#ch-pay-sale-fin-tenure", e => {
			PosState.finance_tenure = $(e.currentTarget).val() || null;
			this._sync_finance_to_payment(PosState.sale_sub_type, PosState.finance_tenure, PosState.sale_reference);
			this._sync_finance_payments();
			this._render_payments();
			this._update_totals();
		});
		ov.on("change", "#ch-pay-sale-ref-input", e => {
			PosState.sale_reference = $(e.currentTarget).val().trim() || null;
			// Sync approval ID to payment row if finance sale
			if (this._is_finance_sale_type(PosState.sale_type)) {
				this._sync_finance_to_payment(PosState.sale_sub_type, PosState.finance_tenure, PosState.sale_reference);
				this._sync_finance_payments();
				this._render_payments();
				this._update_totals();
			}
		});

// ── Bank offer chip click — toggle apply/remove ─────────────────────────
ov.on("click", ".ch-pay-offer-chip", e => {
	const $chip = $(e.currentTarget);
	const offerName = $chip.data("offer-name");
	// Toggle: clicking same offer removes it
	if (this._bank_offer && this._bank_offer.name === offerName) {
		this._bank_offer = null;
		PosState.bank_offer_discount = 0;
	} else {
		// Mutual exclusion: bank offer cannot be combined with additional discount
		if (flt(PosState.additional_discount_pct) > 0 || flt(PosState.additional_discount_amt) > 0) {
			pos_warning(__("Bank offers cannot be combined with additional discounts. Remove the discount first."), 5000);
			return;
		}
		const valueType = ($chip.data("value-type") || "").toLowerCase();
		const value     = flt($chip.data("value"));
		const grand     = this._calc_grand_total_before_offer();
		const discount  = valueType === "percentage" ? (grand * value / 100) : value;
		this._bank_offer = {
			name: offerName,
			offer_name: $chip.text().trim(),
			value_type: valueType,
			value,
			discount: Math.min(discount, grand),
		};
		PosState.bank_offer_discount = this._bank_offer.discount;
		frappe.show_alert({ message: `🏦 ${__('Offer applied')}: ₹${format_number(this._bank_offer.discount)} ${__('off')}`, indicator: 'green' }, 3);
	}
	// Re-render chips to update active state (use cached MOP)
	const mop = this._payments[0] ? this._payments[0].mode : null;
	if (mop) this._load_bank_offers(mop);
	if (this._is_finance_sale_type(PosState.sale_type)) {
		this._sync_finance_payments();
		this._render_payments();
	} else {
		this._set_cash_amount(this._calc_balance_due());
	}
	this._update_totals();
});

// ── Accordion toggles (progressive disclosure) ─────────────────────────
ov.on("click", ".ch-pay-section-hdr", function() {
const targetId = $(this).data("target");
const $body = ov.find("#" + targetId);
const open = $body.is(":visible");
$body.slideToggle(200);
$(this).find(".ch-pay-toggle-icon").toggleClass("ch-pay-icon-open", !open);
});

// ── Discount reason ────────────────────────────────────────────────────
ov.on("change", "#ch-pay-disc-reason", e => {
this._disc_reason = $(e.currentTarget).val() || "";
PosState.discount_reason = this._disc_reason;
const reason = this._disc_reasons.find(r => r.name === this._disc_reason);
// Mutual exclusion: clear bank offer when applying a discount reason
if (reason && this._bank_offer) {
	this._bank_offer = null;
	PosState.bank_offer_discount = 0;
	pos_warning(__("Bank offer removed — cannot combine with additional discount"), 4000);
	const mop = this._payments[0] ? this._payments[0].mode : null;
	if (mop) this._load_bank_offers(mop);
}
const $manual = ov.find("#ch-pay-disc-manual");
const $info   = ov.find("#ch-pay-disc-info");
if (!reason) {
this._disc_pct = 0;
this._disc_amt = 0;
PosState.additional_discount_pct = 0;
PosState.additional_discount_amt = 0;
$manual.hide();
$info.hide();
ov.find("#ch-pay-disc-pct, #ch-pay-disc-amt").val("");
} else if (reason.allow_manual_entry) {
this._disc_pct = 0;
this._disc_amt = 0;
PosState.additional_discount_pct = 0;
PosState.additional_discount_amt = 0;
ov.find("#ch-pay-disc-pct, #ch-pay-disc-amt").val("");
$manual.show();
$info.text(reason.max_manual_percent
? __("Max {0}%", [reason.max_manual_percent])
: __("Enter discount within your role limits")).show();
} else {
// Preset
$manual.hide();
if (reason.discount_type === "Percentage") {
this._disc_pct = flt(reason.discount_value);
this._disc_amt = 0;
PosState.additional_discount_pct = this._disc_pct;
PosState.additional_discount_amt = 0;
$info.text(__("{0}% applied", [reason.discount_value])).show();
} else {
this._disc_amt = flt(reason.discount_value);
this._disc_pct = 0;
PosState.additional_discount_pct = 0;
PosState.additional_discount_amt = this._disc_amt;
$info.text(__("₹{0} applied", [reason.discount_value])).show();
}
}
this._update_totals();
});
ov.on("change blur", "#ch-pay-disc-pct", e => {
const pct = parseFloat($(e.currentTarget).val()) || 0;
if (!$(e.currentTarget).val()) {
this._disc_pct = 0;
PosState.additional_discount_pct = 0;
this._update_totals();
return;
}
const reason = this._disc_reasons.find(r => r.name === this._disc_reason);
if (reason && reason.max_manual_percent > 0 && pct > reason.max_manual_percent) {
frappe.show_alert({ message: __("Max {0}% for this reason", [reason.max_manual_percent]), indicator: "orange" });
$(e.currentTarget).val("");
return;
}
this._disc_pct = pct;
this._disc_amt = 0;
PosState.additional_discount_pct = pct;
PosState.additional_discount_amt = 0;
ov.find("#ch-pay-disc-amt").val("");
this._update_totals();
});
ov.on("change blur", "#ch-pay-disc-amt", e => {
const amt = parseFloat($(e.currentTarget).val()) || 0;
if (!$(e.currentTarget).val()) {
this._disc_amt = 0;
PosState.additional_discount_amt = 0;
this._update_totals();
return;
}
this._disc_amt = amt;
this._disc_pct = 0;
PosState.additional_discount_pct = 0;
PosState.additional_discount_amt = amt;
ov.find("#ch-pay-disc-pct").val("");
this._update_totals();
});

// ── Coupon / Voucher apply ────────────────────────────────────────────
ov.on("click", "#ch-pay-coupon-apply", () => {
const code = ov.find("#ch-pay-coupon-code").val().trim();
if (!code) return;
frappe.xcall("ch_pos.api.pos_api.apply_coupon_or_voucher", {
code,
customer: PosState.customer,
company:  PosState.company,
}).then(data => {
const $msg = ov.find("#ch-pay-coupon-msg");
if (data.is_voucher) {
this._dlg_voucher_code    = code;
this._dlg_voucher_amount  = flt(data.amount);
this._dlg_voucher_name    = data.voucher_name || "";
this._dlg_voucher_balance = flt(data.balance);
this._dlg_coupon_code     = "";
this._dlg_coupon_discount = 0;
PosState.voucher_code    = code;
PosState.voucher_amount  = this._dlg_voucher_amount;
PosState.voucher_name    = data.voucher_name || "";
PosState.voucher_balance = this._dlg_voucher_balance;
PosState.coupon_code     = null;
PosState.coupon_discount = 0;
$msg.html(`<span style="color:var(--pos-success)">🎟️ ${__("Voucher")} — ₹${format_number(data.amount)} ${__("off")} (${__("Bal")}: ₹${format_number(data.balance)})</span>`).show();
} else {
this._dlg_coupon_code     = code;
this._dlg_coupon_discount = flt(data.amount);
this._dlg_voucher_code    = "";
this._dlg_voucher_amount  = 0;
PosState.coupon_code     = code;
PosState.coupon_discount = this._dlg_coupon_discount;
PosState.voucher_code    = null;
PosState.voucher_amount  = 0;
$msg.html(`<span style="color:var(--pos-success)">🏷️ ${__("Coupon")} — ₹${format_number(data.amount)} ${__("off")}</span>`).show();
}
this._update_totals();
}).catch(err => {
const msg = err && err.message ? frappe.utils.strip_html(err.message) : __("Invalid code");
ov.find("#ch-pay-coupon-msg")
.html(`<span style="color:var(--pos-danger)">${frappe.utils.escape_html(msg)}</span>`).show();
});
});
ov.on("keydown", "#ch-pay-coupon-code", e => {
if (e.key === "Enter") ov.find("#ch-pay-coupon-apply").trigger("click");
});

// ── Confirm on Enter when balance == 0 ───────────────────────────────
ov.on("keydown", e => {
if (e.key === "Enter" && !$(e.target).is("input, textarea, select, button")) {
const $btn = ov.find("#ch-pay-submit");
if (!$btn.prop("disabled")) $btn.trigger("click");
}
});

// Escape to close
		// Escape to close
		$(document).one("keydown.ch_pay_overlay", e => {
			if (e.key === "Escape") this._close();
		});
	}

	_set_cash_amount(amt) {
		// Prefer a Cash row; if none exists fall back to the first non-finance, non-frozen row
		let idx = this._payments.findIndex(p => this._mop_type(p.mode) === "cash");
		if (idx < 0) {
			idx = this._payments.findIndex(p => this._mop_type(p.mode) !== "finance" && !p.gateway_initiated);
		}
		if (idx < 0) return;
		this._payments[idx].amount = amt;
		this._sync_payment_amount_inputs();
		this._update_totals();
	}

	_sync_payment_amount_inputs() {
		if (!this._overlay) return;
		this._payments.forEach((payment, idx) => {
			this._overlay.find(`.ch-pay-row-amount[data-idx="${idx}"]`).val(flt(payment.amount, 2).toFixed(2));
		});
	}

	_rebalance_payments_after_total_change(previousGrand, nextGrand) {
		if (this._is_free_sale || previousGrand == null) return;
		if (Math.abs(flt(nextGrand) - flt(previousGrand)) <= 0.005) return;
		if (!this._payments.length) return;

		// In Finance mode, _sync_finance_payments handles the Finance row — skip rebalance
		if (this._is_finance_sale_type(PosState.sale_type)) return;

		// Net amount the tender rows must cover (grand minus any non-cash instruments)
		const loyalty  = this._redeem_loyalty ? Math.min(this._loyalty_amount, nextGrand) : 0;
		const advance  = Math.min(this._advance_amount, Math.max(0, nextGrand - loyalty));
		const net_due  = Math.max(0, nextGrand - loyalty - advance);

		const total_tendered = this._payments.reduce((s, p) => s + flt(p.amount), 0);

		if (total_tendered > net_due + 0.005) {
			// Over-tendered vs new payable: reduce last non-frozen rows first
			let excess = flt(total_tendered - net_due);
			for (let i = this._payments.length - 1; i >= 0 && excess > 0.005; i--) {
				if (this._payments[i].gateway_initiated) continue;  // frozen — skip
				const current   = flt(this._payments[i].amount);
				const reduction = Math.min(current, excess);
				this._payments[i].amount = flt(current - reduction, 2);
				excess = flt(excess - reduction, 2);
			}
			if (excess > 0.005) {
				// Remaining excess is locked in gateway rows — warn cashier
				frappe.show_alert({
					message: __("Gateway payment initiated — reduce tender manually if needed"),
					indicator: "orange",
				});
			}
		} else if (total_tendered < net_due - 0.005 && this._payments.length === 1
				   && !this._payments[0].gateway_initiated) {
			// Single unfrozen row: auto-fill to match new net due
			this._payments[0].amount = flt(net_due, 2);
		}

		this._sync_payment_amount_inputs();
	}

	_refresh_totals_block() {
		this._overlay?.find(".ch-pay-totals-block").html(this._build_totals_html());
	}

	_sync_finance_payments() {
		if (!this._is_finance_sale_type(PosState.sale_type) || this._is_free_sale) return;

		const grand = this._calc_grand_total();
		// Loyalty reduces the amount that must be financed
		const loyalty = this._redeem_loyalty ? Math.min(this._loyalty_amount || 0, grand) : 0;
		const net_payable = Math.max(0, grand - loyalty);
		const finance_idx = this._payments.findIndex(p => this._mop_type(p.mode) === "finance");
		const non_finance_paid = this._payments.reduce((sum, payment, idx) => {
			if (idx === finance_idx) return sum;
			return sum + flt(payment.amount);
		}, 0);
		const financed_amount = Math.max(0, net_payable - non_finance_paid);

		if (finance_idx >= 0) {
			// Finance row exists — just update amount (it is non-editable / auto-calc)
			this._payments[finance_idx].amount = financed_amount;
			// Sync DOM input immediately so Finance row always shows the correct amount
			this._overlay?.find(`.ch-pay-row-amount[data-idx="${finance_idx}"]`).val(flt(financed_amount, 2).toFixed(2));
			return;
		}

		// No finance row yet: push one. Existing rows (any mode) become down payment rows.
		// If there is a single row that covers the full net_payable with no intended down payment,
		// reset it to 0 so the Finance row carries the whole amount.
		if (this._payments.length === 1 && !this._payments[0].is_down_payment) {
			this._payments[0].amount = 0;
			this._payments[0].is_down_payment = true;
		}
		this._payments.push({
			mode: "Finance",
			amount: financed_amount,
			upi_transaction_id: "", card_reference: "", card_last_four: "",
			finance_provider: "", finance_tenure: "", finance_approval_id: "",
			finance_down_payment: 0,
		});
	}

	// ───────────────────────────────────── Discount Reasons ──

	_load_disc_reasons() {
		this._disc_reasons = [];
		if (!PosState.company) return;
		frappe.xcall("ch_pos.api.pos_api.get_discount_reasons", {
			company: PosState.company,
		}).then((reasons) => {
			this._disc_reasons = reasons || [];
			const sel = this._overlay?.find("#ch-pay-disc-reason");
			if (!sel) return;
			sel.find("option:not(:first)").remove();
			(reasons || []).forEach((r) => {
				const label = r.allow_manual_entry
					? r.reason_name
					: `${r.reason_name} (${r.discount_type === "Percentage" ? r.discount_value + "%" : "₹" + r.discount_value})`;
				sel.append(`<option value="${frappe.utils.escape_html(r.name)}">${frappe.utils.escape_html(label)}</option>`);
			});
		if (this._disc_reason) {
			sel.val(this._disc_reason);
			sel.trigger("change");
			if (this._disc_pct) this._overlay?.find("#ch-pay-disc-pct").val(this._disc_pct);
			if (this._disc_amt) this._overlay?.find("#ch-pay-disc-amt").val(this._disc_amt);
			// Open the accordion to show the restored discount
			this._overlay?.find("#ch-pay-disc-body").show();
			this._overlay?.find("#ch-pay-disc-block .ch-pay-toggle-icon").addClass("ch-pay-icon-open");
		}
		});
	}

	_load_finance_partners() {
		this._finance_partners = [];
		frappe.xcall("ch_pos.api.pos_api.get_finance_partners").then(partners => {
			this._finance_partners = partners || [];
			// Re-render payment rows to populate finance dropdowns if any finance row exists
			if (this._payments.some(p => this._mop_type(p.mode) === "finance")) {
				this._render_payments();
				this._update_totals();
			}
			// If FS sale type is already selected, refresh its sub-type dropdown
			if (this._is_finance_sale_type(PosState.sale_type)) {
				this._update_sale_sub_type(PosState.sale_type);
			}
		});
	}

	_load_payment_machines() {
		this._payment_machine_data = { providers: [], machines: [] };
		if (!PosState.company || !PosState.store) return;
		frappe.xcall("ch_pos.api.payment_gateway_api.get_payment_machines", {
			company: PosState.company,
			store: PosState.store,
			pos_profile: PosState.pos_profile,
		}).then((data) => {
			this._payment_machine_data = data || { providers: [], machines: [] };
			if (this._payments.some(p => ["upi", "card"].includes(this._mop_type(p.mode)))) {
				this._render_payments();
				this._update_totals();
			}
		}).catch((err) => {
			console.error("Payment machines load failed:", err);
		});
	}

	_gateway_providers_for_mode(mode) {
		const target = this._normalize_gateway_mode(mode);
		const providers = new Set();
		(this._payment_machine_data.machines || []).forEach((machine) => {
			const supported = (machine.supported_payment_modes || "").toUpperCase();
			if (!supported || supported.includes(target)) {
				providers.add(machine.provider);
			}
		});
		return Array.from(providers);
	}

	_machines_for_row(payment) {
		const target = this._normalize_gateway_mode(payment.mode);
		return (this._payment_machine_data.machines || []).filter((machine) => {
			if (payment.gateway_provider && machine.provider !== payment.gateway_provider) return false;
			const supported = (machine.supported_payment_modes || "").toUpperCase();
			return !supported || supported.includes(target);
		});
	}

	_normalize_gateway_mode(mop_name) {
		const type = this._mop_type(mop_name);
		if (type === "upi") return "UPI";
		if (type === "card") return "CARD";
		if (type === "cash") return "CASH";
		return (mop_name || "").toUpperCase();
	}

	_initiate_gateway_payment(idx) {
		const payment = this._payments[idx];
		if (!payment) return;
		if (flt(payment.amount) <= 0) {
			frappe.show_alert({ message: __("Enter amount before Pay Now"), indicator: "orange" });
			return;
		}
		if (!payment.payment_machine) {
			frappe.show_alert({ message: __("Select a payment machine"), indicator: "orange" });
			return;
		}

		frappe.xcall("ch_pos.api.payment_gateway_api.initiate_payment", {
			machine_name: payment.payment_machine,
			amount: flt(payment.amount),
			payment_mode: payment.mode,
			customer: PosState.customer,
			customer_name: PosState.customer_info?.customer_name || PosState.customer,
			customer_email: PosState.customer_info?.email_id || "",
			customer_phone: PosState.customer_info?.mobile_no || PosState.customer_info?.mobile || "",
			merchant_order_reference: this._gen_uuid(),
			notes: `POS ${payment.mode} payment for ${PosState.customer || "Customer"}`,
		}).then((res) => {
			payment.gateway_provider = res.provider || payment.gateway_provider || "";
			payment.payment_machine = res.machine || payment.payment_machine || "";
			payment.gateway_order_id = res.order_id || "";
			payment.gateway_status = res.status || "CREATED";
			payment.gateway_initiated = true;   // amount is committed — freeze from auto-rebalance
			this._render_payments();
			this._update_totals();
			frappe.show_alert({
				message: __("{0} order {1} created on {2}", [payment.mode, res.order_id || res.merchant_order_reference || "", res.machine_name || res.machine || "machine"]),
				indicator: "green",
			});
		}).catch((err) => {
			console.error("Gateway initiation failed:", err);
		});
	}

	/** Sync finance sale type selections into the payment row's finance fields */
	_sync_finance_to_payment(provider, tenure, approval_id) {
		for (let i = 0; i < this._payments.length; i++) {
			const p = this._payments[i];
			// Provider, tenure, approval are captured in the Sale Type section —
			// store on payment object for submission but don't re-render the row.
			p.finance_provider = provider || "";
			p.finance_tenure = tenure || "";
			p.finance_approval_id = approval_id || "";
		}
		this._update_totals();
	}

	// ───────────────────────────────────── Sale type pills ──

	_load_sale_types() {
		this._sale_types = [];
		console.log("[POS] Loading sale types for company:", PosState.company);
		frappe.xcall("ch_pos.api.pos_api.get_sale_types", {
			company: PosState.company,
		}).then((types) => {
			console.log("[POS] Sale types loaded:", types);
			this._sale_types = types || [];
			if (this._sale_types.length) {
				this._render_sale_type_pills();
			} else {
				// Show "not configured" hint so users know why pills are missing
				const pills = this._overlay.find("#ch-pay-sale-type-pills");
				pills.html(`<span class="text-muted small">${__("No sale types configured for {0}. Check CH Sale Type master.", [PosState.company || __("this company")])}</span>`);
			}
		}).catch((err) => {
			console.error("[POS] Sale type load error:", err);
			const pills = this._overlay.find("#ch-pay-sale-type-pills");
			pills.html(`<span class="text-danger small">${__("Failed to load sale types. Please reload.")}</span>`);
		});
	}

	_render_sale_type_pills() {
		const pills = this._overlay.find("#ch-pay-sale-type-pills");
		let btns = "";
		for (const st of this._sale_types) {
			const active = (st.is_default || st.sale_type_name === PosState.sale_type) ? " active" : "";
			btns += `<button class="ch-pay-saletype-btn${active}" data-type="${frappe.utils.escape_html(st.sale_type_name)}">${frappe.utils.escape_html(st.code || st.sale_type_name)}</button>`;
			if (st.is_default && !PosState.sale_type) {
				PosState.sale_type = st.sale_type_name;
			}
		}
		pills.html(btns);
		if (PosState.sale_type) {
			this._update_sale_sub_type(PosState.sale_type);
		}

		// Sync free/credit toggles with the actual current sale type.
		// This corrects any stale _is_free_sale/_is_credit_sale state that
		// was restored from a previous dialog session (e.g. user went Back
		// while in Free Sale mode, then re-opened with a normal sale type).
		const cur = this._sale_types.find(t => t.sale_type_name === PosState.sale_type);
		if (cur) {
			const should_free   = !cur.requires_payment;
			// FS (Finance Sale) is NOT a credit sale
			const should_credit = !!(cur.triggers_credit_sale ||
				["CS"].includes((cur.code || "").toUpperCase())) &&
				!this._is_finance_sale_type(PosState.sale_type);
			if (should_free !== this._is_free_sale) {
				this._overlay.find("#ch-pay-free-chk").prop("checked", should_free).trigger("change");
			}
			if (should_credit !== this._is_credit_sale) {
				this._overlay.find("#ch-pay-credit-chk").prop("checked", should_credit).trigger("change");
			}
		}
	}

	_is_finance_sale_type(type_name) {
		const st = this._sale_types.find(t => t.sale_type_name === type_name);
		if (!st) return false;
		const code = (st.code || "").toUpperCase();
		return code === "FS" || type_name.toLowerCase().includes("finance") || type_name.toLowerCase().includes("emi");
	}

	_update_sale_sub_type(type_name) {
		const st = this._sale_types.find(t => t.sale_type_name === type_name);
		const row = this._overlay.find("#ch-pay-sale-sub-row");
		const sel = row.find("#ch-pay-sale-sub-select");
		const ref = row.find("#ch-pay-sale-ref-input");
		const tenure_sel = row.find("#ch-pay-sale-fin-tenure");

		// Reset finance tenure state
		PosState.finance_tenure = null;
		tenure_sel.hide();

		// Finance sale type — populate from CH Finance Partner master
		if (this._is_finance_sale_type(type_name)) {
			const partners = this._finance_partners || [];
			if (!partners.length) {
				row.hide();
				PosState.sale_sub_type = null;
				PosState.sale_reference = null;
				return;
			}
			let options = `<option value="">${__("Select Finance Partner...")}</option>`;
			for (const fp of partners) {
				options += `<option value="${frappe.utils.escape_html(fp.partner_name)}">${frappe.utils.escape_html(fp.partner_name)}</option>`;
			}
			sel.html(options);
			ref.hide().val("");
			row.show();
			return;
		}

		if (!st || !st.sub_types || !st.sub_types.length) {
			row.hide();
			PosState.sale_sub_type = null;
			PosState.sale_reference = null;
			return;
		}

		let options = `<option value="">${__("Select sub-type...")}</option>`;
		for (const sub of st.sub_types) {
			options += `<option value="${frappe.utils.escape_html(sub.sale_sub_type)}" data-ref="${sub.requires_reference ? 1 : 0}">${frappe.utils.escape_html(sub.sale_sub_type)}</option>`;
		}
		sel.html(options);
		ref.hide().val("");
		row.show();
	}

	// ───────────────────────────────────────── Render rows ──

	_render_payments() {
		const container = this._overlay.find("#ch-pay-rows");
		container.empty();

		this._payments.forEach((p, idx) => {
			const type = this._mop_type(p.mode);
			let ref_html = "";
			if (type === "upi") {
				const providers = this._gateway_providers_for_mode(p.mode);
				const machines = this._machines_for_row(p);
				ref_html = `
					<div class="ch-pay-gateway-refs mt-1">
						<div class="ch-pay-gateway-grid ch-pay-gateway-grid-two">
							<select class="form-control form-control-sm ch-pay-row-gateway-provider" data-idx="${idx}">
								<option value="">${__("Select Provider")}</option>
								${providers.map((provider) => `<option value="${frappe.utils.escape_html(provider)}" ${provider === (p.gateway_provider || "") ? "selected" : ""}>${frappe.utils.escape_html(provider)}</option>`).join("")}
							</select>
							<select class="form-control form-control-sm ch-pay-row-machine" data-idx="${idx}">
								<option value="">${__("Select Machine")}</option>
								${machines.map((machine) => `<option value="${frappe.utils.escape_html(machine.name)}" ${machine.name === (p.payment_machine || "") ? "selected" : ""}>${frappe.utils.escape_html(machine.machine_name)}${machine.terminal_id ? ` (${frappe.utils.escape_html(machine.terminal_id)})` : ""}</option>`).join("")}
							</select>
						</div>
						<div class="ch-pay-gateway-actions">
							<button class="btn btn-xs btn-default ch-pay-row-paynow" data-idx="${idx}"><i class="fa fa-bolt"></i> ${__("Pay Now")}</button>
							<div class="ch-pay-gateway-status">${p.gateway_order_id ? `${__("Order")}: ${frappe.utils.escape_html(p.gateway_order_id)}${p.gateway_status ? ` | ${frappe.utils.escape_html(p.gateway_status)}` : ""}` : __("Choose provider and machine before sending the amount")}</div>
						</div>
						<input type="text" class="form-control form-control-sm ch-pay-row-utr" data-idx="${idx}"
							placeholder="${__("UPI UTR / Txn ID")}" value="${frappe.utils.escape_html(p.upi_transaction_id || "")}">
					</div>`;
			} else if (type === "card") {
				const providers = this._gateway_providers_for_mode(p.mode);
				const machines = this._machines_for_row(p);
				ref_html = `
					<div class="ch-pay-card-refs mt-1">
						<div class="ch-pay-gateway-grid ch-pay-gateway-grid-two">
							<select class="form-control form-control-sm ch-pay-row-gateway-provider" data-idx="${idx}">
								<option value="">${__("Select Provider")}</option>
								${providers.map((provider) => `<option value="${frappe.utils.escape_html(provider)}" ${provider === (p.gateway_provider || "") ? "selected" : ""}>${frappe.utils.escape_html(provider)}</option>`).join("")}
							</select>
							<select class="form-control form-control-sm ch-pay-row-machine" data-idx="${idx}">
								<option value="">${__("Select Machine")}</option>
								${machines.map((machine) => `<option value="${frappe.utils.escape_html(machine.name)}" ${machine.name === (p.payment_machine || "") ? "selected" : ""}>${frappe.utils.escape_html(machine.machine_name)}${machine.terminal_id ? ` (${frappe.utils.escape_html(machine.terminal_id)})` : ""}</option>`).join("")}
							</select>
						</div>
						<div class="ch-pay-gateway-actions">
							<button class="btn btn-xs btn-default ch-pay-row-paynow" data-idx="${idx}"><i class="fa fa-bolt"></i> ${__("Pay Now")}</button>
							<div class="ch-pay-gateway-status">${p.gateway_order_id ? `${__("Order")}: ${frappe.utils.escape_html(p.gateway_order_id)}${p.gateway_status ? ` | ${frappe.utils.escape_html(p.gateway_status)}` : ""}` : __("Choose provider and machine before sending the amount")}</div>
						</div>
						<div class="ch-pay-gateway-grid ch-pay-gateway-grid-two">
							<input type="text" class="form-control form-control-sm ch-pay-row-rrn" data-idx="${idx}"
								placeholder="${__("Card RRN")}" value="${frappe.utils.escape_html(p.card_reference || "")}">
							<input type="text" class="form-control form-control-sm ch-pay-row-card4" data-idx="${idx}"
								placeholder="${__("Last 4 digits")}" maxlength="4" value="${frappe.utils.escape_html(p.card_last_four || "")}">
						</div>
					</div>`;
			} else if (type === "finance") {
				// Amount is auto-calculated (net_payable - down payments); show as read-only.
				ref_html = `<div class="ch-pay-fin-auto-note"><i class="fa fa-info-circle"></i> ${__("Auto-calculated. Add Cash/UPI/Card rows above for down payment.")}</div>`;
			}

			// Finance row: amount is read-only (auto-calc). All other rows are editable.
			const is_finance_row = type === "finance";
			container.append(`
				<div class="ch-pay-row${is_finance_row ? " ch-pay-row-finance" : ""}" data-idx="${idx}">
					<div class="ch-pay-row-header">
						<span class="ch-pay-row-mop-label">${_mop_icon(p.mode)} ${frappe.utils.escape_html(p.mode)}</span>
						${(!is_finance_row && this._payments.length > 1)
							? `<button class="ch-pay-row-remove" data-idx="${idx}" title="${__("Remove")}"><i class="fa fa-times-circle"></i></button>`
							: ""}
					</div>
					<div class="ch-pay-row-amt-group${is_finance_row ? " ch-pay-row-amt-readonly" : ""}">
						<span class="ch-pay-amt-prefix">₹</span>
						<input type="number" class="ch-pay-row-amount" data-idx="${idx}"
							value="${flt(p.amount, 2)}" min="0" step="0.01"${is_finance_row ? " readonly tabindex=\"-1\"" : ""}>
					</div>
					${ref_html}
				</div>`);
		});

		// Show/hide quick cash section — hide in Finance mode (down payment amounts are entered directly)
		const has_cash = this._payments.some(p => this._mop_type(p.mode) === "cash");
		const show_quick_cash = has_cash && !this._is_free_sale && !this._is_finance_sale_type(PosState.sale_type);
		this._overlay.find("#ch-pay-quick-cash").toggle(show_quick_cash);
		if (show_quick_cash) this._render_quick_cash();
	}

	_render_quick_cash() {
		const due = this._calc_balance_due();
		const amounts = this._quick_amounts(due);
		const btns = this._overlay.find("#ch-pay-quick-btns");
		btns.empty();
		btns.append(`<button class="btn btn-xs btn-default ch-pay-quick-btn" id="ch-pay-exact-btn">${__("Exact")} ₹${format_number(due)}</button>`);
		amounts.forEach(a => {
			btns.append(`<button class="btn btn-xs btn-default ch-pay-quick-btn" data-amt="${a}">₹${format_number(a)}</button>`);
		});
	}

	_quick_amounts(due) {
		const rounds = [10, 50, 100, 500, 1000];
		const result = [];
		for (const r of rounds) {
			const rounded = Math.ceil(due / r) * r;
			if (rounded > due && rounded <= due * 2 && !result.includes(rounded)) {
				result.push(rounded);
				if (result.length >= 4) break;
			}
		}
		return result;
	}

	// ───────────────────────────────────────── Live totals ──

	_update_totals() {
		this._refresh_totals_block();
		if (this._is_finance_sale_type(PosState.sale_type) && !this._is_free_sale) {
			this._sync_finance_payments();
		}
		const grand = this._is_free_sale ? 0 : this._calc_grand_total();
		this._rebalance_payments_after_total_change(this._last_grand_total, grand);
		this._last_grand_total = grand;
		const finance_amount = this._payments.reduce((sum, payment) => {
			return sum + (this._mop_type(payment.mode) === "finance" ? flt(payment.amount) : 0);
		}, 0);

		// Sync loyalty amount — clamp to current payable grand
		if (this._redeem_loyalty && this._loyalty_amount > grand) {
			this._loyalty_amount = grand;
			this._overlay.find("#ch-pay-loyalty-amt").val(grand.toFixed(2));
		}
		// Dynamic max label: min(points_value, grand)
		const _loy_pts_max = flt(PosState.loyalty_points) * flt(PosState.conversion_factor || 0);
		this._overlay.find("#ch-pay-loyalty-max").text(`${__("max")} ₹${format_number(Math.min(_loy_pts_max, grand))}`);

		const loyalty    = this._redeem_loyalty ? Math.min(this._loyalty_amount, grand) : 0;
		const advance    = Math.min(this._advance_amount, Math.max(0, grand - loyalty));
		const net_due    = Math.max(0, grand - loyalty - advance);
		// In Finance mode: cash_paid for display = only down payment rows (not Finance row)
		const is_finance_mode = this._is_finance_sale_type(PosState.sale_type);
		const cash_paid  = this._is_free_sale ? 0 : this._payments.reduce((s, p) => {
			if (is_finance_mode && this._mop_type(p.mode) === "finance") return s;
			return s + flt(p.amount);
		}, 0);
		const total_paid = cash_paid + loyalty + advance;
		const balance    = Math.max(0, grand - total_paid - finance_amount);
		const change     = Math.max(0, total_paid - (grand - finance_amount));
		const refs_valid = this._validate_payment_refs();

		// Ready logic:
		// - Free sale: reason + all category managers approved required
		// - Credit sale: balance can be > 0, just need refs valid
		// - Normal: balance <= 0 + refs valid
		let ready = false;
		if (this._is_free_sale) {
			ready = !!(this._free_sale_reason && this._free_sale_approved_by);
		} else if (this._is_credit_sale || this._is_finance_sale_type(PosState.sale_type)) {
			ready = refs_valid && cash_paid >= 0;
		} else {
			ready = balance <= 0.005 && refs_valid;
		}

		this._overlay.find("#ch-pay-amount-due").text(`₹${format_number(Math.max(0, net_due))}`);
		// "Total Paid" shows only actual tender (cash/card/UPI/finance).
		// Loyalty is already shown as a red deduction in the bill totals on the left.
		this._overlay.find("#ch-pay-total-paid").text(`₹${format_number(cash_paid + advance)}`);
		if (loyalty > 0) {
			this._overlay.find("#ch-pay-loyalty-bal-row").show();
			this._overlay.find("#ch-pay-loyalty-applied").text(`₹${format_number(loyalty)}`);
		} else {
			this._overlay.find("#ch-pay-loyalty-bal-row").hide();
		}
		if (advance > 0) {
			this._overlay.find("#ch-pay-advance-bal-row").show();
			this._overlay.find("#ch-pay-advance-applied").text(`₹${format_number(advance)}`);
		} else {
			this._overlay.find("#ch-pay-advance-bal-row").hide();
		}

		// Balance label changes for credit sale
		const $bal = this._overlay.find("#ch-pay-balance-due");
		const $label = this._overlay.find("#ch-pay-balance-label");
		if (this._is_finance_sale_type(PosState.sale_type) && finance_amount > 0.005) {
			$label.text(__("Financed Amount"));
			$bal.text(`₹${format_number(finance_amount)}`)
				.removeClass("ch-pay-bal-positive ch-pay-bal-zero")
				.addClass("ch-pay-bal-credit");
		} else if (this._is_credit_sale && balance > 0.005) {
			$label.text(__("Credit Amount"));
			$bal.text(`₹${format_number(balance)}`)
				.removeClass("ch-pay-bal-positive ch-pay-bal-zero")
				.addClass("ch-pay-bal-credit");
		} else if (this._is_free_sale) {
			$label.text(__("Balance Due"));
			$bal.text("₹0").removeClass("ch-pay-bal-positive ch-pay-bal-credit").addClass("ch-pay-bal-zero");
		} else {
			$label.text(balance > 0 ? __("Remaining") : __("Paid in Full"));
			$bal.text(`₹${format_number(balance)}`)
				.removeClass("ch-pay-bal-positive ch-pay-bal-zero ch-pay-bal-credit")
				.addClass(balance > 0 ? "ch-pay-bal-positive" : "ch-pay-bal-zero");
		}

		const $cr = this._overlay.find("#ch-pay-change-row");
		// Finance sales: don't show "Change to Return" — balance is handled by finance company
		if (change > 0.005 && !this._is_free_sale && !this._is_finance_sale_type(PosState.sale_type)) {
			$cr.show();
			this._overlay.find("#ch-pay-change").text(`₹${format_number(change)}`);
		} else {
			$cr.hide();
		}

		// Submit button label & state
		const $btn = this._overlay.find("#ch-pay-submit");
		const $lbl = this._overlay.find("#ch-pay-submit-label");
		$btn.prop("disabled", !ready || this._submitting)
			.toggleClass("btn-success", ready)
			.toggleClass("btn-default", !ready);

		if (this._is_free_sale) {
			$lbl.text(__("Confirm Free Sale"));
		} else if (this._is_finance_sale_type(PosState.sale_type)) {
			$lbl.text(finance_amount > 0.005 ? __("Confirm Finance Sale — ₹{0} financed", [format_number(finance_amount)]) : __("Confirm Finance Sale"));
		} else if (this._is_credit_sale) {
			$lbl.text(balance > 0.005 ? __("Confirm Credit Sale — ₹{0} on credit", [format_number(balance)]) : __("Confirm Payment"));
		} else {
			$lbl.text(__("Confirm Payment"));
		}

		// Refresh quick cash buttons if cash row present
		const has_cash = this._payments.some(p => this._mop_type(p.mode) === "cash");
		if (has_cash && !this._is_free_sale) this._render_quick_cash();

		// Lock commercial controls once a gateway payment has been initiated
		const gateway_active = this._payments.some(p => p.gateway_initiated);
		const $commercials = this._overlay.find("#ch-pay-commercials");
		if (gateway_active) {
			$commercials.find("input, select").prop("disabled", true);
			$commercials.find(".ch-pay-section-hdr").prop("disabled", true);
			if (!$commercials.find(".ch-pay-gateway-lock-msg").length) {
				$commercials.prepend(`<div class="ch-pay-gateway-lock-msg"><i class="fa fa-lock"></i> ${__("Payment initiated — discounts locked")}</div>`);
			}
		} else {
			$commercials.find("input, select").prop("disabled", false);
			$commercials.find(".ch-pay-section-hdr").prop("disabled", false);
			$commercials.find(".ch-pay-gateway-lock-msg").remove();
		}
	}

	_calc_balance_due() {
		const grand   = this._is_free_sale ? 0 : this._calc_grand_total();
		const loyalty = this._redeem_loyalty ? Math.min(this._loyalty_amount, grand) : 0;
		const advance = Math.min(this._advance_amount, Math.max(0, grand - loyalty));
		const net_payable = Math.max(0, grand - loyalty - advance);
		if (this._is_finance_sale_type(PosState.sale_type)) {
			// In Finance mode: balance = what's left after non-finance down payment rows
			// (Finance row itself auto-covers the remainder — don't count it as "paid")
			const down_paid = this._payments.reduce((s, p) => {
				return this._mop_type(p.mode) === "finance" ? s : s + flt(p.amount);
			}, 0);
			return Math.max(0, net_payable - down_paid);
		}
		const paid = this._payments.reduce((s, p) => s + flt(p.amount), 0);
		return Math.max(0, grand - loyalty - advance - paid);
	}

	// ───────────────────────────────────────── Bank offers ──

	_load_bank_offers(mop) {
		if (!mop) return;
		const cart_total = this._calc_grand_total();
		frappe.xcall("ch_pos.api.offers.get_applicable_offers", {
			cart_total,
			payment_mode: mop,
		}).then(offers => {
			const container = this._overlay?.find("#ch-pay-bank-offers");
			if (!container) return;
			if (!offers || !offers.length) { container.empty(); return; }
			const active = this._bank_offer ? this._bank_offer.name : null;
			container.html(
				`<div class="ch-pay-offers-header"><i class="fa fa-tag"></i> ${__("Offers for")} <b>${frappe.utils.escape_html(mop)}</b></div>` +
				`<div class="ch-pay-offer-chips-wrap">` +
				offers.map(o => {
					const applied = (active === o.name) ? " ch-pay-offer-chip-active" : "";
					const fullLabel = o.offer_name + (o.conditions_text ? " \u2014 " + o.conditions_text : "");
					return `<div class="ch-pay-offer-chip${applied}" data-offer-name="${frappe.utils.escape_html(o.name)}" data-value-type="${frappe.utils.escape_html(o.value_type || "")}" data-value="${flt(o.value)}" title="${frappe.utils.escape_html(fullLabel)}">
						🏦 ${frappe.utils.escape_html(o.offer_name)}${active === o.name ? ` <span class="ch-pay-offer-applied">✓</span>` : ""}
					</div>`;
				}).join("") +
				`</div>`
			);
		}).catch(e => { console.error("Bank offers load failed:", e); });
	}

	// ───────────────────────────────────────── Helpers ──

	/** Validate that all payment reference fields are filled */
	_validate_payment_refs() {
		let all_valid = true;
		for (let i = 0; i < this._payments.length; i++) {
			const p = this._payments[i];
			const type = this._mop_type(p.mode);
			const has_amount = flt(p.amount) > 0;

			if (type === "upi" && has_amount) {
				const valid = !!(p.upi_transaction_id || "").trim();
				this._overlay?.find(`.ch-pay-row-utr[data-idx="${i}"]`)
					.toggleClass("ch-pay-ref-invalid", !valid)
					.toggleClass("ch-pay-ref-valid", valid);
				if (!valid) all_valid = false;
			}
			if (type === "card" && has_amount) {
				const valid = !!(p.card_reference || "").trim();
				this._overlay?.find(`.ch-pay-row-rrn[data-idx="${i}"]`)
					.toggleClass("ch-pay-ref-invalid", !valid)
					.toggleClass("ch-pay-ref-valid", valid);
				if (!valid) all_valid = false;
			}
			if (type === "finance" && has_amount) {
				const prov_valid = !!(p.finance_provider || "").trim();
				const tenure_valid = !!(p.finance_tenure);
				const appr_valid = !!(p.finance_approval_id || "").trim();
				this._overlay?.find(`.ch-pay-row-fin-provider[data-idx="${i}"]`)
					.toggleClass("ch-pay-ref-invalid", !prov_valid)
					.toggleClass("ch-pay-ref-valid", prov_valid);
				this._overlay?.find(`.ch-pay-row-fin-tenure[data-idx="${i}"]`)
					.toggleClass("ch-pay-ref-invalid", !tenure_valid)
					.toggleClass("ch-pay-ref-valid", tenure_valid);
				this._overlay?.find(`.ch-pay-row-fin-approval[data-idx="${i}"]`)
					.toggleClass("ch-pay-ref-invalid", !appr_valid)
					.toggleClass("ch-pay-ref-valid", appr_valid);
				if (!prov_valid || !tenure_valid || !appr_valid) all_valid = false;
			}
		}
		return all_valid;
	}

	_mop_type(mop_name) {
		const lc = (mop_name || "").toLowerCase();
		if (lc.includes("upi") || lc.includes("gpay") || lc.includes("phonepe") || lc.includes("paytm")) return "upi";
		if (lc.includes("card") || lc.includes("debit") || lc.includes("edc")) return "card";
		if (lc.includes("finance") || lc.includes("emi") || lc.includes("bajaj") || lc.includes("hdfc") || lc.includes("tvs")) return "finance";
		if (lc.includes("cash")) return "cash";
		return "other";
	}

	_close() {
		clearTimeout(this._auto_timer);
		clearInterval(this._approval_poll);
		$(document).off("keydown.ch_pay_overlay");

		// Save payment state to PosState so it persists when Back is clicked
		PosState._payment_state = {
			payments: this._payments,
			loyalty_amount: this._loyalty_amount,
			redeem_loyalty: this._redeem_loyalty,
			is_credit_sale: this._is_credit_sale,
			credit_days: this._credit_days,
			credit_terms: this._credit_terms,
			credit_interest_rate: this._credit_interest_rate,
			credit_grace_period: this._credit_grace_period,
			credit_partial_payment: this._credit_partial_payment,
			credit_approved_by: this._credit_approved_by,
			is_free_sale: this._is_free_sale,
			free_sale_reason: this._free_sale_reason,
			free_sale_approved_by: this._free_sale_approved_by,
			free_sale_approved_at: this._free_sale_approved_at,
			free_sale_approval_name: this._free_sale_approval_name,
			required_managers: this._required_managers,
			advance_amount: this._advance_amount,
			customer_advances: this._customer_advances,
			disc_pct:    this._disc_pct,
			disc_amt:    this._disc_amt,
			disc_reason: this._disc_reason,
			dlg_coupon_code:     this._dlg_coupon_code,
			dlg_coupon_discount: this._dlg_coupon_discount,
			dlg_voucher_code:    this._dlg_voucher_code,
			dlg_voucher_amount:  this._dlg_voucher_amount,
			dlg_voucher_name:    this._dlg_voucher_name,
			dlg_voucher_balance: this._dlg_voucher_balance,
		};

		if (this._overlay) {
			this._overlay.removeClass("ch-pay-visible");
			setTimeout(() => { this._overlay?.remove(); this._overlay = null; }, 280);
		}
	}

	// ───────────────────────────────────────── Invoice submit ──

	_submit_invoice() {
		if (this._submitting) return;
		// POS-19 fix: Set submitting flag immediately to prevent double-submit race
		this._submitting = true;

		const grand   = this._is_free_sale ? 0 : this._calc_grand_total();
		const loyalty = this._redeem_loyalty ? Math.min(this._loyalty_amount, grand) : 0;
		const advance = Math.min(this._advance_amount, Math.max(0, grand - loyalty));
		const balance = this._calc_balance_due();

		// Free sale validations
		if (this._is_free_sale) {
			if (!this._free_sale_reason) {
				frappe.show_alert({ message: __("Enter reason for free sale"), indicator: "orange" });
				this._submitting = false;
				return;
			}
			if (!this._free_sale_approved_by) {
				frappe.show_alert({ message: __("Enter manager name who approved this free sale"), indicator: "orange" });
				this._submitting = false;
				return;
			}
		}

		// Normal/credit sale validations
		if (!this._is_free_sale && !this._is_credit_sale && !this._is_finance_sale_type(PosState.sale_type) && balance > 0.005) {
			frappe.show_alert({ message: __("Payment not complete — ₹{0} still due", [format_number(balance)]), indicator: "red" });
			this._submitting = false;
			return;
		}

		// Validate UPI, Card, and Finance references (mandatory for non-zero amounts)
		if (!this._is_free_sale) {
			for (const p of this._payments) {
				const type = this._mop_type(p.mode);
				if (type === "upi" && flt(p.amount) > 0 && !p.upi_transaction_id) {
					frappe.show_alert({ message: __("Enter UPI Transaction ID for {0}", [p.mode]), indicator: "orange" });
					this._submitting = false;
					return;
				}
				if (type === "card" && flt(p.amount) > 0 && !p.card_reference) {
					frappe.show_alert({ message: __("Enter Card RRN for {0}", [p.mode]), indicator: "orange" });
					this._submitting = false;
					return;
				}
				if (type === "finance" && flt(p.amount) > 0) {
					if (!p.finance_provider) {
						frappe.show_alert({ message: __("Enter Finance Provider for {0}", [p.mode]), indicator: "orange" });
						this._submitting = false;
						return;
					}
					if (!p.finance_tenure) {
						frappe.show_alert({ message: __("Select EMI Tenure for {0}", [p.mode]), indicator: "orange" });
						this._submitting = false;
						return;
					}
					if (!p.finance_approval_id) {
						frappe.show_alert({ message: __("Enter Approval/Loan ID for {0}", [p.mode]), indicator: "orange" });
						this._submitting = false;
						return;
					}
				}
			}
		}

		// Credit Sale validations
		if (this._is_credit_sale) {
			if (!this._credit_approved) {
				frappe.show_alert({ message: __("Manager approval required — credit limit exceeded or overdue invoices"), indicator: "orange" });
				this._submitting = false;
				return;
			}
		}

		// Finance Sale type validations
		if (!this._is_free_sale && this._is_finance_sale_type(PosState.sale_type)) {
			if (!PosState.sale_sub_type) {
				frappe.show_alert({ message: __("Select a Finance Partner"), indicator: "orange" });
				this._submitting = false;
				return;
			}
			if (!PosState.finance_tenure) {
				frappe.show_alert({ message: __("Select EMI Tenure"), indicator: "orange" });
				this._submitting = false;
				return;
			}
			if (!PosState.sale_reference) {
				frappe.show_alert({ message: __("Enter Approval / Loan ID"), indicator: "orange" });
				this._submitting = false;
				return;
			}
		}

		this._overlay.find("#ch-pay-submit").prop("disabled", true);
		this._overlay.find("#ch-pay-submit-label").text(__("Processing..."));

		const items = PosState.cart.map(c => ({
			item_code:        c.item_code,
			item_name:        c.item_name,
			qty:              c.qty,
			rate:             c.rate,
			uom:              c.uom,
			discount_amount:  c.discount_amount || 0,
			warranty_plan:    c.warranty_plan || null,
			for_item_code:    c.for_item_code || null,
			for_serial_no:    c.for_serial_no || null,
			is_warranty:      c.is_warranty || false,
			is_vas:           c.is_vas || false,
			manager_approved: c.manager_approved || false,
			manager_user:     c.manager_user || null,
			override_reason:  c.override_reason || null,
			serial_no:        c.serial_no || null,
		}));

		const payments = this._is_free_sale ? [] : this._payments.map(p => ({
			mode_of_payment:      p.mode,
			amount:               flt(p.amount),
			upi_transaction_id:   p.upi_transaction_id || "",
			card_reference:       p.card_reference || "",
			card_last_four:       p.card_last_four || "",
			finance_provider:     p.finance_provider || "",
			finance_tenure:       p.finance_tenure || "",
			finance_approval_id:  p.finance_approval_id || "",
			finance_down_payment: flt(p.finance_down_payment) || 0,
			gateway_provider:     p.gateway_provider || "",
			payment_machine:      p.payment_machine || "",
			gateway_order_id:     p.gateway_order_id || "",
			gateway_status:       p.gateway_status || "",
		}));

		const invoice_data = {
			pos_profile:                    PosState.pos_profile,
			customer:                       PosState.customer,
			items,
			payments,
			exchange_assessment:            PosState.exchange_assessment || null,
			additional_discount_percentage: PosState.additional_discount_pct || 0,
			additional_discount_amount:     PosState.additional_discount_amt || PosState.coupon_discount || 0,
			coupon_code:                    PosState.coupon_code || null,
			voucher_code:                   PosState.voucher_code || null,
			voucher_amount:                 PosState.voucher_amount || 0,
			redeem_loyalty_points:          this._redeem_loyalty ? 1 : 0,
			loyalty_points:                 this._redeem_loyalty ? cint(loyalty / (PosState.conversion_factor || 1)) : 0,
			loyalty_amount:                 this._redeem_loyalty ? loyalty : 0,
			bank_offer_discount:            flt(this._bank_offer ? this._bank_offer.discount : 0),
			bank_offer_name:                this._bank_offer ? this._bank_offer.name : null,
			sales_executive:                PosState.sales_executive || null,
			sale_type:                      this._is_free_sale ? "Free Sale" : (PosState.sale_type || null),
			sale_sub_type:                  PosState.sale_sub_type || null,
			sale_reference:                 PosState.sale_reference || null,
			finance_tenure:                 PosState.finance_tenure || null,
			discount_reason:                PosState.discount_reason || null,
			client_request_id:              this._gen_uuid(),
			// New payment type fields
			is_credit_sale:                 this._is_credit_sale ? 1 : 0,
			credit_days:                    this._is_credit_sale ? this._credit_days : 0,
			credit_terms:                   this._is_credit_sale ? (this._credit_terms || "Custom") : "",
			credit_interest_rate:           this._is_credit_sale ? (this._credit_interest_rate || 0) : 0,
			credit_grace_period:            this._is_credit_sale ? (this._credit_grace_period || 0) : 0,
			credit_partial_payment:         this._is_credit_sale ? (this._credit_partial_payment || 0) : 0,
			credit_approved_by:             this._is_credit_sale ? (this._credit_approved_by || "") : "",
			credit_reference:               this._is_credit_sale ? this._credit_reference : "",
			credit_notes:                   this._is_credit_sale ? this._credit_notes : "",
			is_free_sale:                   this._is_free_sale ? 1 : 0,
			free_sale_reason:               this._is_free_sale ? this._free_sale_reason : "",
			free_sale_approved_by:          this._is_free_sale ? this._free_sale_approved_by : "",
			free_sale_approved_at:          this._is_free_sale ? (this._free_sale_approved_at || null) : null,
			free_sale_approval_name:        this._is_free_sale ? (this._free_sale_approval_name || "") : "",
			advance_amount:                 advance > 0 ? advance : 0,
			kiosk_token:                    PosState.kiosk_token || null,
			guided_session:                 PosState.guided_session || null,
			exception_request:              PosState.exception_request || null,
			warranty_claim:                 PosState.warranty_claim || null,
			customer_gstin:                 PosState.billing_gstin || "",
		};

		if (!navigator.onLine) {
			EventBus.emit("sync:queue_invoice", {
				data: invoice_data,
				callback: () => {
					frappe.show_alert({ message: __("Invoice queued — will sync when online"), indicator: "blue" });
					this._close();
					PosState.reset_transaction();
				},
			});
			return;
		}

		const do_create = () => {
			frappe.call({
				method: "ch_pos.api.pos_api.create_pos_invoice",
				args:   invoice_data,
				callback: r => {
					if (r.message && r.message.name) {
						this._show_success(r.message);
					} else {
						this._on_error(__("Invoice creation failed"));
					}
				},
				error: (xhr) => {
					const resp = xhr && xhr.responseJSON;
					const server_msg = resp && (resp.message || resp.exc_type);
					this._on_error(server_msg ? frappe.utils.strip_html(server_msg) : __("Invoice creation failed"));
				},
			});
		};

		if (PosState.product_exchange_invoice && (PosState.return_items || []).length) {
			frappe.call({
				method: "ch_pos.api.pos_api.create_pos_return",
				args: {
					original_invoice: PosState.product_exchange_invoice,
					return_items:     PosState.return_items,
					sales_executive:  PosState.sales_executive || null,
				},
				callback: r => {
					if (r.message) {
						frappe.show_alert({ message: __("Return {0} processed", [r.message.name]), indicator: "blue" });
						do_create();
					} else {
						this._on_error(__("Return creation failed"));
					}
				},
				error: (xhr) => {
					const resp = xhr && xhr.responseJSON;
					const server_msg = resp && (resp.message || resp.exc_type);
					const msg = server_msg
						? frappe.utils.strip_html(server_msg)
						: __("Return creation failed");
					// Show as a dialog so it is clearly visible above the payment popup
					frappe.msgprint({ title: __("Return Failed"), indicator: "red", message: msg });
					this._on_error(msg);
				},
			});
		} else {
			do_create();
		}
	}

	// ───────────────────────────────────────── Success screen ──

	_show_success(result) {
		this._submitting = false;
		if (!this._overlay) return;

		const inv_name  = result.name;
		const grand     = this._calc_grand_total();
		const incentive = flt(result.incentive_earned || 0);
		const vouchers  = result.generated_vouchers || [];
		const change    = this._payments.reduce((s, p) => s + flt(p.amount), 0) +
			(this._redeem_loyalty ? Math.min(this._loyalty_amount, grand) : 0) - grand;

		// Auto-clear countdown (5 seconds)
		let countdown = 5;

		this._overlay.find(".ch-pay-screen").html(`
			<div class="ch-pay-success">
				<div class="ch-pay-success-icon"><i class="fa fa-check-circle"></i></div>
				<div class="ch-pay-success-title">${__("Payment Received")}</div>
				<div class="ch-pay-success-inv">${frappe.utils.escape_html(inv_name)}</div>
				<div class="ch-pay-success-amount">₹${format_number(grand)}</div>
				${change > 0.005 ? `<div class="ch-pay-success-change"><i class="fa fa-money"></i> ${__("Change to return")}: <b>₹${format_number(change)}</b></div>` : ""}
				${incentive > 0 ? `<div class="ch-pay-success-meta"><i class="fa fa-star text-warning"></i> ₹${format_number(incentive)} ${__("incentive earned")}</div>` : ""}
				${vouchers.length ? `<div class="ch-pay-success-meta"><i class="fa fa-gift text-primary"></i> ${vouchers.length} ${__("VAS voucher(s) sent to customer")}</div>` : ""}
				<div class="ch-pay-success-actions">
					<button class="btn btn-default ch-pay-print-btn" data-name="${frappe.utils.escape_html(inv_name)}">
						<i class="fa fa-print"></i> ${__("Print Receipt")}
					</button>
					<button class="btn btn-success ch-pay-next-btn">
						<i class="fa fa-shopping-basket"></i>
						<span class="ch-pay-next-label">${__("Next Sale")}</span>
						<span class="ch-pay-countdown-badge">${countdown}</span>
					</button>
				</div>
				<div class="ch-pay-auto-note text-muted">
					<i class="fa fa-clock-o"></i> ${__("Cart clears automatically in")} <span id="ch-pay-cd-sec">${countdown}</span>s
				</div>
			</div>`);

		// Auto-clear timer
		const tick = () => {
			countdown--;
			if (!this._overlay) return;
			this._overlay.find("#ch-pay-cd-sec").text(countdown);
			this._overlay.find(".ch-pay-countdown-badge").text(countdown);
			if (countdown <= 0) {
				this._close();
				PosState.reset_transaction();
			} else {
				this._auto_timer = setTimeout(tick, 1000);
			}
		};
		this._auto_timer = setTimeout(tick, 1000);

		// Manual "Next Sale" cancels timer and proceeds immediately
		this._overlay.on("click", ".ch-pay-next-btn", () => {
			clearTimeout(this._auto_timer);
			this._close();
			PosState.reset_transaction();
		});

		// Print resets/pauses countdown but does not close
		this._overlay.on("click", ".ch-pay-print-btn", e => {
			clearTimeout(this._auto_timer);
			countdown = 10; // extend after print
			this._overlay.find("#ch-pay-cd-sec").text(countdown);
			this._overlay.find(".ch-pay-countdown-badge").text(countdown);
			this._auto_timer = setTimeout(tick, 1000);
			const name = $(e.currentTarget).data("name");
			// Check if this is a GoFix invoice by looking up the SR link
			frappe.xcall("frappe.client.get_value", {
				doctype: "Sales Invoice", filters: name,
				fieldname: "custom_gofix_service_request"
			}).then(r => {
				const fmt = r && r.custom_gofix_service_request ? "GoFix Service Invoice" : "Custom Sales Invoice";
				const url = `/printview?doctype=Sales%20Invoice&name=${encodeURIComponent(name)}&format=${encodeURIComponent(fmt)}&no_letterhead=1`;
				window.open(url, "_blank");
			});
		});
	}

	_on_error(msg) {
		this._submitting = false;
		frappe.show_alert({ message: msg, indicator: "red" });
		if (this._overlay) {
			this._overlay.find("#ch-pay-submit").prop("disabled", false);
			this._overlay.find("#ch-pay-submit-label").text(__("Confirm Payment"));
		}
	}

	_gen_uuid() {
		return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, c => {
			const r = Math.random() * 16 | 0;
			return (c === "x" ? r : (r & 0x3 | 0x8)).toString(16);
		});
	}

	_calc_grand_total_before_offer() {
		let subtotal = 0, disc_total = 0;
		PosState.cart.forEach(c => {
			subtotal   += flt(c.qty) * flt(c.rate);
			disc_total += flt(c.discount_amount || 0) * flt(c.qty);
		});
		let net = subtotal - disc_total;
		if (PosState.additional_discount_pct) net -= net * PosState.additional_discount_pct / 100;
		else if (PosState.additional_discount_amt) net -= flt(PosState.additional_discount_amt);
		net -= flt(PosState.coupon_discount);
		net -= flt(PosState.voucher_amount);
		net -= flt(PosState.exchange_amount);
		net -= flt(PosState.product_exchange_credit);
		return Math.max(0, net);
	}

	_calc_grand_total() {
		let net = this._calc_grand_total_before_offer();
		net -= flt(this._bank_offer ? this._bank_offer.discount : 0);
		return Math.max(0, net);
	}

	// ─────────────────────────────────── Customer advance lookup ──

	_load_customer_advances() {
		if (!PosState.customer || PosState.customer === "Walk-in Customer") return;
		frappe.xcall("ch_pos.api.pos_api.get_customer_advances", {
			customer: PosState.customer,
		}).then(advances => {
			this._customer_advances = advances || [];
			if (this._customer_advances.length > 0 && this._overlay) {
				this._overlay.find("#ch-pay-advance-section").show();
				const list = this._overlay.find("#ch-pay-advance-list");
				list.empty();
				this._customer_advances.forEach(adv => {
					list.append(`
						<div class="ch-pay-advance-item" data-advance="${frappe.utils.escape_html(adv.name)}" data-balance="${flt(adv.balance)}">
							<label class="ch-pay-advance-lbl">
								<input type="checkbox" class="ch-pay-advance-chk">
								<span><b>${frappe.utils.escape_html(adv.name)}</b> — ₹${format_number(adv.balance)} available</span>
							</label>
							<span class="ch-pay-advance-date">${frappe.utils.escape_html(adv.posting_date || "")}</span>
						</div>`);
				});
			}
		}).catch(e => { console.error("Advance payments load failed:", e); });
	}

	_load_credit_info() {
		if (!PosState.customer || PosState.customer === "Walk-in Customer") return;
		frappe.xcall("ch_pos.api.pos_api.get_customer_credit_info", {
			customer: PosState.customer,
			company: PosState.company,
		}).then(info => {
			if (!this._overlay) return;
			const $info = this._overlay.find("#ch-pay-credit-info");
			const $history = this._overlay.find("#ch-pay-credit-history");
			const $approval = this._overlay.find("#ch-pay-credit-approval");
			if (!info) {
				$info.html(`<div class="text-muted">${__("No credit limit configured for this customer")}</div>`);
				$history.hide();
				$approval.hide();
				return;
			}
			const limit = flt(info.credit_limit);
			const outstanding = flt(info.outstanding);
			const available = Math.max(0, limit - outstanding);
			const cart_total = this._calc_grand_total();
			const over_limit = cart_total > available;
			const overdue_count = cint(info.overdue_count || 0);
			const avg_days = cint(info.avg_payment_days || 0);
			const last_payment = info.last_payment_date || "—";

			// Credit info summary with utilization bar
			const utilization_pct = limit > 0 ? Math.min(100, Math.round((outstanding / limit) * 100)) : 0;
			const bar_color = utilization_pct > 80 ? "#dc2626" : utilization_pct > 50 ? "#f59e0b" : "#16a34a";

			$info.html(`
				<div class="ch-pay-credit-stat-grid">
					<div class="ch-pay-credit-stat">
						<span>${__("Credit Limit")}</span>
						<b>₹${format_number(limit)}</b>
					</div>
					<div class="ch-pay-credit-stat">
						<span>${__("Outstanding")}</span>
						<b style="color:#dc2626">₹${format_number(outstanding)}</b>
					</div>
					<div class="ch-pay-credit-stat">
						<span>${__("Available")}</span>
						<b style="color:${over_limit ? "#dc2626" : "#16a34a"}">₹${format_number(available)}</b>
					</div>
					<div class="ch-pay-credit-stat">
						<span>${__("This Sale")}</span>
						<b>₹${format_number(cart_total)}</b>
					</div>
				</div>
				<div class="ch-pay-credit-bar-wrap">
					<div class="ch-pay-credit-bar" style="width:${utilization_pct}%;background:${bar_color}"></div>
				</div>
				<div style="display:flex;justify-content:space-between;font-size:11px;color:#6b7280;margin-top:2px">
					<span>${__("Utilization")}: ${utilization_pct}%</span>
					<span>${over_limit ? `<span style="color:#dc2626;font-weight:600"><i class="fa fa-exclamation-triangle"></i> ${__("Over Limit by")} ₹${format_number(cart_total - available)}</span>` : `<span style="color:#16a34a">${__("Within Limit")}</span>`}</span>
				</div>
			`);

			// Payment history
			$history.show().html(`
				<div class="ch-pay-credit-history-grid">
					<div class="ch-pay-credit-history-item">
						<i class="fa fa-clock-o" style="color:${overdue_count > 0 ? "#dc2626" : "#16a34a"}"></i>
						<div>
							<div class="ch-pay-credit-history-label">${__("Overdue Invoices")}</div>
							<div class="ch-pay-credit-history-val" style="color:${overdue_count > 0 ? "#dc2626" : "#1a1a2e"}">${overdue_count}</div>
						</div>
					</div>
					<div class="ch-pay-credit-history-item">
						<i class="fa fa-calendar-check-o" style="color:#6366f1"></i>
						<div>
							<div class="ch-pay-credit-history-label">${__("Avg. Payment Days")}</div>
							<div class="ch-pay-credit-history-val">${avg_days || "—"}</div>
						</div>
					</div>
					<div class="ch-pay-credit-history-item">
						<i class="fa fa-calendar" style="color:#0ea5e9"></i>
						<div>
							<div class="ch-pay-credit-history-label">${__("Last Payment")}</div>
							<div class="ch-pay-credit-history-val">${last_payment}</div>
						</div>
					</div>
				</div>
			`);

			// Over-limit approval requirement
			this._credit_approved = !over_limit && overdue_count === 0;
			if (over_limit || overdue_count > 0) {
				const reasons = [];
				if (over_limit) reasons.push(__("exceeds available credit by ₹{0}", [format_number(cart_total - available)]));
				if (overdue_count > 0) reasons.push(__("{0} overdue invoice(s)", [overdue_count]));
				$approval.show().html(`
					<div class="ch-pay-credit-approval-warn">
						<i class="fa fa-exclamation-triangle"></i>
						<span>${__("Manager approval required")}: ${reasons.join(", ")}</span>
					</div>
					<button class="btn btn-sm btn-warning" id="ch-pay-credit-override-btn" style="margin-top:6px">
						<i class="fa fa-unlock"></i> ${__("Request Manager Override")}
					</button>
					<div id="ch-pay-credit-override-status" style="margin-top:6px"></div>
				`);
				this._overlay.find("#ch-pay-credit-override-btn").on("click", () => {
					this._request_credit_override(cart_total, available, overdue_count);
				});
			} else {
				$approval.hide();
			}
		}).catch(e => { console.error("Credit info load failed:", e); });
	}

	_update_credit_due_date() {
		if (!this._overlay) return;
		const days = parseInt(this._credit_days) || 30;
		const due = frappe.datetime.add_days(frappe.datetime.nowdate(), days);
		this._overlay.find("#ch-pay-credit-due-date").text(frappe.datetime.str_to_user(due));
		// Payment reminder = due_date - 5 days (min today)
		const today = frappe.datetime.nowdate();
		let reminder = frappe.datetime.add_days(due, -5);
		if (reminder < today) reminder = today;
		this._overlay.find("#ch-pay-credit-reminder").text(frappe.datetime.str_to_user(reminder));
	}

	_request_credit_override(cart_total, available, overdue_count) {
		const btn = this._overlay.find("#ch-pay-credit-override-btn");
		const status_el = this._overlay.find("#ch-pay-credit-override-status");
		btn.prop("disabled", true).html(`<i class="fa fa-spinner fa-spin"></i> ${__("Requesting...")}`);

		const me = this;
		frappe.prompt([
			{
				fieldname: "manager_pin",
				label: __("Manager PIN / Password"),
				fieldtype: "Password",
				reqd: 1,
				description: __("Manager must enter their PIN to approve this credit sale"),
			},
			{
				fieldname: "override_reason",
				label: __("Override Reason"),
				fieldtype: "Small Text",
				reqd: 1,
			},
		], (values) => {
			frappe.xcall("ch_pos.api.pos_api.approve_credit_override", {
				customer: PosState.customer,
				company: PosState.company,
				manager_pin: values.manager_pin,
				override_reason: values.override_reason,
				cart_total: cart_total,
				store: PosState.store,
			}).then(r => {
				if (r && r.approved) {
					me._credit_approved = true;
					// Store approver name for audit trail
					me._credit_approved_by = r.manager_name || "";
					if (me._overlay) {
						me._overlay.find("#ch-pay-credit-approved-by").val(me._credit_approved_by);
					}
					me._credit_notes = (me._credit_notes ? me._credit_notes + "; " : "") +
						`Override: ${values.override_reason} (by ${r.manager_name})`;
					if (me._overlay) {
						me._overlay.find("#ch-pay-credit-notes").val(me._credit_notes);
					}
					status_el.html(`<div class="text-success"><i class="fa fa-check-circle"></i> ${__("Approved by {0}", [r.manager_name])}</div>`);
					btn.hide();
					me._update_totals();
				} else {
					btn.prop("disabled", false).html(`<i class="fa fa-unlock"></i> ${__("Request Manager Override")}`);
					status_el.html(`<div class="text-danger"><i class="fa fa-times-circle"></i> ${r ? r.message : __("Override rejected")}</div>`);
				}
			}).catch(() => {
				btn.prop("disabled", false).html(`<i class="fa fa-unlock"></i> ${__("Request Manager Override")}`);
				status_el.html(`<div class="text-danger">${__("Failed to verify. Try again.")}</div>`);
			});
		}, __("Manager Credit Override"), __("Approve"));
	}

	// ── Category Manager Approval Flow ──────────────────────────

	_load_category_managers() {
		const items = PosState.cart.map(c => ({
			item_code: c.item_code,
			is_warranty: c.is_warranty || false,
			is_vas: c.is_vas || false,
		}));
		const $mgr = this._overlay.find("#ch-pay-free-managers");
		$mgr.html(`<div class="text-muted" style="padding:8px 0"><i class="fa fa-spinner fa-spin"></i> ${__("Loading category managers...")}</div>`);

		frappe.xcall("ch_pos.api.free_sale_api.get_category_managers_for_cart", {
			items: JSON.stringify(items),
		}).then(managers => {
			this._required_managers = managers || [];
			if (!managers || !managers.length) {
				$mgr.html(`<div class="text-warning" style="padding:8px 0"><i class="fa fa-exclamation-triangle"></i> ${__("No category managers assigned. Please set Category Manager in CH Category master.")}</div>`);
				return;
			}
			const rows = managers.map(m => `
				<div class="ch-pay-free-mgr-row" style="display:flex;align-items:center;gap:8px;padding:6px 0;border-bottom:1px solid var(--border-color)">
					<span class="badge" style="background:var(--primary);color:#fff">${frappe.utils.escape_html(m.category)}</span>
					<span style="flex:1">${frappe.utils.escape_html(m.manager_name)}</span>
					<span class="badge badge-warning">⏳ ${__("Pending")}</span>
				</div>
			`).join("");
			$mgr.html(`
				<div class="ch-pay-free-mgr-label" style="font-weight:600;margin-top:8px;margin-bottom:4px">
					${__("Approvals needed from")}:
				</div>
				${rows}
			`);
		}).catch(() => {
			$mgr.html(`<div class="text-danger" style="padding:8px 0"><i class="fa fa-times-circle"></i> ${__("Failed to load category managers")}</div>`);
		});
	}

	_request_free_sale_approval() {
		if (!this._free_sale_reason) {
			frappe.show_alert({ message: __("Enter reason for free sale"), indicator: "orange" });
			return;
		}

		const items = PosState.cart.map(c => ({
			item_code: c.item_code,
			item_name: c.item_name,
			qty: c.qty,
			rate: c.rate,
			is_warranty: c.is_warranty || false,
			is_vas: c.is_vas || false,
			serial_no: c.serial_no || "",
		}));

		this._overlay.find("#ch-pay-free-request-btn")
			.prop("disabled", true)
			.html(`<i class="fa fa-spinner fa-spin"></i> ${__("Sending...")}`);

		frappe.xcall("ch_pos.api.free_sale_api.request_free_sale_approval", {
			reason: this._free_sale_reason,
			customer: PosState.customer,
			items: JSON.stringify(items),
			grand_total: this._calc_grand_total(),
			store: PosState.store,
			company: PosState.company,
		}).then(result => {
			this._free_sale_approval_name = result.approval_name;
			this._overlay.find("#ch-pay-free-request-btn").hide();
			this._overlay.find("#ch-pay-free-reason").prop("readonly", true);

			frappe.show_alert({ message: __("Approval request sent to category managers"), indicator: "blue" });
			this._render_approval_status(result.managers);
			this._start_approval_polling();
		}).catch(err => {
			this._overlay.find("#ch-pay-free-request-btn")
				.prop("disabled", false)
				.html(`<i class="fa fa-paper-plane"></i> ${__("Request Approval")}`);
			frappe.show_alert({ message: err.message || __("Failed to send approval request"), indicator: "red" });
		});
	}

	_render_approval_status(managers) {
		const $status = this._overlay.find("#ch-pay-free-status");
		$status.show();

		const rows = managers.map(m => {
			const icon = m.status === "Approved" ? "✅" : m.status === "Rejected" ? "❌" : "⏳";
			const badge_cls = m.status === "Approved" ? "badge-success" : m.status === "Rejected" ? "badge-danger" : "badge-warning";
			return `
				<div class="ch-pay-free-mgr-row" style="display:flex;align-items:center;gap:8px;padding:6px 0;border-bottom:1px solid var(--border-color)">
					<span class="badge" style="background:var(--primary);color:#fff">${frappe.utils.escape_html(m.category)}</span>
					<span style="flex:1">${frappe.utils.escape_html(m.manager_name)}</span>
					<span class="badge ${badge_cls}">${icon} ${__(m.status)}</span>
				</div>`;
		}).join("");

		const all_approved = managers.every(m => m.status === "Approved");
		const any_rejected = managers.some(m => m.status === "Rejected");

		$status.html(`
			<div style="margin-top:8px;padding:10px;border-radius:6px;background:${any_rejected ? '#fef2f2' : all_approved ? '#f0fdf4' : '#fffbeb'}">
				<div style="font-weight:600;margin-bottom:6px">
					${all_approved ? '✅ ' + __("All managers approved") : any_rejected ? '❌ ' + __("Approval rejected") : '⏳ ' + __("Waiting for approvals...")}
				</div>
				${rows}
			</div>
		`);

		// Update approval state
		if (all_approved) {
			this._free_sale_approved_by = managers.map(m => m.manager_name).join(", ");
			// Capture approval timestamp for audit trail
			this._free_sale_approved_at = frappe.datetime.now_datetime();
			if (this._overlay) {
				const $at = this._overlay.find("#ch-pay-free-approved-at");
				$at.find("#ch-pay-free-approved-at-txt").text(
					__("Approved at {0}", [frappe.datetime.str_to_user(this._free_sale_approved_at)])
				);
				$at.show();
			}
			clearInterval(this._approval_poll);
		}
		this._update_totals();
	}

	_start_approval_polling() {
		clearInterval(this._approval_poll);
		this._approval_poll = setInterval(() => {
			if (!this._free_sale_approval_name || !this._overlay) {
				clearInterval(this._approval_poll);
				return;
			}
			frappe.xcall("ch_pos.api.free_sale_api.check_approval_status", {
				approval_name: this._free_sale_approval_name,
			}).then(result => {
				this._render_approval_status(result.approvals);
			}).catch(e => { console.error("Approval poll failed:", e); });
		}, 5000);
	}

	// ─────────────────────────────────── Legacy single-mode compat ──
	// (kept so that any code still emitting old payment_values format doesn't break)
	_build_summary_html() { return ""; }
}
