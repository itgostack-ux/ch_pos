/**
 * CH POS — Service Tracker Workspace
 *
 * Search and track GoFix Service Requests by SR#, phone, IMEI, or customer.
 * Premium card-based result display with status badges.
 */
import { PosState, EventBus } from "../../state.js";
import { format_number } from "../../shared/helpers.js";
import { print_invoice_pdf } from "../../shared/print_helper.js";

const DECISION_MAP = {
	Draft: "warning", Accepted: "info", "In Service": "warning",
	Completed: "success", Invoiced: "info", Delivered: "success",
	Withdrawn: "muted", Rejected: "danger", Cancelled: "muted",
};

export class ServiceWorkspace {
	constructor() {
		EventBus.on("workspace:render", (ctx) => {
			if (ctx.mode !== "service") return;
			this.render(ctx.panel);
		});
	}

	render(panel) {
		// A company whose stores do both retail and service resolves to "hybrid",
		// not "service" — which is every GoFix store. Demanding an exact match
		// hid the tracker from the only company it exists for.
		const services_repairs = ["service", "hybrid"].includes(PosState.active_company_type);
		if (!services_repairs) {
			panel.html(`
				<div class="ch-pos-mode-panel">
					<div class="ch-pos-empty-state" style="padding:48px 20px;">
						<div class="empty-icon"><i class="fa fa-building-o"></i></div>
						<div class="empty-title">${__("GoFix only")}</div>
						<div class="empty-subtitle">${__("Repair ticket tracking is available only inside the GoFix company context.")}</div>
					</div>
				</div>
			`);
			return;
		}

		panel.html(`
			<div class="ch-pos-mode-panel">
				<div class="ch-mode-header">
					<h4>
						<span class="mode-icon" style="background:#fef3c7;color:#92400e">
							<i class="fa fa-cog"></i>
						</span>
						${__("Service Tracker")}
					</h4>
					<span class="ch-mode-hint">${__("Track GoFix Service Requests for walk-in and pending repairs")}</span>
				</div>

				<div style="display:flex;gap:10px;align-items:stretch;margin-bottom:10px;">
					<div class="ch-pos-search-wrap" style="flex:1;max-width:none">
						<i class="fa fa-search ch-pos-search-icon"></i>
						<input type="text" class="form-control ch-pos-search ch-svc-search"
							placeholder="${__("Filter by SR#, phone, IMEI, or customer name...")}">
					</div>
					<button class="btn btn-primary ch-svc-lookup" style="border-radius:var(--pos-radius);font-weight:700;padding:0 20px">
						<i class="fa fa-search"></i>
					</button>
					<button class="btn btn-outline-secondary ch-svc-open-gofix" style="border-radius:var(--pos-radius);font-weight:700;white-space:nowrap">
						<i class="fa fa-external-link"></i> ${__("Open GoFix")}
					</button>
				</div>

				<div class="ch-svc-tabs" style="display:flex;gap:6px;margin-bottom:14px;flex-wrap:wrap">
					${[
						["in_progress", __("In Progress")],
						// A device that has left the store is nobody here's to act on,
						// and the customer asking after it needs a different answer.
						["out_for_repair", __("Out for Repair")],
						["ready", __("Ready to Bill")],
						["invoiced", __("Invoiced")],
						["delivered", __("Delivered")],
						["all", __("All")],
					].map(([key, label]) => `
						<button class="btn btn-sm ch-svc-tab" data-tab="${key}"
							style="border-radius:14px;font-weight:700;border:1px solid var(--pos-border,#e2e8f0)">
							${label} <span class="ch-svc-tab-count" data-tab-count="${key}"></span>
						</button>
					`).join("")}
				</div>

				<div class="ch-svc-results">
					<div style="padding:24px;text-align:center"><i class="fa fa-spinner fa-spin"></i></div>
				</div>
			</div>
		`);
		this._bind(panel);
	}

