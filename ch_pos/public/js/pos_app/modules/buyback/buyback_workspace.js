/**
 * CH POS — Buyback Workspace (Embedded Operational)
 *
 * Full buyback assessment flow inside POS:
 * - Search by mobile/IMEI/assessment ID
 * - Recent assessments
 * - Assessment detail card with stage tracker
 * - Valuation block
 * - Convert to exchange / resume actions
 * - New assessment dialog
 */
import { PosState, EventBus } from "../../state.js";
import { format_number } from "../../shared/helpers.js";

const STAGES = ["Draft", "Inspection", "Grading", "Pricing", "Approved", "Complete"];

export class BuybackWorkspace {
	constructor() {
		this._active_assessment = null;
		EventBus.on("workspace:render", (ctx) => {
			if (ctx.mode !== "buyback") return;
			this.render(ctx.panel);
		});
	}

	render(panel) {
		panel.html(`
			<div class="ch-pos-mode-panel">
				<!-- Header -->
				<div class="ch-mode-header">
					<h4>
						<span class="mode-icon" style="background:var(--pos-warning-light);color:#92400e">
							<i class="fa fa-exchange"></i>
						</span>
						${__("Buyback & Exchange")}
					</h4>
					<span class="ch-mode-hint">${__("Assess devices, manage trade-ins, apply exchange credit")}</span>
				</div>

				<!-- Search + Actions Row -->
				<div style="display:flex;gap:10px;align-items:stretch;margin-bottom:16px;">
					<div class="ch-pos-search-wrap" style="flex:1;max-width:none">
						<i class="fa fa-search ch-pos-search-icon"></i>
						<input type="text" class="form-control ch-pos-search ch-bb-search"
							placeholder="${__("Search by mobile, IMEI, or assessment ID...")}">
					</div>
					<button class="btn btn-primary ch-bb-lookup" style="border-radius:var(--pos-radius);font-weight:700;padding:0 20px;">
						<i class="fa fa-search"></i>
					</button>
					<button class="btn btn-outline-primary ch-bb-new-assessment" style="border-radius:var(--pos-radius);font-weight:700;white-space:nowrap">
						<i class="fa fa-plus"></i> ${__("New Assessment")}
					</button>
				</div>

				<!-- Split layout: results + detail -->
				<div style="display:flex;gap:16px;flex:1;min-height:0;">
					<!-- Left: Results list -->
					<div class="ch-bb-results-col" style="width:340px;flex-shrink:0;overflow-y:auto;">
						<div class="ch-bb-results">
							<div class="ch-pos-empty-state" style="padding:40px 16px;">
								<div class="empty-icon"><i class="fa fa-exchange"></i></div>
								<div class="empty-title">${__("Search for assessments")}</div>
								<div class="empty-subtitle">${__("Enter a mobile number, IMEI, or assessment ID")}</div>
							</div>
						</div>
					</div>
					<!-- Right: Detail panel -->
					<div class="ch-bb-detail-col" style="flex:1;overflow-y:auto;">
						<div class="ch-bb-detail">
							<div class="ch-pos-empty-state" style="padding:40px 16px;">
								<div class="empty-icon"><i class="fa fa-file-text-o"></i></div>
								<div class="empty-title">${__("Select an assessment")}</div>
								<div class="empty-subtitle">${__("Click on an assessment to view details and take action")}</div>
							</div>
						</div>
					</div>
				</div>
			</div>
		`);
		this._bind(panel);
	}

