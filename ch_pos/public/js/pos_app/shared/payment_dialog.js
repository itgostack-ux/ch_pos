/**
 * CH POS — Professional Payment Screen v2
 *
 * Poorvika/Sathya-grade checkout overlay:
 *  Left  : Itemized bill, serial numbers, all discounts, tax note
 *  Right : Split payment rows (Cash/Card/UPI), reference capture,
 *          bank offers, loyalty toggle, quick-cash amounts,
 *          live balance/change, idempotency UUID, success screen
 *
 * Replaces the old single-MOP dialog.
 */
import { PosState, EventBus } from "../state.js";
import { format_number } from "./helpers.js";

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
	if (lc.includes("voucher") || lc.includes("gift"))
		return `<i class="fa fa-gift" style="color:#f59e0b"></i>`;
	return `<i class="fa fa-exchange" style="color:#64748b"></i>`;
}

export class PaymentDialog {
	constructor() {
		this._overlay = null;
		this._payments = [];    // [{mode, amount, upi_transaction_id, card_reference, card_last_four}]
		this._loyalty_amount = 0;
		this._redeem_loyalty = false;
		this._submitting = false;
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

		// Reset per-transaction payment state
		this._payments = [];
		this._loyalty_amount = 0;
		this._redeem_loyalty = false;
		this._submitting = false;

		// Pre-seed with default MOP covering full amount
		const total = this._calc_grand_total();
		const def_mop = PosState.payment_modes.find(p => p.default) || PosState.payment_modes[0];
		if (def_mop) {
			this._payments = [{ mode: def_mop.mode_of_payment, amount: total, upi_transaction_id: "", card_reference: "", card_last_four: "" }];
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
		this._update_totals();
		if (this._payments[0]) this._load_bank_offers(this._payments[0].mode);
	}

	_build_overlay_html() {
		const total = this._calc_grand_total();
		const loyalty_pts = flt(PosState.loyalty_points) || 0;
		const has_loyalty = loyalty_pts > 0 && PosState.loyalty_program;
		const max_loyalty = has_loyalty ? flt(loyalty_pts * (PosState.conversion_factor || 0)) : 0;

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
						<span class="input-group-addon">${__("max")} ₹${format_number(max_loyalty)}</span>
					</div>
				</div>
			</div>` : "";

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

					<!-- MOP quick-add buttons -->
					<div class="ch-pay-mop-section">
						<div class="ch-pay-mop-label">${__("Add Payment")}</div>
						<div class="ch-pay-mop-btns">${mop_btns}</div>
					</div>

					<!-- Bank / card offers (loaded dynamically) -->
					<div id="ch-pay-bank-offers" class="ch-pay-bank-offers"></div>

					<!-- Payment rows -->
					<div id="ch-pay-rows" class="ch-pay-rows"></div>

					<!-- Loyalty -->
					${loyalty_html}

					<!-- Balance bar -->
					<div class="ch-pay-balance-bar">
						<div class="ch-pay-bal-row">
							<span>${__("Total Paid")}</span>
							<b id="ch-pay-total-paid">₹0</b>
						</div>
						<div class="ch-pay-bal-row">
							<span>${__("Balance Due")}</span>
							<b id="ch-pay-balance-due" class="ch-pay-bal-positive">₹${format_number(total)}</b>
						</div>
						<div class="ch-pay-bal-row ch-pay-change-row" id="ch-pay-change-row" style="display:none">
							<span>${__("Change to Return")}</span>
							<b id="ch-pay-change" class="ch-pay-change-val">₹0</b>
						</div>
					</div>

					<!-- Quick cash amounts -->
					<div id="ch-pay-quick-cash" class="ch-pay-quick-cash" style="display:none">
						<div class="ch-pay-quick-label">${__("Quick Cash")}</div>
						<div id="ch-pay-quick-btns" class="ch-pay-quick-btns"></div>
					</div>

					<!-- Submit -->
					<button class="btn ch-pay-submit-btn btn-default" id="ch-pay-submit" disabled>
						<i class="fa fa-check-circle"></i>
						<span id="ch-pay-submit-label">${__("Confirm Payment")}</span>
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
		const grand    = Math.max(0, net - add_disc - coupon - voucher - exchange - pe_cr);

		let rows = `<div class="ch-pay-total-row"><span>${__("Subtotal")}</span><span>₹${format_number(subtotal)}</span></div>`;
		if (disc_total > 0)  rows += `<div class="ch-pay-total-row ch-pay-deduct"><span>${__("Item Discounts")}</span><span>-₹${format_number(disc_total)}</span></div>`;
		if (add_disc > 0)    rows += `<div class="ch-pay-total-row ch-pay-deduct"><span>${PosState.additional_discount_pct ? PosState.additional_discount_pct + "% " : ""}${__("Additional Discount")}</span><span>-₹${format_number(add_disc)}</span></div>`;
		if (coupon > 0)      rows += `<div class="ch-pay-total-row ch-pay-deduct"><span>🏷️ ${__("Coupon")} (${frappe.utils.escape_html(PosState.coupon_code || "")})</span><span>-₹${format_number(coupon)}</span></div>`;
		if (voucher > 0)     rows += `<div class="ch-pay-total-row ch-pay-deduct"><span>🎟️ ${__("Voucher")}</span><span>-₹${format_number(voucher)}</span></div>`;
		if (exchange > 0)    rows += `<div class="ch-pay-total-row ch-pay-deduct" style="color:var(--pos-success,#16a34a)"><span><i class="fa fa-exchange"></i> ${__("Exchange Credit")}</span><span>-₹${format_number(exchange)}</span></div>`;
		if (pe_cr > 0)       rows += `<div class="ch-pay-total-row ch-pay-deduct" style="color:var(--pos-success,#16a34a)"><span><i class="fa fa-retweet"></i> ${__("Swap Credit")}</span><span>-₹${format_number(pe_cr)}</span></div>`;
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
				this._payments.push({ mode: mop, amount: Math.max(0, due), upi_transaction_id: "", card_reference: "", card_last_four: "" });
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
		});
		// Card RRN
		ov.on("input", ".ch-pay-row-rrn", e => {
			this._payments[parseInt($(e.currentTarget).data("idx"))].card_reference = $(e.currentTarget).val().trim();
		});
		// Card last 4
		ov.on("input", ".ch-pay-row-card4", e => {
			this._payments[parseInt($(e.currentTarget).data("idx"))].card_last_four = $(e.currentTarget).val().trim();
		});

		// Loyalty
		ov.on("change", "#ch-pay-loyalty-chk", e => {
			this._redeem_loyalty = $(e.currentTarget).is(":checked");
			ov.find("#ch-pay-loyalty-input").toggle(this._redeem_loyalty);
			this._loyalty_amount = this._redeem_loyalty ? flt(ov.find("#ch-pay-loyalty-amt").val()) : 0;
			this._update_totals();
		});
		ov.on("input", "#ch-pay-loyalty-amt", e => {
			const max = flt(PosState.loyalty_points) * flt(PosState.conversion_factor);
			this._loyalty_amount = Math.min(flt($(e.currentTarget).val()) || 0, max);
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
			}

			container.append(`
				<div class="ch-pay-row" data-idx="${idx}">
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
		this._overlay.find("#ch-pay-quick-cash").toggle(has_cash);
		if (has_cash) this._render_quick_cash();
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
		const grand      = this._calc_grand_total();
		const loyalty    = this._redeem_loyalty ? Math.min(this._loyalty_amount, grand) : 0;
		const cash_paid  = this._payments.reduce((s, p) => s + flt(p.amount), 0);
		const total_paid = cash_paid + loyalty;
		const balance    = Math.max(0, grand - total_paid);
		const change     = Math.max(0, total_paid - grand);
		const ready      = balance <= 0.005;

		this._overlay.find("#ch-pay-amount-due").text(`₹${format_number(Math.max(0, grand - loyalty))}`);
		this._overlay.find("#ch-pay-total-paid").text(`₹${format_number(total_paid)}`);
		const $bal = this._overlay.find("#ch-pay-balance-due");
		$bal.text(`₹${format_number(balance)}`)
			.removeClass("ch-pay-bal-positive ch-pay-bal-zero")
			.addClass(balance > 0 ? "ch-pay-bal-positive" : "ch-pay-bal-zero");

		const $cr = this._overlay.find("#ch-pay-change-row");
		if (change > 0.005) {
			$cr.show();
			this._overlay.find("#ch-pay-change").text(`₹${format_number(change)}`);
		} else {
			$cr.hide();
		}

		this._overlay.find("#ch-pay-submit")
			.prop("disabled", !ready || this._submitting)
			.toggleClass("btn-success", ready)
			.toggleClass("btn-default", !ready);

		// Refresh quick cash buttons if cash row present
		const has_cash = this._payments.some(p => this._mop_type(p.mode) === "cash");
		if (has_cash) this._render_quick_cash();
	}

	_calc_balance_due() {
		const grand   = this._calc_grand_total();
		const loyalty = this._redeem_loyalty ? Math.min(this._loyalty_amount, grand) : 0;
		const paid    = this._payments.reduce((s, p) => s + flt(p.amount), 0);
		return Math.max(0, grand - loyalty - paid);
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
			container.html(
				`<div class="ch-pay-offers-header"><i class="fa fa-tag"></i> ${__("Offers for")} <b>${frappe.utils.escape_html(mop)}</b></div>` +
				offers.map(o => `<div class="ch-pay-offer-chip">🏦 ${frappe.utils.escape_html(o.offer_name)} — ${frappe.utils.escape_html(o.conditions_text || "")}</div>`).join("")
			);
		}).catch(() => {});
	}

	// ───────────────────────────────────────── Helpers ──

	_mop_type(mop_name) {
		const lc = (mop_name || "").toLowerCase();
		if (lc.includes("upi") || lc.includes("gpay") || lc.includes("phonepe") || lc.includes("paytm")) return "upi";
		if (lc.includes("card") || lc.includes("credit") || lc.includes("debit") || lc.includes("edc")) return "card";
		if (lc.includes("cash")) return "cash";
		return "other";
	}

	_close() {
		clearTimeout(this._auto_timer);
		$(document).off("keydown.ch_pay_overlay");
		if (this._overlay) {
			this._overlay.removeClass("ch-pay-visible");
			setTimeout(() => { this._overlay?.remove(); this._overlay = null; }, 280);
		}
	}

	// ───────────────────────────────────────── Invoice submit ──

	_submit_invoice() {
		if (this._submitting) return;

		const grand   = this._calc_grand_total();
		const loyalty = this._redeem_loyalty ? Math.min(this._loyalty_amount, grand) : 0;
		const balance = this._calc_balance_due();

		if (balance > 0.005) {
			frappe.show_alert({ message: __("Payment not complete — ₹{0} still due", [format_number(balance)]), indicator: "red" });
			return;
		}

		// Validate UPI references (card RRN is optional but encouraged)
		for (const p of this._payments) {
			if (this._mop_type(p.mode) === "upi" && !p.upi_transaction_id) {
				frappe.show_alert({ message: __("Enter UPI Transaction ID for {0}", [p.mode]), indicator: "orange" });
				return;
			}
		}

		this._submitting = true;
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
			is_warranty:      c.is_warranty || false,
			is_vas:           c.is_vas || false,
			manager_approved: c.manager_approved || false,
			manager_user:     c.manager_user || null,
			override_reason:  c.override_reason || null,
			serial_no:        c.serial_no || null,
		}));

		const payments = this._payments.map(p => ({
			mode_of_payment:    p.mode,
			amount:             flt(p.amount),
			upi_transaction_id: p.upi_transaction_id || "",
			card_reference:     p.card_reference || "",
			card_last_four:     p.card_last_four || "",
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
			sales_executive:                PosState.sales_executive || null,
			sale_type:                      PosState.sale_type || null,
			sale_sub_type:                  PosState.sale_sub_type || null,
			sale_reference:                 PosState.sale_reference || null,
			discount_reason:                PosState.discount_reason || null,
			client_request_id:              this._gen_uuid(),
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
				error: () => this._on_error(__("Invoice creation failed")),
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
				error: () => this._on_error(__("Return creation failed")),
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
			const url  = `/printview?doctype=POS%20Invoice&name=${encodeURIComponent(name)}&format=POS%20Invoice&no_letterhead=1`;
			window.open(url, "_blank");
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

	_calc_grand_total() {
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

	// ─────────────────────────────────── Legacy single-mode compat ──
	// (kept so that any code still emitting old payment_values format doesn't break)
	_build_summary_html() { return ""; }
}
