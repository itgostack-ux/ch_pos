/**
 * CH Bin Transfer Dialog
 *
 * Exposes a globally-accessible dialog for POS / store users to move stock
 * between the 5 bins of their store (Sellable / In-Transit / Damaged /
 * Disposed / Reserved) with a controlled "reason".
 *
 * Trigger from anywhere:
 *     frappe.ch_pos.show_bin_transfer_dialog({ store: "STO-BMPL-CHENNA-0001" });
 *
 * The dialog calls the whitelisted endpoint:
 *     ch_item_master.ch_core.bin_transfer.pos_bin_transfer
 * which creates a Stock Entry (Material Transfer) under the hood.
 */

frappe.provide("frappe.ch_pos");

const BIN_TYPES = ["Sellable", "In-Transit", "Damaged", "Disposed", "Reserved"];

frappe.ch_pos.show_bin_transfer_dialog = function (opts = {}) {
	const store = opts.store || null;

	const dlg = new frappe.ui.Dialog({
		title: __("Move Stock Between Bins"),
		size: "large",
		fields: [
			{
				fieldname: "store",
				fieldtype: "Link",
				label: __("Store"),
				options: "CH Store",
				reqd: 1,
				default: store,
				onchange: () => refresh_summary(dlg),
			},
			{
				fieldname: "reason",
				fieldtype: "Link",
				label: __("Reason"),
				options: "CH Bin Transfer Reason",
				reqd: 1,
				get_query: () => ({ filters: { disabled: 0 } }),
				onchange: async () => {
					const reason = dlg.get_value("reason");
					if (!reason) return;
					const r = await frappe.db.get_doc("CH Bin Transfer Reason", reason);
					if (r.source_bin_type) {
						dlg.set_value("from_bin_type", r.source_bin_type);
					}
					if (r.target_bin_type) {
						dlg.set_value("to_bin_type", r.target_bin_type);
					}
					if (r.description) {
						dlg.set_df_property("reason", "description", r.description);
					}
				},
			},
			{ fieldtype: "Column Break" },
			{
				fieldname: "from_bin_type",
				fieldtype: "Select",
				label: __("From Bin"),
				options: ["", ...BIN_TYPES].join("\n"),
				reqd: 1,
				// Changing the source bin invalidates any previously picked item
				// (the item list is scoped to the selected bin's stock).
				onchange: () => {
					dlg.set_value("item_code", "");
				},
			},
			{
				fieldname: "to_bin_type",
				fieldtype: "Select",
				label: __("To Bin"),
				options: ["", ...BIN_TYPES].join("\n"),
				reqd: 1,
			},
			{ fieldtype: "Section Break" },
			{
				fieldname: "item_code",
				fieldtype: "Link",
				label: __("Item"),
				options: "Item",
				reqd: 1,
				// Only list items that actually have stock in the selected store +
				// source bin (parity with the POS Sell menu). Until both Store and
				// From Bin are chosen, the picker returns nothing.
				get_query: () => {
					const store = dlg.get_value("store");
					const from_bin_type = dlg.get_value("from_bin_type");
					if (!store || !from_bin_type) {
						return { filters: { name: ["in", []] } };
					}
					return {
						query: "ch_item_master.ch_core.bin_transfer.get_bin_items",
						filters: { store, from_bin_type },
					};
				},
				onchange: () => refresh_summary(dlg),
			},
			{
				fieldname: "qty",
				fieldtype: "Float",
				label: __("Quantity"),
				default: 1,
				reqd: 1,
			},
			{ fieldtype: "Column Break" },
			{
				fieldname: "serial_no",
				fieldtype: "Small Text",
				label: __("Serial No"),
				description: __("Required for serialised items / disposal reasons."),
			},
			{
				fieldname: "batch_no",
				fieldtype: "Link",
				label: __("Batch No"),
				options: "Batch",
			},
			{ fieldtype: "Section Break", label: __("Current Bin Levels") },
			{
				fieldname: "summary_html",
				fieldtype: "HTML",
			},
		],
		primary_action_label: __("Move Stock"),
		primary_action: async (values) => {
			try {
				dlg.disable_primary_action();
				const r = await frappe.call({
					method: "ch_item_master.ch_core.bin_transfer.pos_bin_transfer",
					args: {
						store: values.store,
						item_code: values.item_code,
						qty: values.qty,
						from_bin_type: values.from_bin_type,
						to_bin_type: values.to_bin_type,
						reason: values.reason,
						serial_no: values.serial_no || null,
						batch_no: values.batch_no || null,
					},
				});
				if (r.message && r.message.stock_entry) {
					frappe.show_alert({
						message: __("Moved {0} {1} from {2} → {3} (Stock Entry: {4})", [
							values.qty,
							values.item_code,
							values.from_bin_type,
							values.to_bin_type,
							r.message.stock_entry,
						]),
						indicator: "green",
					});
					refresh_summary(dlg);
					// Reset item-level fields for the next move.
					dlg.set_value("item_code", "");
					dlg.set_value("qty", 1);
					dlg.set_value("serial_no", "");
					dlg.set_value("batch_no", "");
				}
			} catch (e) {
				console.error(e);
			} finally {
				dlg.enable_primary_action();
			}
		},
	});

	dlg.show();
	if (store) refresh_summary(dlg);
	return dlg;
};