	_bind(panel) {
		const do_search = () => {
			const q = panel.find(".ch-bb-search").val().trim();
			if (!q) {
				frappe.show_alert({ message: __("Enter a search term"), indicator: "orange" });
				return;
			}
			panel.find(".ch-bb-results").html(
				`<div style="padding:24px;text-align:center"><i class="fa fa-spinner fa-spin"></i></div>`
			);
			frappe.call({
				method: "frappe.client.get_list",
				args: {
					doctype: "Buyback Assessment",
					or_filters: [
						["name", "like", `%${q}%`],
						["mobile_no", "like", `%${q}%`],
						["imei_serial", "like", `%${q}%`],
					],
					fields: ["name", "item_name", "brand", "mobile_no", "imei_serial",
						"estimated_grade", "estimated_price", "quoted_price",
						"status", "creation"],
					order_by: "creation desc",
					limit_page_length: 20,
				},
				callback: (r) => this._render_results(panel, r.message || [], q),
			});
		};

		panel.on("click", ".ch-bb-lookup", do_search);
		panel.find(".ch-bb-search").on("keypress", (e) => { if (e.which === 13) do_search(); });

		// Click on result card → show detail
		panel.on("click", ".ch-bb-card", (e) => {
			const name = $(e.currentTarget).data("name");
			panel.find(".ch-bb-card").removeClass("selected");
			$(e.currentTarget).addClass("selected");
			this._load_detail(panel, name);
		});

		// Use as exchange
		panel.on("click", ".ch-bb-use-exchange", (e) => {
			const btn = $(e.currentTarget);
			EventBus.emit("exchange:applied", {
				assessment: btn.data("name"),
				buyback_amount: flt(btn.data("amount")),
				item_name: btn.data("item-name"),
				imei_serial: btn.data("imei"),
				condition_grade: btn.data("grade"),
			});
			PosState.exchange_assessment = btn.data("name");
			PosState.exchange_amount = flt(btn.data("amount"));
			EventBus.emit("cart:updated");
			EventBus.emit("mode:set", "sell");
			EventBus.emit("mode:switch", "sell");
			frappe.show_alert({
				message: __("Exchange credit ₹{0} applied", [format_number(btn.data("amount"))]),
				indicator: "green",
			});
		});

		// Open in full form
		panel.on("click", ".ch-bb-open-full", function () {
			frappe.set_route("Form", "Buyback Assessment", $(this).data("name"));
		});

		// New assessment dialog
		panel.on("click", ".ch-bb-new-assessment", () => this._new_assessment_dialog(panel));
	}

	_render_results(panel, items, query) {
		const el = panel.find(".ch-bb-results");
		if (!items.length) {
			el.html(`
				<div class="ch-pos-empty-state" style="padding:30px 16px;">
					<div class="empty-icon"><i class="fa fa-search"></i></div>
					<div class="empty-title">${__("No results for")} "${frappe.utils.escape_html(query)}"</div>
				</div>`);
			return;
		}

		const cards = items.map((a) => {
			const price = a.quoted_price || a.estimated_price;
			const status_cls = this._status_cls(a.status);
			return `
				<div class="ch-bb-card" data-name="${a.name}">
					<div class="ch-bb-card-top">
						<div style="display:flex;align-items:center;gap:6px">
							<span class="ch-bb-card-id">${a.name}</span>
							<span class="ch-pos-badge badge-${status_cls}">${a.status}</span>
						</div>
						<span class="ch-bb-card-price">₹${format_number(price)}</span>
					</div>
					<div class="ch-bb-card-body">
						<span style="font-weight:600;color:var(--pos-text)">${frappe.utils.escape_html(a.item_name || "Unknown Device")}</span>
						<span>${a.brand ? frappe.utils.escape_html(a.brand) + " · " : ""}${a.imei_serial || a.mobile_no || ""}</span>
					</div>
				</div>`;
		}).join("");
		el.html(cards);

		// Auto-select first
		if (items.length) {
			el.find(".ch-bb-card").first().addClass("selected");
			this._load_detail(panel, items[0].name);
		}
	}

