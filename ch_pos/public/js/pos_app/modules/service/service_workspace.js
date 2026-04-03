/**
 * CH POS — Service Tracker Workspace
 *
 * Search and track GoFix Service Requests by SR#, phone, IMEI, or customer.
 * Premium card-based result display with status badges.
 */
import { PosState, EventBus } from "../../state.js";
import { format_number } from "../../shared/helpers.js";

function _is_service_company(company) {
	const lc = (company || "").toLowerCase();
	return lc.includes("gofix") || lc.includes("service");
}

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
		const active_company = PosState.active_company || PosState.company || "";
		if (!_is_service_company(active_company)) {
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

				<div style="display:flex;gap:10px;align-items:stretch;margin-bottom:16px;">
					<div class="ch-pos-search-wrap" style="flex:1;max-width:none">
						<i class="fa fa-search ch-pos-search-icon"></i>
						<input type="text" class="form-control ch-pos-search ch-svc-search"
							placeholder="${__("Search by SR#, phone, IMEI, or customer name...")}">
					</div>
					<button class="btn btn-primary ch-svc-lookup" style="border-radius:var(--pos-radius);font-weight:700;padding:0 20px">
						<i class="fa fa-search"></i>
					</button>
					<button class="btn btn-outline-secondary ch-svc-open-gofix" style="border-radius:var(--pos-radius);font-weight:700;white-space:nowrap">
						<i class="fa fa-external-link"></i> ${__("Open GoFix")}
					</button>
				</div>

				<div class="ch-svc-results">
					<div class="ch-pos-empty-state" style="padding:40px 16px;">
						<div class="empty-icon"><i class="fa fa-cog"></i></div>
						<div class="empty-title">${__("Search service requests")}</div>
						<div class="empty-subtitle">${__("Enter a SR number, phone, IMEI, or customer name")}</div>
					</div>
				</div>
			</div>
		`);
		this._bind(panel);
	}

	_bind(panel) {
		const do_search = () => {
			const q = panel.find(".ch-svc-search").val().trim();
			const company = PosState.active_company || PosState.company || "";
			if (!q) return;
			panel.find(".ch-svc-results").html(
				`<div style="padding:24px;text-align:center"><i class="fa fa-spinner fa-spin"></i></div>`
			);
			frappe.call({
				method: "frappe.client.get_list",
				args: {
					doctype: "Service Request",
					filters: company ? { company } : {},
					or_filters: [
						["name", "like", `%${q}%`],
						["contact_number", "like", `%${q}%`],
						["actual_imei", "like", `%${q}%`],
						["customer_name", "like", `%${q}%`],
					],
					fields: ["name", "customer_name", "status", "device_item_name",
						"issue_category", "decision", "service_date", "estimated_cost"],
					order_by: "creation desc",
					limit_page_length: 20,
				},
				callback: (r) => {
					const el = panel.find(".ch-svc-results");
					const items = r.message || [];
					if (!items.length) {
						el.html(`
							<div class="ch-pos-empty-state" style="padding:30px 16px;">
								<div class="empty-icon"><i class="fa fa-search"></i></div>
								<div class="empty-title">${__("No results for")} "${frappe.utils.escape_html(q)}"</div>
							</div>`);
						return;
					}
					const cards = items.map((sr) => {
						const badge = DECISION_MAP[sr.decision] || "muted";
						return `
						<div class="ch-svc-card" data-name="${sr.name}">
							<div class="ch-svc-card-top">
								<div style="display:flex;align-items:center;gap:6px">
									<span class="ch-svc-card-id">${sr.name}</span>
									<span class="ch-pos-badge badge-${badge}">${sr.decision || sr.status}</span>
								</div>
								<span style="font-size:var(--pos-fs-xs);color:var(--pos-text-muted)">${sr.service_date || ""}</span>
							</div>
							<div class="ch-svc-card-body">
								<span style="font-weight:600;color:var(--pos-text)">${frappe.utils.escape_html(sr.customer_name || "")}</span>
								<span>${frappe.utils.escape_html(sr.device_item_name || "")}${sr.issue_category ? " · " + frappe.utils.escape_html(sr.issue_category) : ""}</span>
								${sr.estimated_cost ? `<span style="font-weight:700;color:var(--pos-text-secondary)">Est: ₹${format_number(sr.estimated_cost)}</span>` : ""}
							</div>
							<div class="ch-svc-card-actions">
								<button class="btn btn-sm btn-outline-primary ch-svc-open-sr" data-name="${sr.name}"
									style="border-radius:var(--pos-radius-sm);font-weight:700">
									<i class="fa fa-external-link"></i> ${__("Open in GoFix")}
								</button>
							</div>
						</div>`;
					}).join("");
					el.html(`<div class="ch-svc-results-grid">${cards}</div>`);
				},
			});
		};

		panel.on("click", ".ch-svc-lookup", do_search);
		panel.find(".ch-svc-search").on("keypress", (e) => { if (e.which === 13) do_search(); });

		panel.on("click", ".ch-svc-open-sr", function () {
			frappe.set_route("Form", "Service Request", $(this).data("name"));
		});
		panel.on("click", ".ch-svc-open-gofix", () => {
			frappe.set_route("List", "Service Request");
		});
	}
}