	_bind(panel) {
		this._active_tab = this._active_tab || "ready";

		const render_cards = (items) => {
			const el = panel.find(".ch-svc-results");
			if (!items.length) {
				el.html(`
					<div class="ch-pos-empty-state" style="padding:30px 16px;">
						<div class="empty-icon"><i class="fa fa-cog"></i></div>
						<div class="empty-title">${__("Nothing here")}</div>
						<div class="empty-subtitle">${__("No service requests in this bucket for your store")}</div>
					</div>`);
				return;
			}
			const cards = items.map((sr) => {
				const badge = DECISION_MAP[sr.status] || DECISION_MAP[sr.decision] || "muted";
				const inv = sr.service_invoice || "";
				// Device custody chip — the SR belongs to this store even when
				// the device is away at a repair hub; billing away devices
				// still needs the customer-consent OTP.
				const loc_chip = sr.at_home_store
					? `<span class="ch-pos-badge badge-success" title="${__("Device is at this store")}"><i class="fa fa-home"></i> ${__("At Store")}</span>`
					: (sr.device_at
						? `<span class="ch-pos-badge badge-warning" title="${__("Device is away — billing needs customer OTP")}"><i class="fa fa-map-marker"></i> ${frappe.utils.escape_html(sr.device_at)}</span>`
						: `<span class="ch-pos-badge badge-muted"><i class="fa fa-truck"></i> ${__("In Transit")}</span>`);
				const sr_payload = encodeURIComponent(JSON.stringify({
					name: sr.name,
					customer: sr.customer || "",
					customer_name: sr.customer_name || "",
					contact_number: sr.contact_number || "",
					serial_no: sr.serial_no || sr.actual_imei || "",
					source_warehouse: sr.source_warehouse || "",
					estimated_cost: sr.estimated_cost || 0,
				}));
				return `
				<div class="ch-svc-card" data-name="${sr.name}">
					<div class="ch-svc-card-top">
						<div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap">
							<span class="ch-svc-card-id">${sr.name}</span>
							<span class="ch-pos-badge badge-${badge}">${sr.status || sr.decision}</span>
							${loc_chip}
						</div>
						<span style="font-size:var(--pos-fs-xs);color:var(--pos-text-muted)">${sr.service_date || ""}</span>
					</div>
					<div class="ch-svc-card-body">
						<div class="ch-svc-customer">${frappe.utils.escape_html(sr.customer_name || "—")}</div>
						<div class="ch-svc-facts">
							${[
								[__("Device"), sr.device_item_name],
								[__("IMEI / Serial"), sr.serial_no || sr.actual_imei],
								[__("Reported"), sr.issue_category],
								[__("Contact"), sr.contact_number],
								// Where the handset physically is, which is the
								// question a counter gets asked most.
								[__("Location"), sr.transferred_to_store
									? String(sr.transferred_to_store).split(" - ")[0]
									: (sr.current_location ? String(sr.current_location).split(" - ")[0] : null)],
							].filter(([, v]) => v).map(([k, v]) => `
								<div class="ch-svc-fact">
									<span class="ch-svc-fact-k">${k}</span>
									<span class="ch-svc-fact-v">${frappe.utils.escape_html(String(v))}</span>
								</div>`).join("")}
						</div>
						${sr.final_cost
							? `<div class="ch-svc-amount"><span>${__("Final")}</span><b>₹${format_number(sr.final_cost)}</b></div>`
							: (sr.estimated_cost
								? `<div class="ch-svc-amount ch-svc-amount-est"><span>${__("Estimate")}</span><b>₹${format_number(sr.estimated_cost)}</b></div>`
								: "")}
					</div>
					<div class="ch-svc-card-actions">
						<button class="btn btn-sm btn-outline-primary ch-svc-open-hub" data-sr="${sr.name}"
							style="border-radius:var(--pos-radius-sm);font-weight:700">
							<i class="fa fa-external-link"></i> ${__("Open in Ops Hub")}
						</button>
						${(sr.transfer_actions || []).includes("dispatch") && ["Draft", "Accepted", "In Service"].includes(sr.decision) ? `
							<button class="btn btn-sm btn-outline-warning ch-svc-send-hub" data-sr="${sr.name}"
								style="border-radius:var(--pos-radius-sm);font-weight:700"
								title="${__("This store cannot do the repair — send the device to a hub")}">
								<i class="fa fa-truck"></i> ${__("Send to Hub")}
							</button>` : ""}
						${(sr.transfer_actions || []).includes("receive") ? `
							<button class="btn btn-sm btn-outline-success ch-svc-receive-transfer" data-sr="${sr.name}"
								style="border-radius:var(--pos-radius-sm);font-weight:700"
								title="${__("Take the device into stock here — the consignment has arrived")}">
								<i class="fa fa-sign-in"></i> ${__("Receive Device")}
							</button>` : ""}
						${(sr.transfer_actions || []).includes("return") ? `
							<button class="btn btn-sm btn-outline-primary ch-svc-return-transfer" data-sr="${sr.name}"
								style="border-radius:var(--pos-radius-sm);font-weight:700"
								title="${__("Repair done — send the device back to the store that owns the ticket")}">
								<i class="fa fa-reply"></i> ${__("Return to Store")}
							</button>` : ""}
						${(sr.transfer_actions || []).includes("confirm_return") ? `
							<button class="btn btn-sm btn-success ch-svc-confirm-return" data-sr="${sr.name}"
								style="border-radius:var(--pos-radius-sm);font-weight:700"
								title="${__("The device is back — confirm it and the ticket can be billed here")}">
								<i class="fa fa-check"></i> ${__("Confirm Return")}
							</button>` : ""}
						${sr.transfer_status ? `
							<span class="badge" style="background:#fff7ed;color:#9a3412;padding:4px 9px;border-radius:10px;font-size:11px;font-weight:700"
								title="${frappe.utils.escape_html(sr.movement_status ? __("Consignment is at: {0}", [sr.movement_status]) : __("No transfer document"))}">
								<i class="fa fa-truck"></i> ${frappe.utils.escape_html(sr.awaiting_pickup ? __("Awaiting Pickup") : sr.transfer_status)}${sr.transferred_to_store ? " → " + frappe.utils.escape_html(String(sr.transferred_to_store).split(" - ")[0]) : ""}
							</span>` : ""}
						${["Completed","Invoiced","Delivered"].includes(sr.decision) ? `
							<button class="btn btn-sm btn-outline-danger ch-svc-reopen" data-sr="${sr.name}"
								style="border-radius:var(--pos-radius-sm);font-weight:700"
								title="${__("Customer is back — this repair has not held")}">
								<i class="fa fa-undo"></i> ${__("Customer Returned")}
							</button>` : ""}
						${(sr.transfer_actions || []).includes("cancel") ? `
							<button class="btn btn-sm btn-outline-secondary ch-svc-cancel-transfer" data-sr="${sr.name}"
								style="border-radius:var(--pos-radius-sm);font-weight:700"
								title="${__("Call the dispatch back — only while the device has not been picked up")}">
								<i class="fa fa-times"></i> ${__("Cancel Dispatch")}
							</button>` : ""}
						${inv ? `
							<button class="btn btn-sm btn-outline-secondary ch-svc-view-invoice" data-name="${frappe.utils.escape_html(inv)}"
								style="border-radius:var(--pos-radius-sm);font-weight:700">
								<i class="fa fa-file-text-o"></i> ${__("View Invoice")}
							</button>
							<button class="btn btn-sm btn-primary ch-svc-print-invoice" data-name="${frappe.utils.escape_html(inv)}"
								style="border-radius:var(--pos-radius-sm);font-weight:700">
								<i class="fa fa-print"></i> ${__("Print Invoice")}
							</button>
						` : `
							${sr.status === "Completed" ? `
								<button class="btn btn-sm btn-success ch-svc-add-to-bill" data-name="${sr.name}"
									style="border-radius:var(--pos-radius-sm);font-weight:700">
									<i class="fa fa-cart-plus"></i> ${__("Add to Bill")}
								</button>
							` : ""}
							<button class="btn btn-sm btn-outline-warning ch-svc-raise-exception" data-sr="${sr_payload}"
								style="border-radius:var(--pos-radius-sm);font-weight:700">
								<i class="fa fa-exclamation-triangle"></i> ${__("Raise Exception")}
							</button>
						`}
					</div>
				</div>`;
			}).join("");
			panel.find(".ch-svc-results").html(`<div class="ch-svc-results-grid">${cards}</div>`);
		};

		const load_board = () => {
			const warehouse = PosState.warehouse || "";
			const search = panel.find(".ch-svc-search").val().trim();
			panel.find(".ch-svc-results").html(
				`<div style="padding:24px;text-align:center"><i class="fa fa-spinner fa-spin"></i></div>`
			);
			// Highlight active tab
			panel.find(".ch-svc-tab").each(function () {
				const active = $(this).data("tab") === this_tab();
				$(this).toggleClass("btn-primary", active).toggleClass("btn-default", !active);
			});
			frappe.xcall("gofix.gofix_services.api.get_store_service_board", {
				warehouse,
				tab: this_tab() === "all" ? null : this_tab(),
				search: search || null,
			}).then((r) => {
				render_cards(r.rows || []);
				const counts = r.counts || {};
				panel.find(".ch-svc-tab-count").each(function () {
					const key = $(this).data("tab-count");
					const n = counts[key];
					$(this).text(n != null ? `(${n})` : "");
				});
			}).catch(() => {
				panel.find(".ch-svc-results").html(`
					<div class="ch-pos-empty-state" style="padding:30px 16px;">
						<div class="empty-title">${__("Could not load service requests")}</div>
					</div>`);
			});
		};
		const this_tab = () => this._active_tab || "ready";
		this._reload_board = load_board;

		panel.on("click", ".ch-svc-tab", (e) => {
			this._active_tab = $(e.currentTarget).data("tab");
			load_board();
		});
		panel.on("click", ".ch-svc-lookup", load_board);
		panel.find(".ch-svc-search").on("keypress", (e) => { if (e.which === 13) load_board(); });

		load_board();

		panel.on("click", ".ch-svc-open-sr", function () {
			frappe.set_route("Form", "Service Request", $(this).data("name"));
		});
		panel.on("click", ".ch-svc-view-invoice", function () {
			frappe.set_route("Form", "Sales Invoice", $(this).data("name"));
		});
		panel.on("click", ".ch-svc-print-invoice", function () {
			const invoice = $(this).data("name");
			if (!invoice) return;
			print_invoice_pdf(invoice, null, { doctype: "Sales Invoice" });
		});
		panel.on("click", ".ch-svc-raise-exception", function () {
			let sr = {};
			try {
				sr = JSON.parse(decodeURIComponent($(this).data("sr") || "%7B%7D"));
			} catch (e) {
				sr = {};
			}
			PosState.active_mode = "exceptions";
			EventBus.emit("mode:set", "exceptions");
			EventBus.emit("mode:switch", "exceptions");
			EventBus.emit("exception:open", {
				source: "service_request",
				reference_doctype: "Service Request",
				reference_name: sr.name || "",
				customer: sr.customer || "",
				customer_name: sr.customer_name || "",
				customer_phone: sr.contact_number || "",
				serial_no: sr.serial_no || "",
				original_value: sr.estimated_cost || 0,
				store_warehouse: sr.source_warehouse || PosState.warehouse || "",
				reason: sr.name
					? __("Customer requested billing exception for GoFix service request {0}.", [sr.name])
					: __("Customer requested billing exception for GoFix service request."),
			});
		});
		panel.on("click", ".ch-svc-open-gofix", () => {
			// The Ops Hub is where a repair is actually worked; the raw list is
			// not somewhere a counter operator can do anything.
			frappe.set_route("gofix-ops-hub");
		});

		// Open ONE ticket straight in the Ops Hub, pre-selected.
		panel.on("click", ".ch-svc-open-hub", (e) => {
			const sr = $(e.currentTarget).data("sr");
			if (!sr) return;
			window.open(`/app/gofix-ops-hub?sr=${encodeURIComponent(sr)}`, "_blank");
		});

		// The customer is back and says it is not fixed. Ask WHICH repair, not
		// just which ticket — a job can carry several, and only one usually
		// failed.
		panel.on("click", ".ch-svc-reopen", (e) => {
			const sr = $(e.currentTarget).data("sr");
			if (!sr) return;
			frappe.xcall("gofix.gofix_services.api.get_reopenable_repairs", { service_request: sr })
				.then((repairs) => {
					const opts = [];
					(repairs || []).forEach((r) => {
						(r.solutions || []).forEach((s) => {
							opts.push({
								label: `${s.repair_solution} · ${r.service_date}` +
								       (r.under_warranty ? ` — ${__("under warranty")}` : ""),
								line: s.solution_line,
							});
						});
					});
					if (!opts.length) {
						frappe.msgprint({
							title: __("Nothing to reopen"),
							message: __("No completed repair on this device to come back on."),
							indicator: "orange",
						});
						return;
					}
					const d = new frappe.ui.Dialog({
						title: __("Customer returned"),
						fields: [
							{ fieldname: "line", fieldtype: "Select", reqd: 1,
							  label: __("Which repair has not held?"),
							  options: opts.map((o) => o.label) },
							{ fieldname: "note", fieldtype: "Small Text",
							  label: __("What is the customer reporting?") },
						],
						primary_action_label: __("Reopen"),
						primary_action: (v) => {
							const picked = opts[opts.map((o) => o.label).indexOf(v.line)];
							if (!picked) return;
							d.get_primary_btn().prop("disabled", true);
							frappe.xcall("gofix.gofix_services.api.reopen_repair", {
								solution_line: picked.line, description: v.note,
							}).then((r) => {
								d.hide();
								frappe.show_alert({
									message: __("Reopened as {0}.", [r.service_request]),
									indicator: "green",
								});
								load_board();
							}).catch((err) => {
								d.get_primary_btn().prop("disabled", false);
								frappe.msgprint({ title: __("Could not reopen"),
									message: err.message || String(err), indicator: "red" });
							});
						},
					});
					d.show();
				});
		});

		// The three legs a device travels after it leaves the counter. Each is a
		// confirmation rather than a form: the server already knows the origin,
		// the destination and whether the consignment has landed, so asking the
		// operator to restate any of it only invites a wrong answer.
		const movement_action = (selector, method, title, body, label, done) => {
			panel.on("click", selector, (e) => {
				const sr = $(e.currentTarget).data("sr");
				if (!sr) return;
				frappe.confirm(`<b>${frappe.utils.escape_html(sr)}</b><br>${body}`, () => {
					frappe.xcall(method, { service_request: sr })
						.then(() => {
							frappe.show_alert({ message: done, indicator: "green" });
							load_board();
						})
						.catch((err) => {
							frappe.msgprint({ title: title,
								message: err.message || String(err), indicator: "red" });
						});
				}, null, { primary_action_label: label });
			});
		};

		movement_action(
			".ch-svc-receive-transfer",
			"gofix.gofix_services.api.receive_service_transfer",
			__("Could not receive the device"),
			__("Confirm the device has physically arrived and is being taken into stock here."),
			__("Receive"),
			__("Device received."),
		);
		movement_action(
			".ch-svc-return-transfer",
			"gofix.gofix_services.api.return_service_transfer",
			__("Could not start the return"),
			__("Send the device back to the store that owns the ticket. Any spares still on order will be redirected there too."),
			__("Return"),
			__("Return transfer raised."),
		);
		movement_action(
			".ch-svc-confirm-return",
			"gofix.gofix_services.api.complete_service_transfer_return",
			__("Could not confirm the return"),
			__("Confirm the device is back at this store. The ticket can then be billed here."),
			__("Confirm"),
			__("Device is back at the store."),
		);

		// Call back a dispatch that has not been picked up.
		panel.on("click", ".ch-svc-cancel-transfer", (e) => {
			const sr = $(e.currentTarget).data("sr");
			if (!sr) return;
			const d = new frappe.ui.Dialog({
				title: __("Cancel dispatch of {0}", [sr]),
				fields: [{ fieldname: "reason", fieldtype: "Small Text", reqd: 1,
					label: __("Why is it being called back?"),
					description: __("Only possible while the device has not been picked up.") }],
				primary_action_label: __("Cancel Dispatch"),
				primary_action: (v) => {
					d.get_primary_btn().prop("disabled", true);
					frappe.xcall("gofix.gofix_services.api.cancel_service_transfer", {
						service_request: sr, reason: v.reason,
					}).then(() => {
						d.hide();
						frappe.show_alert({ message: __("Dispatch cancelled."), indicator: "green" });
						load_board();
					}).catch((err) => {
						d.get_primary_btn().prop("disabled", false);
						frappe.msgprint({ title: __("Could not cancel"),
							message: err.message || String(err), indicator: "red" });
					});
				},
			});
			d.show();
		});

		// ── Send the device to a repair hub ──────────────────────────────
		panel.on("click", ".ch-svc-send-hub", (e) => {
			const sr = $(e.currentTarget).data("sr");
			if (!sr) return;
			// Destinations come from the location hierarchy, not the warehouse
			// tree — a raw Warehouse link offered Damaged bins and Supplier
			// Returns as places to send a customer's phone.
			frappe.xcall("gofix.gofix_services.api.get_repair_destinations", {
				service_request: sr,
			}).then((dests) => {
			if (!(dests || []).length) {
				frappe.msgprint({
					title: __("Nowhere to send it"),
					message: __("No other service-enabled store is available in this company."),
					indicator: "orange",
				});
				return;
			}
			const options = dests.map((x) => ({
				value: x.warehouse,
				label: `${x.label}${x.is_hub ? " — " + __("Hub") : ""}${x.city ? " · " + x.city : ""}`,
			}));
			const d = new frappe.ui.Dialog({
				title: __("Send {0} to a repair hub", [sr]),
				fields: [
					{
						fieldname: "to_store", fieldtype: "Select", reqd: 1,
						label: __("Repair hub"),
						options: options.map((o) => o.label),
						description: __("The device is dispatched into transit; the hub confirms receipt on arrival."),
					},
					{
						fieldname: "reason", fieldtype: "Small Text", reqd: 1,
						label: __("Why can this store not do it?"),
					},
				],
				primary_action_label: __("Dispatch"),
				primary_action: (v) => {
					const picked = options.find((o) => o.label === v.to_store);
					if (!picked) return;
					d.get_primary_btn().prop("disabled", true);
					frappe.xcall("gofix.gofix_services.api.create_service_transfer", {
						service_request: sr, to_store: picked.value, reason: v.reason,
					}).then(() => {
						d.hide();
						frappe.show_alert({
							message: __("{0} dispatched to {1}. Track it in logistics.", [sr, v.to_store]),
							indicator: "green",
						});

						load_board();
					}).catch((err) => {
						d.get_primary_btn().prop("disabled", false);
						frappe.msgprint({
							title: __("Could not dispatch"),
							message: err.message || String(err),
							indicator: "red",
						});
					});
				},
			});
			d.show();
			});
		});

		// ── Add a completed repair to the POS cart ───────────────────────
		// Bills as ONE fixed, non-stock line (labour + spares + part
		// warranties in the description). The server re-enforces every gate
		// (custody, below-cost floor, customer match) at invoice submit.
		// Push the line into the cart (no gating here — the server re-enforces
		// the below-cost floor at invoice submit).
		const push_service_line = (line) => {
				if (PosState.customer && line.customer && PosState.customer !== line.customer) {
					frappe.msgprint({
						title: __("Different Customer"),
						message: __(
							"This repair belongs to {0}. Finish or clear the current bill first.",
							[frappe.utils.escape_html(line.customer_name || line.customer)]
						),
						indicator: "red",
					});
					return;
				}
				if (!PosState.customer && line.customer) {
					PosState.customer = line.customer;
					EventBus.emit("customer:set", line.customer);
				}
				PosState.cart.push({
					item_code: line.item_code,
					item_name: line.item_name,
					qty: 1,
					rate: flt(line.rate),
					price_list_rate: flt(line.rate),
					mrp: 0,
					uom: "Nos",
					discount_percentage: 0,
					discount_amount: 0,
					offers: [],
					applied_offer: null,
					warranty_plan: null,
					is_warranty: false,
					is_vas: false,
					is_service: true,
					service_request: line.service_request,
					description: line.description,
					has_serial_no: 0,
					serial_no: "",
					stock_qty: 0,
					must_be_whole_number: 1,
				});
				EventBus.emit("cart:updated");
				PosState.active_mode = "sell";
				EventBus.emit("mode:set", "sell");
				EventBus.emit("mode:switch", "sell");
				frappe.show_alert({
					message: __("Repair {0} added to bill — ₹{1}", [line.service_request, format_number(line.rate)]),
					indicator: "green",
				});
		};

		const add_service_line_to_cart = (line) => {
			if (line.below_cost && !["Approved", "Auto-Approved"].includes(line.below_cost_exception_status)) {
				open_below_cost_dialog(line);
				return;
			}
			push_service_line(line);
		};

		// Below-cost resolution at the counter: set a Final Cost right here.
		// Below Cost-to-Company it auto-raises the "Service Below Cost
		// Billing" exception (approval + SoD is the control); at/above cost
		// it simply reprices. "Add Anyway" parks the line — payment stays
		// blocked server-side until the exception is approved.
		const open_below_cost_dialog = (line) => {
			const d = new frappe.ui.Dialog({
				title: __("Below Cost to Company"),
				fields: [
					{
						fieldname: "info",
						fieldtype: "HTML",
						options: `<div style="background:#fffbeb;border:1px solid #fde68a;border-radius:6px;padding:10px 12px;margin-bottom:8px;font-size:12px;color:#78350f">
							${__("Billing ₹{0} is below Cost to Company ₹{1}.", [format_number(line.rate), format_number(line.company_cost_total)])}
							${line.below_cost_exception_status ? "<br>" + __("Exception status: {0}", [frappe.utils.escape_html(line.below_cost_exception_status)]) : ""}
							<br>${__("Set the final agreed price — below cost it goes for approval; at or above cost it bills directly.")}
						</div>`,
					},
					{
						fieldname: "final_cost",
						fieldtype: "Currency",
						label: __("Final Cost to Customer (incl. tax)"),
						default: line.rate,
						reqd: 1,
					},
					{
						fieldname: "reason",
						fieldtype: "Small Text",
						label: __("Reason (needed when below cost)"),
					},
				],
				primary_action_label: __("Set Final Cost & Add"),
				primary_action: (v) => {
					const fc = flt(v.final_cost);
					if (fc <= 0) {
						frappe.show_alert({ message: __("Enter the final price"), indicator: "orange" });
						return;
					}
					if (fc < flt(line.company_cost_total) && !(v.reason || "").trim()) {
						frappe.show_alert({ message: __("A reason is required for a below-cost price"), indicator: "orange" });
						return;
					}
					frappe.xcall(
						"gofix.gofix_services.page.gofix_ops_hub.gofix_ops_hub.set_final_cost",
						{ sr_name: line.service_request, final_cost: fc, reason: v.reason || null }
					).then((r) => {
						d.hide();
						if (r.below_cost && !["Approved", "Auto-Approved"].includes(r.exception_status)) {
							frappe.show_alert({
								message: __("Below-cost exception {0} raised — payment stays blocked until it is approved.", [r.exception]),
								indicator: "orange",
							});
						}
						frappe.xcall(
							"gofix.gofix_services.page.gofix_ops_hub.gofix_ops_hub.get_service_billing_line",
							{ sr_name: line.service_request }
						).then(push_service_line);
					});
				},
				secondary_action_label: __("Add Anyway"),
				secondary_action: () => {
					d.hide();
					frappe.show_alert({
						message: __("Added below cost — payment will stay blocked until the below-cost exception is approved."),
						indicator: "orange",
					});
					push_service_line(line);
				},
			});
			d.show();
		};

		// Customer-consent OTP for off-store billing. Verifying caches a
		// 30-minute consent server-side, so the later invoice submit passes
		// the same custody gate without re-entering the OTP.
		const open_remote_billing_otp_dialog = (sr_name, custody_message) => {
			const d = new frappe.ui.Dialog({
				title: __("Customer Consent Required"),
				fields: [
					{
						fieldname: "info",
						fieldtype: "HTML",
						options: `<div style="background:#fffbeb;border:1px solid #fde68a;border-radius:6px;padding:10px 12px;margin-bottom:8px;font-size:12px;color:#78350f">
							${frappe.utils.escape_html(custody_message || __("The device is not at its home store."))}
						</div>`,
					},
					{
						fieldname: "otp",
						fieldtype: "Data",
						label: __("Billing OTP from customer"),
						description: __("Click 'Send OTP' first — the customer receives it via WhatsApp / SMS / email."),
					},
				],
				primary_action_label: __("Verify & Add to Bill"),
				primary_action: (v) => {
					if (!(v.otp || "").trim()) {
						frappe.show_alert({ message: __("Enter the OTP the customer received"), indicator: "orange" });
						return;
					}
					frappe.xcall("gofix.gofix_services.api.verify_remote_billing_otp", {
						service_request: sr_name, otp: v.otp.trim(),
					}).then(() => {
						d.hide();
						frappe.show_alert({ message: __("Consent verified — billing unlocked for 30 minutes"), indicator: "green" });
						frappe.xcall(
							"gofix.gofix_services.page.gofix_ops_hub.gofix_ops_hub.get_service_billing_line",
							{ sr_name }
						).then(add_service_line_to_cart);
					});
				},
				secondary_action_label: __("Send OTP"),
				secondary_action: () => {
					frappe.xcall("gofix.gofix_services.api.request_remote_billing_otp", {
						service_request: sr_name,
					}).then(() => {
						frappe.show_alert({ message: __("OTP sent to the customer"), indicator: "blue" });
					});
				},
			});
			d.show();
		};

		panel.on("click", ".ch-svc-add-to-bill", function () {
			const sr_name = $(this).data("name");
			if (PosState.cart.some((c) => c.service_request === sr_name)) {
				frappe.show_alert({ message: __("This repair is already in the cart"), indicator: "orange" });
				return;
			}
			frappe.xcall(
				"gofix.gofix_services.page.gofix_ops_hub.gofix_ops_hub.get_service_billing_line",
				{ sr_name }
			).then((line) => {
				if (!line.at_home_store) {
					open_remote_billing_otp_dialog(sr_name, line.custody_message);
					return;
				}
				add_service_line_to_cart(line);
			});
		});
	}
}
