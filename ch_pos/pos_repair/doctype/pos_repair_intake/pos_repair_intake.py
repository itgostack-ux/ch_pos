import frappe
from frappe.model.document import Document


class POSRepairIntake(Document):
    def on_submit(self):
        self._create_service_request()

    def _create_service_request(self):
        """Auto-create a gofix Service Request from the intake data."""
        if self.service_request:
            return

        device_item = self._resolve_device_item()

        sr = frappe.new_doc("Service Request")
        sr.customer = self.customer
        sr.contact_number = self.customer_phone
        sr.source_warehouse = self.store
        sr.company = frappe.db.get_value("Warehouse", self.store, "company")
        sr.service_date = frappe.utils.today()
        sr.decision = "Draft"
        if device_item:
            sr.device_item = device_item
        sr.serial_no = self.serial_no
        sr.actual_imei = self.imei_number
        sr.issue_category = self.issue_category
        sr.issue_description = self.issue_description
        sr.mode_of_service = self.mode_of_service
        sr.password = self.password_pattern
        sr.walkin_source = "POS Counter"
        sr.insert(ignore_permissions=True)

        self.db_set("service_request", sr.name)
        self.db_set("status", "Converted")
        frappe.msgprint(
            f"Service Request <b>{sr.name}</b> created.",
            indicator="green",
            alert=True,
        )

    def _resolve_device_item(self):
        """Try to find an Item matching the device brand + model."""
        if not self.device_brand and not self.device_model:
            return None
        search = f"{self.device_brand or ''} {self.device_model or ''}".strip()
        return frappe.db.get_value("Item", {"item_name": ["like", f"%{search}%"]}, "name")
