/**
 * CH POS — Returns Workspace
 *
 * Search invoices for return or product exchange.
 * Shows return item picker and processes returns / exchange credits.
 */
import { PosState, EventBus } from "../../state.js";
import { format_number } from "../../shared/helpers.js";

export class ReturnsWorkspace {
	constructor() {
		EventBus.on("workspace:render", (ctx) => {
			if (ctx.mode !== "returns") return;
			this.render(ctx.panel);
		});
		EventBus.on("returns:pick_items", (opts) => {
			this._show_return_item_picker(opts.invoice, opts.action);
		});
	}

	render(panel) {
		panel.html(`
			<div class="ch-pos-mode-panel">
				<div class="ch-mode-header">
					<h4>
						<span class="mode-icon" style="background:var(--pos-danger-light);color:var(--pos-danger)">
							<i class="fa fa-undo"></i>
						</span>
						${__("Returns & Exchange")}
					</h4>
					<span class="ch-mode-hint">${__("Process returns or exchange products from a previous sale")}</span>
				</div>

				<div style="display:flex;gap:10px;align-items:stretch;margin-bottom:16px;">
					<div class="ch-pos-search-wrap" style="flex:1;max-width:none">
						<i class="fa fa-search ch-pos-search-icon"></i>
						<input type="text" class="form-control ch-pos-search ch-ret-search"
							placeholder="${__("Invoice #, phone, or customer name...")}">
					</div>
					<button class="btn btn-primary ch-ret-lookup" style="border-radius:var(--pos-radius);font-weight:700;padding:0 20px">
						<i class="fa fa-search"></i>
					</button>
				</div>

				<div class="ch-ret-results">
					<div class="ch-pos-empty-state" style="padding:40px 16px;">
						<div class="empty-icon"><i class="fa fa-undo"></i></div>
						<div class="empty-title">${__("Find an invoice")}</div>
						<div class="empty-subtitle">${__("Search by invoice number, phone, or customer name to start a return")}</div>
					</div>
				</div>
				<div class="ch-ret-detail" style="display:none;"></div>
			</div>
		`);
		this._bind(panel);
	}

	_bind(panel) {
		const do_search = () => {
			const q = panel.find(".ch-ret-search").val().trim();
			if (!q) return;
			frappe.call({
				method: "ch_pos.api.pos_api.search_invoices_for_return",
				args: { search_term: q, pos_profile: PosState.pos_profile },
				callback: (r) => {
					const el = panel.find(".ch-ret-results");
					panel.find(".ch-ret-detail").hide().empty();
					const invoices = r.message || [];
					if (!invoices.length) {
						el.html(`
							<div class="ch-pos-empty-state" style="padding:30px 16px;">
								<div class="empty-icon"><i class="fa fa-search"></i></div>
								<div class="empty-title">${__("No invoices for")} "${frappe.utils.escape_html(q)}"</div>
							</div>`);
						return;
					}
					const cards = invoices.map((inv) => {
						const status_cls = inv.status === "Paid" ? "success" :
							inv.status === "Credit Note Issued" ? "warning" : "info";
						return `<div class="ch-ret-inv-card" data-name="${inv.name}">
							<div class="ch-ret-inv-top">
								<div style="display:flex;align-items:center;gap:6px">
									<span class="ch-ret-inv-id">${inv.name}</span>
									<span class="ch-pos-badge badge-${status_cls}">${inv.status}</span>
								</div>
								<span class="ch-ret-inv-total">₹${format_number(inv.grand_total)}</span>
							</div>
							<div class="ch-ret-inv-body">
								<span style="font-weight:600;color:var(--pos-text)">${frappe.utils.escape_html(inv.customer_name || inv.customer)}</span>
								<span>${inv.posting_date} · ${inv.items_count || 0} items</span>
							</div>
							<div class="ch-ret-inv-actions">
								<button class="btn btn-sm btn-warning ch-ret-select-return"
									data-name="${inv.name}" ${inv.status === "Credit Note Issued" ? "disabled" : ""}
									style="border-radius:var(--pos-radius-sm);font-weight:700">
									<i class="fa fa-undo"></i> ${__("Return Items")}
								</button>
								<button class="btn btn-sm btn-primary ch-ret-select-exchange"
									data-name="${inv.name}" ${inv.status === "Credit Note Issued" ? "disabled" : ""}
									style="border-radius:var(--pos-radius-sm);font-weight:700">
									<i class="fa fa-retweet"></i> ${__("Product Exchange")}
								</button>
							</div>
						</div>`;
					}).join("");
					el.html(`<div class="ch-ret-results-grid">${cards}</div>`);
				},
			});
		};

		panel.on("click", ".ch-ret-lookup", do_search);
		panel.find(".ch-ret-search").on("keypress", (e) => { if (e.which === 13) do_search(); });

		panel.on("click", ".ch-ret-select-return", function () {
			EventBus.emit("returns:pick_items", {
				invoice: $(this).data("name"),
				action: "return",
			});
		});

		panel.on("click", ".ch-ret-select-exchange", function () {
			EventBus.emit("returns:pick_items", {
				invoice: $(this).data("name"),
				action: "exchange",
			});
		});
	}

