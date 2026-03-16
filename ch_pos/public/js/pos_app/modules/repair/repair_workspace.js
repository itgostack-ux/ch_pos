/**
 * CH POS — Repair Workspace (Guided Service Intake Console)
 *
 * Walk-in repair intake using sectioned card layout:
 * 1. Customer & Device  2. Issue Details  3. Condition
 * Creates GoFix Service Requests directly.
 */
import { PosState, EventBus } from "../../state.js";

export class RepairWorkspace {
	constructor() {
		EventBus.on("workspace:render", (ctx) => {
			if (ctx.mode !== "repair") return;
			this.render(ctx.panel);
		});
	}

	render(panel) {
		panel.html(`
			<div class="ch-pos-mode-panel">
				<div class="ch-mode-header">
					<h4>
						<span class="mode-icon" style="background:#dbeafe;color:#2563eb">
							<i class="fa fa-wrench"></i>
						</span>
						${__("Service Intake")}
					</h4>
					<span class="ch-mode-hint">${__("Create a walk-in GoFix Service Request from the POS counter")}</span>
				</div>

				<!-- Section 1: Customer & Device -->
				<div class="ch-pos-section-card" style="margin-bottom:var(--pos-space-md)">
					<div class="section-header"><i class="fa fa-user"></i> ${__("Customer & Device")}</div>
					<div class="section-body">
						<div style="display:grid;grid-template-columns:1fr 1fr;gap:var(--pos-space-md)">
							<div class="ch-pos-field-group">
								<label>${__("Customer")} <span style="color:var(--pos-danger)">*</span></label>
								<div class="ch-repair-customer-link"></div>
							</div>
							<div class="ch-pos-field-group">
								<label>${__("Contact Phone")} <span style="color:var(--pos-danger)">*</span></label>
								<input type="text" class="form-control ch-rep-phone" placeholder="${__("Phone number")}">
							</div>
							<div class="ch-pos-field-group">
								<label>${__("Device")} <span style="color:var(--pos-danger)">*</span></label>
								<div class="ch-repair-device-link"></div>
							</div>
							<div class="ch-pos-field-group">
								<label>${__("Serial / IMEI")}</label>
								<div class="ch-repair-serial-link"></div>
							</div>
						</div>
					</div>
				</div>

				<!-- Section 2: Condition & Accessories -->
				<div class="ch-pos-section-card" style="margin-bottom:var(--pos-space-md)">
					<div class="section-header"><i class="fa fa-clipboard"></i> ${__("Condition & Accessories")}</div>
					<div class="section-body">
						<div style="display:grid;grid-template-columns:1fr 1fr;gap:var(--pos-space-md)">
							<div class="ch-pos-field-group">
								<label>${__("Device Condition")} <span style="color:var(--pos-danger)">*</span></label>
								<select class="form-control ch-rep-condition">
									<option value="">${__("Select condition")}</option>
									<option value="Good">${__("Good")}</option>
									<option value="Minor Scratches">${__("Minor Scratches")}</option>
									<option value="Cracked Screen">${__("Cracked Screen")}</option>
									<option value="Damaged">${__("Damaged")}</option>
									<option value="Water Damage">${__("Water Damage")}</option>
								</select>
							</div>
							<div class="ch-pos-field-group">
								<label>${__("Accessories Received")}</label>
								<input type="text" class="form-control ch-rep-accessories" placeholder="${__("Charger, case, earphones...")}">
							</div>
						</div>
						<div class="ch-pos-field-group" style="margin-top:var(--pos-space-sm)">
							<label style="display:flex;align-items:center;gap:6px;font-weight:normal">
								<input type="checkbox" class="ch-rep-data-disclaimer">
								${__("Customer acknowledges data may be lost during repair")}
							</label>
						</div>
					</div>
				</div>

				<!-- Section 3: Issue Details -->
				<div class="ch-pos-section-card" style="margin-bottom:var(--pos-space-md)">
					<div class="section-header"><i class="fa fa-exclamation-circle"></i> ${__("Issue Details")}</div>
					<div class="section-body">
						<div style="display:grid;grid-template-columns:1fr 1fr;gap:var(--pos-space-md)">
							<div class="ch-pos-field-group">
								<label>${__("Issue Category")}</label>
								<div class="ch-repair-issue-cat-link"></div>
							</div>
							<div class="ch-pos-field-group">
								<label>${__("Warranty Status")}</label>
								<select class="form-control ch-rep-warranty">
									<option value="">${__("Select warranty status")}</option>
									<option value="Under Warranty">${__("Under Warranty")}</option>
									<option value="Out of Warranty">${__("Out of Warranty")}</option>
									<option value="No Warranty">${__("No Warranty")}</option>
								</select>
							</div>
						</div>
						<div class="ch-pos-field-group" style="margin-top:var(--pos-space-sm)">
							<label>${__("Issue Description")} <span style="color:var(--pos-danger)">*</span></label>
							<textarea class="form-control ch-rep-issue" rows="3"
								style="min-height:80px;resize:vertical"
								placeholder="${__("Describe the customer's issue...")}"></textarea>
						</div>
						<div style="display:grid;grid-template-columns:1fr 1fr;gap:var(--pos-space-md);margin-top:var(--pos-space-sm)">
							<div class="ch-pos-field-group">
								<label>${__("Priority")}</label>
								<select class="form-control ch-rep-priority">
									<option value="Medium">${__("Medium")}</option>
									<option value="Low">${__("Low")}</option>
									<option value="High">${__("High")}</option>
									<option value="Urgent">${__("Urgent")}</option>
								</select>
							</div>
							<div class="ch-pos-field-group">
								<label>${__("Estimated Hours")}</label>
								<input type="number" class="form-control ch-rep-est-hours" min="0.5" step="0.5" placeholder="${__("e.g. 2")}">
							</div>
						</div>
					</div>
				</div>

				<!-- Success result (injected after creation) -->
				<div class="ch-rep-result-area"></div>

				<!-- Pending Repairs Pipeline -->
				<div class="ch-pos-section-card" style="margin-bottom:var(--pos-space-md)">
					<div class="section-header" style="display:flex;align-items:center;justify-content:space-between">
						<span><i class="fa fa-clock-o"></i> ${__("Pending Store Repairs")}</span>
						<button class="btn btn-xs btn-default ch-rep-refresh-pipeline" style="border-radius:var(--pos-radius-sm)">
							<i class="fa fa-refresh"></i>
						</button>
					</div>
					<div class="section-body ch-rep-pipeline">
						<div class="text-muted text-center" style="padding:16px">${__("Loading...")}</div>
					</div>
				</div>

				<!-- Actions -->
				<div class="ch-mode-actions">
					<button class="btn btn-success ch-rep-quick-job" style="flex:1">
						<i class="fa fa-bolt"></i> ${__("Quick Job Card")}
					</button>
					<button class="btn btn-primary ch-rep-create" style="flex:1">
						<i class="fa fa-plus-circle"></i> ${__("Create Service Request")}
					</button>
					<button class="btn btn-outline-secondary ch-rep-clear">
						<i class="fa fa-eraser"></i> ${__("Clear")}
					</button>
				</div>
			</div>
		`);
		this._bind(panel);
		this._load_pipeline(panel);
	}

