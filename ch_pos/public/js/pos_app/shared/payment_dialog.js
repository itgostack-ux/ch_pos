/**
 * CH POS — Payment Dialog
 *
 * Handles payment collection, loyalty points redemption,
 * invoice submission, and offline queuing.
 */
import { PosState, EventBus } from "../state.js";
import { format_number } from "./helpers.js";

export class PaymentDialog {
	constructor() {
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

		const grand_total = this._calc_grand_total();
		const default_mode = PosState.payment_modes.find((p) => p.default);
		const mode_options = PosState.payment_modes.map((p) => p.mode).join("\n");

		const loyalty_available = PosState.loyalty_points > 0 && PosState.loyalty_program;
		const max_loyalty_amount = loyalty_available ? flt(PosState.loyalty_points * PosState.conversion_factor) : 0;

		// Build itemized summary
		const summary_html = this._build_summary_html(grand_total);

		const fields = [
			{
				fieldtype: "HTML",
				fieldname: "payment_summary",
				options: summary_html,
			},
			{ fieldtype: "Section Break", label: __("Payment") },
			{
				fieldname: "mode_of_payment",
				fieldtype: "Select",
				label: __("Primary Payment Mode"),
				options: mode_options || "Cash",
				default: default_mode ? default_mode.mode : "Cash",
				reqd: 1,
			},
		];

		if (loyalty_available) {
			fields.push(
				{ fieldtype: "Section Break", label: __("Loyalty Points") },
				{
					fieldname: "redeem_loyalty",
					fieldtype: "Check",
					label: `${__("Redeem Loyalty Points")} (${format_number(PosState.loyalty_points)} ${__("pts")} ≈ ₹${format_number(max_loyalty_amount)})`,
					default: 0,
				},
				{
					fieldname: "loyalty_amount",
					fieldtype: "Currency",
					label: __("Loyalty Amount to Redeem"),
					default: Math.min(max_loyalty_amount, grand_total),
					depends_on: "eval:doc.redeem_loyalty",
					description: `${__("Max")}: ₹${format_number(max_loyalty_amount)}`,
				},
				{ fieldtype: "Section Break" }
			);
		}

		fields.push(
			{
				fieldname: "amount",
				fieldtype: "Currency",
				label: __("Amount Paid"),
				default: grand_total,
				reqd: 1,
			},
			{
				fieldtype: "HTML",
				fieldname: "change_html",
				options: `<div class="ch-pos-change"></div>`,
			}
		);

		const dialog = new frappe.ui.Dialog({
			title: __("Payment"),
			fields: fields,
			size: "small",
			primary_action_label: __("Submit"),
			primary_action: (values) => {
				if (values.redeem_loyalty && flt(values.loyalty_amount) > max_loyalty_amount) {
					frappe.show_alert({ message: __("Loyalty amount exceeds available balance"), indicator: "red" });
					return;
				}
				dialog.hide();
				this._submit_invoice(values, grand_total);
			},
		});

		// Update amount due when loyalty changes
		const update_balance = () => {
			const loyalty_amt = dialog.get_value("redeem_loyalty") ? flt(dialog.get_value("loyalty_amount")) : 0;
			const due = Math.max(0, grand_total - loyalty_amt);
			dialog.set_value("amount", due);
		};

		if (loyalty_available) {
			dialog.fields_dict.redeem_loyalty.$input.on("change", update_balance);
			dialog.fields_dict.loyalty_amount.$input.on("change", update_balance);
		}

		dialog.fields_dict.amount.$input.on("change", function () {
			const loyalty_amt = dialog.get_value("redeem_loyalty") ? flt(dialog.get_value("loyalty_amount")) : 0;
			const paid = flt($(this).val()) + loyalty_amt;
			const change = paid - grand_total;
			dialog.$wrapper.find(".ch-pos-change").html(
				change > 0 ? `<b>${__("Change")}: ₹${format_number(change)}</b>` : ""
			);
		});

		dialog.show();
	}