	_show_return_item_picker(invoice_name, action) {
		frappe.call({
			method: "ch_pos.api.pos_api.get_invoice_items_for_return",
			args: { invoice_name },
			callback: (r) => {
				const items = r.message || [];
				if (!items.length) {
					frappe.show_alert({ message: __("No returnable items in this invoice"), indicator: "orange" });
					return;
				}

				const fields = [
					{
						fieldtype: "HTML",
						fieldname: "items_html",
						options: `<p class="text-muted" style="margin-bottom:12px;">
							${action === "return"
								? __("Select items to return. Quantities will be credited back.")
								: __("Select items to return for exchange. Credit will be applied to new purchase.")}
						</p>`,
					},
				];

				// Build a row-name -> index map so we can wire device <-> VAS auto-fill
				const idx_by_row = {};
				items.forEach((it, i) => { idx_by_row[it.name] = i; });

				items.forEach((item, i) => {
					const has_serial = !!(item.serial_no);
					const bound_vas = item.has_attached_vas && (item.attached_vas || []).length;
					const is_bound_vas = !!item.is_bound_vas;
					const vas_badge = bound_vas
						? `<span class="badge badge-warning" style="margin-left:4px" title="${__("Linked Extended Warranty / VAS will be auto-refunded with this device")}">${__("+ VAS auto-refund")}</span>`
						: "";
					const vas_bound_badge = is_bound_vas
						? `<span class="badge badge-info" style="margin-left:4px" title="${__("Bound to a device on this invoice -- qty follows the device")}">${__("Bound to device")}</span>`
						: "";
					const attached_list = bound_vas
						? `<div class="text-muted" style="font-size:11px;margin-top:2px">${__("Auto-included on return:")} ${(item.attached_vas || []).map(v => frappe.utils.escape_html(v.item_name || v.item_code)).join(", ")}</div>`
						: "";
					fields.push({ fieldtype: "Section Break", collapsible: 0 });
					fields.push({
						fieldtype: "HTML",
						fieldname: `item_label_${i}`,
						options: `<div style="display:flex;justify-content:space-between;align-items:flex-start;padding:4px 0;">
							<div>
								<b>${frappe.utils.escape_html(item.item_name)}</b>
								<span class="text-muted"> (${frappe.utils.escape_html(item.item_code)})</span>
								${has_serial ? `<span class="badge badge-info" style="margin-left:4px">IMEI: ${frappe.utils.escape_html(item.serial_no)}</span>` : ""}
								${vas_badge}${vas_bound_badge}
								${attached_list}
							</div>
							<div class="text-muted">
								Sold: ${item.qty} @ ₹${format_number(item.rate)}
								${item.already_returned ? `<span class="text-warning"> · Returned: ${Math.abs(item.already_returned)}</span>` : ""}
							</div>
						</div>`,
					});
					if (has_serial) {
						fields.push({
							fieldname: `return_serial_${i}`,
							fieldtype: "Data",
							label: __("Scan IMEI to confirm return"),
							description: __("Must match: {0}", [item.serial_no]),
						});
					}
					fields.push({
						fieldname: `return_qty_${i}`,
						fieldtype: "Int",
						label: __("Return Qty"),
						default: 0,
						description: is_bound_vas
							? __("Auto-set from device. Max: {0}", [item.returnable_qty])
							: __("Max: {0}", [item.returnable_qty]),
						read_only: is_bound_vas ? 1 : 0,
					});
				});

				const dlg = new frappe.ui.Dialog({
					title: action === "return"
						? __("Return Items — {0}", [invoice_name])
						: __("Exchange Items — {0}", [invoice_name]),
					fields: fields,
					size: "large",
					primary_action_label: action === "return" ? __("Process Return") : __("Apply Exchange Credit"),
					primary_action: (values) => {
						const return_items = [];
						let total_credit = 0;
						let serial_mismatch = false;

						items.forEach((item, i) => {
							const qty = Math.min(
								Math.max(0, cint(values[`return_qty_${i}`])),
								item.returnable_qty
							);
							if (qty > 0) {
								// IMEI match check for serial items
								if (item.serial_no) {
									const scanned = (values[`return_serial_${i}`] || "").trim();
									if (scanned !== item.serial_no) {
										frappe.show_alert({
											message: __("IMEI mismatch for {0}: scanned '{1}' but expected '{2}'",
												[item.item_name, scanned || "empty", item.serial_no]),
											indicator: "red",
										});
										serial_mismatch = true;
										return;
									}
								}
								return_items.push({
									item_code: item.item_code,
									item_name: item.item_name,
									qty: qty,
									rate: item.rate,
									original_item_row: item.name,
									serial_no: item.serial_no || "",
								});
								total_credit += qty * flt(item.rate);
							}
						});

						if (serial_mismatch) return;

						if (!return_items.length) {
							frappe.show_alert({ message: __("Select at least one item to return"), indicator: "orange" });
							return;
						}

						// Validate serial items are returnable (not scrapped/transferred)
						const serial_items = return_items.filter(ri => ri.serial_no);
						if (serial_items.length) {
							const checks = serial_items.map(ri =>
								frappe.xcall("ch_pos.api.pos_api.check_serial_returnable", {
									serial_no: ri.serial_no,
									original_invoice: invoice_name,
								})
							);
							Promise.all(checks).then(results => {
								for (let k = 0; k < results.length; k++) {
									if (!results[k].returnable) {
										frappe.msgprint({
											title: __("Return Blocked"),
											message: __("{0} (IMEI: {1}): {2}", [
												serial_items[k].item_name,
												serial_items[k].serial_no,
												results[k].reason,
											]),
											indicator: "red",
										});
										return;
									}
								}
								dlg.hide();
								if (action === "return") {
									this._process_return(invoice_name, return_items, total_credit);
								} else {
									this._process_product_exchange(invoice_name, return_items, total_credit);
								}
							}).catch(() => {
								frappe.show_alert({ message: __("Error checking return eligibility"), indicator: "red" });
							});
							return;
						}

						dlg.hide();

						if (action === "return") {
							this._process_return(invoice_name, return_items, total_credit);
						} else {
							this._process_product_exchange(invoice_name, return_items, total_credit);
						}
					},
				});
				dlg.show();

				// Auto-mirror device qty -> bound VAS rows so the cashier sees
				// exactly what will be refunded before clicking Process.
				items.forEach((dev, dev_idx) => {
					if (!(dev.has_attached_vas && (dev.attached_vas || []).length)) return;
					const dev_field = dlg.get_field(`return_qty_${dev_idx}`);
					if (!dev_field || !dev_field.df) return;
					dev_field.df.onchange = () => {
						const dev_qty = cint(dlg.get_value(`return_qty_${dev_idx}`));
						(dev.attached_vas || []).forEach(v => {
							const vas_idx = idx_by_row[v.vas_si_row];
							if (vas_idx === undefined) return;
							const vas_item = items[vas_idx];
							if (!vas_item) return;
							const ratio = dev.qty ? Math.min(1, dev_qty / dev.qty) : 1;
							const new_vas_qty = Math.min(
								Math.round(vas_item.qty * ratio),
								vas_item.returnable_qty
							);
							dlg.set_value(`return_qty_${vas_idx}`, new_vas_qty);
						});
					};
				});
			},
		});
	}

