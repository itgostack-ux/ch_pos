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

		// Free Sale state
		this._is_free_sale = false;
		this._free_sale_reason = "";
		this._free_sale_approved_by = "";

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
			this._is_free_sale = saved.is_free_sale || false;
			this._free_sale_reason = saved.free_sale_reason || "";
			this._free_sale_approved_by = saved.free_sale_approved_by || "";
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
			this._is_free_sale = false;
			this._free_sale_reason = "";
			this._free_sale_approved_by = "";
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
					<i class="fa fa-handshake-o"></i> ${__("Credit Sale Details")}
				</div>
				<div class="ch-pay-credit-body">
					<div class="ch-pay-credit-info" id="ch-pay-credit-info"></div>
					<div class="ch-pay-credit-field">
						<label>${__("Credit Days")}</label>
						<input type="number" class="form-control form-control-sm" id="ch-pay-credit-days"
							value="30" min="1" max="180" step="1">
					</div>
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
				</div>
			</div>` : "";

		// Advance adjustment section
		const advance_html = !is_walkin ? `
			<div id="ch-pay-advance-section" class="ch-pay-advance-section" style="display:none">
				<div class="ch-pay-advance-header">
					<i class="fa fa-history"></i> ${__("Customer Advances")}
				</div>
				<div id="ch-pay-advance-list" class="ch-pay-advance-list"></div>
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