	_bind(panel) {
		const cust_field = frappe.ui.form.make_control({
			df: { fieldname: "customer", fieldtype: "Link", options: "Customer", placeholder: __("Select customer") },
			parent: panel.find(".ch-repair-customer-link"),
			render_input: true,
		});
		const device_field = frappe.ui.form.make_control({
			df: { fieldname: "device_item", fieldtype: "Link", options: "Item", placeholder: __("Device model") },
			parent: panel.find(".ch-repair-device-link"),
			render_input: true,
		});
		const serial_field = frappe.ui.form.make_control({
			df: { fieldname: "serial_no", fieldtype: "Link", options: "Serial No", placeholder: __("IMEI / Serial") },
			parent: panel.find(".ch-repair-serial-link"),
			render_input: true,
		});
		const issue_cat_field = frappe.ui.form.make_control({
			df: { fieldname: "issue_category", fieldtype: "Link", options: "Issue Category", placeholder: __("Issue category") },
			parent: panel.find(".ch-repair-issue-cat-link"),
			render_input: true,
		});

		panel.on("click", ".ch-rep-create", () => {
			const customer = cust_field.get_value();
			const device_item = device_field.get_value();
			const phone = panel.find(".ch-rep-phone").val().trim();
			const issue_desc = panel.find(".ch-rep-issue").val().trim();
			const priority = panel.find(".ch-rep-priority").val() || "Medium";
			const device_condition = panel.find(".ch-rep-condition").val() || "";
			const accessories = panel.find(".ch-rep-accessories").val().trim();
			const data_disclaimer = panel.find(".ch-rep-data-disclaimer").is(":checked") ? 1 : 0;

			if (!customer || !phone || !device_item || !issue_desc) {
				frappe.show_alert({ message: __("Customer, phone, device, and issue description are required"), indicator: "orange" });
				return;
			}

			frappe.xcall("frappe.client.insert", {
				doc: {
					doctype: "Service Request",
					customer: customer,
					contact_number: phone,
					device_item: device_item,
					serial_no: serial_field.get_value() || "",
					issue_category: issue_cat_field.get_value() || "",
					issue_description: issue_desc,
					warranty_status: panel.find(".ch-rep-warranty").val() || "",
					device_condition: device_condition,
					accessories_received: accessories,
					data_backup_disclaimer: data_disclaimer,
					mode_of_service: "Walk-in",
					company: PosState.company || "",
					source_warehouse: PosState.warehouse || "",
					service_date: frappe.datetime.get_today(),
					decision: "Draft",
					priority: priority,
					walkin_source: "POS Counter",
				},
			}).then((doc) => {
				frappe.show_alert({
					message: `${__("Service Request")} <b>${doc.name}</b> ${__("created")}`,
					indicator: "green",
				});
				panel.find(".ch-rep-result-area").html(`
					<div class="ch-rep-result">
						<i class="fa fa-check-circle" style="font-size:18px;color:var(--pos-success)"></i>
						<span><b>${doc.name}</b> ${__("created successfully")}</span>
						<div style="margin-left:auto;display:flex;gap:6px">
							<button class="btn btn-sm btn-primary ch-rep-accept-job"
								data-name="${doc.name}" style="border-radius:var(--pos-radius-sm);font-weight:700">
								<i class="fa fa-cog"></i> ${__("Accept & Create Job")}
							</button>
							<button class="btn btn-sm btn-outline-primary ch-rep-open-sr"
								data-name="${doc.name}" style="border-radius:var(--pos-radius-sm);font-weight:700">
								<i class="fa fa-external-link"></i> ${__("Open in GoFix")}
							</button>
						</div>
					</div>`);

				panel.find("input, textarea").val("");
				panel.find("select").prop("selectedIndex", 0);
				cust_field.set_value("");
				device_field.set_value("");
				serial_field.set_value("");
				issue_cat_field.set_value("");
				// Increment walk-in + repair intake counter
				if (PosState.pos_profile) {
					frappe.call({ method: "ch_pos.api.pos_api.log_walkin", args: { pos_profile: PosState.pos_profile, source: "POS Counter" } });
					frappe.call({ method: "ch_pos.api.pos_api.increment_repair_intake_count", args: { pos_profile: PosState.pos_profile } });
				}
			});
		});

		panel.on("click", ".ch-rep-open-sr", function () {
			frappe.set_route("Form", "Service Request", $(this).data("name"));
		});

		// Accept & Create Job — chains SR → SO → Job Assignment in one click
		panel.on("click", ".ch-rep-accept-job", (e) => {
			const btn = $(e.currentTarget);
			const sr_name = btn.data("name");
			btn.prop("disabled", true).html(`<i class="fa fa-spinner fa-spin"></i> ${__("Creating...")}`);

			frappe.xcall("ch_pos.api.pos_api.create_repair_job_from_pos", {
				service_request: sr_name,
			}).then((result) => {
				btn.replaceWith(`
					<span class="text-success" style="font-weight:700;font-size:13px">
						<i class="fa fa-check"></i> ${__("Job")} ${result.job_assignment} ${__("created")}
					</span>
				`);
				frappe.show_alert({
					message: __("Job Assignment {0} created via Service Order {1}", [result.job_assignment, result.service_order]),
					indicator: "green",
				});
				this._load_pipeline(panel);
			}).catch(() => {
				btn.prop("disabled", false).html(`<i class="fa fa-cog"></i> ${__("Accept & Create Job")}`);
			});
		});

		// Refresh pipeline
		panel.on("click", ".ch-rep-refresh-pipeline", () => this._load_pipeline(panel));

		// Collect Payment for completed repairs
		panel.on("click", ".ch-rep-collect-payment", (e) => {
			const btn = $(e.currentTarget);
			const sr_name = btn.data("name");
			const service_order = btn.data("service");
			const estimated_cost = parseFloat(btn.data("cost")) || 0;

			frappe.prompt([
				{ fieldtype: "Currency", fieldname: "amount", label: __("Repair Charge (₹)"), default: estimated_cost, reqd: 1 },
				{ fieldtype: "Select", fieldname: "mode_of_payment", label: __("Payment Mode"),
					options: PosState.payment_modes.map(m => m.mode_of_payment).join("\n") || "Cash\nUPI\nCredit Card",
					default: "Cash", reqd: 1 },
				{ fieldtype: "Data", fieldname: "upi_txn_id", label: __("UPI/Ref No (optional)") },
			], (values) => {
				btn.prop("disabled", true).html(`<i class="fa fa-spinner fa-spin"></i>`);
				frappe.xcall("ch_pos.api.pos_api.collect_repair_payment", {
					service_request: sr_name,
					service_order,
					amount: values.amount,
					mode_of_payment: values.mode_of_payment,
					upi_txn_id: values.upi_txn_id || "",
					pos_profile: PosState.pos_profile,
					customer: PosState.customer || "Walk-in Customer",
				}).then((r) => {
					frappe.show_alert({ message: __("Payment collected — Invoice {0}", [r.invoice]), indicator: "green" });
					this._load_pipeline(panel);
				}).catch(() => {
					btn.prop("disabled", false).html(`<i class="fa fa-inr"></i> ${__("Collect")}`);
				});
			}, __("Collect Repair Payment"), __("Collect"));
		});

		panel.on("click", ".ch-rep-clear", () => {
			panel.find("input, select, textarea").val("");
			panel.find(".ch-rep-data-disclaimer").prop("checked", false);
			panel.find(".ch-rep-result-area").empty();
			cust_field.set_value("");
			device_field.set_value("");
			serial_field.set_value("");
			issue_cat_field.set_value("");
		});

		// ── Quick Job Card: SR → Accept → Job Assignment in one call ──
		panel.on("click", ".ch-rep-quick-job", () => {
			const customer = cust_field.get_value();
			const device_item = device_field.get_value();
			const phone = panel.find(".ch-rep-phone").val().trim();
			const issue_desc = panel.find(".ch-rep-issue").val().trim();
			const priority = panel.find(".ch-rep-priority").val() || "Medium";
			const est_hours = parseFloat(panel.find(".ch-rep-est-hours").val()) || undefined;
			const device_condition = panel.find(".ch-rep-condition").val() || undefined;
			const accessories = panel.find(".ch-rep-accessories").val().trim() || undefined;
			const data_disclaimer = panel.find(".ch-rep-data-disclaimer").is(":checked") ? 1 : 0;

			if (!customer || !phone || !device_item || !issue_desc) {
				frappe.show_alert({ message: __("Customer, phone, device, and issue description are required"), indicator: "orange" });
				return;
			}

			const btn = panel.find(".ch-rep-quick-job");
			btn.prop("disabled", true).html(`<i class="fa fa-spinner fa-spin"></i> ${__("Creating Job Card...")}`);

			frappe.xcall("ch_pos.api.pos_api.create_quick_job_card", {
				customer,
				contact_number: phone,
				device_item,
				issue_description: issue_desc,
				serial_no: serial_field.get_value() || undefined,
				issue_category: issue_cat_field.get_value() || undefined,
				warranty_status: panel.find(".ch-rep-warranty").val() || undefined,
				priority,
				estimated_hours: est_hours,
				device_condition,
				accessories_received: accessories,
				data_backup_disclaimer: data_disclaimer,
			}).then((result) => {
				btn.prop("disabled", false).html(`<i class="fa fa-bolt"></i> ${__("Quick Job Card")}`);
				frappe.show_alert({
					message: __("Job Card {0} created (SR: {1}, SO: {2})", [
						result.job_assignment, result.service_request, result.service_order
					]),
					indicator: "green",
				});

				panel.find(".ch-rep-result-area").html(`
					<div class="ch-rep-result" style="margin-bottom:var(--pos-space-md)">
						<i class="fa fa-check-circle" style="font-size:18px;color:var(--pos-success)"></i>
						<span><b>${__("Job Card")}: ${result.job_assignment}</b></span>
						<div style="margin-left:auto;display:flex;gap:6px">
							<span class="badge badge-info" style="font-size:11px">${__("SR")}: ${result.service_request}</span>
							<span class="badge badge-warning" style="font-size:11px">${__("SO")}: ${result.service_order}</span>
							<button class="btn btn-sm btn-outline-primary ch-rep-open-sr"
								data-name="${result.service_request}" style="border-radius:var(--pos-radius-sm);font-weight:700">
								<i class="fa fa-external-link"></i> ${__("Open in GoFix")}
							</button>
						</div>
					</div>`);

				// Clear form
				panel.find("input, textarea").val("");
				panel.find("select").prop("selectedIndex", 0);
				cust_field.set_value("");
				device_field.set_value("");
				serial_field.set_value("");
				issue_cat_field.set_value("");
				this._load_pipeline(panel);
			}).catch(() => {
				btn.prop("disabled", false).html(`<i class="fa fa-bolt"></i> ${__("Quick Job Card")}`);
			});
		});
	}

