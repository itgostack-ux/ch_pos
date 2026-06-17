/**
 * CH POS — Pre-Book / Proforma Workspace
 *
 * Lets the cashier convert the current Sell cart (or a fresh entry) into:
 *   1. A **Proforma Invoice** — submits a Quotation and opens the
 *      "Proforma Invoice" print format (Phase H deliverable, ch_erp15).
 *   2. A **Pre-Booking** — submits a Sales Order with stock reservation +
 *      advance amount tracking (Phase H, ch_payments/advance_payments).
 *
 * Reuse-first: backend wrappers `create_pos_quotation` and `create_pre_booking`
 * already exist in ch_pos/api/pos_api.py; this workspace only renders the
 * cashier-facing UI and calls them.
 */
import { PosState, EventBus } from "../../state.js";
import { format_number } from "../../shared/helpers.js";

export class PrebookWorkspace {
	constructor() {
		EventBus.on("workspace:render", (ctx) => {
			if (ctx.mode !== "prebook") return;
			this.render(ctx.panel);
		});
	}

	render(panel) {
		const cart = PosState.cart || [];
		const customer = PosState.customer || PosState.default_customer || "";
		const rows = cart.length
			? cart.map((it, i) => `
				<tr>
					<td>${frappe.utils.escape_html(it.item_name || it.item_code)}</td>
					<td style="text-align:right">${flt(it.qty || 1)}</td>
					<td style="text-align:right">₹${format_number(it.rate || 0)}</td>
					<td style="text-align:right"><b>₹${format_number(flt(it.qty || 1) * flt(it.rate || 0))}</b></td>
				</tr>
			`).join("")
			: `<tr><td colspan="4" class="text-muted" style="padding:20px;text-align:center;">
				${__("Cart is empty. Add items in the Sell workspace first, then return here.")}
			</td></tr>`;

		const total = cart.reduce((s, it) => s + flt(it.qty || 1) * flt(it.rate || 0), 0);

		panel.html(`
			<div class="ch-pos-mode-panel">
				<div class="ch-mode-header">
					<h4>
						<span class="mode-icon" style="background:#e0f2fe;color:#0369a1">
							<i class="fa fa-bookmark"></i>
						</span>
						${__("Pre-Book / Proforma")}
					</h4>
					<span class="ch-mode-hint">${__("Issue a Proforma Invoice (Quotation) or reserve stock as a Pre-Booking (Sales Order).")}</span>
				</div>

				<div style="display:flex;gap:20px;align-items:flex-start;flex-wrap:wrap;">
					<div style="flex:2;min-width:380px;">
						<div style="background:#fff;border:1px solid var(--pos-border);border-radius:var(--pos-radius);padding:14px;">
							<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
								<b>${__("Customer")}</b>
								<span class="text-muted">${frappe.utils.escape_html(customer || __("(no customer selected)"))}</span>
							</div>
							<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">
								<b>${__("Cart Items")}</b>
								<span>${cart.length} ${__("line(s)")}</span>
							</div>
							<table class="table table-condensed" style="margin:0;">
								<thead>
									<tr>
										<th>${__("Item")}</th>
										<th style="text-align:right">${__("Qty")}</th>
										<th style="text-align:right">${__("Rate")}</th>
										<th style="text-align:right">${__("Amount")}</th>
									</tr>
								</thead>
								<tbody>${rows}</tbody>
								<tfoot>
									<tr>
										<th colspan="3" style="text-align:right">${__("Total")}</th>
										<th style="text-align:right">₹${format_number(total)}</th>
									</tr>
								</tfoot>
							</table>
						</div>
					</div>

					<div style="flex:1;min-width:280px;">
						<div style="background:#fff;border:1px solid var(--pos-border);border-radius:var(--pos-radius);padding:14px;display:flex;flex-direction:column;gap:10px;">
							<button class="btn btn-primary btn-block ch-prebook-proforma" ${cart.length ? "" : "disabled"}>
								<i class="fa fa-file-text-o"></i> ${__("Generate Proforma Invoice")}
							</button>
							<div class="text-muted" style="font-size:11px;margin-top:-4px;">
								${__("Creates a submitted Quotation and opens the Proforma Invoice print format. No stock reservation.")}
							</div>

							<hr style="margin:8px 0;">

							<button class="btn btn-success btn-block ch-prebook-reserve" ${cart.length ? "" : "disabled"}>
								<i class="fa fa-bookmark"></i> ${__("Create Pre-Booking (Reserve Stock)")}
							</button>
							<div class="text-muted" style="font-size:11px;margin-top:-4px;">
								${__("Creates a Sales Order with stock reservation. Accepts an optional advance amount.")}
							</div>
						</div>
					</div>
				</div>
			</div>
		`);

		this._bind(panel, cart, customer, total);
	}

