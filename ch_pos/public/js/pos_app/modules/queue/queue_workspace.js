/**
 * CH POS — Queue Workspace (Universal)
 *
 * Company-aware token queue panel:
 * - GoFix (service): "GoFix Request" conversion (existing flow)
 * - GoGizmo (retail): "Start Billing" + "Withdraw" actions
 *
 * Shows Waiting / Engaged / In-Progress tokens for the current store.
 * Auto-refreshes every 30 seconds while the queue tab is active.
 */
import { PosState, EventBus } from "../../state.js";

/** Heuristic: service company? */
function _is_service() {
	const c = (PosState.company || "").toLowerCase();
	return c.includes("gofix") || c.includes("service");
}

// Withdrawal reason taxonomy — aligned with Salesforce/Dynamics 365 lost-opportunity
// reason codes so funnel analytics can be reported consistently.
const WITHDRAW_REASONS = [
	"Price Too High",
	"Item Not Available",
	"Just Browsing",
	"Found Elsewhere",
	"Will Come Back Later",
	"Long Wait Time",
	"Stock Not Available",
	"Customer Decision Pending",
	"Other",
];

// The device-intake vocabulary is owned by GoFix and published on boot, so the
// POS can never offer a condition the Service Request will reject.
const FALLBACK_DEVICE_CONDITIONS = [
	"Good", "Minor Scratches", "Cracked Screen", "Damaged", "Water Damaged", "Broken",
];

function deviceConditionOptions() {
	const fromBoot = frappe.boot && frappe.boot.gofix_device_conditions;
	return (Array.isArray(fromBoot) && fromBoot.length) ? fromBoot : FALLBACK_DEVICE_CONDITIONS;
}

function defaultDeviceCondition() {
	return (frappe.boot && frappe.boot.gofix_default_device_condition) || "Good";
}

export class QueueWorkspace {
	constructor() {
		this._panel = null;
		this._refreshTimer = null;
		this._tokens = [];
		EventBus.on("workspace:render", (ctx) => {
			if (ctx.mode !== "queue") return;
			this._panel = ctx.panel;
			this._render(ctx.panel);
			this._startAutoRefresh();
		});
		EventBus.on("mode:switch", (mode) => {
			if (mode !== "queue") this._stopAutoRefresh();
		});
	}

	_startAutoRefresh() {
		this._stopAutoRefresh();
		this._refreshTimer = setInterval(() => this._loadTokens(), 30000);
	}

	_stopAutoRefresh() {
		if (this._refreshTimer) {
			clearInterval(this._refreshTimer);
			this._refreshTimer = null;
		}
	}

	_render(panel) {
		const is_svc = _is_service();
		const title = is_svc ? __("Service Queue") : __("Store Queue");
		const hint = is_svc
			? __("Waiting tokens from the kiosk — accept or convert to service requests")
			: __("Manage walk-in customers — start billing or close out tokens");

		panel.html(`
			<div class="ch-pos-mode-panel">
				<div class="ch-mode-header">
					<h4>
						<span class="mode-icon ch-queue-icon ${is_svc ? "ch-queue-icon--service" : "ch-queue-icon--retail"}">
							<i class="fa fa-${is_svc ? "stethoscope" : "users"}"></i>
						</span>
						${title}
					</h4>
					<span class="ch-mode-hint">${hint}</span>
				</div>

				<div class="ch-queue-toolbar">
					<div class="ch-queue-stats">
						<span class="ch-queue-count"></span>
					</div>
					<button class="btn btn-xs btn-default ch-queue-refresh-btn">
						<i class="fa fa-refresh"></i> ${__("Refresh")}
					</button>
				</div>

				<div class="ch-queue-token-list">
					<div class="ch-queue-empty-state ch-queue-loading-state">
						<i class="fa fa-spinner fa-spin fa-2x"></i>
						<span>${__("Loading tokens…")}</span>
					</div>
				</div>
			</div>
		`);

		panel.find(".ch-queue-refresh-btn").on("click", () => this._loadTokens());
		this._loadTokens();
	}

