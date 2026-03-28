/**
 * CH POS — Exception Request Workspace
 *
 * Allows store staff to:
 *   - Raise exception requests (price override, discount beyond limit, etc.)
 *   - Track pending / approved / rejected exceptions
 *   - Apply approved exceptions to the current transaction
 */
import { PosState, EventBus } from "../../state.js";

export class ExceptionWorkspace {
	constructor() {
		this._panel = null;
		EventBus.on("workspace:render", (ctx) => {
			if (ctx.mode !== "exceptions") return;
			this.render(ctx.panel);
		});
	}

	render(panel) {
		this._panel = panel;
		panel.html(`
			<div class="ch-pos-mode-panel">
				<div class="ch-mode-header">
					<h4>
						<span class="mode-icon" style="background:#fef3c7;color:#d97706">
							<i class="fa fa-exclamation-triangle"></i>
						</span>
						${__("Exception Requests")}
					</h4>
					<span class="ch-mode-hint">${__("Request price overrides, extra discounts, or policy exceptions")}</span>
				</div>

				<!-- Raise New Exception -->
				<div class="ch-pos-section-card" style="margin-bottom:var(--pos-space-md)">
					<div class="section-header">
						<i class="fa fa-plus-circle"></i> ${__("Raise New Exception")}
					</div>
					<div class="section-body ch-exc-form">
						${this._render_form()}
					</div>
				</div>

				<!-- My Pending Requests -->
				<div class="ch-pos-section-card" style="margin-bottom:var(--pos-space-md)">
					<div class="section-header" style="display:flex;align-items:center;justify-content:space-between">
						<span><i class="fa fa-clock-o"></i> ${__("My Requests")}</span>
						<button class="btn btn-xs btn-default ch-exc-refresh" style="border-radius:var(--pos-radius-sm)">
							<i class="fa fa-refresh"></i>
						</button>
					</div>
					<div class="section-body ch-exc-list">
						<div class="text-muted text-center" style="padding:16px">${__("Loading...")}</div>
					</div>
				</div>
			</div>
		`);

		this._bind(panel);
		this._load_exception_types(panel);
		this._load_my_requests(panel);
	}

	// ── Form ────────────────────────────────────────────────────

	_render_form() {
		return `
			<div class="row" style="gap:8px 0">
				<div class="col-sm-6">
					<label class="control-label">${__("Exception Type")} *</label>
					<select class="form-control ch-exc-type"></select>
				</div>
				<div class="col-sm-6">
					<label class="control-label">${__("Item")}</label>
					<div class="ch-exc-item-link"></div>
				</div>
				<div class="col-sm-6" style="margin-top:8px">
					<label class="control-label">${__("Original Value")}</label>
					<input type="number" class="form-control ch-exc-original" placeholder="Current price / limit" step="any" readonly>
				</div>
				<div class="col-sm-6" style="margin-top:8px">
					<label class="control-label">${__("Requested Value")}</label>
					<input type="number" class="form-control ch-exc-requested" placeholder="Requested price / amount" step="any">
				</div>
				<div class="col-sm-6" style="margin-top:8px">
					<label class="control-label">${__("Serial / IMEI")}</label>
					<input type="text" class="form-control ch-exc-serial" placeholder="Optional">
				</div>
				<div class="col-sm-6" style="margin-top:8px">
					<label class="control-label">${__("Customer")}</label>
					<div class="ch-exc-customer-link"></div>
				</div>
				<div class="col-sm-12" style="margin-top:8px">
					<label class="control-label">${__("Reason")} *</label>
					<textarea class="form-control ch-exc-reason" rows="2"
						placeholder="${__("Why is this exception needed?")}"></textarea>
				</div>
				<div class="col-sm-12" style="margin-top:12px">
					<button class="btn btn-primary btn-sm ch-exc-submit">
						<i class="fa fa-paper-plane"></i> ${__("Submit Request")}
					</button>
				</div>
			</div>
		`;
	}

	_bind(panel) {
		// Submit request
		panel.on("click", ".ch-exc-submit", () => this._submit_request(panel));
		panel.on("click", ".ch-exc-refresh", () => this._load_my_requests(panel));

		// Detail view
		panel.on("click", ".ch-exc-view", (e) => {
			const name = $(e.currentTarget).data("name");
			if (name) frappe.set_route("Form", "CH Exception Request", name);
		});

		// Item link control
		this._item_control = frappe.ui.form.make_control({
			df: { fieldtype: "Link", options: "Item", fieldname: "exc_item", placeholder: __("Select Item") },
			parent: panel.find(".ch-exc-item-link"),
			render_input: true,
		});

		
		this._item_control.$input.addClass("form-control");

		// Customer link control
		this._customer_control = frappe.ui.form.make_control({
			df: { fieldtype: "Link", options: "Customer", fieldname: "exc_customer", placeholder: __("Select Customer") },
			parent: panel.find(".ch-exc-customer-link"),
			render_input: true,
		});
		this._customer_control.$input.addClass("form-control");
	}

