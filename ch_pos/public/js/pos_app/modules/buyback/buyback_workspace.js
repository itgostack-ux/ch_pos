/**
 * CH POS — Buyback Workspace (Full POS-Native Flow)
 *
 * Complete buyback lifecycle inside POS:
 *
 *  SEARCH → ASSESS → INSPECT → APPROVE → SETTLE
 *
 *  - Search assessments from mobile diagnostic app or created manually
 *  - Review mobile diagnostic results inline
 *  - Inspector sets final price inline
 *  - Customer approval via OTP or in-store signature
 *  - Settlement: Cashback (record payment) or Exchange (apply credit + go to Sell)
 */
import { PosState, EventBus } from "../../state.js";
import { format_number } from "../../shared/helpers.js";

// ──────────────────────────────────────────── Stage helpers ──────────
const STAGE = {
	ASSESS: "assess",   // Assessment selected, no order yet (or pre-inspection)
	INSPECT: "inspect", // Order created, set final price
	APPROVE: "approve", // Price confirmed, awaiting customer sign-off
	SETTLE: "settle",   // Customer approved, choose cashback/exchange
	DONE: "done",       // Paid / Closed
};

function _order_stage(order) {
	if (!order) return STAGE.ASSESS;
	const s = order.status || "";
	if (["Paid", "Closed"].includes(s)) return STAGE.DONE;
	if (s === "Customer Approved") return STAGE.SETTLE;
	if (["Ready to Pay", "Approved", "Awaiting Approval",
		"Draft", "Awaiting Customer Approval"].includes(s)) return STAGE.INSPECT;
	return STAGE.ASSESS;
}

// Stage labels for progress bar
const STAGE_LABELS = [
	{ key: STAGE.ASSESS, label: "Assess" },
	{ key: STAGE.INSPECT, label: "Inspect" },
	{ key: STAGE.APPROVE, label: "Approve" },
	{ key: STAGE.SETTLE, label: "Settle" },
];

export class BuybackWorkspace {
	constructor() {
		this._panel = null;
		this._current_data = null;
		EventBus.on("workspace:render", (ctx) => {
			if (ctx.mode !== "buyback") return;
			this._panel = ctx.panel;
			this.render(ctx.panel);
		});
	}

	// ─────────────────────────────── render shell ──
	render(panel) {
		panel.html(`
			<div class="ch-pos-mode-panel ch-bb-root">
				<!-- Header -->
				<div class="ch-mode-header">
					<h4>
						<span class="mode-icon" style="background:var(--pos-warning-light);color:#92400e">
							<i class="fa fa-exchange"></i>
						</span>
						${__("Buyback & Exchange")}
					</h4>
					<span class="ch-mode-hint">${__("Search mobile diagnostics, inspect, approve and settle")}</span>
				</div>

				<!-- Search Row -->
				<div style="display:flex;gap:10px;margin-bottom:14px;">
					<div class="ch-pos-search-wrap" style="flex:1;max-width:none">
						<i class="fa fa-search ch-pos-search-icon"></i>
						<input type="text" class="form-control ch-pos-search ch-bb-search"
							placeholder="${__("Mobile number, IMEI, assessment ID, or customer name...")}">
					</div>
					<button class="btn btn-primary ch-bb-lookup"
						style="border-radius:var(--pos-radius,8px);font-weight:700;padding:0 18px">
						<i class="fa fa-search"></i>
					</button>
					<button class="btn btn-outline-primary ch-bb-new-btn"
						style="border-radius:var(--pos-radius,8px);font-weight:700;white-space:nowrap">
						<i class="fa fa-plus"></i> ${__("New")}
					</button>
				</div>

				<!-- Split -->
				<div class="ch-bb-split">
					<div class="ch-bb-results-col">
						<div class="ch-bb-results">
							<div class="ch-pos-empty-state" style="padding:40px 16px">
								<div class="empty-icon"><i class="fa fa-mobile fa-2x"></i></div>
								<div class="empty-title">${__("Search or create an assessment")}</div>
								<div class="empty-subtitle">${__("Mobile diagnostics submitted from the app will appear here")}</div>
							</div>
						</div>
					</div>
					<div class="ch-bb-detail-col">
						<div class="ch-bb-detail">
							<div class="ch-pos-empty-state" style="padding:40px 16px">
								<div class="empty-icon"><i class="fa fa-file-text-o fa-2x"></i></div>
								<div class="empty-title">${__("Select an assessment")}</div>
							</div>
						</div>
					</div>
				</div>
			</div>
		`);
		this._bind(panel);
	}

