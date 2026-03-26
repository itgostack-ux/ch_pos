/**
 * CH POS — Customer 360° Workspace
 *
 * Complete customer history: purchases, returns, repairs,
 * buybacks, loyalty points.
 */
import { PosState, EventBus } from "../../state.js";
import { format_number } from "../../shared/helpers.js";

export class Customer360Workspace {
	constructor() {
		EventBus.on("workspace:render", (ctx) => {
			if (ctx.mode !== "customer360") return;
			this.render(ctx.panel);
		});
	}

	render(panel) {
		panel.html(`
			<div class="ch-pos-mode-panel">
				<div class="ch-mode-header">
					<h4>
						<span class="mode-icon" style="background:#ecfdf5;color:#059669">
							<i class="fa fa-user"></i>
						</span>
						${__("Customer 360°")}
					</h4>
					<span class="ch-mode-hint">${__("Complete customer history: purchases, returns, repairs, buybacks, loyalty")}</span>
				</div>

				<div style="display:flex;gap:10px;align-items:stretch;margin-bottom:16px;">
					<div class="ch-pos-search-wrap" style="flex:1;max-width:none">
						<i class="fa fa-user ch-pos-search-icon"></i>
						<input type="text" class="form-control ch-pos-search ch-cust360-search"
							placeholder="${__("Phone number, name, or customer ID...")}">
					</div>
					<button class="btn btn-primary ch-cust360-lookup" style="border-radius:var(--pos-radius);font-weight:700;padding:0 20px">
						<i class="fa fa-search"></i>
					</button>
				</div>

				<div class="ch-cust360-result">
					<div class="ch-pos-empty-state" style="padding:40px 16px;">
						<div class="empty-icon"><i class="fa fa-user"></i></div>
						<div class="empty-title">${__("Search for a customer")}</div>
						<div class="empty-subtitle">${__("Enter a phone number, name, or customer ID to view their profile")}</div>
					</div>
				</div>
			</div>
		`);
		this._bind(panel);
	}

	_bind(panel) {
		const do_search = () => {
			const q = panel.find(".ch-cust360-search").val().trim();
			if (!q) {
				frappe.show_alert({ message: __("Enter a phone number, name, or customer ID"), indicator: "orange" });
				return;
			}
			panel.find(".ch-cust360-result").html(
				`<div style="padding:24px;text-align:center"><i class="fa fa-spinner fa-spin"></i></div>`
			);
			frappe.call({
				method: "ch_pos.api.pos_api.customer_360",
				args: { identifier: q, company: PosState.company },
				callback: (r) => {
					const d = r.message;
					if (!d || d.error) {
						panel.find(".ch-cust360-result").html(`
							<div class="ch-pos-empty-state" style="padding:30px 16px;">
								<div class="empty-icon"><i class="fa fa-search"></i></div>
								<div class="empty-title">${d?.error || __("No customer for")} "${frappe.utils.escape_html(q)}"</div>
							</div>`);
						return;
					}
					this._render_360(panel, d);
				},
			});
		};

		panel.on("click", ".ch-cust360-lookup", do_search);
		panel.find(".ch-cust360-search").on("keypress", (e) => { if (e.which === 13) do_search(); });
	}