	_load_pipeline(panel) {
		const el = panel.find(".ch-rep-pipeline");
		if (!PosState.pos_profile) {
			el.html(`<div class="text-muted text-center" style="padding:16px">${__("No POS profile loaded")}</div>`);
			return;
		}
		frappe.xcall("ch_pos.api.pos_api.get_store_repairs", {
			pos_profile: PosState.pos_profile,
		}).then((repairs) => {
			if (!repairs || !repairs.length) {
				el.html(`<div class="text-muted text-center" style="padding:16px">${__("No pending repairs for this store")}</div>`);
				return;
			}
			const rows = repairs.map((r) => {
				const status_cls = r.job_status === "Completed" ? "success"
					: r.job_assignment ? "info" : r.decision === "Accepted" ? "warning" : "muted";
				const pipeline_step = r.job_assignment
					? `<span class="ch-pos-badge badge-info">${__("Job")}: ${r.job_assignment}</span>`
					: r.service_order
						? `<span class="ch-pos-badge badge-warning">${__("SO")}: ${r.service_order}</span>`
						: `<span class="ch-pos-badge badge-muted">${__("Pending")}</span>`;
				const collect_btn = r.job_status === "Completed" && !r.billed
					? `<button class="btn btn-xs btn-success ch-rep-collect-payment"
						data-name="${r.name}" data-service="${r.service_order || ""}" data-cost="${r.estimated_cost || 0}"
						style="border-radius:var(--pos-radius-sm);white-space:nowrap;font-weight:700">
						<i class="fa fa-inr"></i> ${__("Collect")}
					</button>`
					: r.billed
						? `<span class="ch-pos-badge badge-success"><i class="fa fa-check"></i> ${__("Paid")}</span>`
						: "";
				return `
					<div style="display:flex;align-items:center;gap:10px;padding:8px 0;border-bottom:1px solid var(--pos-border-light,#eee)">
						<div style="flex:1;min-width:0">
							<div style="font-weight:600;font-size:13px">${frappe.utils.escape_html(r.name)}</div>
							<div class="text-muted" style="font-size:12px">
								${frappe.utils.escape_html(r.customer || "")} · ${frappe.utils.escape_html(r.device_item || "")}
								${r.estimated_cost ? ` · ₹${r.estimated_cost}` : ""}
							</div>
						</div>
						<div style="display:flex;gap:4px;align-items:center">
							${pipeline_step}
							<span class="ch-pos-badge badge-${status_cls}">${r.job_status || r.decision || "Draft"}</span>
						</div>
						${!r.job_assignment ? `
							<button class="btn btn-xs btn-outline-primary ch-rep-accept-job" data-name="${r.name}"
								style="border-radius:var(--pos-radius-sm);white-space:nowrap">
								<i class="fa fa-cog"></i> ${__("Create Job")}
							</button>` : ""}
						${collect_btn}
					</div>`;
			}).join("");
			el.html(rows);
		}).catch(() => {
			el.html(`<div class="text-muted text-center" style="padding:16px">${__("Could not load repairs")}</div>`);
		});
	}
}