	_loadTokens() {
		const pos_profile = PosState.pos_profile;
		if (!pos_profile) return;

		if (this._panel) {
			this._panel.find(".ch-queue-token-list").html(
				`<div class="ch-queue-empty-state ch-queue-loading-state">
					<i class="fa fa-spinner fa-spin fa-2x"></i>
					<span>${__("Loading tokens…")}</span>
				</div>`
			);
		}

		frappe.xcall("ch_pos.api.token_api.get_pos_waiting_tokens", { pos_profile })
			.then((tokens) => {
				this._tokens = tokens || [];
				this._renderTokenList(this._tokens);
			})
			.catch(() => {
				if (this._panel) {
					this._panel.find(".ch-queue-token-list").html(
						`<div class="ch-queue-empty-state ch-queue-error-state">
							<i class="fa fa-exclamation-circle fa-2x"></i>
							<span>${__("Failed to load queue")}</span>
							<span class="ch-queue-empty-hint">${__("Check your connection and try refreshing")}</span>
						</div>`
					);
				}
			});
	}

	_renderTokenList(tokens) {
		if (!this._panel) return;
		const list = this._panel.find(".ch-queue-token-list");

		// Update stats bar
		const stats = this._panel.find(".ch-queue-stats");
		const waiting = (tokens || []).filter((t) => t.status === "Waiting").length;
		const hold = (tokens || []).filter((t) => t.status === "Hold").length;
		const engaged = (tokens || []).filter((t) => t.status === "Engaged" || t.status === "In Progress").length;

		if (!tokens || !tokens.length) {
			stats.html(`<span class="ch-queue-count-text">${__("No tokens")}</span>`);
			list.html(`
				<div class="ch-queue-empty-state">
					<div class="ch-queue-empty-icon">
						<i class="fa fa-check-circle"></i>
					</div>
					<span class="ch-queue-empty-title">${__("All clear!")}</span>
					<span class="ch-queue-empty-hint">${__("No waiting or in-progress tokens right now")}</span>
				</div>
			`);
			return;
		}

		let stats_html = `<span class="ch-queue-count-text">${tokens.length} ${__("token(s)")}</span>`;
		if (hold > 0) stats_html += `<span class="ch-queue-stat-badge ch-queue-stat--waiting">${hold} ${__("on hold")}</span>`;
		if (waiting > 0) stats_html += `<span class="ch-queue-stat-badge ch-queue-stat--waiting">${waiting} ${__("waiting")}</span>`;
		if (engaged > 0) stats_html += `<span class="ch-queue-stat-badge ch-queue-stat--active">${engaged} ${__("active")}</span>`;
		stats.html(stats_html);

		const cards = tokens.map((t) => this._tokenCard(t)).join("");
		list.html(`<div class="ch-queue-cards">${cards}</div>`);

		// Bind action buttons
		list.find(".ch-queue-convert-btn").on("click", (e) => {
			const name = $(e.currentTarget).data("token");
			const token = this._tokens.find((t) => t.name === name);
			if (token) this._openIntake(token);
		});
		list.find(".ch-queue-bill-btn").on("click", (e) => {
			const name = $(e.currentTarget).data("token");
			const token = this._tokens.find((t) => t.name === name);
			if (token) this._startBilling(token);
		});
		list.find(".ch-queue-drop-btn").on("click", (e) => {
			const name = $(e.currentTarget).data("token");
			const token = this._tokens.find((t) => t.name === name);
			if (token) this._showDropDialog(token);
		});
		list.find(".ch-queue-status-btn, .ch-queue-collect-btn").on("click", (e) => {
			const name = $(e.currentTarget).data("token");
			const token = this._tokens.find((t) => t.name === name);
			if (token) this._openWalkinContext(token);
		});

		// Resolve status and collection detail on load, not on click, so the
		// executive reads the answer off the card and can tell the customer
		// without opening anything.
		this._resolveContexts(list, tokens);
	}

	// ── Reason-aware context ────────────────────────────────────

	_resolveContexts(list, tokens) {
		const needs = tokens.filter(
			(t) => t.counter_action === "Show Repair Status" || t.counter_action === "Collect Device"
		);
		needs.forEach((t) => {
			frappe.xcall("ch_pos.api.token_api.get_walkin_context", { token: t.name })
				.then((ctx) => {
					this._context_cache = this._context_cache || {};
					this._context_cache[t.name] = ctx;
					const slot = list.find(`[data-context-for="${t.name}"]`);
					if (!slot.length) return;
					slot.html(this._contextHtml(t, ctx));
				})
				.catch(() => { /* a lookup failure must never blank the queue */ });
		});
	}