	_render_360(panel, d) {
		const el = panel.find(".ch-cust360-result");

		const section_table = (title, icon, items, columns) => {
			if (!items || !items.length) return `<div class="ch-pos-section-card" style="margin-bottom:var(--pos-space-md)">
				<div class="section-header"><i class="fa fa-${icon}"></i> ${title}</div>
				<div class="section-body">
					<div class="ch-pos-empty-state" style="padding:20px 0;">
						<div class="empty-title" style="font-size:var(--pos-fs-sm)">${__("None found")}</div>
					</div>
				</div>
			</div>`;
			const thead = columns.map((c) => `<th>${c.label}</th>`).join("");
			const tbody = items.map((row) => {
				const tds = columns.map((c) => `<td>${c.render(row)}</td>`).join("");
				return `<tr>${tds}</tr>`;
			}).join("");
			return `<div class="ch-pos-section-card" style="margin-bottom:var(--pos-space-md)">
				<div class="section-header"><i class="fa fa-${icon}"></i> ${title} (${items.length})</div>
				<div class="section-body" style="padding:0">
					<div style="overflow-x:auto"><table class="ch-c360-table">
						<thead><tr>${thead}</tr></thead><tbody>${tbody}</tbody>
					</table></div>
				</div>
			</div>`;
		};

		let html = `<div class="ch-c360-detail">
			<div class="ch-pos-section-card" style="margin-bottom:var(--pos-space-md)">
				<div class="section-body">
					<div class="ch-c360-profile">
						<div class="ch-c360-avatar"><i class="fa fa-user"></i></div>
						<div style="flex:1;min-width:0">
							<div style="font-weight:700;font-size:var(--pos-fs-lg);color:var(--pos-text)">${frappe.utils.escape_html(d.customer_name || d.customer)}</div>
							<div style="font-size:var(--pos-fs-sm);color:var(--pos-text-muted);display:flex;gap:8px;flex-wrap:wrap">
								<span>${frappe.utils.escape_html(d.customer)}</span>
								${d.membership_id ? `<span>· <b>${frappe.utils.escape_html(d.membership_id)}</b></span>` : ""}
								${d.mobile_no ? `<span>· ${frappe.utils.escape_html(d.mobile_no)}</span>` : ""}
								${d.email_id ? `<span>· ${frappe.utils.escape_html(d.email_id)}</span>` : ""}
							</div>
							${d.previous_phones ? `<div style="font-size:var(--pos-fs-2xs);color:var(--pos-text-muted);margin-top:2px"><i class="fa fa-history"></i> Previous: ${frappe.utils.escape_html(d.previous_phones.split("\\n")[0])}</div>` : ""}
						</div>
						<button class="btn btn-sm btn-default ch-c360-edit-btn" style="align-self:flex-start;border-radius:var(--pos-radius);font-weight:600">
							<i class="fa fa-pencil"></i> ${__("Edit")}
						</button>
					</div>
					<div class="ch-c360-stats">
						<div class="ch-c360-stat">
							<span class="ch-c360-stat-val">₹${format_number(d.total_spent || 0)}</span>
							<span class="ch-c360-stat-label">${__("Total Spent")}</span>
						</div>
						<div class="ch-c360-stat">
							<span class="ch-c360-stat-val">${d.total_invoices || 0}</span>
							<span class="ch-c360-stat-label">${__("Purchases")}</span>
						</div>
						${d.loyalty ? `<div class="ch-c360-stat" style="background:#fef3c7">
							<span class="ch-c360-stat-val">${format_number(d.loyalty.points || 0)}</span>
							<span class="ch-c360-stat-label">${__("Loyalty Points")}</span>
							<span style="font-size:var(--pos-fs-2xs);color:#92400e">≈ ₹${format_number(d.loyalty.currency_value || 0)}</span>
						</div>` : ""}
					</div>
				</div>
			</div>`;

		html += section_table(__("Recent Purchases"), "shopping-cart", d.invoices, [
			{ label: __("Invoice"), render: (r) => `<a class="ch-c360-link" data-doctype="Sales Invoice" data-name="${r.name}">${r.name}</a>` },
			{ label: __("Date"), render: (r) => frappe.datetime.str_to_user(r.posting_date) },
			{ label: __("Items"), render: (r) => `${r.items_count || 0}` },
			{ label: __("Total"), render: (r) => `₹${format_number(r.grand_total)}` },
			{ label: __("Status"), render: (r) => {
				const cls = r.status === "Paid" ? "success" : r.status === "Return" ? "danger" : "info";
				return `<span class="ch-pos-badge badge-${cls}">${r.status}</span>`;
			}},
		]);

		html += section_table(__("Service Requests"), "wrench", d.service_requests, [
			{ label: __("ID"), render: (r) => `<a class="ch-c360-link" data-doctype="Service Request" data-name="${r.name}">${r.name}</a>` },
			{ label: __("Date"), render: (r) => frappe.datetime.str_to_user(r.service_date || r.creation) },
			{ label: __("Device"), render: (r) => frappe.utils.escape_html(r.device_item_name || "") },
			{ label: __("Issue"), render: (r) => frappe.utils.escape_html(r.issue_category || "") },
			{ label: __("Status"), render: (r) => {
				const cls = { Completed: "success", "In Service": "warning", Draft: "warning", Delivered: "success" }[r.decision] || "info";
				return `<span class="ch-pos-badge badge-${cls}">${r.decision || r.status}</span>`;
			}},
		]);

		html += section_table(__("Buyback Assessments"), "exchange", d.buybacks, [
			{ label: __("ID"), render: (r) => `<a class="ch-c360-link" data-doctype="Buyback Assessment" data-name="${r.name}">${r.name}</a>` },
			{ label: __("Date"), render: (r) => frappe.datetime.str_to_user(r.creation) },
			{ label: __("Device"), render: (r) => frappe.utils.escape_html(r.item_name || "") },
			{ label: __("Grade"), render: (r) => r.estimated_grade || "—" },
			{ label: __("Price"), render: (r) => `₹${format_number(r.quoted_price || r.estimated_price || 0)}` },
			{ label: __("Status"), render: (r) => {
				const cls = r.status === "Submitted" ? "success" : r.status === "Draft" ? "warning" : "muted";
				return `<span class="ch-pos-badge badge-${cls}">${r.status}</span>`;
			}},
		]);

		html += section_table(__("Warranty & AMC Plans"), "shield", d.warranties, [
			{ label: __("Plan"), render: (r) => `<a class="ch-c360-link" data-doctype="CH Sold Plan" data-name="${r.name}">${frappe.utils.escape_html(r.plan_name || r.name)}</a>` },
			{ label: __("Type"), render: (r) => frappe.utils.escape_html(r.plan_type || "") },
			{ label: __("Item"), render: (r) => frappe.utils.escape_html(r.item_name || "") },
			{ label: __("Valid To"), render: (r) => r.end_date ? frappe.datetime.str_to_user(r.end_date) : "—" },
			{ label: __("Status"), render: (r) => {
				const cls = r.status === "Active" ? "success" : r.status === "Expired" ? "danger" : "info";
				return `<span class="ch-pos-badge badge-${cls}">${r.status || "—"}</span>`;
			}},
		]);

		html += section_table(__("Warranty Claims"), "gavel", d.warranty_claims, [
			{ label: __("ID"), render: (r) => `<a class="ch-c360-link" data-doctype="CH Warranty Claim" data-name="${r.name}">${r.name}</a>` },
			{ label: __("Date"), render: (r) => frappe.datetime.str_to_user(r.claim_date) },
			{ label: __("Item"), render: (r) => frappe.utils.escape_html(r.item_name || "") },
			{ label: __("Issue"), render: (r) => frappe.utils.escape_html(r.issue_category || "") },
			{ label: __("Status"), render: (r) => {
				const cls = r.claim_status === "Approved" ? "success" : r.claim_status === "Rejected" ? "danger" : "warning";
				return `<span class="ch-pos-badge badge-${cls}">${r.claim_status || "—"}</span>`;
			}},
		]);

		html += section_table(__("Vouchers"), "ticket", d.vouchers, [
			{ label: __("Code"), render: (r) => `<a class="ch-c360-link" data-doctype="CH Voucher" data-name="${r.name}">${frappe.utils.escape_html(r.voucher_code || r.name)}</a>` },
			{ label: __("Type"), render: (r) => frappe.utils.escape_html(r.voucher_type || "") },
			{ label: __("Amount"), render: (r) => `₹${format_number(r.original_amount || 0)}` },
			{ label: __("Balance"), render: (r) => `₹${format_number(r.balance || 0)}` },
			{ label: __("Status"), render: (r) => {
				const cls = r.status === "Active" ? "success" : r.status === "Redeemed" ? "muted" : "warning";
				return `<span class="ch-pos-badge badge-${cls}">${r.status || "—"}</span>`;
			}},
		]);

		html += section_table(__("Refunds / Returns"), "undo", d.refunds, [
			{ label: __("Invoice"), render: (r) => `<a class="ch-c360-link" data-doctype="Sales Invoice" data-name="${r.name}">${r.name}</a>` },
			{ label: __("Date"), render: (r) => frappe.datetime.str_to_user(r.posting_date) },
			{ label: __("Against"), render: (r) => r.return_against || "—" },
			{ label: __("Amount"), render: (r) => `₹${format_number(Math.abs(r.grand_total || 0))}` },
		]);

		html += section_table(__("Swap / Exchange"), "retweet", d.swap_invoices, [
			{ label: __("Invoice"), render: (r) => `<a class="ch-c360-link" data-doctype="Sales Invoice" data-name="${r.name}">${r.name}</a>` },
			{ label: __("Date"), render: (r) => frappe.datetime.str_to_user(r.posting_date) },
			{ label: __("Type"), render: (r) => frappe.utils.escape_html(r.custom_ch_sale_type || "") },
			{ label: __("Total"), render: (r) => `₹${format_number(r.grand_total || 0)}` },
		]);

		html += section_table(__("Coupon Usage"), "tag", d.coupon_usage, [
			{ label: __("Invoice"), render: (r) => `<a class="ch-c360-link" data-doctype="Sales Invoice" data-name="${r.name}">${r.name}</a>` },
			{ label: __("Date"), render: (r) => frappe.datetime.str_to_user(r.posting_date) },
			{ label: __("Coupon"), render: (r) => frappe.utils.escape_html(r.coupon_code || "") },
			{ label: __("Total"), render: (r) => `₹${format_number(r.grand_total || 0)}` },
		]);

		html += section_table(__("Escalations / Exceptions"), "exclamation-triangle", d.exceptions, [
			{ label: __("ID"), render: (r) => `<a class="ch-c360-link" data-doctype="CH Exception Request" data-name="${r.name}">${r.name}</a>` },
			{ label: __("Date"), render: (r) => frappe.datetime.str_to_user(r.creation) },
			{ label: __("Type"), render: (r) => frappe.utils.escape_html(r.exception_type || "") },
			{ label: __("Ref"), render: (r) => r.reference_name || "—" },
			{ label: __("Status"), render: (r) => {
				const cls = r.status === "Approved" ? "success" : r.status === "Rejected" ? "danger" : "warning";
				return `<span class="ch-pos-badge badge-${cls}">${r.status || "—"}</span>`;
			}},
		]);

		html += `</div>`;
		el.html(html);

		el.find(".ch-c360-link").on("click", function () {
			frappe.set_route("Form", $(this).data("doctype"), $(this).data("name"));
		});

		el.find(".ch-c360-edit-btn").on("click", () => {
			this._open_edit_dialog(panel, d);
		});
	}