	_load_exception_types(panel) {
		frappe.xcall("frappe.client.get_list", {
			doctype: "CH Exception Type",
			filters: { enabled: 1 },
			fields: ["name", "exception_type"],
			order_by: "exception_type asc",
			limit_page_length: 0,
		}).then((types) => {
			const sel = panel.find(".ch-exc-type");
			sel.empty().append(`<option value="">${__("Select type...")}</option>`);
			(types || []).forEach((t) => {
				sel.append(`<option value="${frappe.utils.escape_html(t.name)}">${frappe.utils.escape_html(t.exception_type || t.name)}</option>`);
			});
		});
	}

	// ── Submit Request ──────────────────────────────────────────

	_submit_request(panel) {
		const exception_type = panel.find(".ch-exc-type").val();
		const reason = panel.find(".ch-exc-reason").val().trim();
		const requested_value = parseFloat(panel.find(".ch-exc-requested").val()) || 0;
		const original_value = parseFloat(panel.find(".ch-exc-original").val()) || 0;
		const serial_no = panel.find(".ch-exc-serial").val().trim();
		const item_code = this._item_control ? this._item_control.get_value() : "";
		const customer = this._customer_control ? this._customer_control.get_value() : "";

		if (!exception_type) {
			frappe.show_alert({ message: __("Select an exception type"), indicator: "orange" });
			return;
		}
		if (!reason) {
			frappe.show_alert({ message: __("Please provide a reason"), indicator: "orange" });
			return;
		}

		const btn = panel.find(".ch-exc-submit");
		btn.prop("disabled", true).html(`<i class="fa fa-spinner fa-spin"></i> ${__("Submitting...")}`);

		frappe.xcall(
			"ch_item_master.ch_item_master.exception_api.raise_exception",
			{
				exception_type,
				company: PosState.company,
				reason,
				requested_value,
				original_value,
				item_code,
				serial_no,
				store_warehouse: PosState.warehouse,
				pos_profile: PosState.pos_profile,
				customer,
			}
		).then((res) => {
			btn.prop("disabled", false).html(`<i class="fa fa-paper-plane"></i> ${__("Submit Request")}`);

			if (res.status === "Auto-Approved") {
				frappe.show_alert({ message: __("Exception auto-approved! Ref: {0}", [res.name]), indicator: "green" });
			} else {
				frappe.show_alert({ message: __("Request submitted: {0}. Status: {1}", [res.name, res.status]), indicator: "blue" });
			}

			// Clear form
			panel.find(".ch-exc-type").val("");
			panel.find(".ch-exc-reason").val("");
			panel.find(".ch-exc-requested, .ch-exc-original, .ch-exc-serial").val("");
			if (this._item_control) this._item_control.set_value("");
			if (this._customer_control) this._customer_control.set_value("");

			this._load_my_requests(panel);
		}).catch(() => {
			btn.prop("disabled", false).html(`<i class="fa fa-paper-plane"></i> ${__("Submit Request")}`);
		});
	}

	// ── My Requests List ────────────────────────────────────────

	_load_my_requests(panel) {
		const container = panel.find(".ch-exc-list");
		frappe.xcall(
			"ch_item_master.ch_item_master.exception_api.get_pending_exceptions",
			{
				company: PosState.company,
				store_warehouse: PosState.warehouse,
			}
		).then((rows) => {
			if (!rows || !rows.length) {
				container.html(`<div class="text-muted text-center" style="padding:16px">${__("No requests found")}</div>`);
				return;
			}
			let html = `<table class="table table-condensed table-hover" style="margin:0;font-size:13px">
				<thead><tr>
					<th>${__("ID")}</th>
					<th>${__("Type")}</th>
					<th>${__("Item")}</th>
					<th style="text-align:right">${__("Requested")}</th>
					<th>${__("Status")}</th>
					<th>${__("Raised")}</th>
					<th></th>
				</tr></thead><tbody>`;

			rows.forEach((r) => {
				const status_cls = {
					"Pending": "warning", "Approved": "success",
					"Rejected": "danger", "Expired": "secondary",
					"Auto-Approved": "success",
				}[r.status] || "default";

				html += `<tr>
					<td><a class="ch-exc-view" data-name="${frappe.utils.escape_html(r.name)}" style="cursor:pointer;color:var(--primary)">${frappe.utils.escape_html(r.name)}</a></td>
					<td>${frappe.utils.escape_html(r.exception_type || "")}</td>
					<td>${frappe.utils.escape_html(r.item_code || "—")}</td>
					<td style="text-align:right">${r.requested_value ? format_currency(r.requested_value) : "—"}</td>
					<td><span class="badge badge-${status_cls}">${frappe.utils.escape_html(r.status || "")}</span></td>
					<td>${r.raised_at ? frappe.datetime.prettyDate(r.raised_at) : "—"}</td>
					<td>
						${r.status === "Approved" ? `<button class="btn btn-xs btn-success ch-exc-view" data-name="${frappe.utils.escape_html(r.name)}" title="${__("View")}"><i class="fa fa-check"></i></button>` : ""}
					</td>
				</tr>`;
			});

			html += `</tbody></table>`;
			container.html(html);
		}).catch(() => {
			container.html(`<div class="text-muted text-center" style="padding:16px">${__("Error loading requests")}</div>`);
		});
	}
}

function format_currency(val) {
	return frappe.format(val, { fieldtype: "Currency" });
}