	_contextHtml(t, ctx) {
		const esc = (v) => frappe.utils.escape_html(String(v));
		const repairs = (ctx && ctx.repairs) || [];
		if (!repairs.length) {
			return `<p class="ch-q-note ch-q-context-empty">
				<i class="fa fa-info-circle"></i>
				${__("No open repair found for this number.")}
			</p>`;
		}
		const collecting = t.counter_action === "Collect Device";
		// Collection is about what is payable; a status question is about
		// where the job has reached.
		const rows = repairs.slice(0, 3).map((r) => {
			const money = (r.outstanding !== null && r.outstanding !== undefined)
				? `<b>${format_currency(r.outstanding)}</b> ${__("due")}`
				: (r.estimate ? `${__("est.")} ${format_currency(r.estimate)}` : "");
			return `<div class="ch-q-context-row">
				<a href="/app/service-request/${encodeURIComponent(r.service_request)}" target="_blank">
					${esc(r.service_request)}</a>
				${r.status ? `<span class="ch-q-tag">${esc(r.status)}</span>` : ""}
				${r.device ? `<span class="ch-q-context-device">${esc(r.device)}</span>` : ""}
				${collecting && r.invoice ? `<a href="/app/sales-invoice/${encodeURIComponent(r.invoice)}" target="_blank">${esc(r.invoice)}</a>` : ""}
				${collecting && money ? `<span class="ch-q-context-due">${money}</span>` : ""}
			</div>`;
		}).join("");
		return `<div class="ch-q-context-box">${rows}</div>`;
	}

	// Full detail, for when the counter wants more than the card shows.
	_openWalkinContext(token) {
		const cached = (this._context_cache || {})[token.name];
		const render = (ctx) => {
			const repairs = (ctx && ctx.repairs) || [];
			const collecting = token.counter_action === "Collect Device";
			if (!repairs.length) {
				frappe.msgprint({
					title: __("No open repair"),
					message: __("Nothing is open for {0}. Raise a new request if they have brought a device in.",
						[token.customer_phone || token.customer_name || __("this customer")]),
					indicator: "orange",
				});
				return;
			}
			const body = repairs.map((r) => `
				<tr>
					<td><a href="/app/service-request/${encodeURIComponent(r.service_request)}" target="_blank">${frappe.utils.escape_html(r.service_request)}</a></td>
					<td>${frappe.utils.escape_html(r.status || "—")}</td>
					<td>${frappe.utils.escape_html(r.device || "—")}</td>
					<td>${r.invoice ? `<a href="/app/sales-invoice/${encodeURIComponent(r.invoice)}" target="_blank">${frappe.utils.escape_html(r.invoice)}</a>` : "—"}</td>
					<td style="text-align:right">${(r.outstanding !== null && r.outstanding !== undefined) ? format_currency(r.outstanding) : "—"}</td>
				</tr>`).join("");
			const d = new frappe.ui.Dialog({
				title: collecting ? __("Hand Over Device") : __("Repair Status"),
				size: "large",
				fields: [{
					fieldtype: "HTML",
					fieldname: "body",
					options: `<table class="table table-bordered" style="margin:0">
						<thead><tr>
							<th>${__("Request")}</th><th>${__("Status")}</th><th>${__("Device")}</th>
							<th>${__("Invoice")}</th><th style="text-align:right">${__("Due")}</th>
						</tr></thead>
						<tbody>${body}</tbody>
					</table>`,
				}],
			});
			// Collection ends in payment, so offer the cart directly rather
			// than making the executive re-find the invoice.
			const payable = repairs.find((r) => r.invoice && r.outstanding);
			if (collecting && payable) {
				d.set_primary_action(__("Collect Payment"), () => {
					d.hide();
					PosState.kiosk_token = token.name;
					frappe.set_route("Form", "Sales Invoice", payable.invoice);
				});
			}
			d.show();
		};
		if (cached) return render(cached);
		frappe.xcall("ch_pos.api.token_api.get_walkin_context", { token: token.name })
			.then(render)
			.catch(() => frappe.show_alert({ message: __("Could not load repair status"), indicator: "orange" }));
	}

