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

		// Repair Closure Wizard
		panel.on("click", ".ch-rep-collect-payment", (e) => {
			const btn = $(e.currentTarget);
			if (btn.data("opening")) return;  // debounce double-click
			btn.data("opening", true).prop("disabled", true).html(`<i class="fa fa-spinner fa-spin"></i>`);
			const sr_name   = btn.data("name");
			const svc_order  = btn.data("service");
			const est_cost   = parseFloat(btn.data("cost")) || 0;
			const customer   = btn.data("customer") || PosState.customer || "";
			const technician = btn.data("technician") || "";
			const restore = () => btn.data("opening", false).prop("disabled", false).html(`<i class="fa fa-inr"></i> ${__("Collect")}`);
			this._show_repair_closure_dialog(panel, sr_name, {
				service_order: svc_order, estimated_cost: est_cost,
				customer, technician,
				on_close: restore,
			}).catch((err) => {
				console.error("Repair closure dialog error", err);
				restore();
			});
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
						data-name="${r.name}"
						data-service="${r.service_order || ""}"
						data-cost="${r.estimated_cost || 0}"
						data-customer="${frappe.utils.escape_html(r.customer || "")}"
						data-technician="${frappe.utils.escape_html(r.technician || "")}"
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

	/* ────────────────────────────────────────────────────────────────
	 * Repair Closure Wizard
	 * Multi-section dialog covering: Tech · QC · Parts · Payment · Delivery
	 * ──────────────────────────────────────────────────────────────── */
	_show_repair_closure_dialog(panel, sr_name, defaults = {}) {
		const mop_options = (PosState.payment_modes || []).map(m => m.mode_of_payment);
		if (!mop_options.length) mop_options.push("Cash", "UPI", "Card");

		// Fetch full SR data then show dialog
		return frappe.xcall("ch_pos.api.pos_api.get_repair_closure_data", {
			service_request: sr_name,
		}).then((d) => {
			const tech_options = (d.technicians || []).map(t => `<option value="${t.name}">${t.full_name || t.name}</option>`).join("");
			const mop_opts_html = mop_options.map(m => `<option value="${m}">${m}</option>`).join("");
			const est_cost = d.estimated_cost || defaults.estimated_cost || 0;

			// Build initial parts rows HTML
			const parts_rows_html = (d.spare_parts || []).map((p, i) =>
				this._part_row_html(i, p, mop_opts_html)
			).join("") || this._part_row_html(0, {}, mop_opts_html);

			const html = `
<div class="ch-closure-dialog" style="font-size:13px">

  <!-- Header -->
  <div style="background:var(--pos-accent-blue,#2563eb);color:#fff;border-radius:8px 8px 0 0;padding:12px 16px;margin:-15px -15px 16px -15px">
    <div style="font-weight:700;font-size:15px"><i class="fa fa-wrench" style="margin-right:6px"></i>${__("Repair Closure")} — ${frappe.utils.escape_html(sr_name)}</div>
    <div style="font-size:12px;opacity:.85;margin-top:2px">
      ${frappe.utils.escape_html(d.customer_name || d.customer || "")}
      ${d.device_item ? " · " + frappe.utils.escape_html(d.device_item) : ""}
      ${d.serial_no ? " · " + frappe.utils.escape_html(d.serial_no) : ""}
    </div>
  </div>

  <!-- 1. Technician -->
  <div class="ch-closure-section">
    <div class="ch-closure-section-title"><i class="fa fa-user-cog"></i> ${__("1. Technician")}</div>
    <div style="display:flex;gap:8px;align-items:center">
      <label style="min-width:90px;color:var(--text-muted)">${__("Assigned to")}</label>
      <select class="form-control form-control-sm ch-cld-technician" style="flex:1">
        <option value="">— ${__("Select")} —</option>
        ${tech_options}
      </select>
    </div>
  </div>

  <!-- 2. QC -->
  <div class="ch-closure-section">
    <div class="ch-closure-section-title"><i class="fa fa-check-circle"></i> ${__("2. Quality Check")}</div>
    <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:8px">
      ${["Pass","Fail","Not Repairable","Customer Cancelled"].map(v =>
        `<label class="ch-qc-opt" style="display:flex;align-items:center;gap:4px;cursor:pointer;padding:4px 10px;border:1px solid var(--pos-border-light,#ddd);border-radius:20px;transition:all .15s">
          <input type="radio" name="ch_cld_qc" value="${v}" ${v === "Pass" ? "checked" : ""}> ${__(v)}
         </label>`
      ).join("")}
    </div>
    <input type="text" class="form-control form-control-sm ch-cld-qc-remarks" placeholder="${__("Remarks (optional)")}">
  </div>

  <!-- 3. Spare Parts -->
  <div class="ch-closure-section">
    <div class="ch-closure-section-title" style="display:flex;align-items:center;gap:6px">
      <span><i class="fa fa-puzzle-piece"></i> ${__("3. Spare Parts")}</span>
      <button class="btn btn-xs btn-outline-primary ch-cld-add-part" style="margin-left:auto;border-radius:20px">
        <i class="fa fa-plus"></i> ${__("Add Part")}
      </button>
    </div>
    <table class="table table-sm" style="margin-bottom:4px">
      <thead><tr>
        <th style="width:35%">${__("Item")}</th>
        <th style="width:12%;text-align:center">${__("Qty")}</th>
        <th style="width:20%;text-align:right">${__("Rate")}</th>
        <th style="width:20%;text-align:right">${__("Amount")}</th>
        <th style="width:8%"></th>
      </tr></thead>
      <tbody class="ch-cld-parts-body">
        ${parts_rows_html}
      </tbody>
    </table>
    <div style="display:flex;gap:8px;align-items:center;margin-top:6px">
      <label style="min-width:120px">${__("Service Charge (₹)")}</label>
      <input type="number" class="form-control form-control-sm ch-cld-service-charge" value="${est_cost}" min="0" style="max-width:140px">
    </div>
    <div style="text-align:right;margin-top:8px;font-weight:600;font-size:14px">
      ${__("Parts Total")}: ₹<span class="ch-cld-parts-total">0.00</span>
      &nbsp;|&nbsp; ${__("Invoice Total")}: ₹<span class="ch-cld-grand-total">0.00</span>
    </div>
  </div>

  <!-- 4. Payment -->
  <div class="ch-closure-section">
    <div class="ch-closure-section-title" style="display:flex;align-items:center;gap:6px">
      <span><i class="fa fa-money"></i> ${__("4. Payment")}</span>
      <button class="btn btn-xs btn-outline-primary ch-cld-add-payment" style="margin-left:auto;border-radius:20px">
        <i class="fa fa-plus"></i> ${__("Add Row")}
      </button>
    </div>
    <table class="table table-sm">
      <thead><tr>
        <th style="width:35%">${__("Mode")}</th>
        <th style="width:25%;text-align:right">${__("Amount")}</th>
        <th style="width:30%">${__("Ref/UPI No.")}</th>
        <th style="width:10%"></th>
      </tr></thead>
      <tbody class="ch-cld-payments-body">
        <tr class="ch-cld-payment-row">
          <td><select class="form-control form-control-sm ch-cld-mop">${mop_opts_html}</select></td>
          <td><input type="number" class="form-control form-control-sm ch-cld-pay-amount text-right" min="0" placeholder="0.00"></td>
          <td><input type="text" class="form-control form-control-sm ch-cld-pay-ref" placeholder="${__("optional")}"></td>
          <td></td>
        </tr>
      </tbody>
    </table>
    <div style="display:flex;justify-content:space-between;font-size:12px;color:var(--text-muted)">
      <span>${__("Paid")}: ₹<span class="ch-cld-paid-total">0.00</span></span>
      <span>${__("Balance")}: ₹<span class="ch-cld-balance">0.00</span></span>
    </div>
  </div>

  <!-- 5. Delivery -->
  <div class="ch-closure-section">
    <div class="ch-closure-section-title"><i class="fa fa-handshake-o"></i> ${__("5. Delivery")}</div>
    <label style="display:flex;align-items:center;gap:8px;cursor:pointer;margin-bottom:8px">
      <input type="checkbox" class="ch-cld-delivery-ack" style="width:16px;height:16px">
      <span>${__("Customer has received the device")}</span>
    </label>
    <input type="text" class="form-control form-control-sm ch-cld-delivery-note" placeholder="${__("Delivery note (optional)")}">
  </div>

</div>
<style>
.ch-closure-section { border:1px solid var(--pos-border-light,#e5e7eb); border-radius:8px; padding:12px 14px; margin-bottom:10px; }
.ch-closure-section-title { font-weight:700; font-size:13px; margin-bottom:10px; color:#374151; }
.ch-qc-opt input { margin:0; }
.ch-qc-opt.active { background:#dbeafe; border-color:#2563eb; color:#2563eb; }
</style>`;

			const restore_btn = defaults.on_close || (() => {});
			const dlg = new frappe.ui.Dialog({
				title: __(""),
				size: "large",
				minimizable: false,
				primary_action_label: __("Close Repair & Create Invoice"),
				primary_action: () => this._submit_closure(dlg, panel, sr_name, mop_options),
				secondary_action_label: __("Cancel"),
				secondary_action: () => { dlg.hide(); restore_btn(); },
			});
			// Restore collect button when dialog is dismissed any way (escape / backdrop)
			dlg.$wrapper.on("hidden.bs.modal", () => restore_btn());
			dlg.$body.html(html);

			// Pre-fill technician
			const tech = defaults.technician || d.current_technician || "";
			if (tech) dlg.$body.find(".ch-cld-technician").val(tech);

			// Style QC radio buttons
			dlg.$body.on("change", "input[name=ch_cld_qc]", (e) => {
				dlg.$body.find(".ch-qc-opt").removeClass("active");
				$(e.target).closest(".ch-qc-opt").addClass("active");
			});
			dlg.$body.find("input[name=ch_cld_qc]:checked").closest(".ch-qc-opt").addClass("active");

			// Live totals recalc
			const recalc = () => {
				let parts_total = 0;
				dlg.$body.find(".ch-cld-part-row").each(function () {
					const qty = parseFloat($(this).find(".ch-cld-part-qty").val()) || 0;
					const rate = parseFloat($(this).find(".ch-cld-part-rate").val()) || 0;
					const amt = qty * rate;
					$(this).find(".ch-cld-part-amount").val(amt.toFixed(2));
					parts_total += amt;
				});
				const svc = parseFloat(dlg.$body.find(".ch-cld-service-charge").val()) || 0;
				const grand = parts_total + svc;
				dlg.$body.find(".ch-cld-parts-total").text(parts_total.toFixed(2));
				dlg.$body.find(".ch-cld-grand-total").text(grand.toFixed(2));

				let paid = 0;
				dlg.$body.find(".ch-cld-pay-amount").each(function () {
					paid += parseFloat($(this).val()) || 0;
				});
				dlg.$body.find(".ch-cld-paid-total").text(paid.toFixed(2));
				const bal = grand - paid;
				dlg.$body.find(".ch-cld-balance").text(bal.toFixed(2)).css("color", Math.abs(bal) < 0.01 ? "var(--pos-success,green)" : "var(--pos-error,red)");
			};

			dlg.$body.on("input change", ".ch-cld-part-qty,.ch-cld-part-rate,.ch-cld-service-charge,.ch-cld-pay-amount", recalc);

			// Auto-fill first payment row with grand total when service charge changes
			dlg.$body.on("blur", ".ch-cld-service-charge", () => {
				const grand = parseFloat(dlg.$body.find(".ch-cld-grand-total").text()) || 0;
				const first_pay = dlg.$body.find(".ch-cld-pay-amount").first();
				if (!(parseFloat(first_pay.val()) > 0)) first_pay.val(grand.toFixed(2)).trigger("input");
			});

			// Add part row
			dlg.$body.on("click", ".ch-cld-add-part", () => {
				const idx = dlg.$body.find(".ch-cld-part-row").length;
				dlg.$body.find(".ch-cld-parts-body").append(this._part_row_html(idx, {}, mop_opts_html));
				recalc();
			});
			dlg.$body.on("click", ".ch-cld-remove-part", (e) => {
				$(e.currentTarget).closest("tr").remove();
				recalc();
			});

			// Add payment row
			dlg.$body.on("click", ".ch-cld-add-payment", () => {
				const row = $(`<tr class="ch-cld-payment-row">
					<td><select class="form-control form-control-sm ch-cld-mop">${mop_opts_html}</select></td>
					<td><input type="number" class="form-control form-control-sm ch-cld-pay-amount text-right" min="0" placeholder="0.00"></td>
					<td><input type="text" class="form-control form-control-sm ch-cld-pay-ref" placeholder="${__("optional")}"></td>
					<td><button class="btn btn-xs btn-danger ch-cld-remove-payment" style="border-radius:50%;padding:1px 5px">&times;</button></td>
				</tr>`);
				dlg.$body.find(".ch-cld-payments-body").append(row);
			});
			dlg.$body.on("click", ".ch-cld-remove-payment", (e) => {
				$(e.currentTarget).closest("tr").remove();
				recalc();
			});

			// Item autocomplete for part rows
			let _item_search_timer = null;
			dlg.$body.on("input", ".ch-cld-part-name", function () {
				const inp = $(this);
				const q = inp.val().trim();
				const sug = inp.closest("td").find(".ch-item-suggestions");
				inp.closest("tr").find(".ch-cld-part-code").val(""); // clear resolved code
				clearTimeout(_item_search_timer);
				if (q.length < 2) { sug.hide(); return; }
				_item_search_timer = setTimeout(() => {
					frappe.call({
						method: "frappe.desk.search.search_link",
						args: { txt: q, doctype: "Item", ignore_user_permissions: 0, page_len: 8 },
						callback: (r) => {
							const results = r.message || [];
							if (!results.length) { sug.hide(); return; }
							sug.html(results.map(res =>
								`<div class="ch-item-sug-row" data-code="${frappe.utils.escape_html(res.value)}"
									style="padding:6px 10px;cursor:pointer;font-size:12px;border-bottom:1px solid #f0f0f0">
									<b>${frappe.utils.escape_html(res.value)}</b>
									${res.description ? `<span style="color:#888;margin-left:6px">${frappe.utils.escape_html(res.description)}</span>` : ""}
								</div>`
							).join("")).show();
						},
					});
				}, 300);
			});
			dlg.$body.on("click", ".ch-item-sug-row", function () {
				const code = $(this).data("code");
				const label = $(this).find("b").text();
				const td = $(this).closest("td");
				td.find(".ch-cld-part-code").val(code);
				td.find(".ch-cld-part-name").val(label);
				td.find(".ch-item-suggestions").hide();
			});
			dlg.$body.on("focusout", ".ch-cld-part-name", function () {
				// Delay hide to allow click on suggestion to register
				setTimeout(() => $(this).closest("td").find(".ch-item-suggestions").hide(), 200);
			});

			// Trigger initial recalc
			recalc();

			dlg.show();
		});
	}

	_part_row_html(i, p = {}, _mop_opts_html = "") {
		const item = frappe.utils.escape_html(p.spare_part_item || "");
		const name = frappe.utils.escape_html(p.item_name || "");
		const qty  = p.qty  || "";
		const rate = p.rate || "";
		const amount = (parseFloat(qty) * parseFloat(rate)) || "";
		// Display name = item_name if available, else item_code
		const display = name || item;
		return `<tr class="ch-cld-part-row">
			<td style="position:relative">
				<input type="hidden" class="ch-cld-part-code" value="${item}">
				<input type="text" class="form-control form-control-sm ch-cld-part-name" value="${display}"
					placeholder="${__("Type to search item…")}" autocomplete="off">
				<div class="ch-item-suggestions" style="display:none;position:absolute;top:100%;left:0;right:0;z-index:9999;background:#fff;border:1px solid #ddd;border-radius:4px;max-height:160px;overflow-y:auto;box-shadow:0 4px 12px rgba(0,0,0,.15)"></div>
			</td>
			<td><input type="number" class="form-control form-control-sm ch-cld-part-qty" value="${qty}" min="0" placeholder="1" style="text-align:center"></td>
			<td><input type="number" class="form-control form-control-sm ch-cld-part-rate" value="${rate}" min="0" placeholder="0.00" style="text-align:right"></td>
			<td><input type="number" class="form-control form-control-sm ch-cld-part-amount" value="${amount}" readonly style="text-align:right;background:transparent;border:none"></td>
			<td><button class="btn btn-xs btn-danger ch-cld-remove-part" style="border-radius:50%;padding:1px 5px">&times;</button></td>
		</tr>`;
	}

	_submit_closure(dlg, panel, sr_name, mop_options) {
		// Gather QC
		const qc_result = dlg.$body.find("input[name=ch_cld_qc]:checked").val() || "Pass";
		const qc_remarks = dlg.$body.find(".ch-cld-qc-remarks").val().trim();

		// Gather spare parts
		const spare_parts = [];
		dlg.$body.find(".ch-cld-part-row").each(function () {
			const code = $(this).find(".ch-cld-part-code").val().trim();
			const name = $(this).find(".ch-cld-part-name").val().trim();
			const qty  = parseFloat($(this).find(".ch-cld-part-qty").val()) || 0;
			const rate = parseFloat($(this).find(".ch-cld-part-rate").val()) || 0;
			if ((code || name) && qty) {
				spare_parts.push({ spare_part_item: code || name, item_name: name, qty, rate, uom: "Nos" });
			}
		});

		// Gather payments
		const payments = [];
		let pay_total = 0;
		dlg.$body.find(".ch-cld-payment-row").each(function () {
			const mop = $(this).find(".ch-cld-mop").val();
			const amt = parseFloat($(this).find(".ch-cld-pay-amount").val()) || 0;
			const ref = $(this).find(".ch-cld-pay-ref").val().trim();
			if (mop && amt > 0) {
				pay_total += amt;
				payments.push({ mode_of_payment: mop, amount: amt, reference_no: ref });
			}
		});

		const service_charge = parseFloat(dlg.$body.find(".ch-cld-service-charge").val()) || 0;
		const parts_total = spare_parts.reduce((s, p) => s + (p.qty * p.rate), 0);
		const grand_total = service_charge + parts_total;

		// Validation
		if (grand_total <= 0) {
			frappe.show_alert({ message: __("Service charge or spare parts amount must be greater than zero"), indicator: "red" });
			return;
		}
		if (!payments.length) {
			frappe.show_alert({ message: __("Add at least one payment row"), indicator: "red" });
			return;
		}
		if (Math.abs(pay_total - grand_total) > 0.01) {
			frappe.show_alert({
				message: __("Payment total ₹{0} does not match invoice total ₹{1}", [pay_total.toFixed(2), grand_total.toFixed(2)]),
				indicator: "red"
			});
			return;
		}

		const technician  = dlg.$body.find(".ch-cld-technician").val();
		const delivery_ack = dlg.$body.find(".ch-cld-delivery-ack").is(":checked") ? 1 : 0;
		const delivery_note = dlg.$body.find(".ch-cld-delivery-note").val().trim();

		dlg.get_primary_btn().prop("disabled", true).html(`<i class="fa fa-spinner fa-spin"></i> ${__("Processing…")}`);

		frappe.xcall("ch_pos.api.pos_api.close_repair_order", {
			service_request: sr_name,
			pos_profile: PosState.pos_profile,
			payments: JSON.stringify(payments),
			qc_result,
			qc_remarks,
			delivery_ack,
			delivery_note,
			technician,
			spare_parts: JSON.stringify(spare_parts),
			service_charge,
		}).then((r) => {
			dlg.hide();
			const se_msg = r.stock_entry ? __(" · Stock Entry: {0}", [r.stock_entry]) : "";
			frappe.msgprint({
				title: __("Repair Closed"),
				indicator: "green",
				message: `
					<div style="font-size:15px;font-weight:700;margin-bottom:8px">
						<i class="fa fa-check-circle" style="color:green"></i> ${__("Invoice {0} created", [r.invoice])}
					</div>
					<div>${__("Grand Total")}: <b>₹${r.grand_total}</b></div>
					<div style="color:var(--text-muted);font-size:12px;margin-top:4px">${r.invoice}${se_msg}</div>
				`,
			});
			this._load_pipeline(panel);
		}).catch(() => {
			dlg.get_primary_btn().prop("disabled", false).html(__("Close Repair & Create Invoice"));
		});
	}
}