	_submit_invoice(payment_values, grand_total) {
		const items = PosState.cart.map((c) => ({
			item_code: c.item_code,
			item_name: c.item_name,
			qty: c.qty,
			rate: c.rate,
			uom: c.uom,
			discount_amount: c.discount_amount || 0,
			applied_offer: c.applied_offer ? c.applied_offer.name : null,
			warranty_plan: c.warranty_plan || null,
			for_item_code: c.for_item_code || null,
			is_warranty: c.is_warranty || false,
			is_vas: c.is_vas || false,
		}));

		const invoice_data = {
			pos_profile: PosState.pos_profile,
			customer: PosState.customer,
			items: items,
			mode_of_payment: payment_values.mode_of_payment,
			amount_paid: payment_values.amount,
			exchange_assessment: PosState.exchange_assessment || null,
			additional_discount_percentage: PosState.additional_discount_pct || 0,
			additional_discount_amount: PosState.additional_discount_amt || PosState.coupon_discount || 0,
			coupon_code: PosState.coupon_code || null,
			redeem_loyalty_points: payment_values.redeem_loyalty ? 1 : 0,
			loyalty_points: payment_values.redeem_loyalty
				? cint(flt(payment_values.loyalty_amount) / (PosState.conversion_factor || 1))
				: 0,
			loyalty_amount: payment_values.redeem_loyalty ? flt(payment_values.loyalty_amount) : 0,
			product_exchange_invoice: PosState.product_exchange_invoice || null,
			return_items: PosState.return_items.length ? PosState.return_items : null,
			sales_executive: PosState.sales_executive || null,
			sale_type: PosState.sale_type || null,
			sale_sub_type: PosState.sale_sub_type || null,
			sale_reference: PosState.sale_reference || null,
		};

		// Offline queue
		if (!navigator.onLine) {
			EventBus.emit("sync:queue_invoice", {
				data: invoice_data,
				callback: () => {
					frappe.show_alert({
						message: __("Invoice queued — will sync when online"),
						indicator: "blue",
					});
					this._post_submit_cleanup();
				},
			});
			return;
		}

		// Product exchange: create return first, then new invoice
		const do_create = () => {
			frappe.call({
				method: "ch_pos.api.pos_api.create_pos_invoice",
				args: invoice_data,
				freeze: true,
				freeze_message: __("Creating Invoice..."),
				callback: (r) => {
					if (r.message) {
						let msg = __("{0} created", [r.message.name]);
						if (r.message.incentive_earned) {
							msg += ` — ₹${format_number(r.message.incentive_earned)} ${__("incentive")}`;
						}
						frappe.show_alert({ message: msg, indicator: "green" });
						this._post_submit_cleanup();
					}
				},
				error: () => {
					frappe.show_alert({ message: __("Invoice creation failed"), indicator: "red" });
				},
			});
		};

		if (PosState.product_exchange_invoice && PosState.return_items.length) {
			frappe.call({
				method: "ch_pos.api.pos_api.create_pos_return",
				args: {
					original_invoice: PosState.product_exchange_invoice,
					return_items: PosState.return_items,
					sales_executive: PosState.sales_executive || null,
				},
				freeze: true,
				freeze_message: __("Creating Return Credit Note..."),
				callback: (r) => {
					if (r.message) {
						frappe.show_alert({
							message: __("Return {0} processed", [r.message.name]),
							indicator: "blue",
						});
						do_create();
					}
				},
				error: () => {
					frappe.show_alert({ message: __("Return creation failed"), indicator: "red" });
				},
			});
		} else {
			do_create();
		}
	}

	_post_submit_cleanup() {
		PosState.reset_transaction();
	}

	_calc_grand_total() {
		let subtotal = 0;
		let discount_total = 0;

		PosState.cart.forEach((item) => {
			subtotal += flt(item.qty) * flt(item.rate);
			discount_total += flt(item.discount_amount || 0) * flt(item.qty);
		});

		let net = subtotal - discount_total;

		// Additional discount
		if (PosState.additional_discount_pct) {
			net -= net * PosState.additional_discount_pct / 100;
		} else if (PosState.additional_discount_amt) {
			net -= PosState.additional_discount_amt;
		}

		net -= flt(PosState.coupon_discount);
		net -= flt(PosState.exchange_amount);
		net -= flt(PosState.product_exchange_credit);

		return Math.max(0, net);
	}