	_load_detail(panel, name) {
		const detail = panel.find(".ch-bb-detail");
		detail.html(`<div style="padding:40px;text-align:center"><i class="fa fa-spinner fa-spin fa-2x" style="opacity:0.3"></i></div>`);

		frappe.call({
			method: "frappe.client.get",
			args: { doctype: "Buyback Assessment", name },
			callback: (r) => {
				if (!r.message) return;
				const a = r.message;
				this._active_assessment = a;
				const price = a.quoted_price || a.estimated_price || 0;
				const can_exchange = ["Submitted", "Inspection Created", "Approved"].includes(a.status);
				const stage_idx = STAGES.indexOf(a.status) >= 0 ? STAGES.indexOf(a.status) : 0;

				detail.html(`
					<div class="ch-bb-detail-card">
						<!-- Header -->
						<div class="ch-bb-detail-header">
							<div>
								<span style="font-weight:700;font-size:var(--pos-fs-md)">${a.name}</span>
								<span class="ch-pos-badge badge-${this._status_cls(a.status)}" style="margin-left:8px">${a.status}</span>
							</div>
							<button class="btn btn-xs btn-default ch-bb-open-full" data-name="${a.name}" style="border-radius:var(--pos-radius-sm)">
								<i class="fa fa-external-link"></i> ${__("Full View")}
							</button>
						</div>

						<!-- Stage Tracker -->
						<div style="padding:12px 20px;">
							<div class="ch-bb-stage-track">
								${STAGES.map((s, i) => {
									const cls = i < stage_idx ? "done" : i === stage_idx ? "active" : "";
									return `<div class="ch-bb-stage ${cls}">${s}</div>`;
								}).join("")}
							</div>
						</div>

						<!-- Detail Grid -->
						<div class="ch-bb-detail-body">
							<div class="ch-bb-detail-grid">
								<div class="ch-bb-detail-field">
									<span class="field-label">${__("Device")}</span>
									<span class="field-value">${frappe.utils.escape_html(a.item_name || "—")}</span>
								</div>
								<div class="ch-bb-detail-field">
									<span class="field-label">${__("Brand")}</span>
									<span class="field-value">${frappe.utils.escape_html(a.brand || "—")}</span>
								</div>
								<div class="ch-bb-detail-field">
									<span class="field-label">${__("IMEI / Serial")}</span>
									<span class="field-value" style="font-family:var(--pos-font-mono)">${frappe.utils.escape_html(a.imei_serial || "—")}</span>
								</div>
								<div class="ch-bb-detail-field">
									<span class="field-label">${__("Customer Mobile")}</span>
									<span class="field-value">${frappe.utils.escape_html(a.mobile_no || "—")}</span>
								</div>
								<div class="ch-bb-detail-field">
									<span class="field-label">${__("Grade")}</span>
									<span class="field-value">${frappe.utils.escape_html(a.estimated_grade || "—")}</span>
								</div>
								<div class="ch-bb-detail-field">
									<span class="field-label">${__("Warranty")}</span>
									<span class="field-value">${frappe.utils.escape_html(a.warranty_status || "—")}</span>
								</div>
							</div>

							<!-- Valuation -->
							<div class="ch-bb-valuation-block">
								<div class="ch-bb-valuation-label">${__("Assessed Value")}</div>
								<div class="ch-bb-valuation-amount">₹${format_number(price)}</div>
							</div>

							<!-- Actions -->
							<div style="display:flex;gap:8px;margin-top:16px">
								${can_exchange ? `
									<button class="btn btn-primary ch-bb-use-exchange" style="flex:1;border-radius:var(--pos-radius);font-weight:700;min-height:var(--pos-touch-comfortable)"
										data-name="${a.name}"
										data-amount="${price}"
										data-item-name="${frappe.utils.escape_html(a.item_name || "")}"
										data-imei="${a.imei_serial || ""}"
										data-grade="${a.estimated_grade || ""}">
										<i class="fa fa-exchange"></i> ${__("Use as Exchange Credit")}
									</button>` : ""}
								<button class="btn btn-outline-secondary ch-bb-open-full" data-name="${a.name}" style="border-radius:var(--pos-radius);font-weight:700;min-height:var(--pos-touch-comfortable)">
									<i class="fa fa-pencil"></i> ${__("Resume in App")}
								</button>
							</div>
						</div>
					</div>
				`);
			},
		});
	}

	_status_cls(status) {
		if (["Submitted", "Approved", "Complete", "Inspection Created"].includes(status)) return "success";
		if (status === "Draft") return "warning";
		if (["Rejected", "Cancelled"].includes(status)) return "danger";
		return "muted";
	}

