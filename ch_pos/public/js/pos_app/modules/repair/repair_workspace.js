/**
 * CH POS — Repair Workspace (Guided Service Intake Console)
 *
 * Walk-in repair intake using sectioned card layout:
 * 1. Customer & Device  2. Issue Details  3. Condition
 * Creates GoFix Service Requests directly.
 */
import { PosState, EventBus } from "../../state.js";
import { assert_india_phone } from "../../shared/helpers.js";

// The device-intake vocabulary is owned by GoFix and published on boot. Offering
// a condition the Service Request DocType does not accept is what made
// "Minor Scratches" fail on save.
const FALLBACK_DEVICE_CONDITIONS = [
	"Good", "Minor Scratches", "Cracked Screen", "Damaged", "Water Damaged", "Broken",
];

function deviceConditionOptions() {
	const fromBoot = frappe.boot && frappe.boot.gofix_device_conditions;
	return (Array.isArray(fromBoot) && fromBoot.length) ? fromBoot : FALLBACK_DEVICE_CONDITIONS;
}

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
								<label style="display:flex;align-items:center;gap:8px">
									${__("Customer")} <span style="color:var(--pos-danger)">*</span>
									<button class="btn btn-xs btn-outline-primary ch-rep-new-customer"
										style="border-radius:var(--pos-radius-sm);font-size:11px;padding:1px 8px;margin-left:auto">
										<i class="fa fa-plus"></i> ${__("New")}
									</button>
								</label>
								<div class="ch-repair-customer-link"></div>
							</div>
							<div class="ch-pos-field-group">
								<label>${__("Contact Phone")} <span style="color:var(--pos-danger)">*</span></label>
								<input type="text" class="form-control ch-rep-phone" placeholder="${__("Phone number")}">
							</div>
							<div class="ch-pos-field-group">
								<label>${__("Category")}</label>
								<div class="ch-repair-category-link"></div>
							</div>
							<div class="ch-pos-field-group">
								<label>${__("Brand")}</label>
								<div class="ch-repair-brand-link"></div>
							</div>
							<div class="ch-pos-field-group">
								<label>${__("Model")}</label>
								<div class="ch-repair-model-link"></div>
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
									${deviceConditionOptions().map(c =>
										`<option value="${frappe.utils.escape_html(c)}">${__(c)}</option>`).join("")}
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

						<!-- Condition evidence. Photographed WITH the customer present,
						     while the device is still in their sight. -->
						<div class="ch-pos-field-group" style="margin-top:var(--pos-space-md)">
							<label style="display:flex;align-items:center;gap:8px">
								<i class="fa fa-camera"></i> ${__("Intake Photos")}
								<span class="text-muted" style="font-weight:400;font-size:11px">
									${__("Photograph the device as received — this is what settles a dispute later")}
								</span>
							</label>
							<input type="file" class="ch-rep-photos" accept="image/*" capture="environment"
								multiple style="display:none">
							<button class="btn btn-sm btn-default ch-rep-photo-add" style="border-radius:var(--pos-radius-sm)">
								<i class="fa fa-camera"></i> ${__("Add Photo")}
							</button>
							<div class="ch-rep-photo-strip" style="display:flex;flex-wrap:wrap;gap:8px;margin-top:8px"></div>
						</div>
					</div>
				</div>

				<!-- Section 3: Issue Details -->
				<div class="ch-pos-section-card" style="margin-bottom:var(--pos-space-md)">
					<div class="section-header"><i class="fa fa-exclamation-circle"></i> ${__("Issue Details")}</div>
					<div class="section-body">
						<div style="display:grid;grid-template-columns:1fr 1fr;gap:var(--pos-space-md)">
							<div class="ch-pos-field-group">
								<label>${__("Issue Categories")}</label>
								<div class="ch-repair-issue-cats">
									<div class="ch-repair-issue-cat-link" style="margin-bottom:4px"></div>
									<div class="ch-rep-issue-tags" style="display:flex;flex-wrap:wrap;gap:4px"></div>
								</div>
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
								<label>${__("Coupon / Voucher")}</label>
								<div class="ch-repair-coupon-link"></div>
								<small class="text-muted">${__("Take it now. Validity is judged on today's intake, so the customer keeps it even if billing happens after it expires.")}</small>
							</div>
							<div class="ch-pos-field-group">
								<label>${__("Estimated Delivery Date & Time")}</label>
								<input type="datetime-local" class="form-control ch-rep-promised">
								<small class="text-muted">${__("The date and time you are giving the customer. A countdown runs against this for the whole repair.")}</small>
							</div>
						</div>
						<div class="ch-pos-field-group" style="margin-top:var(--pos-space-sm)">
							<label>${__("Assign to Technician")}</label>
							<select class="form-control ch-rep-technician">
								<option value="">${__("Unassigned — nobody is recorded as working on it")}</option>
							</select>
							<small class="text-muted">${__("Analysis time starts being recorded against whoever you pick.")}</small>
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

		// The Ops Hub sends a completed repair here to be billed, because an invoice
		// raised there has no pos_profile and no payment rows and so never reaches
		// the till settlement. Surface which ticket arrived; the counter still adds
		// it to the cart and takes the tender.
		let handoff = null;
		try {
			handoff = localStorage.getItem("ch_pos_pending_repair_bill");
			if (handoff) localStorage.removeItem("ch_pos_pending_repair_bill");
		} catch (e) {
			handoff = null;
		}
		if (handoff) {
			panel.find(".ch-mode-header").after(`
				<div class="ch-pos-section-card" style="margin-bottom:var(--pos-space-md);border-left:3px solid var(--pos-primary,#6366f1)">
					<div class="section-body" style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">
						<i class="fa fa-shopping-cart"></i>
						<b>${frappe.utils.escape_html(handoff)}</b>
						<span>${__("is ready to bill.")}</span>
						<span class="text-muted">${__("Find it in the pipeline below and add it to the cart — it can share one invoice with accessories, plans, discounts and vouchers.")}</span>
					</div>
				</div>`);
		}
	}

	/**
	 * Fill the intake technician picker.
	 *
	 * Company-scoped server-side (get_technicians_for_grade defaults to the
	 * caller's company), so a counter cannot enumerate another company's staff.
	 * A failure leaves the select on "Unassigned" rather than blocking intake —
	 * taking the device in matters more than naming who will look at it.
	 */
	_load_technicians(panel) {
		const $sel = panel.find(".ch-rep-technician");
		if (!$sel.length) return;
		frappe.xcall(
			"gofix.gofix_services.page.gofix_ops_hub.gofix_ops_hub.get_technicians_for_grade",
			{ company: PosState.company || undefined }
		).then((res) => {
			const rows = (res && (res.technicians || res.rows)) || (Array.isArray(res) ? res : []);
			rows.forEach((t) => {
                const label = t.employee_name || t.name;
                const load = t.active_jobs ? ` (${__("{0} open", [t.active_jobs])})` : "";
				$sel.append(
					$("<option>").attr("value", t.name).text(label + load)
				);
			});
		}).catch(() => {
			/* leave it on Unassigned */
		});
	}

	_bind(panel) {
		this._load_technicians(panel);

		const cust_field = frappe.ui.form.make_control({
			df: { fieldname: "customer", fieldtype: "Link", options: "Customer", placeholder: __("Select customer") },
			parent: panel.find(".ch-repair-customer-link"),
			render_input: true,
		});
		// Auto-populate phone when customer is selected from awesomplete
		if (cust_field.$input) {
			cust_field.$input.on("awesomplete-selectcomplete", () => {
				setTimeout(() => {
					const cust = cust_field.get_value();
					if (cust) {
						frappe.db.get_value("Customer", cust, "mobile_no").then(r => {
							if (r && r.message && r.message.mobile_no) {
								panel.find(".ch-rep-phone").val(r.message.mobile_no);
							}
						});
					}
				}, 100);
			});
		}
		// A customer arriving with a dead handset cannot read out its full item
		// name, and the advisor cannot type one they have never seen. These three
		// narrow the device list down from what is actually visible on the
		// counter: category, then brand, then model. All three are optional --
		// an advisor who knows the item can still type it straight into Device.
		// Predeclared: each handler clears the fields below it, which are defined
		// after it. const would put those in the temporal dead zone and turn an
		// early onchange into a ReferenceError rather than a no-op.
		let brand_field, model_field, device_field;
		const category_field = frappe.ui.form.make_control({
			df: { fieldname: "ch_category", fieldtype: "Link", options: "CH Category",
				placeholder: __("Category"),
				get_query: () => ({ query: "ch_pos.api.item_search.search_categories" }),
				onchange: () => {
					// A narrower choice cannot outlive a wider one changing, or the
					// device list silently filters to nothing and reads as missing data.
					brand_field?.set_value("");
					model_field?.set_value("");
					device_field?.set_value("");
				} },
			parent: panel.find(".ch-repair-category-link"),
			render_input: true,
		});
		brand_field = frappe.ui.form.make_control({
			df: { fieldname: "device_brand", fieldtype: "Link", options: "Brand",
				placeholder: __("Brand"),
				get_query: () => ({
					query: "ch_pos.api.item_search.search_brands",
					filters: category_field.get_value() ? { ch_category: category_field.get_value() } : {},
				}),
				onchange: () => {
					model_field?.set_value("");
					device_field?.set_value("");
				} },
			parent: panel.find(".ch-repair-brand-link"),
			render_input: true,
		});
		model_field = frappe.ui.form.make_control({
			df: { fieldname: "device_model", fieldtype: "Link", options: "CH Model",
				placeholder: __("Model"),
				get_query: () => {
					const filters = {};
					if (category_field.get_value()) filters.ch_category = category_field.get_value();
					if (brand_field.get_value()) filters.brand = brand_field.get_value();
					return { query: "ch_pos.api.item_search.search_ch_models", filters };
				},
				onchange: () => device_field?.set_value("") },
			parent: panel.find(".ch-repair-model-link"),
			render_input: true,
		});
		device_field = frappe.ui.form.make_control({
			df: { fieldname: "device_item", fieldtype: "Link", options: "Item", placeholder: __("Device model"),
			get_query: () => {
				// Stock is deliberately not consulted: intake records the device a
				// customer walked in with, which the branch may well not sell.
				const filters = { has_variants: 0 };
				if (category_field.get_value()) filters.ch_category = category_field.get_value();
				if (brand_field.get_value()) filters.brand = brand_field.get_value();
				if (model_field.get_value()) filters.ch_model = model_field.get_value();
				return { filters };
			} },
			parent: panel.find(".ch-repair-device-link"),
			render_input: true,
		});
		// A customer walks in with THEIR device. Its IMEI is almost never in the
		// Serial No table — that table holds stock this company sold or bought.
		// A Link control refuses anything it cannot find, so a first-time
		// customer's device could not be recorded at all. Free text, with known
		// serials offered as suggestions: if it exists we bind to it (and pick up
		// warranty), otherwise it is accepted as the customer's own IMEI.
		// Deliberately NOT a Link: the "create a new..." sentinel Link controls
		// append has been saved as a docname on this bench before.
		// Captured at the counter on purpose: a repair is invoiced days or weeks
		// after the device is handed over, and a coupon judged on the invoice
		// date would deny a customer who presented a valid one and then waited
		// for us. The server stamps the capture and tests validity against the
		// intake date.
		const coupon_field = frappe.ui.form.make_control({
			df: { fieldname: "coupon_code", fieldtype: "Link", options: "Coupon Code",
				placeholder: __("Coupon code, if the customer has one") },
			parent: panel.find(".ch-repair-coupon-link"),
			render_input: true,
		});
		const serial_field = frappe.ui.form.make_control({
			df: {
				fieldname: "serial_no",
				fieldtype: "Data",
				placeholder: __("IMEI / Serial — type the customer's own if it is not in stock"),
			},
			parent: panel.find(".ch-repair-serial-link"),
			render_input: true,
		});
		panel.find(".ch-repair-serial-link").append(
			`<div class="ch-rep-serial-hint text-muted" style="font-size:11px;margin-top:4px"></div>`
			+ `<div class="ch-rep-cover" style="margin-top:6px"></div>`);

		const $serialInput = serial_field.$input;
		const serialHint = panel.find(".ch-rep-serial-hint");
		const serialAC = new Awesomplete($serialInput[0], {
			minChars: 3, maxItems: 8, autoFirst: false,
		});

		const coverBox = panel.find(".ch-rep-cover");

		// What cover the device carries, shown before it is taken in. The
		// counter previously had to guess warranty status or take the customer's
		// word for it -- and the server refuses "Under Warranty" with nothing
		// behind it, so guessing wrong blocked the intake. Three different
		// things can cover a repair and different people settle each, so they
		// are listed rather than collapsed into a yes/no.
		const describeCover = (value) => {
			const v = (value || "").trim();
			if (!v) { coverBox.html(""); return; }
			frappe.xcall("ch_pos.api.repair.get_device_coverage", {
				serial_no: v, company: PosState.company || "",
			}).then((res) => {
				if ((serial_field.get_value() || "").trim() !== v) return;   // stale
				const rows = (res && res.cover) || [];
				if (!rows.length) {
					coverBox.html(`<div class="text-muted" style="font-size:11px">`
						+ `<i class="fa fa-info-circle"></i> `
						+ __("No live cover found — this is a paid repair.") + `</div>`);
					const $w = panel.find(".ch-rep-warranty");
					if ($w.val() === "Under Warranty") $w.val("No Warranty");
					return;
				}
				const chip = (r) => {
					const kind = r.kind === "vas" ? __("VAS Claim")
						: r.kind === "spare_warranty" ? __("Part Warranty")
						: __("Repair Warranty");
					const bits = [frappe.utils.escape_html(r.label || "")];
					if (r.expires_on) bits.push(__("until {0}", [frappe.datetime.str_to_user(r.expires_on)]));
					if (r.claim_against) bits.push(__("settled by: {0}", [frappe.utils.escape_html(r.claim_against)]));
					return `<div style="font-size:11px;padding:4px 8px;border-radius:6px;`
						+ `background:var(--pos-success-light,#e8f5e9);margin-bottom:4px">`
						+ `<b>${kind}</b> — ${bits.join(" · ")}</div>`;
				};
				coverBox.html(rows.map(chip).join(""));
				// Cover exists, so the honest default is Under Warranty. The
				// Service Request re-runs this lookup on save; this only spares
				// the advisor a choice the record has already made.
				panel.find(".ch-rep-warranty").val("Under Warranty");
			});
		};

		let serialTimer = null;
		const describeSerial = (value) => {
			const v = (value || "").trim();
			if (!v) { serialHint.html(""); coverBox.html(""); return; }
			describeCover(v);
			frappe.xcall("ch_pos.api.repair.describe_device_serial", { serial_no: v })
				.then((info) => {
					if ((serial_field.get_value() || "").trim() !== v) return;   // stale
					if (info && info.known) {
						serialHint.html(
							`<i class="fa fa-check-circle text-success"></i> `
							+ `${frappe.utils.escape_html(info.item_name || info.item_code || "")}`
							+ (info.warranty ? ` · <b>${frappe.utils.escape_html(info.warranty)}</b>` : ""));
					} else {
						serialHint.html(
							`<i class="fa fa-user"></i> `
							+ __("Not in stock records — recorded as the customer's own IMEI."));
					}
				}).catch(() => serialHint.html(""));
		};

		$serialInput.on("input", () => {
			const txt = ($serialInput.val() || "").trim();
			clearTimeout(serialTimer);
			serialTimer = setTimeout(() => {
				describeSerial(txt);
				if (txt.length < 3) { serialAC.list = []; return; }
				frappe.call({
					method: "frappe.desk.search.search_link",
					args: { doctype: "Serial No", txt: txt, page_length: 8 },
					callback: (r) => {
						serialAC.list = (r.message || []).map((x) => x.value);
					},
				});
			}, 250);
		});

		// ── Arriving from the Service Queue ──────────────────────────────
		// The queue hands the token over here instead of opening its own dialog.
		// Consumed once: a refresh must not silently re-apply a stale token.
		const intakeToken = PosState.repairIntakeToken || null;
		PosState.repairIntakeToken = null;
		this._intakeToken = intakeToken;

		// ── Issue Category Multiselect (tag-based) ──
		const selected_issues = [];
		const issue_cat_field = frappe.ui.form.make_control({
			df: { fieldname: "issue_category", fieldtype: "Link", options: "Issue Category", placeholder: __("Add issue category...") },
			parent: panel.find(".ch-repair-issue-cat-link"),
			render_input: true,
		});
		const _render_issue_tags = () => {
			const container = panel.find(".ch-rep-issue-tags");
			container.empty();
			selected_issues.forEach((cat, idx) => {
				container.append(`
					<span class="badge" style="background:#e8f0fe;color:#1a73e8;padding:4px 10px;border-radius:12px;font-size:12px;display:inline-flex;align-items:center;gap:4px">
						${frappe.utils.escape_html(cat)}
						<i class="fa fa-times ch-rep-remove-issue" data-idx="${idx}" style="cursor:pointer;opacity:0.7"></i>
					</span>
				`);
			});
		};
		// Frappe Link uses awesomplete — listen to selection event, not "change"
		if (issue_cat_field.$input) {
			issue_cat_field.$input.on("awesomplete-selectcomplete", () => {
				setTimeout(() => {
					const val = issue_cat_field.get_value();
					if (val && !selected_issues.includes(val)) {
						selected_issues.push(val);
						_render_issue_tags();
					}
					issue_cat_field.set_value("");
				}, 100);
			});
		}
		panel.on("click", ".ch-rep-remove-issue", function () {
			selected_issues.splice($(this).data("idx"), 1);
			_render_issue_tags();
		});

		// ── New Customer quick-create ──
		panel.on("click", ".ch-rep-new-customer", () => {
			window.ch_open_new_customer_dialog({
				company: PosState.company,
				on_success: (name, mobile) => {
					cust_field.set_value(name);
					if (mobile) panel.find(".ch-rep-phone").val(mobile);
				},
				on_use_existing: (customer, cname) => {
					cust_field.set_value(customer);
				},
			});
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
			if (!assert_india_phone(panel.find(".ch-rep-phone")[0], phone)) return;

			// Build issue_lines from multiselect tags
			const issue_lines = selected_issues.map(cat => ({
				issue_category: cat,
				reported_by: "Customer",
				status: "Open",
			}));
			// Keep first category as primary issue_category for backward compat
			const primary_issue = selected_issues.length ? selected_issues[0] : "";

			// Server API inserts AND submits — POS-raised requests must land as
			// submitted docs so they show in Service Hub / GoFix Ops Hub.
			frappe.xcall("ch_pos.api.repair.create_service_intake_from_pos", {
				pos_profile: PosState.pos_profile,
				data: {
					customer: customer,
					contact_number: phone,
					device_item: device_item,
					serial_no: serial_field.get_value() || "",
					issue_category: primary_issue,
					issue_lines: issue_lines,
					issue_description: issue_desc,
					warranty_status: panel.find(".ch-rep-warranty").val() || "",
					device_condition: device_condition,
					accessories_received: accessories,
					data_backup_disclaimer: data_disclaimer,
					mode_of_service: "Walk-in",
					company: PosState.company || "",
					source_warehouse: PosState.warehouse || "",
					service_date: frappe.datetime.get_today(),
					priority: priority,
					walkin_source: "POS Counter",
					// Starts a Diagnosis Job Assignment so Analysis time is
					// attributed from the moment the device is taken in.
					diagnosis_technician: panel.find(".ch-rep-technician").val() || "",
					// An absolute moment, not a duration: "4 hours" taken in at
					// 6pm means tomorrow morning to a customer and 10pm to a
					// spreadsheet. The counter states the actual time instead.
					promised_completion_datetime: panel.find(".ch-rep-promised").val() || "",
					coupon_code: coupon_field.get_value() || "",
					// Set only when the counter came from the Service Queue; the
					// server closes that token against the new request.
					source_token: this._intakeToken ? this._intakeToken.name : "",
				},
			}).then((doc) => {
				frappe.show_alert({
					message: `${__("Service Request")} <b>${doc.name}</b> ${__("created")}`,
					indicator: "green",
				});
				this._upload_intake_photos(doc.name, pendingPhotos.splice(0));
				renderPhotoStrip();
				panel.find(".ch-rep-result-area").html(`
					<div class="ch-rep-result">
						<i class="fa fa-check-circle" style="font-size:18px;color:var(--pos-success)"></i>
						<span><b>${doc.name}</b> ${__("created & submitted")}</span>
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
				coupon_field.set_value("");
				issue_cat_field.set_value("");
				selected_issues.length = 0;
				_render_issue_tags();
				// Create a walk-in token for this repair intake — but not when the
				// ticket came FROM a token, or the queue gains a duplicate entry
				// for a customer who is already standing at the counter.
				const cameFromToken = !!this._intakeToken;
				this._intakeToken = null;
				if (PosState.pos_profile && !cameFromToken) {
					frappe.call({
						method: "ch_pos.api.token_api.log_counter_walkin",
						args: { pos_profile: PosState.pos_profile, visit_purpose: "Repair" },
					});
				}
			});
		});

		if (intakeToken) {
			// Everything the kiosk already asked the customer, carried across so
			// the counter re-types nothing.
			panel.find(".ch-rep-phone").val(intakeToken.customer_phone || "");
			panel.find(".ch-rep-issue").val(intakeToken.issue_description || "");
			if (intakeToken.device_condition) {
				panel.find(".ch-rep-condition").val(intakeToken.device_condition);
			}
			if (intakeToken.issue_category) {
				selected_issues.push(intakeToken.issue_category);
				_render_issue_tags();
			}
			// The kiosk records the device as free text ("Apple iphone 12"); the
			// catalogue item is chosen here if the counter can identify it.
			const deviceText = [intakeToken.device_brand, intakeToken.device_model]
				.filter(Boolean).join(" ") || intakeToken.device_type || "";
			if (intakeToken.customer_phone) {
				frappe.xcall("ch_pos.api.token_api.lookup_walkin_customer", {
					phone: intakeToken.customer_phone,
				}).then((m) => {
					if (m && m.customer) cust_field.set_value(m.customer);
				}).catch(() => {});
			}
			panel.find(".ch-mode-header").after(`
				<div class="ch-pos-section-card" style="margin-bottom:var(--pos-space-md);border-left:3px solid var(--pos-primary,#6366f1)">
					<div class="section-body" style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">
						<i class="fa fa-ticket"></i>
						<b>${frappe.utils.escape_html(intakeToken.token_display || intakeToken.name)}</b>
						<span>${frappe.utils.escape_html(intakeToken.customer_name || "")}</span>
						${deviceText ? `<span class="text-muted">${frappe.utils.escape_html(deviceText)}</span>` : ""}
						<span class="text-muted" style="margin-left:auto">${__("The token closes when this request is created.")}</span>
					</div>
				</div>`);
		}

		// ── Intake photos ────────────────────────────────────────────────
		// Held in the browser until the ticket exists: there is nothing to
		// attach them to until the Service Request has a name, and asking the
		// counter to photograph the device a second time afterwards is how
		// evidence stops being collected at all.
		const pendingPhotos = [];
		this._pendingPhotos = pendingPhotos;

		const renderPhotoStrip = () => {
			const strip = panel.find(".ch-rep-photo-strip");
			if (!pendingPhotos.length) { strip.html(""); return; }
			strip.html(pendingPhotos.map((ph, i) => `
				<div style="position:relative;width:72px;height:72px;border-radius:var(--pos-radius-sm);overflow:hidden;border:1px solid var(--border-color)">
					<img src="${ph.dataUrl}" alt="${frappe.utils.escape_html(ph.file.name)}"
						style="width:100%;height:100%;object-fit:cover">
					<button class="btn btn-xs ch-rep-photo-drop" data-idx="${i}"
						title="${__("Remove")}"
						style="position:absolute;top:2px;right:2px;padding:0 5px;background:rgba(0,0,0,0.6);color:#fff;border:0;border-radius:3px">&times;</button>
				</div>`).join(""));
		};

		panel.on("click", ".ch-rep-photo-add", (e) => {
			e.preventDefault();
			panel.find(".ch-rep-photos").trigger("click");
		});

		panel.on("change", ".ch-rep-photos", (e) => {
			const files = Array.from(e.currentTarget.files || []);
			e.currentTarget.value = "";                      // allow re-picking the same file
			files.forEach((file) => {
				if (!/^image\//.test(file.type)) {
					frappe.show_alert({ message: __("{0} is not an image", [file.name]), indicator: "orange" });
					return;
				}
				const reader = new FileReader();
				reader.onload = () => {
					pendingPhotos.push({ file, dataUrl: reader.result });
					renderPhotoStrip();
				};
				reader.readAsDataURL(file);
			});
		});

		panel.on("click", ".ch-rep-photo-drop", (e) => {
			e.preventDefault();
			pendingPhotos.splice(parseInt($(e.currentTarget).data("idx"), 10), 1);
			renderPhotoStrip();
		});

		panel.on("click", ".ch-rep-open-sr", function () {
			frappe.set_route("Form", "Service Request", $(this).data("name"));
		});

		// Accept & Create Job — chains SR → SO → Job Assignment in one click
		panel.on("click", ".ch-rep-accept-job", (e) => {
			e.preventDefault();
			e.stopPropagation();
			const btn = $(e.currentTarget);
			const sr_name = btn.data("name");
			console.log("[CH-REPAIR] Accept & Create Job clicked, SR:", sr_name);
			if (!sr_name) {
				frappe.msgprint({ title: __("Error"), message: __("Service Request name not found on button"), indicator: "red" });
				return;
			}
			btn.prop("disabled", true).html(`<i class="fa fa-spinner fa-spin"></i> ${__("Creating...")}`);

			frappe.call({
				method: "ch_pos.api.pos_api.create_repair_job_from_pos",
				args: { service_request: sr_name },
				callback: (r) => {
					console.log("[CH-REPAIR] Job created successfully:", r.message);
					const result = r.message;
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
				},
				error: (r) => {
					console.error("[CH-REPAIR] Job creation failed:", r);
					btn.prop("disabled", false).html(`<i class="fa fa-cog"></i> ${__("Accept & Create Job")}`);
				},
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
			selected_issues.length = 0;
			_render_issue_tags();
		});
	}

	/**
	 * Upload the photos taken at the counter and attach them to the ticket.
	 *
	 * Reported honestly: the ticket is already created and the device is on the
	 * counter, so a failed upload must not look like a failed intake — but it
	 * must not pass silently either, or a counter believes it has evidence it
	 * does not have.
	 */
	_upload_intake_photos(sr_name, photos) {
		if (!photos || !photos.length) return;

		const uploads = photos.map((ph) => new Promise((resolve) => {
			const fd = new FormData();
			fd.append("file", ph.file, ph.file.name);
			fd.append("is_private", 1);
			fd.append("doctype", "Service Request");
			fd.append("docname", sr_name);
			fetch("/api/method/upload_file", {
				method: "POST",
				headers: { "X-Frappe-CSRF-Token": frappe.csrf_token },
				body: fd,
			})
				.then((r) => r.json())
				.then((r) => {
					const url = r && r.message && r.message.file_url;
					if (!url) return resolve(false);
					return frappe.xcall(
						"gofix.gofix_services.page.gofix_ops_hub.gofix_ops_hub.add_device_photo",
						{ sr_name: sr_name, file_url: url, stage: "Intake" }
					).then(() => resolve(true)).catch(() => resolve(false));
				})
				.catch(() => resolve(false));
		}));

		Promise.all(uploads).then((results) => {
			const ok = results.filter(Boolean).length;
			const failed = results.length - ok;
			if (ok) {
				frappe.show_alert({
					message: __("{0} intake photo(s) attached to {1}", [ok, sr_name]),
					indicator: "green",
				});
			}
			if (failed) {
				frappe.msgprint({
					title: __("Photos Not Attached"),
					message: __("{0} of {1} intake photo(s) could not be attached to {2}. "
						+ "The request itself was created. Re-take them from the ticket "
						+ "before the device leaves the counter.",
						[failed, results.length, sr_name]),
					indicator: "orange",
				});
			}
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
				const collect_btn = (r.job_status === "Completed" || r.status === "Completed") && !r.billed
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
		}).catch((err) => {
			el.html(`<div class="text-muted text-center" style="padding:16px">${__("Could not load repairs")}</div>`);
			ch_pos_show_error(err, __("Load Repairs Failed"));
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
			pos_profile: PosState.pos_profile,
		}).then((d) => {
			const tech_options = (d.technicians || []).map(t => `<option value="${t.name}">${t.full_name || t.name}</option>`).join("");
			const mop_opts_html = mop_options.map(m => `<option value="${m}">${m}</option>`).join("");
			const est_cost = d.estimated_cost || defaults.estimated_cost || 0;

			// Build initial parts rows HTML
			const parts_rows_html = (d.spare_parts || []).map((p, i) =>
				this._part_row_html(i, p, mop_opts_html)
			).join("") || `<tr><td colspan="5" class="text-muted text-center">${__("No consumed spares to bill")}</td></tr>`;

			// Solutions summary
			const solutions_html = (d.solutions || []).length ? d.solutions.map(s => {
				const cls = s.status === "Completed" ? "success" : s.status === "Skipped" ? "muted" : "warning";
				return `<span class="ch-pos-badge badge-${cls}" style="margin:2px 4px 2px 0">${frappe.utils.escape_html(s.repair_solution || s.issue_category)}: ${s.status}</span>`;
			}).join("") : `<span class="text-muted">${__("No solutions recorded")}</span>`;

			// Service items summary
			const svc_items_html = (d.service_items || []).length ? d.service_items.map(s =>
				`<div style="display:flex;justify-content:space-between;padding:2px 0;font-size:12px">
					<span>${frappe.utils.escape_html(s.item_name || s.item_code)}</span>
					<span style="font-weight:600">₹${format_number(s.rate)}</span>
				</div>`
			).join("") : "";

			// Status info
			const status_cls = d.status === "Completed" ? "success" : d.status === "Invoiced" ? "blue" : "warning";
			const priority_cls = d.priority === "High" ? "danger" : d.priority === "Medium" ? "warning" : "muted";

			const html = `
<div class="ch-closure-dialog" style="font-size:13px">

  <!-- Header -->
  <div style="background:linear-gradient(135deg,#1e40af,#2563eb);color:#fff;border-radius:8px 8px 0 0;padding:14px 18px;margin:-15px -15px 16px -15px">
    <div style="display:flex;align-items:center;justify-content:space-between">
      <div style="font-weight:700;font-size:16px;letter-spacing:0.3px"><i class="fa fa-wrench" style="margin-right:8px"></i>${__("Repair Closure")}</div>
      <span style="font-size:13px;background:rgba(255,255,255,.2);padding:3px 10px;border-radius:12px;font-weight:600">${frappe.utils.escape_html(sr_name)}</span>
    </div>
    <div style="display:flex;gap:12px;margin-top:8px;font-size:12px;opacity:.9;flex-wrap:wrap">
      <span><i class="fa fa-user" style="width:14px"></i> ${frappe.utils.escape_html(d.customer_name || d.customer || "")}</span>
      ${d.device_item ? `<span><i class="fa fa-mobile" style="width:14px"></i> ${frappe.utils.escape_html(d.device_item)}</span>` : ""}
      ${d.serial_no ? `<span><i class="fa fa-barcode" style="width:14px"></i> ${frappe.utils.escape_html(d.serial_no)}</span>` : ""}
    </div>
    <div style="display:flex;gap:8px;margin-top:8px">
      <span class="ch-pos-badge badge-${status_cls}" style="font-size:11px">${d.status || "Draft"}</span>
      ${d.priority ? `<span class="ch-pos-badge badge-${priority_cls}" style="font-size:11px">${d.priority}</span>` : ""}
      ${d.service_order ? `<span style="font-size:11px;opacity:.8"><i class="fa fa-file-text-o"></i> ${frappe.utils.escape_html(d.service_order)}</span>` : ""}
      ${d.service_invoice ? `<span class="ch-pos-badge badge-success" style="font-size:11px"><i class="fa fa-check"></i> ${d.service_invoice}</span>` : ""}
    </div>
  </div>

  ${d.service_invoice ? `
    <div style="background:#dcfce7;border:1px solid #86efac;border-radius:8px;padding:12px 16px;margin-bottom:12px;text-align:center">
      <i class="fa fa-check-circle" style="color:#16a34a;font-size:18px"></i>
      <div style="font-weight:700;margin-top:4px">${__("Already Invoiced")}: <a href="/app/sales-invoice/${d.service_invoice}" target="_blank">${d.service_invoice}</a></div>
    </div>
  ` : ""}

  ${d.billing_location && d.billing_location.requires_remote_otp && !d.billing_location.otp_verified && !d.service_invoice ? `
    <div class="ch-cld-remote-otp" style="background:#fffbeb;border:1px solid #fbbf24;border-radius:8px;padding:12px 16px;margin-bottom:12px">
      <div style="font-weight:700;color:#92400e;margin-bottom:4px">
        <i class="fa fa-map-marker"></i> ${__("Device is not at its home store")}
      </div>
      <div style="font-size:12px;color:#78350f;margin-bottom:8px">
        ${__("Device at")}: <b>${frappe.utils.escape_html(d.billing_location.device_at || __("in transit"))}</b>
        &nbsp;·&nbsp; ${__("Home store")}: <b>${frappe.utils.escape_html(d.billing_location.home_store || "")}</b><br>
        ${__("Record the return transfer to bill normally, or take the customer's OTP consent to bill here.")}
      </div>
      <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
        <button type="button" class="btn btn-sm btn-warning ch-cld-send-otp">
          <i class="fa fa-paper-plane"></i> ${__("Send OTP (WhatsApp / Email)")}
        </button>
        <input type="text" class="form-control form-control-sm ch-cld-remote-otp-input"
          placeholder="${__("Enter customer OTP")}" maxlength="6"
          style="width:160px;letter-spacing:3px;text-align:center;font-weight:700">
      </div>
    </div>
  ` : ""}

  <!-- Repair Summary (read-only context) -->
  <div class="ch-closure-section" style="background:#f8fafc;border-color:#e2e8f0">
    <div class="ch-closure-section-title" style="margin-bottom:6px"><i class="fa fa-clipboard"></i> ${__("Repair Summary")}</div>
    ${d.issue_category ? `<div style="font-size:12px;margin-bottom:4px"><b>${__("Issue")}:</b> ${frappe.utils.escape_html(d.issue_category)}</div>` : ""}
    <div style="margin-bottom:4px">${solutions_html}</div>
    ${svc_items_html ? `<div style="border-top:1px solid #e2e8f0;padding-top:6px;margin-top:6px">${svc_items_html}</div>` : ""}
  </div>

  <!-- 1. QC -->
  <div class="ch-closure-section">
    <div class="ch-closure-section-title"><i class="fa fa-check-circle"></i> ${__("1. Quality Check")}</div>
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
	  <span><i class="fa fa-puzzle-piece"></i> ${__("2. Spare Parts")}</span>
	  <span class="text-muted" style="margin-left:auto;font-size:11px">${__("From submitted spare usage")}</span>
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
      <input type="number" class="form-control form-control-sm ch-cld-service-charge" value="${est_cost}" min="0" style="max-width:140px;font-weight:600">
    </div>
    <div class="ch-cld-totals-bar">
      ${__("Parts Total")}: ₹<span class="ch-cld-parts-total">0.00</span>
      &nbsp;|&nbsp; ${__("Invoice Total")}: <b>₹<span class="ch-cld-grand-total">0.00</span></b>
    </div>
  </div>

  <!-- 4. Payment -->
  <div class="ch-closure-section">
    <div class="ch-closure-section-title" style="display:flex;align-items:center;gap:6px">
      <span><i class="fa fa-credit-card"></i> ${__("3. Payment")}</span>
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
          <td><input type="number" class="form-control form-control-sm ch-cld-pay-amount text-right" min="0" value="${est_cost}" placeholder="0.00"></td>
          <td><input type="text" class="form-control form-control-sm ch-cld-pay-ref" placeholder="${__("optional")}"></td>
          <td></td>
        </tr>
      </tbody>
    </table>
    <div class="ch-cld-payment-summary">
      <span>${__("Paid")}: ₹<span class="ch-cld-paid-total">0.00</span></span>
      <span>${__("Balance")}: ₹<span class="ch-cld-balance">0.00</span></span>
    </div>
  </div>

  <!-- 5. Delivery -->
  <div class="ch-closure-section">
    <div class="ch-closure-section-title"><i class="fa fa-handshake-o"></i> ${__("4. Delivery")}</div>
    <label style="display:flex;align-items:center;gap:8px;cursor:pointer;margin-bottom:8px">
      <input type="checkbox" class="ch-cld-delivery-ack" style="width:16px;height:16px">
      <span>${__("Customer has received the device")}</span>
    </label>
    <input type="text" class="form-control form-control-sm ch-cld-delivery-note" placeholder="${__("Delivery note (optional)")}">
  </div>

</div>
<style>
.ch-closure-section { border:1px solid #e5e7eb; border-radius:8px; padding:12px 14px; margin-bottom:10px; }
.ch-closure-section-title { font-weight:700; font-size:13px; margin-bottom:10px; color:#374151; }
.ch-qc-opt input { margin:0; }
.ch-qc-opt.active { background:#dbeafe; border-color:#2563eb; color:#1d4ed8; font-weight:600; }
.ch-cld-totals-bar { text-align:right; margin-top:10px; font-size:14px; padding:8px 12px; background:#f0fdf4; border-radius:6px; border:1px solid #bbf7d0; }
.ch-cld-payment-summary { display:flex; justify-content:space-between; font-size:13px; padding:6px 0; font-weight:600; }
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

			// Store technician from SR data (no need to show in dialog)
			this._current_technician = d.current_technician || defaults.technician || "";

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

			// Remote-billing OTP: send consent OTP to the customer
			dlg.$body.on("click", ".ch-cld-send-otp", (e) => {
				const btn = $(e.currentTarget);
				btn.prop("disabled", true).html(`<i class="fa fa-spinner fa-spin"></i> ${__("Sending…")}`);
				frappe.xcall("gofix.gofix_services.api.request_remote_billing_otp", {
					service_request: sr_name,
				}).then((r) => {
					frappe.show_alert({ message: r.message || __("OTP sent"), indicator: "green" });
					btn.html(`<i class="fa fa-refresh"></i> ${__("Resend OTP")}`).prop("disabled", false);
					dlg.$body.find(".ch-cld-remote-otp-input").focus();
				}).catch((err) => {
					frappe.show_alert({ message: err.message || __("Could not send OTP"), indicator: "red" });
					btn.html(`<i class="fa fa-paper-plane"></i> ${__("Send OTP (WhatsApp / Email)")}`).prop("disabled", false);
				});
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

			// Trigger initial recalc
			recalc();

			dlg.show();
		});
	}

	_part_row_html(i, p = {}, _mop_opts_html = "") {
		const usage = frappe.utils.escape_html(p.spare_usage || "");
		const item = frappe.utils.escape_html(p.spare_part_item || "");
		const name = frappe.utils.escape_html(p.item_name || "");
		const qty  = p.qty  || "";
		const rate = p.rate || "";
		const amount = (parseFloat(qty) * parseFloat(rate)) || "";
		const warranty = parseInt(p.warranty_months) || 0;
		const warranty_badge = warranty
			? `<span style="display:inline-block;font-size:10px;padding:1px 6px;border-radius:10px;background:#dbeafe;color:#1d4ed8;font-weight:600;margin-top:2px">${warranty} mo warranty</span>`
			: "";
		const from_mapping = p.from_mapping ? `<span style="display:inline-block;font-size:10px;padding:1px 6px;border-radius:10px;background:#fef3c7;color:#92400e;margin-top:2px">auto</span>` : "";
		// Display name = item_name if available, else item_code
		const display = name || item;
		return `<tr class="ch-cld-part-row">
			<td style="position:relative">
				<input type="hidden" class="ch-cld-spare-usage" value="${usage}">
				<input type="hidden" class="ch-cld-part-code" value="${item}">
				<input type="text" class="form-control form-control-sm ch-cld-part-name" value="${display}"
					readonly style="background:transparent;border:none">
				<div style="display:flex;gap:4px;flex-wrap:wrap">${warranty_badge}${from_mapping}</div>
			</td>
			<td><input type="number" class="form-control form-control-sm ch-cld-part-qty" value="${qty}" readonly style="text-align:center;background:transparent;border:none"></td>
			<td><input type="number" class="form-control form-control-sm ch-cld-part-rate" value="${rate}" readonly style="text-align:right;background:transparent;border:none"></td>
			<td><input type="number" class="form-control form-control-sm ch-cld-part-amount" value="${amount}" readonly style="text-align:right;background:transparent;border:none"></td>
			<td><i class="fa fa-lock text-muted" title="${__("Posted stock usage")}"></i></td>
		</tr>`;
	}

	_submit_closure(dlg, panel, sr_name, mop_options) {
		// Gather QC
		const qc_result = dlg.$body.find("input[name=ch_cld_qc]:checked").val() || "Pass";
		const qc_remarks = dlg.$body.find(".ch-cld-qc-remarks").val().trim();

		// Gather spare parts
		const spare_parts = [];
		dlg.$body.find(".ch-cld-part-row").each(function () {
			const spare_usage = $(this).find(".ch-cld-spare-usage").val().trim();
			const code = $(this).find(".ch-cld-part-code").val().trim();
			const name = $(this).find(".ch-cld-part-name").val().trim();
			const qty  = parseFloat($(this).find(".ch-cld-part-qty").val()) || 0;
			const rate = parseFloat($(this).find(".ch-cld-part-rate").val()) || 0;
			if ((code || name) && qty) {
				spare_parts.push({ spare_usage, spare_part_item: code || name, item_name: name, qty, rate, uom: "Nos" });
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

		const technician  = this._current_technician || "";
		const delivery_ack = dlg.$body.find(".ch-cld-delivery-ack").is(":checked") ? 1 : 0;
		const delivery_note = dlg.$body.find(".ch-cld-delivery-note").val().trim();
		// Off-store billing consent OTP (block shown only when device is away)
		const remote_otp = (dlg.$body.find(".ch-cld-remote-otp-input").val() || "").trim();
		if (dlg.$body.find(".ch-cld-remote-otp").length && !remote_otp) {
			frappe.show_alert({
				message: __("Device is not at its home store — send the customer an OTP and enter it to bill here."),
				indicator: "orange",
			});
			return;
		}

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
			remote_otp,
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
		}).catch((err) => {
			dlg.get_primary_btn().prop("disabled", false).html(__("Close Repair & Create Invoice"));
			ch_pos_show_error(err, __("Repair Closure Failed"));
		});
	}
}