// ── Discount section (collapsed accordion) ────────────────────────────
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
// ── Coupon / Voucher section (collapsed accordion) ─────────────────────
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


		return `
		<div id="ch-pos-payment-overlay" class="ch-pay-overlay">
			<div class="ch-pay-screen">
				<!-- ── LEFT: Bill Summary ───────────────────────────────── -->
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
				</div>

				<!-- ── RIGHT: Payment Panel ─────────────────────────────── -->
				<div class="ch-pay-right">
					<div class="ch-pay-right-header">
						<span class="ch-pay-right-label">${__("Amount Due")}</span>
						<span class="ch-pay-grand-display" id="ch-pay-amount-due">
							₹${format_number(total)}
						</span>
					</div>

					<!-- ─ Pinned top: Sale type + MOP buttons + bank offers ─ -->
					<div class="ch-pay-right-pinned">

						<!-- Sale mode toggles (Credit / Free) -->
						${sale_modes_html}

						<!-- Credit sale details -->
						${credit_html}

						<!-- Free sale details -->
						${free_html}

						<!-- Sale type selector -->
						${sale_type_html}

						<!-- MOP quick-add buttons -->
						<div class="ch-pay-mop-section" id="ch-pay-mop-section">
							<div class="ch-pay-mop-label">${__("Add Payment")}</div>
							<div class="ch-pay-mop-btns">${mop_btns}</div>
						</div>

						<!-- Bank / card offers (loaded dynamically) -->
						<div id="ch-pay-bank-offers" class="ch-pay-bank-offers"></div>

					</div><!-- /.ch-pay-right-pinned -->

					<!-- ─ Scrollable: payment rows + loyalty + quick cash ─ -->
					<div class="ch-pay-right-scroll">

						<!-- Payment rows -->
						<div id="ch-pay-rows" class="ch-pay-rows"></div>

						<!-- Loyalty -->
						${loyalty_html}

						<!-- Quick cash amounts -->
						<div id="ch-pay-quick-cash" class="ch-pay-quick-cash" style="display:none">
							<div class="ch-pay-quick-label">${__("Quick Cash")}</div>
							<div id="ch-pay-quick-btns" class="ch-pay-quick-btns"></div>
						</div>

						<!-- Advance adjustment (walk-in hidden) -->
						${advance_html}

					</div><!-- /.ch-pay-right-scroll -->

					<!-- ─ Pinned bottom: Discount + Coupon always visible ─ -->
					<div class="ch-pay-right-discounts">
						${disc_html}
						${coupon_html}
					</div>

					<!-- Balance bar — always visible above submit -->
					<div class="ch-pay-balance-bar">
						<div class="ch-pay-bal-row">
							<span>${__("Total Paid")}</span>
							<b id="ch-pay-total-paid">₹0</b>
						</div>
						<div class="ch-pay-bal-row" id="ch-pay-advance-bal-row" style="display:none">
							<span>${__("Advance Applied")}</span>
							<b id="ch-pay-advance-applied" style="color:#7c3aed">₹0</b>
						</div>
						<div class="ch-pay-bal-row ch-pay-bal-due-row">
							<span id="ch-pay-balance-label">${__("Balance Due")}</span>
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
		rows += `<div class="ch-pay-total-grand"><span>${__("Grand Total")}</span><span>₹${format_number(grand)}</span></div>`;
		rows += `<div class="ch-pay-tax-note"><i class="fa fa-info-circle"></i> ${__("GST auto-applied per POS Profile")}</div>`;
		return rows;
	}

	// ───────────────────────────────────────── Bind overlay ──

	_bind_overlay() {
		const ov = this._overlay;

		ov.on("click", ".ch-pay-close", () => this._close());

		// MOP button → add/top-up payment row
		ov.on("click", ".ch-pay-mop-btn", e => {
			const mop = $(e.currentTarget).data("mop");
			const due = this._calc_balance_due();
			const idx = this._payments.findIndex(p => p.mode === mop);
			if (idx >= 0) {
				this._payments[idx].amount = flt(this._payments[idx].amount) + Math.max(0, due);
			} else {
				this._payments.push({ mode: mop, amount: Math.max(0, due), upi_transaction_id: "", card_reference: "", card_last_four: "", finance_provider: "", finance_tenure: "", finance_approval_id: "", finance_down_payment: 0 });
			}
			this._render_payments();
			this._update_totals();
			this._load_bank_offers(mop);
		});

		// Remove a row
		ov.on("click", ".ch-pay-row-remove", e => {
			const idx = parseInt($(e.currentTarget).data("idx"));
			this._payments.splice(idx, 1);
			this._render_payments();
			this._update_totals();
		});

		// Amount edit
		ov.on("input", ".ch-pay-row-amount", e => {
			const idx = parseInt($(e.currentTarget).data("idx"));
			this._payments[idx].amount = flt($(e.currentTarget).val()) || 0;
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
		ov.on("input", ".ch-pay-row-fin-down", e => {
			const idx = parseInt($(e.currentTarget).data("idx"));
			this._payments[idx].finance_down_payment = flt($(e.currentTarget).val()) || 0;
			const down = flt(this._payments[idx].finance_down_payment);
			const cash_idx = this._payments.findIndex((p, i) => i !== idx && p.auto_created_by_finance && this._mop_type(p.mode) === "cash");
			if (down > 0 && cash_idx < 0) {
				this._payments.push({ mode: "Cash", amount: down, upi_transaction_id: "", card_reference: "", card_last_four: "", finance_provider: "", finance_tenure: "", finance_approval_id: "", finance_down_payment: 0, auto_created_by_finance: true });
			} else if (cash_idx >= 0 && down > 0) {
				this._payments[cash_idx].amount = down;
			} else if (cash_idx >= 0 && down <= 0) {
				this._payments.splice(cash_idx, 1);
			}
			this._sync_finance_payments();
			this._render_payments();
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
				this._load_credit_info();
			}
			if (this._is_finance_sale_type(PosState.sale_type)) {
				this._sync_finance_payments();
				this._render_payments();
			}
			this._update_totals();
		});
		ov.on("input", "#ch-pay-credit-days", e => {
			this._credit_days = parseInt($(e.currentTarget).val()) || 30;
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

		// Loyalty
		ov.on("change", "#ch-pay-loyalty-chk", e => {
			this._redeem_loyalty = $(e.currentTarget).is(":checked");
			ov.find("#ch-pay-loyalty-input").toggle(this._redeem_loyalty);
			this._loyalty_amount = this._redeem_loyalty ? flt(ov.find("#ch-pay-loyalty-amt").val()) : 0;
			// Auto-adjust cash row so total_paid stays equal to grand_total (not double)
			this._set_cash_amount(this._calc_balance_due());
			this._update_totals();
		});
		ov.on("input", "#ch-pay-loyalty-amt", e => {
			const max = flt(PosState.loyalty_points) * flt(PosState.conversion_factor);
			const max_usable = Math.min(max, this._calc_grand_total());
			this._loyalty_amount = Math.min(flt($(e.currentTarget).val()) || 0, max_usable);
			// Auto-adjust cash row so total_paid stays equal to grand_total (not double)
			this._set_cash_amount(this._calc_balance_due());
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
				const is_credit = !!(st.triggers_credit_sale ||
					["CS", "FS"].includes((st.code || "").toUpperCase()));

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
		const cash_idx = this._payments.findIndex(p => this._mop_type(p.mode) === "cash");
		if (cash_idx < 0) return;
		this._payments[cash_idx].amount = amt;
		this._overlay.find(`.ch-pay-row-amount[data-idx="${cash_idx}"]`).val(amt.toFixed(2));
		this._update_totals();
	}

	_refresh_totals_block() {
		this._overlay?.find(".ch-pay-totals-block").html(this._build_totals_html());
	}

	_sync_finance_payments() {
		if (!this._is_finance_sale_type(PosState.sale_type) || this._is_free_sale) return;

		const grand = this._calc_grand_total();
		const finance_idx = this._payments.findIndex(p => this._mop_type(p.mode) === "finance");
		const non_finance_paid = this._payments.reduce((sum, payment, idx) => {
			if (idx === finance_idx) return sum;
			return sum + flt(payment.amount);
		}, 0);
		const financed_amount = Math.max(0, grand - non_finance_paid);

		if (finance_idx >= 0) {
			this._payments[finance_idx].amount = financed_amount;
			return;
		}

		if (this._payments.length === 0) {
			this._payments.push({ mode: "Finance", amount: financed_amount, upi_transaction_id: "", card_reference: "", card_last_four: "", finance_provider: "", finance_tenure: "", finance_approval_id: "", finance_down_payment: 0 });
			return;
		}

		if (this._payments.length === 1 && this._mop_type(this._payments[0].mode) === "cash") {
			this._payments[0].mode = "Finance";
			this._payments[0].amount = financed_amount;
		}
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

	/** Sync finance sale type selections into the payment row's finance fields */
	_sync_finance_to_payment(provider, tenure, approval_id) {
		for (let i = 0; i < this._payments.length; i++) {
			const p = this._payments[i];
			// Set on all payment rows (the finance fields will be stored on the primary payment)
			p.finance_provider = provider || "";
			p.finance_tenure = tenure || "";
			p.finance_approval_id = approval_id || "";
		}
		// Re-render if any finance-type payment row has dropdowns
		if (this._payments.some(p => this._mop_type(p.mode) === "finance")) {
			this._render_payments();
		}
		this._update_totals();
	}

	// ───────────────────────────────────── Sale type pills ──

	_load_sale_types() {
		this._sale_types = [];
		frappe.xcall("ch_pos.api.pos_api.get_sale_types", {
			company: PosState.company,
		}).then((types) => {
			this._sale_types = types || [];
			if (this._sale_types.length) {
				this._render_sale_type_pills();
			}
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
			const should_credit = !!(cur.triggers_credit_sale ||
				["CS", "FS"].includes((cur.code || "").toUpperCase()));
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
				ref_html = `<input type="text" class="form-control form-control-sm ch-pay-row-utr mt-1" data-idx="${idx}"
					placeholder="${__("UPI UTR / Txn ID")}" value="${frappe.utils.escape_html(p.upi_transaction_id || "")}">`;
			} else if (type === "card") {
				ref_html = `
					<div class="ch-pay-card-refs mt-1">
						<input type="text" class="form-control form-control-sm ch-pay-row-rrn" data-idx="${idx}"
							placeholder="${__("Card RRN")}" value="${frappe.utils.escape_html(p.card_reference || "")}">
						<input type="text" class="form-control form-control-sm ch-pay-row-card4" data-idx="${idx}"
							placeholder="${__("Last 4 digits")}" maxlength="4" value="${frappe.utils.escape_html(p.card_last_four || "")}">
					</div>`;
			} else if (type === "finance") {
				const partners = this._finance_partners || [];
				const sel_partner = partners.find(fp => fp.partner_name === p.finance_provider);
				const tenures = sel_partner ? sel_partner.tenures : [];
				ref_html = `
					<div class="ch-pay-finance-refs mt-1">
						<div class="ch-pay-fin-row">
							<select class="form-control form-control-sm ch-pay-row-fin-provider" data-idx="${idx}">
								<option value="">${__("Select Finance Partner")}</option>
								${partners.map(fp => `<option value="${frappe.utils.escape_html(fp.partner_name)}" ${fp.partner_name === p.finance_provider ? 'selected' : ''}>${frappe.utils.escape_html(fp.partner_name)}</option>`).join('')}
							</select>
							<select class="form-control form-control-sm ch-pay-row-fin-tenure" data-idx="${idx}">
								<option value="">${__("Select Tenure")}</option>
								${tenures.map(t => `<option value="${t}" ${String(p.finance_tenure) === String(t) ? 'selected' : ''}>${t} Months</option>`).join('')}
							</select>
						</div>
						<div class="ch-pay-fin-row">
							<input type="text" class="form-control form-control-sm ch-pay-row-fin-approval" data-idx="${idx}"
								placeholder="${__("Approval / Loan ID")} *" value="${frappe.utils.escape_html(p.finance_approval_id || "")}">
							<div class="input-group input-group-sm">
								<span class="input-group-addon">₹</span>
								<input type="number" class="form-control ch-pay-row-fin-down" data-idx="${idx}"
									placeholder="${__("Down Payment")}" value="${flt(p.finance_down_payment) || ""}" min="0" step="0.01">
							</div>
						</div>
					</div>`;
			}

			container.append(`
				<div class="ch-pay-row${type === "finance" ? " ch-pay-row-finance" : ""}" data-idx="${idx}">
					<div class="ch-pay-row-header">
						<span class="ch-pay-row-mop-label">${_mop_icon(p.mode)} ${frappe.utils.escape_html(p.mode)}</span>
						${this._payments.length > 1
							? `<button class="ch-pay-row-remove" data-idx="${idx}" title="${__("Remove")}"><i class="fa fa-times-circle"></i></button>`
							: ""}
					</div>
					<div class="input-group ch-pay-row-amt-group">
						<span class="input-group-addon">₹</span>
						<input type="number" class="form-control ch-pay-row-amount" data-idx="${idx}"
							value="${flt(p.amount, 2)}" min="0" step="0.01">
					</div>
					${ref_html}
				</div>`);
		});

		// Show/hide quick cash section
		const has_cash = this._payments.some(p => this._mop_type(p.mode) === "cash");
		this._overlay.find("#ch-pay-quick-cash").toggle(has_cash && !this._is_free_sale);
		if (has_cash && !this._is_free_sale) this._render_quick_cash();
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
		const finance_amount = this._payments.reduce((sum, payment) => {
			return sum + (this._mop_type(payment.mode) === "finance" ? flt(payment.amount) : 0);
		}, 0);
		const grand      = this._is_free_sale ? 0 : this._calc_grand_total();

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
		const cash_paid  = this._is_free_sale ? 0 : this._payments.reduce((s, p) => s + flt(p.amount), 0);
		const total_paid = cash_paid + loyalty + advance;
		const balance    = Math.max(0, grand - total_paid);
		const change     = Math.max(0, total_paid - grand);
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
		this._overlay.find("#ch-pay-total-paid").text(`₹${format_number(total_paid)}`);

		// Advance applied row
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
			$label.text(__("Balance Due"));
			$bal.text(`₹${format_number(balance)}`)
				.removeClass("ch-pay-bal-positive ch-pay-bal-zero ch-pay-bal-credit")
				.addClass(balance > 0 ? "ch-pay-bal-positive" : "ch-pay-bal-zero");
		}

		const $cr = this._overlay.find("#ch-pay-change-row");
		if (change > 0.005 && !this._is_free_sale) {
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
	}

	_calc_balance_due() {
		const grand   = this._is_free_sale ? 0 : this._calc_grand_total();
		const loyalty = this._redeem_loyalty ? Math.min(this._loyalty_amount, grand) : 0;
		const advance = Math.min(this._advance_amount, Math.max(0, grand - loyalty));
		const paid    = this._payments.reduce((s, p) => s + flt(p.amount), 0);
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
			is_free_sale: this._is_free_sale,
			free_sale_reason: this._free_sale_reason,
			free_sale_approved_by: this._free_sale_approved_by,
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
		if (!this._is_free_sale && !this._is_credit_sale && balance > 0.005) {
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
			is_free_sale:                   this._is_free_sale ? 1 : 0,
			free_sale_reason:               this._is_free_sale ? this._free_sale_reason : "",
			free_sale_approved_by:          this._is_free_sale ? this._free_sale_approved_by : "",
			free_sale_approval_name:        this._is_free_sale ? (this._free_sale_approval_name || "") : "",
			advance_amount:                 advance > 0 ? advance : 0,
			kiosk_token:                    PosState.kiosk_token || null,
			guided_session:                 PosState.guided_session || null,
			exception_request:              PosState.exception_request || null,
			warranty_claim:                 PosState.warranty_claim || null,
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
			if (!info) {
				$info.html(`<div class="text-muted">${__("No credit limit configured for this customer")}</div>`);
				return;
			}
			const limit = flt(info.credit_limit);
			const outstanding = flt(info.outstanding);
			const available = Math.max(0, limit - outstanding);
			const cart_total = this._calc_grand_total();
			const over_limit = cart_total > available;
			$info.html(`
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
				${over_limit ? `<div class="ch-pay-credit-warn"><i class="fa fa-exclamation-triangle"></i> ${__("Cart total exceeds available credit")}</div>` : ""}
			`);
		}).catch(e => { console.error("Credit info load failed:", e); });
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
