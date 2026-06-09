frappe.ui.form.on("CH POS Password", {
    refresh(frm) {
        frm.set_intro(
            __("Saved PIN values are intentionally masked for security. Use manager-approval flows to verify a PIN."),
            "blue"
        );
    },
});