	// ── Token Card ──────────────────────────────────────────────

	// Whatever this token already turned into. These links were on the token
	// all along and never rendered, so the counter had no way from the queue
	// to the repair job or the invoice it produced.
	_linkLine(t) {
		const bits = [];
		const sr = t.service_request_info || {};
		if (t.linked_service_request) {
			const status = sr.status ? ` · ${frappe.utils.escape_html(sr.status)}` : "";
			bits.push(`<a href="/app/service-request/${encodeURIComponent(t.linked_service_request)}" target="_blank">
				<i class="fa fa-wrench"></i> ${frappe.utils.escape_html(t.linked_service_request)}${status}</a>`);
		}
		const invoice = sr.service_invoice || t.converted_invoice;
		if (invoice) {
			bits.push(`<a href="/app/sales-invoice/${encodeURIComponent(invoice)}" target="_blank">
				<i class="fa fa-file-text-o"></i> ${frappe.utils.escape_html(invoice)}</a>`);
		}
		if (t.linked_customer) {
			bits.push(`<a href="/app/customer/${encodeURIComponent(t.linked_customer)}" target="_blank">
				<i class="fa fa-user"></i> ${frappe.utils.escape_html(t.linked_customer)}</a>`);
		}
		if (!bits.length) return "";
		return `<p class="ch-q-note ch-q-links">${bits.join(" &nbsp; ")}</p>`;
	}

