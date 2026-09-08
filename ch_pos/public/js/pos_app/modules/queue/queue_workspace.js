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
		// One desk, one list. Walk-ins and the requests that arrived before the
		// customer did are the same record with a different channel, so they are
		// worked from the same place rather than two screens that each show half
		// of who is waiting.
		const title = is_svc ? __("Service Front Desk") : __("Store Front Desk");
		const hint = is_svc
			? __("Everyone waiting on this store — in the shop, or who messaged, called or booked ahead")
			: __("Everyone waiting on this store — walk-ins, and enquiries that came in before they did");

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

				<!-- Options are fetched from the doctype and the masters, so a
				     new channel or a retired purpose is configuration, not a
				     release. -->
				<div class="ch-queue-filters"></div>

				<div class="ch-queue-toolbar">
					<div class="ch-queue-stats">
						<span class="ch-queue-count"></span>
					</div>
					<button class="btn btn-xs btn-default ch-queue-refresh-btn">
						<i class="fa fa-refresh"></i> ${__("Refresh")}
					</button>
				</div>

				<div class="ch-queue-split">
				<div class="ch-queue-token-list">
					<div class="ch-queue-empty-state ch-queue-loading-state">
						<i class="fa fa-spinner fa-spin fa-2x"></i>
						<span>${__("Loading tokens…")}</span>
					</div>
				</div>
				<div class="ch-queue-detail">
					<div class="ch-q-detail-empty">
						<i class="fa fa-comments-o"></i>
						<span>${__("Pick anyone waiting to see everything they told us")}</span>
					</div>
				</div>
				</div>
			</div>
		`);

		this._loadOptions().then(() => this._paintFilters());
		panel.find(".ch-queue-refresh-btn").on("click", () => this._loadTokens());

		// Filtering the loaded list rather than re-querying: the desk is a
		// small set and the counter wants it to respond as they type.
		this._filters = { status: "", channel: "", purpose: "", due: "", search: "" };
		panel.on("change", ".ch-q-filter", (e) => {
			const key = $(e.currentTarget).data("f");
			const was = this._filters[key];
			this._filters[key] = e.currentTarget.value;
			// The routing pool is a different query, not a narrower view of the
			// same one, so moving into or out of it has to re-fetch. Filtering
			// the loaded list instead showed this store's own walk-ins under a
			// heading that said they were unrouted.
			const pool = key === "channel"
				&& (was === "__unassigned" || e.currentTarget.value === "__unassigned");
			if (pool) this._loadTokens();
			else this._renderTokenList(this._tokens);
		});
		panel.on("input", ".ch-q-search", frappe.utils.debounce((e) => {
			this._filters.search = (e.target.value || "").trim();
			this._renderTokenList(this._tokens);
		}, 200));
		panel.on("click", ".ch-q-clear", () => {
			const was_pool = this._filters.channel === "__unassigned";
			this._filters = { status: "", channel: "", purpose: "", due: "", search: "" };
			panel.find(".ch-q-filter").val("");
			panel.find(".ch-q-search").val("");
			if (was_pool) this._loadTokens();
			else this._renderTokenList(this._tokens);
		});
		this._loadTokens();
	}

	_loadTokens() {
		const pos_profile = PosState.pos_profile;
		if (!pos_profile) return;

		// The routing pool is a separate, deliberate view. It is company-wide
		// by nature, so it is never mixed into a store's own queue -- that is
		// exactly what put one customer on four desks.
		if ((this._filters || {}).channel === "__unassigned") {
			this._loadUnassigned();
			return;
		}

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
				if (this._selected) this._openDetail(this._selected);
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

	_applyFilters(tokens) {
		const f = this._filters || {};
		const digits = (f.search || "").replace(/\D/g, "");
		return (tokens || []).filter((t) => {
			if (f.status && t.status !== f.status) return false;
			if (f.purpose && (t.visit_purpose || "") !== f.purpose) return false;
			if (f.channel === "__remote" && t.channel_group !== "remote") return false;
			if (f.channel === "__in_person" && t.channel_group === "remote") return false;
			if (f.channel && !f.channel.startsWith("__")
				&& (t.visit_source || "") !== f.channel) return false;
			if (f.due === "overdue" && !this._isOverdue(t)) return false;
			if (f.due === "today" && !this._isDueToday(t)) return false;
			if (f.search) {
				const hay = `${t.customer_name || ""} ${t.customer_phone || ""} ${
					t.token_display || ""} ${t.name}`.toLowerCase();
				const needle = digits.length >= 3 ? digits : f.search.toLowerCase();
				if (!hay.includes(needle)) return false;
			}
			return true;
		});
	}

	_renderTokenList(all_tokens) {
		if (!this._panel) return;
		const tokens = this._applyFilters(all_tokens);
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
					<span class="ch-queue-empty-title">${
						(all_tokens || []).length ? __("Nothing matches") : __("All clear!")}</span>
					<span class="ch-queue-empty-hint">${
						(all_tokens || []).length
							? __("Clear the filters to see the other {0} waiting",
								[(all_tokens || []).length])
							: __("Nobody is waiting on this store right now")}</span>
				</div>
			`);
			return;
		}

		const remote_count = (tokens || []).filter((t) => t.channel_group === "remote").length;
		const unanswered = (tokens || []).filter(
			(t) => t.channel_group === "remote" && t.awaiting_response).length;

		let stats_html = `<span class="ch-queue-count-text">${
			tokens.length - remote_count} ${__("in store")}</span>`;
		if (remote_count > 0) {
			stats_html += `<span class="ch-queue-stat-badge ch-queue-stat--waiting">${
				remote_count} ${__("wrote in")}</span>`;
		}
		if (unanswered > 0) {
			stats_html += `<span class="ch-queue-stat-badge ch-queue-stat--waiting">${
				unanswered} ${__("unanswered")}</span>`;
		}
		if (hold > 0) stats_html += `<span class="ch-queue-stat-badge ch-queue-stat--waiting">${hold} ${__("on hold")}</span>`;
		if (waiting > 0) stats_html += `<span class="ch-queue-stat-badge ch-queue-stat--waiting">${waiting} ${__("waiting")}</span>`;
		if (engaged > 0) stats_html += `<span class="ch-queue-stat-badge ch-queue-stat--active">${engaged} ${__("active")}</span>`;
		stats.html(stats_html);

		// People physically here come first: they are standing in the shop while
		// a message can wait a few minutes. Both are the same record, so this is
		// ordering, not separation.
		const here = tokens.filter((t) => t.channel_group !== "remote");
		const wrote = tokens.filter((t) => t.channel_group === "remote");
		const section = (label, rows) => rows.length
			? `<div class="ch-queue-section-label">${label} · ${rows.length}</div>
			   <div class="ch-queue-cards">${rows.map((t) => this._tokenCard(t)).join("")}</div>`
			: "";
		list.html(
			this._filters && this._filters.channel === "__unassigned"
				? section(__("Not yet routed to any store"), tokens)
				: (here.length && wrote.length)
				? section(__("In the shop"), here) + section(__("Waiting on a reply"), wrote)
				: `<div class="ch-queue-cards">${
					tokens.map((t) => this._tokenCard(t)).join("")}</div>`
		);

		// Bind action buttons
		list.find(".ch-q-card").on("click", (e) => {
			if ($(e.target).closest("button, a").length) return;   // let actions act
			const name = $(e.currentTarget).data("token");
			if (name) this._openDetail(name);
		});

		list.find(".ch-queue-claim-btn").on("click", (e) => {
			const name = $(e.currentTarget).data("token");
			const token = this._tokens.find((t) => t.name === name);
			if (token) this._claimHere(token);
		});

		list.find(".ch-queue-extend-btn").on("click", (e) => {
			const name = $(e.currentTarget).data("token");
			const token = this._tokens.find((t) => t.name === name);
			if (token) this._extendDialog(token);
		});

		list.find(".ch-queue-note-btn").on("click", (e) => {
			const name = $(e.currentTarget).data("token");
			const token = this._tokens.find((t) => t.name === name);
			if (token) this._replyDialog(token);
		});

		list.find(".ch-queue-reply-btn").on("click", (e) => {
			const name = $(e.currentTarget).data("token");
			const token = this._tokens.find((t) => t.name === name);
			if (token) this._replyDialog(token);
		});

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

	// ── Device handover ─────────────────────────────────────────

	// Drives the delivery gates that already existed in gofix and had no caller
	// anywhere: readiness, OTP to the customer's registered number, verify,
	// then complete. complete_handover re-checks every gate server-side, so
	// this dialog guides the executive rather than being the control itself.
	//
	// Keyed on the Service Request. It used to key on the Sales Order, which
	// meant a repair raised under the single-document model -- where no order
	// exists -- could never be handed over from this screen at all.
	_openHandover(token, repair) {
		const sr = repair.service_request;
		const api = "gofix.gofix_services.handover.";
		const d = new frappe.ui.Dialog({
			title: __("Hand Over Device — {0}", [repair.service_request]),
			size: "large",
			fields: [
				{ fieldtype: "HTML", fieldname: "gates" },
				{ fieldtype: "Section Break" },
				{ fieldtype: "Data", fieldname: "otp", label: __("Delivery OTP"),
				  description: __("The customer receives this on their registered number.") },
				{ fieldtype: "Button", fieldname: "send_otp", label: __("Send OTP to Customer") },
				{ fieldtype: "Column Break" },
				{ fieldtype: "Button", fieldname: "verify_otp", label: __("Verify OTP") },
			],
		});

		const paint = (readiness) => {
			const ok = readiness && readiness.ready;
			const blockers = (readiness && readiness.blockers) || [];
			const rows = blockers.length
				? blockers.map((b) => `<li style="color:var(--red-600,#b02a1f)">${frappe.utils.escape_html(b)}</li>`).join("")
				: `<li style="color:var(--green-600,#22684c)">${__("All delivery gates passed.")}</li>`;
			const inv = repair.invoice
				? `<p style="margin:6px 0 0">${__("Invoice")}:
					<a href="/app/sales-invoice/${encodeURIComponent(repair.invoice)}" target="_blank">${frappe.utils.escape_html(repair.invoice)}</a>
					${(repair.outstanding) ? ` — <b>${format_currency(repair.outstanding)} ${__("due")}</b>` : ` — ${__("settled")}`}</p>`
				: "";
			d.fields_dict.gates.$wrapper.html(
				`<div><b>${__("Repair")}:</b> ${frappe.utils.escape_html(sr)}
					${repair.device ? ` · ${frappe.utils.escape_html(repair.device)}` : ""}
				 <ul style="margin:8px 0 0;padding-left:18px">${rows}</ul>${inv}</div>`
			);
			d.set_primary_action(
				ok ? __("Complete Handover") : __("Complete Handover (blocked)"),
				ok ? () => {
					frappe.xcall(api + "complete_handover", { service_request: sr })
						.then(() => {
							frappe.show_alert({ message: __("Device handed over"), indicator: "green" });
							d.hide();
							this._loadTokens();
						})
						.catch(() => { /* server message already shown */ });
				} : null
			);
		};

		const refresh = () => frappe.xcall(api + "handover_readiness", { service_request: sr })
			.then(paint)
			.catch(() => paint({ ready: false, blockers: [__("Could not read delivery readiness.")] }));

		d.fields_dict.send_otp.$input.on("click", () => {
			frappe.xcall(api + "generate_handover_otp", { service_request: sr })
				.then((r) => frappe.show_alert({
					message: (r && r.message) || __("OTP sent to customer"), indicator: "blue" }))
				.catch(() => { /* server message already shown */ });
		});
		d.fields_dict.verify_otp.$input.on("click", () => {
			const otp = d.get_value("otp");
			if (!otp) { frappe.show_alert({ message: __("Enter the OTP"), indicator: "orange" }); return; }
			frappe.xcall(api + "verify_handover_otp", { service_request: sr, otp_input: otp })
				.then((r) => {
					frappe.show_alert({
						message: (r && r.message) || "",
						indicator: (r && r.verified) ? "green" : "red",
					});
					refresh();
				})
				.catch(() => { /* server message already shown */ });
		});

		refresh();
		d.show();
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
			// Handover is not "open the invoice". The gates live on the Service
			// Order -- QC passed, nothing outstanding, delivery OTP verified,
			// accessories returned -- and complete_delivery refuses on any of
			// them. Routing to the invoice skipped all four, which is why the
			// OTP machinery had never once run.
			// Any repair that reached billing can be collected. Requiring a
			// Sales Order here hid the button for every single-document repair.
			const handover = repairs.find((r) => r.service_request);
			if (collecting && handover) {
				d.set_primary_action(__("Hand Over Device"), () => {
					d.hide();
					this._openHandover(token, handover);
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

		// The channel was invisible on the card, so a WhatsApp message and a
		// person standing at the counter looked identical.
		const channel = t.visit_source || (t.channel_group === "remote" ? "Other" : "Counter");
		const chan_pill = `<span class="ch-q-chan" data-c="${frappe.utils.escape_html(channel)}">${
			frappe.utils.escape_html(channel)}</span>`;
		// The agreed date has passed and nobody has decided anything. This is
		// the state a written request rots in, so it is said loudly.
		const overdue = this._isOverdue(t)
			? `<span class="ch-q-overdue" title="${__("The follow-up date has passed")}">${
				__("overdue")}</span>`
			: (this._isDueToday(t)
				? `<span class="ch-q-duetoday">${__("due today")}</span>` : "");

		// Nobody has replied yet, and it is not a person standing here.
		const unanswered = t.channel_group === "remote" && t.awaiting_response
			? `<span class="ch-q-unanswered" title="${
				__("Nobody has replied to this yet")}">${__("unanswered")}</span>`
			: "";

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
		if (t.channel_group === "remote") {
			// What a remote customer gave us is a different set from what a
			// tablet collects, so the card shows that set rather than leaving
			// four empty chips where a walk-in would have them.
			const device = [t.device_brand, t.device_model_name || t.device_model]
				.filter(Boolean)
				.filter((v, i, arr) => arr.indexOf(v) === i)
				.join(" · ") || t.other_device_hint || "";
			detail_html = `
				<div class="ch-q-tags">
					${tag("fa-flag", t.visit_purpose)}
					${tag("fa-mobile", device)}
					${tag("fa-wrench", t.issue_category)}
					${tag("fa-clock-o", t.preferred_datetime
						? __("Wants {0}", [frappe.datetime.str_to_user(t.preferred_datetime)]) : "")}
					${tag("fa-calendar-check-o", t.expires_at
						? __("Decide by {0}", [frappe.datetime.str_to_user(t.expires_at)]) : "")}
					${tag("fa-envelope-o", t.email)}
					${tag("fa-bullhorn", t.referral_source)}
					${tag("fa-user-o", t.assigned_to)}
				</div>
				${t.issue_description ? `<p class="ch-q-note">${
					esc(t.issue_description.substring(0, 220))}${
					t.issue_description.length > 220 ? "…" : ""}</p>` : ""}
				${this._linkLine(t)}`;
		} else if (is_svc) {
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
		if (t.unassigned) {
			// Nobody owns this yet, so the only useful action is to take it.
			actions_html = `
				<button class="btn btn-sm btn-primary ch-queue-claim-btn"
					data-token="${frappe.utils.escape_html(t.name)}">
					<i class="fa fa-hand-paper-o"></i> ${__("Take At This Store")}
				</button>
				<button class="btn btn-sm btn-default ch-queue-note-btn"
					data-token="${frappe.utils.escape_html(t.name)}">
					<i class="fa fa-comment-o"></i> ${__("Note")}
				</button>`;
		} else if (t.channel_group === "remote") {
			// Checked FIRST: counter_action comes from the visit reason, which
			// only a walk-in has. A written request has none, so it defaulted to
			// "None" and the card offered nothing but Withdraw.
			actions_html = `
				<button class="btn btn-sm btn-default ch-queue-reply-btn"
					data-token="${frappe.utils.escape_html(t.name)}">
					<i class="fa fa-comment-o"></i> ${__("Reply")}
				</button>
				${this._isOverdue(t) ? `<button class="btn btn-sm btn-default ch-queue-extend-btn"
					data-token="${frappe.utils.escape_html(t.name)}">
					<i class="fa fa-calendar-plus-o"></i> ${__("Extend")}
				</button>` : ""}
				${is_svc ? `<button class="btn btn-sm btn-primary ch-queue-convert-btn"
					data-token="${frappe.utils.escape_html(t.name)}">
					<i class="fa fa-plus"></i> ${__("Create Service Request")}
				</button>` : `<button class="btn btn-sm btn-primary ch-queue-bill-btn"
					data-token="${frappe.utils.escape_html(t.name)}">
					${__("Bill")}
				</button>`}
				<button class="btn btn-sm btn-default ch-queue-drop-btn"
					data-token="${frappe.utils.escape_html(t.name)}">
					${__("Close")}
				</button>`;
		} else if (is_svc && action !== "Create Request") {
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
			buttons.push(`<button class="btn btn-sm btn-default ch-queue-note-btn"
				data-token="${frappe.utils.escape_html(t.name)}">
				<i class="fa fa-comment-o"></i> ${__("Note")}
			</button>`);
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
				<button class="btn btn-sm btn-default ch-queue-note-btn"
					data-token="${frappe.utils.escape_html(t.name)}">
					<i class="fa fa-comment-o"></i> ${__("Note")}
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
				<button class="btn btn-sm btn-default ch-queue-note-btn"
					data-token="${frappe.utils.escape_html(t.name)}">
					<i class="fa fa-comment-o"></i> ${__("Note")}
				</button>
				<button class="btn btn-sm btn-default ch-queue-drop-btn"
					data-token="${frappe.utils.escape_html(t.name)}">
					${__("Withdraw")}
				</button>`;
		}

		return `
			<div class="ch-q-card ch-q-card--${st.cls}${
				this._selected === t.name ? " ch-q-card--open" : ""}"
			     data-token="${frappe.utils.escape_html(t.name)}">
				<div class="ch-q-indicator"></div>
				<div class="ch-q-content">
					<div class="ch-q-row-top">
						<span class="ch-q-token-id">${frappe.utils.escape_html(t.token_display || t.name)}</span>
						${chan_pill}
						${unanswered}
						${overdue}
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

	// Recording what was said is what moves a request off Waiting -- whether
	// anyone has replied is the one thing this desk must never be wrong about.
	_replyDialog(t) {
		const d = new frappe.ui.Dialog({
			title: __("Reply to {0}", [t.customer_name || t.customer_phone || t.name]),
			fields: [
				{ fieldtype: "HTML", options: t.issue_description
					? `<p class="text-muted">"${frappe.utils.escape_html(t.issue_description)}"</p>`
					: "" },
				{ fieldname: "note", fieldtype: "Small Text", reqd: 1,
				  label: __("What did you tell them?") },
				{ fieldname: "channel", fieldtype: "Select", label: __("Via"),
				  options: ("Phone Call\nWhatsApp\nEmail\nWeb\nMobile App\nSMS\nOther"),
				  default: t.visit_source },
			],
			primary_action_label: __("Save"),
			primary_action: (v) => {
                frappe.xcall("gofix.gofix_services.inbox.add_note", {
					inbox: t.name, note: v.note, channel: v.channel,
				}).then(() => {
					d.hide();
					frappe.show_alert({ message: __("Reply recorded"), indicator: "green" });
					this._loadTokens();
				});
			},
		});
		d.show();
	}

	// ── The whole visit ─────────────────────────────────────────────
	//
	// A card can only carry the opening remark. A request from a website form,
	// WhatsApp or a helpdesk is a conversation, and the desk has to be able to
	// read it — otherwise every reply since is invisible and the next person to
	// pick the request up starts again.
	_openDetail(name) {
		this._selected = name;
		const $d = this._panel.find(".ch-queue-detail");
		this._panel.find(".ch-q-card").removeClass("ch-q-card--open");
		this._panel.find(`.ch-q-card[data-token="${name}"]`).addClass("ch-q-card--open");
		$d.html(`<div class="ch-q-detail-empty"><i class="fa fa-circle-o-notch fa-spin"></i>
			<span>${__("Loading…")}</span></div>`);

		frappe.xcall("gofix.gofix_services.inbox.get_visit", { name }).then((d) => {
			if (this._selected !== name) return;          // they clicked on
			$d.html(this._detailHtml(d));
			$d.find(".ch-q-d-reply").on("click", () => this._replyDialog(d));
			$d.find(".ch-q-d-extend").on("click", () => this._extendDialog(d));
			$d.find(".ch-q-d-convert").on("click", () => this._openIntake(d));
			// Retail: the same billing path a walk-in takes, so the invoice
			// links back to the visit exactly as it already does.
			$d.find(".ch-q-d-bill").on("click", () => this._startBilling(d));
			$d.find(".ch-q-d-close").on("click", () => this._showDropDialog(d));
		}).catch(() => {
			$d.html(`<div class="ch-q-detail-empty">
				<span>${__("Could not load this visit.")}</span></div>`);
		});
	}

	_detailHtml(d) {
		const esc = (v) => frappe.utils.escape_html(String(v));
		const remote = !["Kiosk", "Counter"].includes(d.visit_source);

		// Only what we actually hold. Empty rows teach nobody anything and make
		// a sparse request look like a broken screen.
		const fact = (l, v) => v
			? `<div class="ch-q-d-fact"><dt>${l}</dt><dd>${esc(v)}</dd></div>` : "";
		const device = [d.device_type, d.device_brand, d.device_model_name || d.device_model]
			.filter(Boolean).filter((v, i, a) => a.indexOf(v) === i).join(" · ");
		const facts = [
			fact(__("Reached us by"), d.visit_source),
			fact(__("Received"), frappe.datetime.str_to_user(d.creation)),
			fact(__("Phone"), d.customer_phone),
			fact(__("Alternate"), d.alternate_number),
			fact(__("Email"), d.email),
			fact(__("Customer"), d.linked_customer),
			fact(__("Wants"), d.visit_purpose),
			fact(__("Reason"), d.visit_reason),
			fact(__("Device"), device || d.other_device_hint),
			fact(__("IMEI / Serial"), d.serial_no),
			fact(__("Issue"), d.issue_category),
			fact(__("Symptoms"), (d.symptom_labels || []).join(", ")),
			fact(__("Decide by"), remote && d.expires_at
				? frappe.datetime.str_to_user(d.expires_at) : ""),
			fact(__("Preferred slot"), d.preferred_datetime
				? frappe.datetime.str_to_user(d.preferred_datetime) : ""),
			fact(__("Heard about us via"), d.referral_source),
			fact(__("Language"), d.customer_language),
			fact(__("Owner"), d.assigned_to),
			fact(__("Channel reference"), d.external_ref),
			fact(__("Store"), d.pos_profile),
		].join("");

		const notes = (d.notes || []).length
			? `<ul class="ch-q-d-notes">${d.notes.map((n) => `
				<li>
					<span class="ch-q-d-note-meta">${esc((n.note_datetime || "").slice(0, 16))}
						${n.channel ? "· " + esc(n.channel) : ""}
						${n.noted_by ? "· " + esc(n.noted_by) : ""}</span>
					<div class="ch-q-d-note-body">${esc(n.note)}</div>
				</li>`).join("")}</ul>`
			: `<p class="ch-q-d-none">${
				remote ? __("Nothing said back yet. Reply to start the thread.")
					   : __("Nothing recorded on this visit yet.")}</p>`;

		const repairs = (d.repairs || []).length
			? `<ul class="ch-q-d-list">${d.repairs.map((r) => `
				<li><a href="/app/service-request/${encodeURIComponent(r.name)}" target="_blank">${
					esc(r.name)}</a>
				<span class="ch-q-d-dim">${esc(r.decision || "")}${
					r.device_model ? " · " + esc(r.device_model) : ""}</span></li>`).join("")}</ul>`
			: `<p class="ch-q-d-none">${__("No repairs on this number yet.")}</p>`;

		const others = (d.other_visits || []).length
			? `<p class="ch-q-d-also">${__("Also open from this number:")} ${
				d.other_visits.map((o) => `<b>${esc(o.visit_source)}</b>`).join(", ")}</p>`
			: "";

		return `
			<div class="ch-q-d-head">
				<div>
					<div class="ch-q-d-title">${esc(d.customer_name || __("Unknown caller"))}
						<span class="ch-q-chan" data-c="${esc(d.visit_source || "")}">${
							esc(d.visit_source || "")}</span></div>
					<div class="ch-q-d-sub">${esc(d.token_display || d.name)} · ${
						esc(d.customer_phone || "")}</div>
				</div>
				<div class="ch-q-d-actions">
					<button class="btn btn-sm btn-default ch-q-d-reply">
						<i class="fa fa-comment-o"></i> ${remote ? __("Reply") : __("Note")}</button>
					${remote && d.expires_at ? `<button class="btn btn-sm btn-default ch-q-d-extend">
						<i class="fa fa-calendar-plus-o"></i> ${__("Extend")}</button>` : ""}
					${d.linked_service_request || d.converted_invoice ? "" : (_is_service()
						? `<button class="btn btn-sm btn-primary ch-q-d-convert">
							<i class="fa fa-plus"></i> ${__("Create Service Request")}</button>`
						: `<button class="btn btn-sm btn-primary ch-q-d-bill">
							${__("Bill")}</button>`)}
					<button class="btn btn-sm btn-default ch-q-d-close">${__("Close")}</button>
				</div>
			</div>
			${others}
			${d.issue_description
				? `<div class="ch-q-d-quote">${esc(d.issue_description)}</div>` : ""}
			<div class="ch-q-d-section">${__("What the customer told us")}</div>
			<dl class="ch-q-d-facts">${facts}</dl>
			<div class="ch-q-d-section">${__("Conversation")}</div>
			${notes}
			<div class="ch-q-d-section">${__("This number's repairs")}</div>
			${repairs}
			${d.linked_service_request
				? `<div class="ch-q-d-linked"><i class="fa fa-check-circle"></i>
					${__("Booked in as")} <a href="/app/service-request/${
						encodeURIComponent(d.linked_service_request)}" target="_blank">${
						esc(d.linked_service_request)}</a></div>` : ""}
			${d.converted_invoice
				? `<div class="ch-q-d-linked"><i class="fa fa-check-circle"></i>
					${__("Billed on")} <a href="/app/sales-invoice/${
						encodeURIComponent(d.converted_invoice)}" target="_blank">${
						esc(d.converted_invoice)}</a></div>` : ""}`;
	}

	// ── Options, from where they are configured ─────────────────────
	_loadOptions() {
		if (this._options) return Promise.resolve(this._options);
		return frappe.xcall("gofix.gofix_services.inbox.get_options")
			.then((o) => { this._options = o; return o; })
			.catch(() => {
				// Losing the masters must not leave the desk without filters.
				this._options = { channels: [], purposes: [], open_statuses: [],
								  in_person_channels: [], remote_channels: [],
								  visit_reasons: [], referral_sources: [], followup_days: 3 };
				return this._options;
			});
	}

	_paintFilters() {
		const o = this._options || {};
		const opt = (v, l) => `<option value="${frappe.utils.escape_html(v)}">${
			frappe.utils.escape_html(l)}</option>`;
		this._panel.find(".ch-queue-filters").html(`
			<select class="ch-q-filter" data-f="status" aria-label="${__("Filter by status")}">
				${opt("", __("All open"))}
				${(o.open_statuses || []).map((x) => opt(x, __(x))).join("")}
			</select>
			<select class="ch-q-filter" data-f="channel" aria-label="${__("Filter by channel")}">
				${opt("", __("All channels"))}
				${opt("__in_person", __("In the shop"))}
				${opt("__remote", __("Wrote in"))}
				${opt("__unassigned", __("Not yet routed (all stores)"))}
				${(o.channels || []).map((x) => opt(x, __(x))).join("")}
			</select>
			<select class="ch-q-filter" data-f="purpose" aria-label="${__("Filter by purpose")}">
				${opt("", __("Any purpose"))}
				${(o.purposes || []).map((x) => opt(x, __(x))).join("")}
			</select>
			<select class="ch-q-filter" data-f="due" aria-label="${__("Filter by follow-up")}">
				${opt("", __("Any follow-up"))}
				${opt("overdue", __("Overdue"))}
				${opt("today", __("Due today"))}
			</select>
			<input type="search" class="ch-q-search" placeholder="${__("Phone or name")}"
			       aria-label="${__("Search by phone or name")}">
			<button class="btn btn-xs btn-default ch-q-clear">${__("Clear")}</button>`);
	}

	// A written request carries an agreed date; a walk-in does not, and its
	// expiry is a queue TTL rather than a promise to anybody.
	_isOverdue(t) {
		if (t.channel_group !== "remote" || !t.expires_at) return false;
		return frappe.datetime.str_to_obj(t.expires_at) < new Date();
	}

	_isDueToday(t) {
		if (t.channel_group !== "remote" || !t.expires_at || this._isOverdue(t)) return false;
		return frappe.datetime.str_to_obj(t.expires_at).toDateString() === new Date().toDateString();
	}

	_extendDialog(t) {
		const days = (this._options && this._options.followup_days) || 3;
		const d = new frappe.ui.Dialog({
			title: __("Move the follow-up date"),
			fields: [
				{ fieldtype: "HTML", options: `<p class="text-muted">${
					__("This request was due on {0} and nobody has decided anything. Give it a new date, or close it as withdrawn.",
						[frappe.datetime.str_to_user(t.expires_at)])}</p>` },
				{ fieldname: "follow_up_on", fieldtype: "Datetime", reqd: 1,
				  label: __("Decide by"),
				  default: frappe.datetime.add_days(frappe.datetime.now_datetime(), days) },
				{ fieldname: "note", fieldtype: "Small Text", label: __("Why"),
				  description: __("A date that slips quietly is worse than none at all.") },
			],
			primary_action_label: __("Extend"),
			primary_action: (v) => {
				frappe.xcall("gofix.gofix_services.inbox.extend_follow_up", {
					inbox: t.name, follow_up_on: v.follow_up_on, note: v.note,
				}).then(() => {
					d.hide();
					frappe.show_alert({ message: __("Follow-up moved"), indicator: "green" });
					this._loadTokens();
				});
			},
			secondary_action_label: __("Close as withdrawn"),
			secondary_action: () => { d.hide(); this._showDropDialog(t); },
		});
		d.show();
	}

	_loadUnassigned() {
		this._panel.find(".ch-queue-token-list").html(
			`<div class="ch-queue-empty-state ch-queue-loading-state">
				<i class="fa fa-spinner fa-spin fa-2x"></i>
				<span>${__("Loading unrouted requests…")}</span></div>`);

		frappe.xcall("gofix.gofix_services.inbox.unassigned_requests", {
			company: PosState.active_company || "",
		}).then((rows) => {
			this._tokens = (rows || []).map((r) => Object.assign({}, r, {
				channel_group: "remote", unassigned: 1,
			}));
			this._renderTokenList(this._tokens);
		}).catch(() => {
			this._panel.find(".ch-queue-token-list").html(
				`<div class="ch-queue-empty-state">
					<span>${__("Could not load unrouted requests")}</span></div>`);
		});
	}

	// Claiming an unrouted request for this store. One click, because the
	// alternative is a request sitting in the pool while everyone assumes
	// somebody else has it.
	_claimHere(t) {
		frappe.confirm(
			__("Take {0} at this store?", [
				frappe.utils.escape_html(t.customer_name || t.customer_phone || t.name)]),
			() => {
				frappe.xcall("gofix.gofix_services.inbox.assign_store", {
					inbox: t.name, pos_profile: PosState.pos_profile,
				}).then(() => {
					frappe.show_alert({ message: __("Routed to this store"), indicator: "green" });
					this._loadTokens();
				});
			});
	}

}