	_open_edit_dialog(panel, d) {
		const dlg = new frappe.ui.Dialog({
			title: __("Edit Customer Details"),
			fields: [
				{
					fieldname: "customer_name",
					fieldtype: "Data",
					label: __("Customer Name"),
					default: d.customer_name || "",
					reqd: 1,
				},
				{
					fieldname: "mobile_no",
					fieldtype: "Data",
					label: __("Mobile Number"),
					default: d.mobile_no || "",
					description: __("10-digit Indian mobile number"),
				},
				{
					fieldname: "email_id",
					fieldtype: "Data",
					label: __("Email"),
					default: d.email_id || "",
					options: "Email",
				},
				{
					fieldname: "alternate_phone",
					fieldtype: "Data",
					label: __("Alternate Phone"),
					default: d.alternate_phone || "",
				},
				{
					fieldname: "whatsapp_number",
					fieldtype: "Data",
					label: __("WhatsApp Number"),
					default: d.whatsapp_number || "",
				},
				{
					fieldname: "info_section",
					fieldtype: "Section Break",
				},
				{
					fieldname: "membership_id",
					fieldtype: "Data",
					label: __("Membership ID"),
					default: d.membership_id || "",
					read_only: 1,
				},
			],
			primary_action_label: __("Save"),
			primary_action: (values) => {
				frappe.call({
					method: "ch_pos.api.pos_api.update_customer_details",
					args: {
						customer: d.customer,
						customer_name: values.customer_name,
						mobile_no: values.mobile_no || "",
						email_id: values.email_id || "",
						alternate_phone: values.alternate_phone || "",
						whatsapp_number: values.whatsapp_number || "",
					},
					freeze: true,
					freeze_message: __("Updating customer..."),
					callback: (r) => {
						if (r.message && r.message.ok) {
							dlg.hide();
							frappe.show_alert({ message: __("Customer details updated"), indicator: "green" });
							// Refresh the 360 view with updated data
							Object.assign(d, r.message);
							this._render_360(panel, d);
						}
					},
					error: () => {
						// Error toast shown by Frappe automatically (validation errors etc.)
					},
				});
			},
		});
		dlg.show();
	}
}