async function refresh_summary(dlg) {
	const store = dlg.get_value("store");
	const item_code = dlg.get_value("item_code");
	const wrapper = dlg.get_field("summary_html").$wrapper;
	if (!store) {
		wrapper.html(`<div class="text-muted">${__("Pick a store to see bin levels.")}</div>`);
		return;
	}
	wrapper.html(`<div class="text-muted">${__("Loading…")}</div>`);
	try {
		const r = await frappe.call({
			method: "ch_item_master.ch_core.bin_transfer.get_pos_bin_summary",
			args: { store, item_code: item_code || null },
		});
		const bins = (r.message && r.message.bins) || [];
		if (!bins.length) {
			wrapper.html(`<div class="text-muted">${__("No bins found for this store.")}</div>`);
			return;
		}
		const rows = bins
			.map(
				(b) => `
				<tr>
					<td>${frappe.utils.escape_html(b.bin_type)}</td>
					<td>${frappe.utils.escape_html(b.warehouse)}</td>
					<td class="text-right">${b.qty}</td>
					<td class="text-right">${b.items}</td>
				</tr>`,
			)
			.join("");
		wrapper.html(`
			<table class="table table-bordered table-sm">
				<thead>
					<tr>
						<th>${__("Bin")}</th>
						<th>${__("Warehouse")}</th>
						<th class="text-right">${__("Qty")}</th>
						<th class="text-right">${__("Items")}</th>
					</tr>
				</thead>
				<tbody>${rows}</tbody>
			</table>`);
	} catch (e) {
		console.error(e);
		wrapper.html(`<div class="text-danger">${__("Failed to load bin levels.")}</div>`);
	}
}

// Make the dialog reachable from Frappe's global keyboard shortcut menu and
// from the Awesome Bar.
$(document).on("app_ready", function () {
	if (frappe.search && frappe.search.AwesomeBar) {
		// Awesome Bar verb so users can type "Move stock between bins" anywhere.
		const orig = frappe.search.utils.get_creatables;
		if (orig && !orig.__ch_bin_patched) {
			frappe.search.utils.get_creatables = function () {
				const list = orig.apply(this, arguments) || [];
				list.push({
					label: __("Move Stock Between Bins"),
					value: __("Move Stock Between Bins"),
					match: __("Move Stock Between Bins"),
					index: 80,
					default: "Search",
					onclick: () => frappe.ch_pos.show_bin_transfer_dialog(),
				});
				return list;
			};
			frappe.search.utils.get_creatables.__ch_bin_patched = true;
		}
	}
});