	_bind(panel, cart, customer, total) {
		panel.on("click", ".ch-prebook-proforma", () => this._proforma_flow(cart, customer));
		panel.on("click", ".ch-prebook-reserve", () => this._prebook_flow(cart, customer, total));
	}

	_proforma_flow(cart, customer) {
		const cart_total = (cart || []).reduce(
			(s, it) => s + flt(it.qty || 1) * flt(it.rate || 0), 0,
		);
		const dlg = new frappe.ui.Dialog({
			title: __("Generate Proforma Invoice"),
			fields: [
				{
					fieldname: "customer", fieldtype: "Link", options: "Customer",
					label: __("Customer"), reqd: 1, default: customer,
				},
				{
					fieldname: "valid_till", fieldtype: "Date", label: __("Valid Till"),
					default: frappe.datetime.add_days(frappe.datetime.nowdate(), 15),
				},
				{ fieldname: "column_break_a", fieldtype: "Column Break" },
				{
					fieldname: "advance_amount", fieldtype: "Currency",
					label: __("Advance Amount"),
					description: __("Optional. Shown on the Proforma as Advance Received and Balance Due. Collect via Payment Entry separately."),
				},
				{ fieldname: "section_break_b", fieldtype: "Section Break" },
				{ fieldname: "notes", fieldtype: "Small Text", label: __("Terms / Notes") },
				{
					fieldname: "html_total", fieldtype: "HTML",
					options: `<div style="text-align:right;padding:6px 0;"><b>${__("Order Total")}:</b> \u20B9${format_number(cart_total)}</div>`,
				},
			],
			primary_action_label: __("Generate"),
			primary_action: (v) => {
				if (flt(v.advance_amount) > cart_total + 0.005) {
					frappe.show_alert({
						message: __("Advance cannot exceed Order Total"),
						indicator: "orange",
					});
					return;
				}
				frappe.call({
					method: "ch_pos.api.pos_api.create_pos_quotation",
					args: {
						pos_profile: PosState.pos_profile,
						customer: v.customer,
						items: cart.map((it) => ({
							item_code: it.item_code,
							qty: flt(it.qty || 1),
							rate: flt(it.rate || 0),
							uom: it.uom || "Nos",
							warehouse: it.warehouse,
						})),
						valid_till: v.valid_till,
						notes: v.notes,
						advance_amount: flt(v.advance_amount),
					},
					freeze: true,
					freeze_message: __("Creating Proforma..."),
					callback: (r) => {
						if (!r.message) return;
						dlg.hide();
						this._show_proforma_success(r.message);
					},
				});
			},
		});
		dlg.show();
	}

	_show_proforma_success(qtn) {
		const print_url = `/printview?doctype=Quotation&name=${encodeURIComponent(qtn.name)}`
			+ `&format=${encodeURIComponent(qtn.print_format || "Proforma Invoice")}&no_letterhead=0`;
		const adv = flt(qtn.advance_received);
		const bal = flt(qtn.balance_due);
		const advance_html = adv > 0
			? `<p>${__("Advance Received")}: <b>₹${format_number(adv)}</b></p>
			   <p>${__("Balance Due")}: <b>₹${format_number(bal)}</b></p>`
			: "";
		frappe.msgprint({
			title: __("Proforma Created"),
			indicator: "green",
			message: `
				<div style="text-align:center;padding:12px;">
					<i class="fa fa-check-circle text-success" style="font-size:42px;"></i>
					<h4 style="margin:14px 0 6px;">${frappe.utils.escape_html(qtn.name)}</h4>
					<p>${__("Grand Total")}: <b>₹${format_number(qtn.grand_total)}</b></p>
					${advance_html}
					<p class="text-muted">${__("Valid till")} ${qtn.valid_till}</p>
					<div style="margin-top:14px;display:flex;gap:8px;justify-content:center;">
						${qtn.docstatus === 1
							? `<a class="btn btn-primary btn-sm" target="_blank" href="${print_url}">
								<i class="fa fa-print"></i> ${__("Print Proforma")}
							</a>`
							: `<span class="text-warning" style="font-size:12px;align-self:center;">
								<i class="fa fa-exclamation-triangle"></i> ${__("Proforma saved as draft — submit manually before printing.")}
							</span>`
						}
						
					</div>
				</div>`,
		});
	}