	_tokenCard(t) {
		const is_svc = _is_service();
		const statusMap = {
			Waiting:       { cls: "waiting",  icon: "fa-clock-o",     label: __("Waiting") },
			Hold:          { cls: "waiting",  icon: "fa-pause-circle", label: __("Hold") },
			Engaged:       { cls: "engaged",  icon: "fa-handshake-o", label: __("Engaged") },
			"In Progress": { cls: "progress", icon: "fa-cogs",        label: __("In Progress") },
		};
		const st = statusMap[t.status] || { cls: "default", icon: "fa-circle", label: t.status };
		const timeAgo = frappe.datetime.comment_when(t.creation);

		// Customer display
		const cust_name  = frappe.utils.escape_html(t.customer_name || __("Walk-in"));
		const cust_phone = t.customer_phone ? frappe.utils.escape_html(t.customer_phone) : "";

		// Detail — everything the customer actually filled in on the tablet.
		// The card used to show brand and issue category only, so a device type,
		// model, the chosen symptoms and the visit reason itself were all
		// captured and then thrown away. The counter had to open the token to
		// learn why the person was standing there.
		const esc = (v) => frappe.utils.escape_html(String(v));
		const tag = (icon, v) => v ? `<span class="ch-q-tag"><i class="fa ${icon}"></i> ${esc(v)}</span>` : "";
		let detail_html = "";
		if (is_svc) {
			// Device reads type → brand → model, each only when it adds
			// something, so "Smart Phones · Oneplus" does not become
			// "Smart Phones Smart Phones".
			const device = [t.device_type, t.device_brand, t.device_model_name || t.device_model]
				.filter(Boolean)
				.filter((v, i, arr) => arr.indexOf(v) === i)
				.join(" · ") || t.other_device_hint || "";
			const symptoms = (t.symptom_labels || []).join(", ");
			detail_html = `
				<div class="ch-q-tags">
					${tag("fa-question-circle", t.visit_reason)}
					${tag("fa-mobile", device)}
					${tag("fa-wrench", t.issue_category)}
					${tag("fa-bullhorn", t.referral_source)}
					${tag("fa-language", t.customer_language && t.customer_language !== "English" ? t.customer_language : "")}
				</div>
				${symptoms ? `<p class="ch-q-note"><b>${__("Symptoms")}:</b> ${esc(symptoms)}</p>` : ""}
				${t.issue_description ? `<p class="ch-q-note">${esc(t.issue_description.substring(0, 160))}${t.issue_description.length > 160 ? "…" : ""}</p>` : ""}
				${this._linkLine(t)}
				<div class="ch-q-context" data-context-for="${frappe.utils.escape_html(t.name)}"></div>`;
		} else {
			const purpose = t.visit_purpose || "Sales";
			const purposeClsMap = { Sales: "ch-q-purpose--sales", Repair: "ch-q-purpose--repair", Buyback: "ch-q-purpose--buyback" };
			const purposeCls = purposeClsMap[purpose] || "ch-q-purpose--other";
			const tags = [t.visit_reason, t.category_interest, t.brand_interest, t.budget_range, t.referral_source].filter(Boolean);
			detail_html = `
				<div class="ch-q-tags">
					<span class="ch-q-purpose ${purposeCls}">${frappe.utils.escape_html(purpose)}</span>
					${tags.map(x => `<span class="ch-q-tag">${frappe.utils.escape_html(x)}</span>`).join("")}
				</div>
				${this._linkLine(t)}`;
		}

		// Action buttons — driven by the visit reason, not by the company.
		// The previous build branched on whether the company name contained
		// "gofix", so every service token was offered Create GoFix Request:
		// a customer who came only to collect a repaired phone got the same
		// button as a water-damage intake. The reason was on the token all
		// along. counter_action comes from the GoFix Visit Reason master so
		// ops can add a reason and say what it means here without a code change.
		const action = t.counter_action || (is_svc ? "Create Request" : "Bill");
		const allow_create = !!t.allow_create_request;
		let actions_html = "";
		if (is_svc && action !== "Create Request") {
			const buttons = [];
			if (action === "Show Repair Status") {
				buttons.push(`<button class="btn btn-sm btn-primary ch-queue-status-btn"
					data-token="${frappe.utils.escape_html(t.name)}">
					<i class="fa fa-search"></i> ${__("Repair Status")}
				</button>`);
			} else if (action === "Collect Device") {
				buttons.push(`<button class="btn btn-sm btn-primary ch-queue-collect-btn"
					data-token="${frappe.utils.escape_html(t.name)}">
					<i class="fa fa-handshake-o"></i> ${__("Hand Over")}
				</button>`);
			}
			// Warranty visits genuinely split — usually a status question,
			// sometimes a new job — so that reason carries both.
			if (allow_create) {
				buttons.push(`<button class="btn btn-sm btn-default ch-queue-convert-btn"
					data-token="${frappe.utils.escape_html(t.name)}">
					<i class="fa fa-plus"></i> ${__("GoFix Request")}
				</button>`);
			}
			buttons.push(`<button class="btn btn-sm btn-default ch-queue-drop-btn"
				data-token="${frappe.utils.escape_html(t.name)}">
				${__("Withdraw")}
			</button>`);
			actions_html = buttons.join("\n");
		} else if (is_svc) {
			// Withdraw belongs here too. A service walk-in leaves without
			// proceeding just as a retail one does -- the customer hears the
			// quote and declines, or will not wait -- and until now the only
			// service action was "GoFix Request", so those tokens sat In
			// Progress forever and never reached the drop analysis the retail
			// side has had all along. Same endpoint, same mandatory reason and
			// remarks, so the funnel reads consistently across both companies.
			actions_html = `
				<button class="btn btn-sm btn-primary ch-queue-convert-btn"
					data-token="${frappe.utils.escape_html(t.name)}">
					<i class="fa fa-plus"></i> ${__("GoFix Request")}
				</button>
				<button class="btn btn-sm btn-default ch-queue-drop-btn"
					data-token="${frappe.utils.escape_html(t.name)}">
					${__("Withdraw")}
				</button>`;
		} else {
			actions_html = `
				<button class="btn btn-sm btn-primary ch-queue-bill-btn"
					data-token="${frappe.utils.escape_html(t.name)}">
					${__("Bill")}
				</button>
				<button class="btn btn-sm btn-default ch-queue-drop-btn"
					data-token="${frappe.utils.escape_html(t.name)}">
					${__("Withdraw")}
				</button>`;
		}

		return `
			<div class="ch-q-card ch-q-card--${st.cls}">
				<div class="ch-q-indicator"></div>
				<div class="ch-q-content">
					<div class="ch-q-row-top">
						<span class="ch-q-token-id">${frappe.utils.escape_html(t.token_display || t.name)}</span>
						<span class="ch-q-status ch-q-status--${st.cls}">
							<span class="ch-q-status-dot"></span> ${st.label}
						</span>
						<span class="ch-q-time">${timeAgo}</span>
					</div>
					<div class="ch-q-row-mid">
						<span class="ch-q-customer-name">${cust_name}</span>
						${cust_phone ? `<span class="ch-q-customer-phone">${cust_phone}</span>` : ""}
					</div>
					${detail_html}
				</div>
				<div class="ch-q-actions">
					${actions_html}
				</div>
			</div>
		`;
	}