	_process_return(invoice_name, return_items, total_credit) {
		frappe.call({
			method: "ch_pos.api.pos_api.create_pos_return",
			args: { original_invoice: invoice_name, return_items: return_items },
			freeze: true,
			freeze_message: __("Processing Return..."),
			callback: (r) => {
				if (r.message) {
					const cn = r.message;
					frappe.msgprint({
						title: __("Return Processed"),
						indicator: "green",
						message: `
							<div style="text-align:center;padding:16px;">
								<i class="fa fa-check-circle text-success" style="font-size:48px;"></i>
								<h4 style="margin:16px 0 8px;">${__("Credit Note Created")}</h4>
								<p><b>${cn.name}</b></p>
								<p>${__("Refund Amount")}: <b class="text-success">₹${format_number(Math.abs(cn.grand_total))}</b></p>
								<p class="text-muted">${__("Customer")}: ${frappe.utils.escape_html(cn.customer_name || cn.customer)}</p>
							</div>`,
					});
				}
			},
		});
	}

	_process_product_exchange(invoice_name, return_items, total_credit) {
		PosState.product_exchange_invoice = invoice_name;
		PosState.product_exchange_credit = total_credit;
		PosState.return_items = return_items;

		// Set customer from original invoice
		frappe.db.get_value("Sales Invoice", invoice_name, "customer").then((r) => {
			if (r.message && r.message.customer) {
				PosState.customer = r.message.customer;
				EventBus.emit("customer:set", r.message.customer);
			}
		});

		EventBus.emit("product_exchange:applied", { total_credit, return_items });
		EventBus.emit("cart:updated");

		// Switch to sell mode
		EventBus.emit("mode:set", "sell");
		EventBus.emit("mode:switch", "sell");

		frappe.show_alert({
			message: __("Exchange credit ₹{0} applied. Add new items to cart.", [format_number(total_credit)]),
			indicator: "blue",
		});
	}
}