	_new_assessment_dialog(panel) {
		const condition_keys = ["screen", "body", "buttons", "charging", "camera", "speaker_mic"];
		const condition_labels = {
			screen: __("Screen"),
			body: __("Body / Frame"),
			buttons: __("Buttons"),
			charging: __("Charging Port"),
			camera: __("Camera"),
			speaker_mic: __("Speaker / Mic"),
		};

		const fields = [
			// --- Device Info ---
			{ fieldtype: "Section Break", label: __("Device Information") },
			{ fieldname: "mobile_no", fieldtype: "Data", label: __("Customer Mobile"), reqd: 1 },
			{ fieldname: "customer", fieldtype: "Link", options: "Customer", label: __("Customer") },
			{ fieldtype: "Column Break" },
			{ fieldname: "item", fieldtype: "Link", options: "Item", label: __("Device"), reqd: 1 },
			{ fieldname: "imei_serial", fieldtype: "Data", label: __("IMEI / Serial") },

			// --- Condition Checks ---
			{ fieldtype: "Section Break", label: __("Device Condition") },
			{
				fieldtype: "HTML",
				fieldname: "condition_html",
				options: `<p class="text-muted" style="margin-bottom:8px">${__("Toggle each check. Red = defective, deduction applied.")}</p>`,
			},
			...condition_keys.flatMap((key, i) => {
				const row = [
					{
						fieldname: `cond_${key}`,
						fieldtype: "Check",
						label: condition_labels[key] + " " + __("OK"),
						default: 1,
					},
				];
				if (i % 2 === 0 && i < condition_keys.length - 1) {
					row.push({ fieldtype: "Column Break" });
				} else if (i % 2 === 1) {
					row.push({ fieldtype: "Section Break", hide_border: 1 });
				}
				return row;
			}),

			// --- Live Valuation ---
			{
				fieldtype: "HTML",
				fieldname: "valuation_display",
				options: `<div class="ch-bb-live-valuation" style="background:var(--pos-bg-alt,#f8f9fb);border-radius:var(--pos-radius,8px);padding:16px 20px;margin:8px 0 12px;text-align:center;">
					<div style="font-size:12px;color:var(--pos-text-muted);text-transform:uppercase;letter-spacing:0.04em">${__("Estimated Buyback Value")}</div>
					<div class="ch-bb-live-price" style="font-size:28px;font-weight:800;color:var(--pos-primary);margin-top:4px">—</div>
					<div class="ch-bb-live-grade" style="font-size:13px;color:var(--pos-text-muted);margin-top:2px"></div>
				</div>`,
			},

			// --- KYC ---
			{ fieldtype: "Section Break", label: __("KYC Details") },
			{
				fieldname: "kyc_id_type", fieldtype: "Select", label: __("ID Type"),
				options: "\nAadhaar\nPAN\nPassport\nDriving Licence\nVoter ID",
			},
			{ fieldname: "kyc_id_number", fieldtype: "Data", label: __("ID Number") },
			{ fieldtype: "Column Break" },
			{ fieldname: "kyc_name", fieldtype: "Data", label: __("Name on ID") },
		];

		const dlg = new frappe.ui.Dialog({
			title: __("New Buyback Assessment"),
			fields: fields,
			size: "extra-large",
			primary_action_label: __("Create Assessment"),
			primary_action: (values) => {
				if (!values.item) return;
				const condition_checks = {};
				condition_keys.forEach(k => { condition_checks[k] = values[`cond_${k}`] ? true : false; });

				dlg.disable_primary_action();

				// Pre-check IMEI blacklist before creating assessment
				const _proceed = () => {
					frappe.xcall("ch_pos.api.pos_api.create_buyback_assessment_with_grading", {
						mobile_no: values.mobile_no,
						item_code: values.item,
						imei_serial: values.imei_serial || "",
						customer: values.customer || "",
						condition_checks: condition_checks,
						kyc_id_type: values.kyc_id_type || "",
						kyc_id_number: values.kyc_id_number || "",
						kyc_name: values.kyc_name || "",
					}).then((doc) => {
						dlg.hide();
						frappe.show_alert({
							message: `${__("Assessment")} <b>${doc.name}</b> ${__("created · Grade")} ${doc.estimated_grade} · ₹${format_number(doc.estimated_price)}`,
							indicator: "green",
						});
						panel.find(".ch-bb-search").val(doc.name);
						panel.find(".ch-bb-lookup").click();
					}).catch(() => {
						dlg.enable_primary_action();
					});
				};

				if (values.imei_serial) {
					frappe.xcall("ch_pos.api.pos_api.check_imei_blacklist", {
						imei: values.imei_serial,
					}).then((res) => {
						if (res && res.blacklisted) {
							dlg.enable_primary_action();
							frappe.msgprint({
								title: __("Blacklisted Device"),
								message: __("This IMEI is blacklisted — Reason: {0}.{1}<br><br>Cannot proceed with buyback.", [
									`<b>${res.reason}</b>`,
									res.reference ? ` Ref: ${res.reference}` : "",
								]),
								indicator: "red",
							});
						} else {
							_proceed();
						}
					}).catch(() => _proceed());
				} else {
					_proceed();
				}
			},
		});

		// Live valuation update on item or condition change
		const update_valuation = () => {
			const item_code = dlg.get_value("item");
			if (!item_code) {
				dlg.$wrapper.find(".ch-bb-live-price").text("—");
				dlg.$wrapper.find(".ch-bb-live-grade").text("");
				return;
			}
			const condition_checks = {};
			condition_keys.forEach(k => { condition_checks[k] = dlg.get_value(`cond_${k}`) ? true : false; });

			frappe.xcall("ch_pos.api.pos_api.calculate_buyback_valuation", {
				item_code: item_code,
				condition_checks: condition_checks,
			}).then((val) => {
				dlg.$wrapper.find(".ch-bb-live-price").text(`₹${format_number(val.offered_price)}`);
				dlg.$wrapper.find(".ch-bb-live-grade").text(
					`${__("Grade")}: ${val.grade} · ${__("Base")}: ₹${format_number(val.base_price)} · ${__("Deduction")}: ${val.total_deduction_pct}%`
				);
			});
		};

		dlg.fields_dict.item.$input.on("change", update_valuation);
		condition_keys.forEach(k => {
			dlg.fields_dict[`cond_${k}`].$input.on("change", update_valuation);
		});

		dlg.show();
	}
}