	// ── Retail: Start Billing ───────────────────────────────────

	_startBilling(token) {
		const _proceed = () => {
			// Store token reference in state — will be passed to Sales Invoice
			PosState.kiosk_token = token.name;
			PosState.kiosk_token_status = "In Progress";

			// Auto-set customer from token data
			this._resolve_customer(token).then((customer) => {
				if (customer) {
					PosState.customer = customer;
					EventBus.emit("customer:set", customer);
					this._open_sell_mode(token);
					return;
				}

				// New customer path: enforce verified phone before billing.
				if (token.customer_phone && window.ch_open_new_customer_dialog) {
					window.ch_open_new_customer_dialog({
						company: PosState.company,
						prefill_name: token.customer_name || "",
						prefill_mobile: token.customer_phone || "",
						on_success: (name) => {
							if (name) {
								PosState.customer = name;
								EventBus.emit("customer:set", name);
							}
							this._open_sell_mode(token);
						},
						on_use_existing: (customer) => {
							if (customer) {
								PosState.customer = customer;
								EventBus.emit("customer:set", customer);
							}
							this._open_sell_mode(token);
						},
					});
					return;
				}

				// No phone captured → proceed as walk-in.
				const fallback = PosState.default_customer || null;
				if (fallback) {
					PosState.customer = fallback;
					EventBus.emit("customer:set", fallback);
				}
				this._open_sell_mode(token);
			});
		};

		frappe.xcall("ch_pos.api.token_api.start_pos_billing", {
			token_name: token.name,
			pos_profile: PosState.pos_profile,
				sales_executive: PosState.sales_executive || "",
		}).then((result) => {
			if (result && result.action === "held") {
				frappe.show_alert({
					message: __("Billing is already active for {0}. {1} has been placed on Hold.", [
						(result.active_token && (result.active_token.token_display || result.active_token.name)) || __("another token"),
						token.token_display || token.name,
					]),
					indicator: "orange",
				}, 6);
				this._loadTokens();
				return;
			}
			_proceed();
		}).catch((err) => {
				frappe.show_alert({
					message: err.message || __("Failed to engage token"),
					indicator: "red",
				});
			});
	}

	_open_sell_mode(token) {
		PosState.active_mode = "sell";
		EventBus.emit("mode:set", "sell");
		EventBus.emit("mode:switch", "sell");

		frappe.show_alert({
			message: __("Billing started for token {0} — {1}", [
				token.token_display || token.name,
				token.customer_name || __("Walk-in"),
			]),
			indicator: "blue",
		}, 5);
	}

	/**
	 * Resolve an ERPNext Customer from token data.
	 * Priority: linked_customer > phone lookup > default_customer (Walk-in).
	 */
	_resolve_customer(token) {
		// 1. Already linked to an ERPNext Customer
		if (token.linked_customer) {
			return Promise.resolve(token.linked_customer);
		}
		// 2. Try to find Customer by phone number
		if (token.customer_phone) {
			return frappe.xcall("ch_pos.api.token_api.find_customer_by_phone", {
				phone: token.customer_phone,
				pos_profile: token.pos_profile || PosState.pos_profile,
			}).then((name) => name || null)
			  .catch(() => null);
		}
		// 3. Fall back to POS Profile's default customer (Walk-in Customer)
		return Promise.resolve(PosState.default_customer || null);
	}

	// ── Retail: Withdraw Token ──────────────────────────────────
	// Internally still posts status="Dropped" for backward compatibility with
	// existing reports, but the UX uses "Withdraw"/"Withdrawn" terminology that
	// matches Salesforce, Dynamics 365 and Oracle Service Cloud conventions.