	_prebook_flow(cart, customer, total) {
		const dlg = new frappe.ui.Dialog({
			title: __("Create Pre-Booking (Reserve Stock)"),
			fields: [
				{
					fieldname: "customer", fieldtype: "Link", options: "Customer",
					label: __("Customer"), reqd: 1, default: customer,
				},
				{
					fieldname: "delivery_date", fieldtype: "Date", label: __("Delivery Date"), reqd: 1,
					default: frappe.datetime.add_days(frappe.datetime.nowdate(), 7),
				},
				{ fieldname: "column_break_a", fieldtype: "Column Break" },
				{
					fieldname: "advance_amount", fieldtype: "Currency", label: __("Advance Amount"),
					description: __("Optional. Logged as a comment on the Sales Order; collect payment via Payment Entry."),
				},
				{
					fieldname: "reserve_stock", fieldtype: "Check",
					label: __("Reserve Stock"), default: 1,
				},
				{ fieldname: "section_break_b", fieldtype: "Section Break" },
				{ fieldname: "notes", fieldtype: "Small Text", label: __("Notes") },
				{
					fieldname: "html_total", fieldtype: "HTML",
					options: `<div style="text-align:right;padding:6px 0;"><b>${__("Order Total")}:</b> ₹${format_number(total)}</div>`,
				},
			],
			primary_action_label: __("Create Pre-Booking"),
			primary_action: (v) => {
				frappe.call({
					method: "ch_pos.api.pos_api.create_pre_booking",
					args: {
						pos_profile: PosState.pos_profile,
						customer: v.customer,
						items: cart.map((it) => ({
							item_code: it.item_code,
							qty: flt(it.qty || 1),
							rate: flt(it.rate || 0),
							uom: it.uom || "Nos",
							warehouse: it.warehouse,
						})),
						delivery_date: v.delivery_date,
						advance_amount: flt(v.advance_amount),
						notes: v.notes,
						reserve_stock: v.reserve_stock ? 1 : 0,
					},
					freeze: true,
					freeze_message: __("Creating Pre-Booking..."),
					callback: (r) => {
						if (!r.message) return;
						dlg.hide();
						const so = r.message;
						PosState.reset_transaction();
						const so_name = so.name || __("Sales Order");
						const so_url = so.name ? `/app/sales-order/${encodeURIComponent(so.name)}` : "/app/sales-order";
						// frappe.msgprint({
						// 	title: __("Pre-Booking Created"),
						// 	indicator: so.docstatus === 1 ? "green" : "orange",
						// 	message: `
						// 		<div style="text-align:center;padding:12px;">
						// 			<i class="fa fa-bookmark text-success" style="font-size:42px;"></i>
						// 			<h4 style="margin:14px 0 6px;">${frappe.utils.escape_html(so_name)}</h4>
						// 			<p>${__("Created Sales Order")}: <b>${frappe.utils.escape_html(so_name)}</b></p>
						// 			<p>${__("Status")}: <b>${frappe.utils.escape_html(so.status || "-")}</b></p>
						// 			<p class="text-muted">${__("Delivery")}: ${frappe.utils.escape_html(so.delivery_date || "-")} · ${__("Stock reserved")}: ${so.reserve_stock ? __("Yes") : __("No")}</p>
						// 			${so.warning ? `<p class="text-warning">${frappe.utils.escape_html(so.warning)}</p>` : ""}
						// 			<a class="btn btn-default btn-sm" target="_blank" href="${so_url}">
						// 				<i class="fa fa-external-link"></i> ${__("Open Sales Order")}
						// 			</a>
						// 		</div>`,
						// });
					},
				});
			},
		});
		dlg.show();
	}
}