	// ─────────────────────────────── bind events ──
	_bind(panel) {
		const do_search = () => {
			const q = panel.find(".ch-bb-search").val().trim();
			if (!q) {
				frappe.show_alert({ message: __("Enter a search term"), indicator: "orange" });
				return;
			}
			this._search(panel, q);
		};

		panel.on("click", ".ch-bb-lookup", do_search);
		panel.find(".ch-bb-search").on("keypress", (e) => { if (e.which === 13) do_search(); });
		panel.on("click", ".ch-bb-card", (e) => {
			panel.find(".ch-bb-card").removeClass("selected");
			$(e.currentTarget).addClass("selected");
			this._load_detail(panel, $(e.currentTarget).data("name"));
		});
		panel.on("click", ".ch-bb-new-btn", () => this._new_assessment_dialog(panel));
	}

	// ─────────────────────────────── search ──
	_search(panel, q) {
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
					["customer_name", "like", `%${q}%`],
				],
				fields: ["name", "item_name", "brand", "mobile_no", "imei_serial",
					"estimated_grade", "estimated_price", "quoted_price",
					"status", "source", "creation"],
				order_by: "creation desc",
				limit_page_length: 25,
			},
			callback: (r) => this._render_results(panel, r.message || [], q),
		});
	}

	_render_results(panel, items, query) {
		const el = panel.find(".ch-bb-results");
		if (!items.length) {
			el.html(`<div class="ch-pos-empty-state" style="padding:30px 16px">
				<div class="empty-icon"><i class="fa fa-search"></i></div>
				<div class="empty-title">${__("No results for")} "${frappe.utils.escape_html(query)}"</div>
				<div class="empty-subtitle">
					<button class="btn btn-sm btn-primary ch-bb-new-btn" style="margin-top:8px">
						<i class="fa fa-plus"></i> ${__("Create New Assessment")}
					</button>
				</div>
			</div>`);
			return;
		}
		const cards = items.map(a => {
			const price = a.quoted_price || a.estimated_price;
			const from_app = a.source === "Mobile App";
			return `
				<div class="ch-bb-card" data-name="${a.name}">
					<div class="ch-bb-card-top">
						<div style="display:flex;align-items:center;gap:5px;flex-wrap:wrap">
							<span class="ch-bb-card-id">${a.name}</span>
							${from_app ? `<span class="ch-pos-badge badge-primary" style="font-size:10px"><i class="fa fa-mobile"></i> ${__("App")}</span>` : ""}
							<span class="ch-pos-badge badge-${this._status_cls(a.status)}">${a.status || __("Draft")}</span>
						</div>
						<span class="ch-bb-card-price">₹${format_number(price)}</span>
					</div>
					<div class="ch-bb-card-body">
						<span style="font-weight:600">${frappe.utils.escape_html(a.item_name || __("Unknown Device"))}</span>
						<span>${a.brand ? frappe.utils.escape_html(a.brand) + " · " : ""}${frappe.utils.escape_html(a.imei_serial || a.mobile_no || "")}</span>
					</div>
				</div>`;
		}).join("");
		el.html(cards);
		el.find(".ch-bb-card").first().addClass("selected");
		this._load_detail(panel, items[0].name);
	}

	// ─────────────────────────────── load detail ──
	_load_detail(panel, name) {
		const detail = panel.find(".ch-bb-detail");
		detail.html(`<div style="padding:40px;text-align:center">
			<i class="fa fa-spinner fa-spin fa-2x" style="opacity:0.3"></i>
		</div>`);
		frappe.xcall("ch_pos.api.pos_api.get_pos_buyback_detail", { assessment_name: name })
			.then(data => {
				this._current_data = data;
				this._render_detail(detail, data);
			});
	}

	_reload() {
		if (!this._current_data || !this._panel) return;
		this._load_detail(this._panel, this._current_data.name);
	}

	// ─────────────────────────────── detail router ──
	_render_detail(el, data) {
		const stage = _order_stage(data.order);
		const active_idx = STAGE_LABELS.findIndex(s => s.key === stage);

		const progress_html = `<div class="ch-bb-progress">
			${STAGE_LABELS.map((s, i) => `
				<div class="ch-bb-progress-step ${i < active_idx ? "done" : i === active_idx ? "active" : ""}">
					<div class="ch-bb-progress-dot">${i < active_idx ? '<i class="fa fa-check"></i>' : i + 1}</div>
					<div class="ch-bb-progress-label">${__(s.label)}</div>
				</div>
				${i < STAGE_LABELS.length - 1 ? `<div class="ch-bb-progress-line${i < active_idx ? " done" : ""}"></div>` : ""}
			`).join("")}
		</div>`;

		let body_html = "";
		if (stage === STAGE.DONE) body_html = this._html_done(data);
		else if (stage === STAGE.SETTLE) body_html = this._html_settle(data);
		else if (stage === STAGE.APPROVE) body_html = this._html_approve(data);
		else if (stage === STAGE.INSPECT) body_html = this._html_inspect(data);
		else body_html = this._html_assess(data);

		el.html(`
			<div class="ch-bb-detail-card">
				<!-- Title Bar -->
				<div class="ch-bb-detail-header">
					<div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
						<span style="font-weight:700;font-size:var(--pos-fs-md,14px)">${data.name}</span>
						${data.source === "Mobile App"
							? `<span class="ch-pos-badge badge-primary"><i class="fa fa-mobile"></i> ${__("Mobile Diagnostic")}</span>`
							: `<span class="ch-pos-badge badge-muted">${__("Manual")}</span>`}
					</div>
					<a href="#" class="ch-bb-open-desk text-muted" style="font-size:12px" data-name="${data.name}">
						<i class="fa fa-external-link"></i> ${__("Desk")}
					</a>
				</div>

				<!-- Device strip -->
				<div class="ch-bb-device-strip">
					<div><span class="ch-bb-strip-label">${__("Device")}</span>
						<span class="ch-bb-strip-val">${frappe.utils.escape_html(data.item_name || "—")}</span></div>
					<div><span class="ch-bb-strip-label">${__("IMEI")}</span>
						<span class="ch-bb-strip-val" style="font-family:monospace">${frappe.utils.escape_html(data.imei_serial || "—")}</span></div>
					<div><span class="ch-bb-strip-label">${__("Mobile")}</span>
						<span class="ch-bb-strip-val">${frappe.utils.escape_html(data.mobile_no || "—")}</span></div>
					<div><span class="ch-bb-strip-label">${__("Grade")}</span>
						<span class="ch-bb-strip-val">${frappe.utils.escape_html(data.estimated_grade || "—")}</span></div>
				</div>

				<!-- Progress -->
				${progress_html}

				<!-- Stage body -->
				<div class="ch-bb-stage-body">${body_html}</div>
			</div>
		`);

		el.find(".ch-bb-open-desk").on("click", function (e) {
			e.preventDefault();
			frappe.set_route("Form", "Buyback Assessment", $(this).data("name"));
		});

		this._bind_stage_actions(el, data, stage);
	}

	// ─────────────────────────────── stage: ASSESS ──
	_html_assess(data) {
		const has_diag = data.diagnostics && data.diagnostics.length > 0;
		const diag_html = has_diag
			? `<div class="ch-bb-section-label" style="margin-top:12px">${__("Diagnostic Results (from Mobile App)")}</div>
			   <div class="ch-bb-diag-grid">
				${data.diagnostics.map(d => `
					<div class="ch-bb-diag-item ${d.result === "Pass" ? "pass" : "fail"}">
						<i class="fa ${d.result === "Pass" ? "fa-check-circle" : "fa-times-circle"}"></i>
						<span>${frappe.utils.escape_html(d.test_name)}</span>
						<span class="ch-bb-diag-result">${__(d.result)}</span>
					</div>`).join("")}
			   </div>`
			: `<div class="ch-bb-empty-note">${__("No automated diagnostics — manual condition check used for grading.")}</div>`;

		return `
			<div class="ch-bb-valuation-banner">
				<div class="ch-bb-val-label">${__("Assessed Value")}</div>
				<div class="ch-bb-val-amount">₹${format_number(data.quoted_price || data.estimated_price)}</div>
				<div class="ch-bb-val-sub">
					${__("Grade")} ${frappe.utils.escape_html(data.estimated_grade || "—")} ·
					${frappe.utils.escape_html(data.warranty_status || "—")}
				</div>
			</div>
			${diag_html}
			<div class="ch-bb-actions" style="margin-top:16px">
				<button class="btn btn-primary btn-lg ch-bb-act ch-bb-start-inspect"
					style="flex:1;border-radius:var(--pos-radius,8px);font-weight:700">
					<i class="fa fa-search-plus"></i> ${__("Start Inspection & Set Price")}
				</button>
			</div>`;
	}

	// ─────────────────────────────── stage: INSPECT ──
	_html_inspect(data) {
		const order = data.order;
		const final_price = order ? order.final_price : (data.quoted_price || data.estimated_price);
		const status_note = order
			? `<span class="ch-pos-badge badge-${this._status_cls(order.status)}">${__(order.status)}</span>` : "";

		return `
			<div class="ch-bb-valuation-banner">
				<div class="ch-bb-val-label">${__("Assessed Value")}</div>
				<div class="ch-bb-val-amount">₹${format_number(data.quoted_price || data.estimated_price)}</div>
			</div>
			<div class="ch-bb-section-label" style="margin-top:14px">
				${__("Inspector — Set Final Price")} ${status_note}
			</div>
			<div style="margin-bottom:8px">
				<label class="ch-bb-field-label">${__("Final Buyback Price (₹)")}</label>
				<input type="number" class="form-control ch-bb-final-price"
					value="${final_price}" min="0" step="1"
					style="font-size:22px;font-weight:700;text-align:right;padding:10px;border-radius:var(--pos-radius,8px)">
			</div>
			<div style="margin-bottom:10px">
				<label class="ch-bb-field-label">${__("Inspector Notes")}</label>
				<textarea class="form-control ch-bb-inspector-notes" rows="2"
					placeholder="${__("Optional: reason for price change, condition notes...")}"
					style="border-radius:var(--pos-radius,8px)"
					>${(order && order.inspector_notes) || ""}</textarea>
			</div>
			<div class="ch-bb-actions">
				<button class="btn btn-primary ch-bb-act ch-bb-confirm-price"
					style="flex:1;border-radius:var(--pos-radius,8px);font-weight:700;min-height:44px">
					<i class="fa fa-check"></i> ${__("Confirm Price & Proceed to Approval")}
				</button>
			</div>`;
	}

	// ─────────────────────────────── stage: APPROVE ──
	_html_approve(data) {
		const order = data.order;
		const price = order ? order.final_price : (data.quoted_price || data.estimated_price);
		const mobile = data.mobile_no || "";

		return `
			<div class="ch-bb-valuation-banner"
				style="background:var(--pos-success-light,#d1fae5);border-color:var(--pos-success,#10b981)">
				<div class="ch-bb-val-label" style="color:var(--pos-success,#10b981)">
					${__("Final Buyback Price — Awaiting Customer Approval")}
				</div>
				<div class="ch-bb-val-amount" style="color:var(--pos-success,#10b981)">₹${format_number(price)}</div>
				<div class="ch-bb-val-sub">
					${__("Customer:")} ${frappe.utils.escape_html(data.customer_name || data.mobile_no || "—")}
				</div>
			</div>
			<div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:14px">
				<!-- In-Store Signature -->
				<div class="ch-bb-approval-card">
					<div class="ch-bb-approval-icon"><i class="fa fa-pencil fa-2x"></i></div>
					<div class="ch-bb-approval-title">${__("In-Store Signature")}</div>
					<div class="ch-bb-approval-desc">
						${__("Customer physically signs off on the price at the counter.")}
					</div>
					<button class="btn btn-primary ch-bb-act ch-bb-approve-instor"
						style="width:100%;border-radius:var(--pos-radius,8px);font-weight:700;margin-top:8px">
						<i class="fa fa-check"></i> ${__("Confirm (Signature)")}
					</button>
				</div>
				<!-- OTP -->
				<div class="ch-bb-approval-card">
					<div class="ch-bb-approval-icon"><i class="fa fa-mobile fa-2x"></i></div>
					<div class="ch-bb-approval-title">${__("OTP Verification")}</div>
					<div class="ch-bb-approval-desc">
						${__("Send OTP to customer's mobile")}
						${mobile ? `<b>${mobile.slice(0, 2)}****${mobile.slice(-2)}</b>` : ""}
					</div>
					<button class="btn btn-outline-primary ch-bb-act ch-bb-send-otp"
						style="width:100%;border-radius:var(--pos-radius,8px);font-weight:700;margin-top:8px">
						<i class="fa fa-paper-plane"></i> ${__("Send OTP")}
					</button>
					<div class="ch-bb-otp-row" style="display:none;margin-top:8px">
						<input type="text" class="form-control ch-bb-otp-input" maxlength="6"
							placeholder="${__("Enter 6-digit OTP")}"
							style="text-align:center;letter-spacing:6px;font-size:18px;font-weight:700;border-radius:var(--pos-radius,8px)">
						<button class="btn btn-success ch-bb-act ch-bb-verify-otp w-100"
							style="border-radius:var(--pos-radius,8px);font-weight:700;margin-top:6px">
							<i class="fa fa-check"></i> ${__("Verify OTP")}
						</button>
					</div>
				</div>
			</div>`;
	}

	// ─────────────────────────────── stage: SETTLE ──
	_html_settle(data) {
		const order = data.order;
		const price = order ? order.final_price : (data.quoted_price || data.estimated_price);

		return `
			<div class="ch-bb-valuation-banner" style="background:#f0f9ff;border-color:#0ea5e9">
				<div class="ch-bb-val-label" style="color:#0284c7">
					${__("Customer Approved ✓ — Choose Settlement")}
				</div>
				<div class="ch-bb-val-amount" style="color:#0284c7">₹${format_number(price)}</div>
			</div>
			<div class="ch-bb-section-label" style="margin-top:14px">
				${__("How does the customer want to receive value?")}
			</div>
			<div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:10px">
				<!-- Cashback -->
				<div class="ch-bb-settle-card">
					<div class="ch-bb-settle-icon" style="background:#fef3c7;color:#92400e">
						<i class="fa fa-money fa-2x"></i>
					</div>
					<div class="ch-bb-settle-title">${__("Cashback")}</div>
					<div class="ch-bb-settle-desc">
						${__("Pay customer ₹{0} directly. No new purchase needed.", [format_number(price)])}
					</div>
					<div style="margin:10px 0">
						<label class="ch-bb-field-label">${__("Payment Method")}</label>
						<select class="form-control ch-bb-cashback-mode"
							style="border-radius:var(--pos-radius,8px)">
							<option value="Cash">${__("Cash")}</option>
							<option value="UPI">${__("UPI")}</option>
							<option value="Bank Transfer">${__("Bank Transfer")}</option>
						</select>
					</div>
					<button class="btn btn-warning ch-bb-act ch-bb-cashback"
						style="width:100%;border-radius:var(--pos-radius,8px);font-weight:700;min-height:44px"
						data-price="${price}">
						<i class="fa fa-money"></i> ${__("Settle as Cashback")}
					</button>
				</div>
				<!-- Exchange -->
				<div class="ch-bb-settle-card">
					<div class="ch-bb-settle-icon" style="background:#d1fae5;color:#065f46">
						<i class="fa fa-exchange fa-2x"></i>
					</div>
					<div class="ch-bb-settle-title">${__("Exchange")}</div>
					<div class="ch-bb-settle-desc">
						${__("Apply ₹{0} as credit toward a new device purchase.", [format_number(price)])}
					</div>
					<div style="margin:10px 0;font-size:12px;color:var(--pos-text-muted)">
						${__("Credit will appear in cart. Cashier selects the new device.")}
					</div>
					<button class="btn btn-success ch-bb-act ch-bb-exchange"
						style="width:100%;border-radius:var(--pos-radius,8px);font-weight:700;min-height:44px"
						data-name="${data.name}"
						data-amount="${price}"
						data-order-name="${order ? order.name : ""}"
						data-item-name="${frappe.utils.escape_html(data.item_name || "")}"
						data-imei="${data.imei_serial || ""}"
						data-grade="${data.estimated_grade || ""}">
						<i class="fa fa-exchange"></i> ${__("Add to Cart & Sell")}
					</button>
				</div>
			</div>`;
	}

	// ─────────────────────────────── stage: DONE ──
	_html_done(data) {
		const order = data.order;
		const price = order ? order.final_price : 0;
		return `
			<div class="ch-bb-valuation-banner" style="background:#d1fae5;border-color:#10b981">
				<div class="ch-bb-val-label" style="color:#065f46">
					<i class="fa fa-check-circle"></i> ${__("Buyback Complete")}
				</div>
				<div class="ch-bb-val-amount" style="color:#065f46">₹${format_number(price)}</div>
				<div class="ch-bb-val-sub">
					${__("Order")} ${order ? order.name : ""} · ${order ? __(order.status) : ""}
				</div>
			</div>
			<div style="text-align:center;margin-top:16px">
				<button class="btn btn-outline-secondary ch-bb-open-desk" data-name="${data.name}"
					style="border-radius:var(--pos-radius,8px)">
					<i class="fa fa-external-link"></i> ${__("View in Desk")}
				</button>
			</div>`;
	}

	// ─────────────────────────────── stage action wiring ──
	_bind_stage_actions(el, data, stage) {
		// ── ASSESS: Start Inspection ────────────────────
		el.on("click", ".ch-bb-start-inspect", () => {
			const btn = el.find(".ch-bb-start-inspect");
			btn.prop("disabled", true)
				.html(`<i class="fa fa-spinner fa-spin"></i> ${__("Starting...")}`);
			frappe.xcall("ch_pos.api.pos_api.pos_start_buyback_order", {
				assessment_name: data.name,
				pos_profile: PosState.pos_profile || "",
				final_price: data.quoted_price || data.estimated_price,
			}).then(() => {
				frappe.show_alert({ message: __("Inspection started"), indicator: "green" });
				this._reload();
			}).catch(() => {
				btn.prop("disabled", false)
					.html(`<i class="fa fa-search-plus"></i> ${__("Start Inspection & Set Price")}`);
			});
		});

		// ── INSPECT: Confirm Price ──────────────────────
		el.on("click", ".ch-bb-confirm-price", () => {
			const price = parseFloat(el.find(".ch-bb-final-price").val()) || 0;
			if (price <= 0) {
				frappe.show_alert({ message: __("Enter a valid price"), indicator: "orange" });
				return;
			}
			const notes = el.find(".ch-bb-inspector-notes").val();
			const btn = el.find(".ch-bb-confirm-price");
			btn.prop("disabled", true).html(`<i class="fa fa-spinner fa-spin"></i>`);

			const order_name = data.order && data.order.name;
			const call = order_name
				? frappe.xcall("ch_pos.api.pos_api.pos_update_buyback_price", {
					order_name, final_price: price, inspector_notes: notes,
				})
				: frappe.xcall("ch_pos.api.pos_api.pos_start_buyback_order", {
					assessment_name: data.name,
					pos_profile: PosState.pos_profile || "",
					final_price: price, inspector_notes: notes,
				});

			call.then(() => {
				frappe.show_alert({ message: __("Price confirmed"), indicator: "green" });
				this._reload();
			}).catch(() => btn.prop("disabled", false));
		});

		// ── APPROVE: In-Store Signature ─────────────────
		el.on("click", ".ch-bb-approve-instor", () => {
			const btn = el.find(".ch-bb-approve-instor");
			btn.prop("disabled", true);
			frappe.xcall("ch_pos.api.pos_api.pos_approve_customer_buyback", {
				order_name: data.order.name,
				method: "In-Store Signature",
			}).then(() => {
				frappe.show_alert({ message: __("Customer approved (in-store)"), indicator: "green" });
				this._reload();
			}).catch(() => btn.prop("disabled", false));
		});

		// ── APPROVE: Send OTP ───────────────────────────
		el.on("click", ".ch-bb-send-otp", () => {
			const btn = el.find(".ch-bb-send-otp");
			btn.prop("disabled", true).html(`<i class="fa fa-spinner fa-spin"></i>`);
			frappe.xcall("ch_pos.api.pos_api.pos_send_customer_otp", {
				order_name: data.order.name,
			}).then((res) => {
				btn.prop("disabled", false)
					.html(`<i class="fa fa-redo"></i> ${__("Resend")}`);
				el.find(".ch-bb-otp-row").show();
				frappe.show_alert({
					message: __("OTP sent to {0}", [res.masked_mobile]),
					indicator: "green",
				});
			}).catch(() => {
				btn.prop("disabled", false)
					.html(`<i class="fa fa-paper-plane"></i> ${__("Send OTP")}`);
			});
		});

		// ── APPROVE: Verify OTP ─────────────────────────
		el.on("click", ".ch-bb-verify-otp", () => {
			const otp = el.find(".ch-bb-otp-input").val().trim();
			if (otp.length !== 6) {
				frappe.show_alert({ message: __("Enter the 6-digit OTP"), indicator: "orange" });
				return;
			}
			const btn = el.find(".ch-bb-verify-otp");
			btn.prop("disabled", true);
			frappe.xcall("ch_pos.api.pos_api.pos_approve_customer_buyback", {
				order_name: data.order.name,
				method: "OTP",
				otp_code: otp,
			}).then(() => {
				frappe.show_alert({ message: __("OTP verified! Customer approved."), indicator: "green" });
				this._reload();
			}).catch(() => btn.prop("disabled", false));
		});

		// ── SETTLE: Cashback ────────────────────────────
		el.on("click", ".ch-bb-cashback", (e) => {
			const price = flt($(e.currentTarget).data("price"));
			const payment_method = el.find(".ch-bb-cashback-mode").val();
			frappe.confirm(
				__("Pay ₹{0} cashback to customer via {1}?", [format_number(price), payment_method]),
				() => {
					const btn = el.find(".ch-bb-cashback");
					btn.prop("disabled", true).html(`<i class="fa fa-spinner fa-spin"></i>`);
					frappe.xcall("ch_pos.api.pos_api.pos_settle_buyback_cashback", {
						order_name: data.order.name,
						payment_method,
					}).then(() => {
						frappe.show_alert({
							message: __("Cashback ₹{0} recorded via {1}", [format_number(price), payment_method]),
							indicator: "green",
						});
						this._reload();
					}).catch(() => {
						btn.prop("disabled", false)
							.html(`<i class="fa fa-money"></i> ${__("Settle as Cashback")}`);
					});
				}
			);
		});

		// ── SETTLE: Exchange → go to Sell ───────────────
		el.on("click", ".ch-bb-exchange", (e) => {
			const btn = $(e.currentTarget);
			PosState.exchange_assessment = btn.data("name");
			PosState.exchange_amount = flt(btn.data("amount"));
			EventBus.emit("exchange:applied", {
				assessment: btn.data("name"),
				buyback_amount: flt(btn.data("amount")),
				item_name: btn.data("item-name"),
				imei_serial: btn.data("imei"),
				condition_grade: btn.data("grade"),
			});
			EventBus.emit("cart:updated");
			EventBus.emit("mode:set", "sell");
			EventBus.emit("mode:switch", "sell");
			frappe.show_alert({
				message: __("Exchange credit ₹{0} applied — add new device to cart", [
					format_number(btn.data("amount")),
				]),
				indicator: "green",
			});
		});
	}

	// ─────────────────────────────── new assessment dialog ──
	_new_assessment_dialog(panel) {
		const condition_keys = ["screen", "body", "buttons", "charging", "camera", "speaker_mic"];
		const condition_labels = {
			screen: __("Screen"), body: __("Body / Frame"), buttons: __("Buttons"),
			charging: __("Charging Port"), camera: __("Camera"), speaker_mic: __("Speaker / Mic"),
		};

		const dlg = new frappe.ui.Dialog({
			title: __("New Buyback Assessment"),
			size: "extra-large",
			fields: [
				{ fieldtype: "Section Break", label: __("Device Information") },
				{ fieldname: "mobile_no", fieldtype: "Data", label: __("Customer Mobile"), reqd: 1,
					description: __("Customer's registered mobile number") },
				{ fieldname: "customer", fieldtype: "Link", options: "Customer", label: __("Customer") },
				{ fieldtype: "Column Break" },
				{ fieldname: "item", fieldtype: "Link", options: "Item",
					get_query: () => ({ filters: { has_serial_no: 1 } }),
					label: __("Device Model"), reqd: 1 },
				{ fieldname: "imei_serial", fieldtype: "Data", label: __("IMEI / Serial No"),
					description: __("15-digit IMEI or serial number") },

				{ fieldtype: "Section Break", label: __("Device Condition") },
				{ fieldtype: "HTML", fieldname: "cond_hint",
					options: `<p class="text-muted small">${__("Toggle each — unchecked means defective, a deduction is applied")}</p>` },
				...condition_keys.flatMap((key, i) => [
					{ fieldname: `cond_${key}`, fieldtype: "Check",
						label: condition_labels[key] + " " + __("OK"), default: 1 },
					...(i % 2 === 0 ? [{ fieldtype: "Column Break" }] : []),
					...(i % 2 === 1 && i < condition_keys.length - 1
						? [{ fieldtype: "Section Break", hide_border: 1 }] : []),
				]),

				{ fieldtype: "HTML", fieldname: "valuation_display", options: `
					<div class="ch-bb-live-valuation"
						style="background:var(--pos-bg-alt,#f8f9fb);border-radius:var(--pos-radius,8px);padding:16px;margin:10px 0;text-align:center">
						<div style="font-size:11px;color:var(--pos-text-muted);text-transform:uppercase;letter-spacing:.06em">
							${__("Estimated Buyback Value")}
						</div>
						<div class="ch-bb-live-price"
							style="font-size:30px;font-weight:800;color:var(--pos-primary,#6366f1);margin-top:4px">—</div>
						<div class="ch-bb-live-grade" style="font-size:12px;color:var(--pos-text-muted)"></div>
					</div>` },

				{ fieldtype: "Section Break", label: __("KYC Details") },
				{ fieldname: "kyc_id_type", fieldtype: "Select", label: __("ID Type"),
					options: "\nAadhaar\nPAN\nPassport\nDriving Licence\nVoter ID" },
				{ fieldname: "kyc_id_number", fieldtype: "Data", label: __("ID Number") },
				{ fieldtype: "Column Break" },
				{ fieldname: "kyc_name", fieldtype: "Data", label: __("Name on ID") },
			],
			primary_action_label: __("Create Assessment"),
			primary_action: (values) => {
				if (!values.item || !values.mobile_no) return;
				const checks = {};
				condition_keys.forEach(k => { checks[k] = values[`cond_${k}`] ? true : false; });
				dlg.disable_primary_action();

				const proceed = () => {
					frappe.xcall("ch_pos.api.pos_api.create_buyback_assessment_with_grading", {
						mobile_no: values.mobile_no,
						item_code: values.item,
						imei_serial: values.imei_serial || "",
						customer: values.customer || "",
						condition_checks: checks,
						kyc_id_type: values.kyc_id_type || "",
						kyc_id_number: values.kyc_id_number || "",
						kyc_name: values.kyc_name || "",
					}).then((doc) => {
						dlg.hide();
						frappe.show_alert({
							message: `${__("Assessment")} <b>${doc.name}</b> ${__("created")} · ${__("Grade")} ${doc.estimated_grade} · ₹${format_number(doc.estimated_price)}`,
							indicator: "green",
						});
						panel.find(".ch-bb-search").val(doc.name);
						panel.find(".ch-bb-lookup").click();
					}).catch(() => dlg.enable_primary_action());
				};

				if (values.imei_serial) {
					frappe.xcall("ch_pos.api.pos_api.check_imei_blacklist", { imei: values.imei_serial })
						.then(res => {
							if (res && res.blacklisted) {
								dlg.enable_primary_action();
								frappe.msgprint({
									title: __("Blacklisted Device"),
									message: __("IMEI {0} is blacklisted — {1}", [values.imei_serial, res.reason]),
									indicator: "red",
								});
							} else { proceed(); }
						}).catch(() => proceed());
				} else { proceed(); }
			},
		});

		// Live valuation on item / condition change
		const update_val = () => {
			const item_code = dlg.get_value("item");
			if (!item_code) {
				dlg.$wrapper.find(".ch-bb-live-price").text("—");
				dlg.$wrapper.find(".ch-bb-live-grade").text("");
				return;
			}
			const checks = {};
			condition_keys.forEach(k => { checks[k] = dlg.get_value(`cond_${k}`) ? true : false; });
			frappe.xcall("ch_pos.api.pos_api.calculate_buyback_valuation", {
				item_code, condition_checks: checks,
			}).then(val => {
				dlg.$wrapper.find(".ch-bb-live-price").text(`₹${format_number(val.final_price || val.offered_price)}`);
				dlg.$wrapper.find(".ch-bb-live-grade").text(
					`${__("Grade")}: ${val.grade} · ${__("Base")}: ₹${format_number(val.base_price)} · ${__("Deduction")}: ₹${format_number(val.total_deduction)}`
				);
			});
		};

		dlg.fields_dict.item.$input.on("change", update_val);
		condition_keys.forEach(k => dlg.fields_dict[`cond_${k}`].$input.on("change", update_val));
		dlg.show();
	}

	// ─────────────────────────────── helpers ──
	_status_cls(status) {
		if (["Submitted", "Approved", "Complete", "Inspection Created",
			"Customer Approved", "Paid", "Closed"].includes(status)) return "success";
		if (["Draft", "Awaiting Approval", "Awaiting Customer Approval",
			"Ready to Pay"].includes(status)) return "warning";
		if (["Rejected", "Cancelled"].includes(status)) return "danger";
		return "muted";
	}
}