	_showDropDialog(token) {
		const d = new frappe.ui.Dialog({
			title: `${__("Withdraw Token")} — ${token.token_display || token.name}`,
			fields: [
				{
					label: __("Customer"),
					fieldtype: "Data",
					fieldname: "customer_name",
					default: token.customer_name,
					read_only: 1,
				},
				{
					label: __("Withdrawal Reason"),
					fieldtype: "Select",
					fieldname: "drop_reason",
					options: WITHDRAW_REASONS.join("\n"),
					reqd: 1,
					// Pre-fill from token if the customer already declared an
					// intent at the kiosk (or a prior cancel attempt). The
					// cashier can still override from the dropdown.
					default: token.drop_reason || "",
					description: __("Required for funnel analytics — pick the closest match"),
				},
				{
					label: __("Sub-Reason / Detail"),
					fieldtype: "Data",
					fieldname: "drop_sub_reason",
					depends_on: "drop_reason",
					default: token.drop_sub_reason || "",
				},
				{
					label: __("Remarks"),
					fieldtype: "Small Text",
					fieldname: "drop_remarks",
					reqd: 1,
					default: token.drop_remarks || "",
					placeholder: __("Capture why the customer did not convert (mandatory for audit)"),
				},
			],
			primary_action_label: `<i class="fa fa-times-circle"></i> ${__("Withdraw Token")}`,
			primary_action: (values) => {
				if (!String(values.drop_remarks || "").trim()) {
					frappe.show_alert({ message: __("Remarks are required"), indicator: "red" });
					return;
				}
				d.disable_primary_action();
				frappe.xcall("ch_pos.api.token_api.drop_token", {
					token_name: token.name,
					drop_reason: values.drop_reason,
					drop_sub_reason: values.drop_sub_reason || "",
					drop_remarks: values.drop_remarks || "",
				}).then(() => {
					d.hide();
					frappe.show_alert({
						message: __("Token {0} withdrawn — {1}", [
							token.token_display || token.name,
							values.drop_reason,
						]),
						indicator: "orange",
					});
					this._loadTokens();
				}).catch((err) => {
					d.enable_primary_action();
					frappe.show_alert({
						message: err.message || __("Failed to withdraw token"),
						indicator: "red",
					});
				});
			},
		});
		d.show();
	}

	// ── Service: GoFix Request ──────────────────────────────────

	/**
	 * Hand the token to the Service Intake form in the Repair section.
	 *
	 * This used to open a modal that rebuilt the intake form from scratch with a
	 * smaller field list, so a queue-raised ticket could not record a technician,
	 * a promised completion time or a device serial, and "Device Item (optional)"
	 * in the modal contradicted a mandatory field on the DocType. One form now
	 * serves both the counter and the queue.
	 */
	_openIntake(token) {
		// Hand the token to the repair intake — but a walk-in from a NEW customer
		// gets the customer created FIRST, exactly like the retail Start-Billing
		// flow above, so the repair ticket is linked to a real Customer instead
		// of a bare name string (which is how duplicate Customers get made).
		const proceed = (customer) => {
			// Carry the resolved/created customer across so the intake sets it
			// directly instead of re-guessing from the phone.
			token._resolved_customer = customer || "";
			PosState.repairIntakeToken = token;
			EventBus.emit("mode:set", "repair");
			EventBus.emit("mode:switch", "repair");
		};

		this._resolve_customer(token).then((customer) => {
			if (customer) {
				proceed(customer);
				return;
			}
			// New customer: open the creation form first, pre-filled with the
			// name and phone the walk-in already captured. Only after the
			// customer exists do we move to the repair page.
			if (token.customer_phone && window.ch_open_new_customer_dialog) {
				window.ch_open_new_customer_dialog({
					company: PosState.company,
					prefill_name: token.customer_name || "",
					prefill_mobile: token.customer_phone || "",
					on_success: (name) => proceed(name),
					on_use_existing: (existing) => proceed(existing),
				});
				return;
			}
			// No phone to identify or create against — open intake as-is.
			proceed(null);
		}).catch(() => proceed(null));
	}
}