	_build_summary_html(grand_total) {
		const cart = PosState.cart;
		const product_items = cart.filter((c) => !c.is_warranty && !c.is_vas);
		const warranty_items = cart.filter((c) => c.is_warranty);
		const vas_items = cart.filter((c) => c.is_vas);

		let subtotal = 0, discount_total = 0;
		cart.forEach((item) => {
			subtotal += flt(item.qty) * flt(item.rate);
			discount_total += flt(item.discount_amount || 0) * flt(item.qty);
		});

		let add_disc = 0;
		if (PosState.additional_discount_pct) {
			add_disc = (subtotal - discount_total) * PosState.additional_discount_pct / 100;
		} else if (PosState.additional_discount_amt) {
			add_disc = PosState.additional_discount_amt;
		}

		const coupon_disc = flt(PosState.coupon_discount);
		const exchange_credit = flt(PosState.exchange_amount);
		const pe_credit = flt(PosState.product_exchange_credit);

		// Item lines
		const item_lines = product_items.map((c) => {
			const amt = flt(c.qty) * flt(c.rate);
			const serial = c.serial_no ? `<span style="font-size:10px;color:var(--pos-text-muted)">${frappe.utils.escape_html(c.serial_no)}</span>` : "";
			return `<tr>
				<td>${frappe.utils.escape_html(c.item_name)} ${serial}</td>
				<td style="text-align:center">${c.qty}</td>
				<td style="text-align:right">₹${format_number(amt)}</td>
			</tr>`;
		}).join("");

		const warranty_lines = warranty_items.map((c) => {
			return `<tr style="color:var(--pos-success)">
				<td>${frappe.utils.escape_html(c.item_name)}</td>
				<td style="text-align:center">${c.qty}</td>
				<td style="text-align:right">₹${format_number(flt(c.qty) * flt(c.rate))}</td>
			</tr>`;
		}).join("");

		const vas_lines = vas_items.map((c) => {
			return `<tr style="color:var(--pos-info)">
				<td>${frappe.utils.escape_html(c.item_name)}</td>
				<td style="text-align:center">${c.qty}</td>
				<td style="text-align:right">₹${format_number(flt(c.qty) * flt(c.rate))}</td>
			</tr>`;
		}).join("");

		// Deduction rows
		let deductions = "";
		if (discount_total > 0) {
			deductions += `<tr class="ch-pay-deduction"><td colspan="2">${__("Item Discounts")}</td><td style="text-align:right">-₹${format_number(discount_total)}</td></tr>`;
		}
		if (add_disc > 0) {
			deductions += `<tr class="ch-pay-deduction"><td colspan="2">${__("Additional Discount")}</td><td style="text-align:right">-₹${format_number(add_disc)}</td></tr>`;
		}
		if (coupon_disc > 0) {
			deductions += `<tr class="ch-pay-deduction"><td colspan="2">🏷️ ${__("Coupon")} (${PosState.coupon_code})</td><td style="text-align:right">-₹${format_number(coupon_disc)}</td></tr>`;
		}
		if (exchange_credit > 0) {
			deductions += `<tr class="ch-pay-deduction"><td colspan="2"><i class="fa fa-exchange"></i> ${__("Exchange Credit")}</td><td style="text-align:right;color:var(--pos-success)">-₹${format_number(exchange_credit)}</td></tr>`;
		}
		if (pe_credit > 0) {
			deductions += `<tr class="ch-pay-deduction"><td colspan="2"><i class="fa fa-retweet"></i> ${__("Swap Credit")}</td><td style="text-align:right;color:var(--pos-success)">-₹${format_number(pe_credit)}</td></tr>`;
		}

		return `<div class="ch-pos-payment-summary">
			<div class="ch-pay-customer">
				<i class="fa fa-user"></i> <b>${frappe.utils.escape_html(PosState.customer)}</b>
				${PosState.sales_executive_name ? `<span style="float:right;font-size:11px;color:var(--pos-text-muted)"><i class="fa fa-id-badge"></i> ${frappe.utils.escape_html(PosState.sales_executive_name)}</span>` : ""}
			</div>
			<table class="ch-pay-items-table">
				<thead><tr><th>${__("Item")}</th><th style="text-align:center">${__("Qty")}</th><th style="text-align:right">${__("Amount")}</th></tr></thead>
				<tbody>
					${item_lines}
					${warranty_lines ? `<tr><td colspan="3" style="font-size:11px;font-weight:600;color:var(--pos-success);padding-top:6px">🛡 ${__("Warranty")}</td></tr>` + warranty_lines : ""}
					${vas_lines ? `<tr><td colspan="3" style="font-size:11px;font-weight:600;color:var(--pos-info);padding-top:6px">✦ ${__("Value Added Services")}</td></tr>` + vas_lines : ""}
				</tbody>
			</table>			${this._margin_summary_html(cart)}			<table class="ch-pay-totals-table">
				<tbody>
					<tr><td colspan="2"><b>${__("Subtotal")}</b></td><td style="text-align:right"><b>₹${format_number(subtotal)}</b></td></tr>
					${deductions}
					<tr class="ch-pay-grand-total"><td colspan="2"><b>${__("Net Payable")}</b></td><td style="text-align:right"><b>₹${format_number(grand_total)}</b></td></tr>
				</tbody>
			</table>
		</div>`;
	}

	_margin_summary_html(cart) {
		const margin_items = cart.filter(
			(c) => c.ch_item_type === "Refurbished" || c.ch_item_type === "Pre-Owned"
		);
		if (!margin_items.length) return "";

		const margin_total = margin_items.reduce(
			(s, c) => s + flt(c.qty) * flt(c.rate), 0
		);

		return `<div class="ch-pos-margin-info" style="background:var(--pos-bg-alt,#fffbea);border-radius:8px;padding:8px 12px;margin:6px 0;font-size:12px;border:1px solid var(--pos-warning,#f0c060);">
			<div style="font-weight:600;margin-bottom:2px;color:var(--pos-warning-dark,#8a6d00);">
				<i class="fa fa-info-circle"></i> ${__("Margin Scheme")}
			</div>
			<div style="color:var(--pos-text-muted)">
				${margin_items.length} ${__("item(s)")} — ${__("GST on margin only")} &middot; ₹${format_number(margin_total)} ${__("total")}
			</div>
		</div>`;
	}
}
