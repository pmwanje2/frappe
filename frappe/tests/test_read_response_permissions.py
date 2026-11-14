"""Native read responses must distinguish withheld amounts from genuine zero.

Real metadata, ordinary users, ORM storage, native serializers and read/query/
history APIs. No authorization or arithmetic implementation is substituted.
"""

import json
from unittest.mock import patch

import frappe
import frappe.client
from frappe.desk.form.load import get_versions
from frappe.tests import IntegrationTestCase

SENTINEL = 314159.27
PRIVATE = ("private_currency", "private_float", "private_int", "private_check", "private_nullable")


class TestReadResponsePermissions(IntegrationTestCase):
	def setUp(self):
		super().setUp()
		from frappe.core.doctype.doctype.test_doctype import new_doctype

		frappe.set_user("Administrator")
		self.addCleanup(frappe.set_user, "Administrator")
		self.child = new_doctype(
			istable=1,
			fields=[
				{"fieldname": "public_value", "label": "Public", "fieldtype": "Data"},
				{
					"fieldname": "private_currency",
					"label": "Currency",
					"fieldtype": "Currency",
					"permlevel": 1,
				},
				{"fieldname": "private_nullable", "label": "Nullable", "fieldtype": "Data", "permlevel": 1},
			],
		).insert()
		self.parent = new_doctype(
			track_changes=1,
			fields=[
				{"fieldname": "public_value", "label": "Public", "fieldtype": "Data", "reqd": 1},
				{"fieldname": "public_zero", "label": "True Zero", "fieldtype": "Currency"},
				{"fieldname": "public_nullable", "label": "Nullable", "fieldtype": "Data"},
				{
					"fieldname": "private_currency",
					"label": "Currency",
					"fieldtype": "Currency",
					"permlevel": 1,
				},
				{"fieldname": "private_float", "label": "Float", "fieldtype": "Float", "permlevel": 1},
				{"fieldname": "private_int", "label": "Int", "fieldtype": "Int", "permlevel": 1},
				{"fieldname": "private_check", "label": "Check", "fieldtype": "Check", "permlevel": 1},
				{
					"fieldname": "private_nullable",
					"label": "Private Nullable",
					"fieldtype": "Data",
					"permlevel": 1,
				},
				{
					"fieldname": "private_computed",
					"label": "Computed",
					"fieldtype": "Currency",
					"is_virtual": 1,
					"options": str(SENTINEL),
					"permlevel": 1,
				},
				{"fieldname": "rows", "label": "Rows", "fieldtype": "Table", "options": self.child.name},
				{
					"fieldname": "private_rows",
					"label": "Private Rows",
					"fieldtype": "Table",
					"options": self.child.name,
					"permlevel": 1,
				},
			],
			permissions=[
				{"role": "All", "read": 1, "write": 1, "create": 1},
				{"role": "System Manager", "read": 1, "write": 1},
				{"role": "System Manager", "read": 1, "write": 1, "permlevel": 1},
			],
		).insert()
		self.addCleanup(frappe.delete_doc, "DocType", self.child.name, force=True)
		self.addCleanup(frappe.delete_doc, "DocType", self.parent.name, force=True)
		self.users = {}
		for actor, roles in (("reader", []), ("finance", [{"role": "System Manager"}])):
			user = frappe.get_doc(
				doctype="User",
				email="read-response-" + frappe.generate_hash(length=10) + "@example.test",
				first_name=actor,
				send_welcome_email=0,
				roles=roles,
			).insert()
			self.users[actor] = user.name
		self.doc = frappe.get_doc(
			doctype=self.parent.name,
			public_value="visible",
			public_zero=0,
			public_nullable=None,
			private_currency=SENTINEL,
			private_float=0,
			private_int=0,
			private_check=0,
			private_nullable=None,
			rows=[{"public_value": "row", "private_currency": SENTINEL, "private_nullable": None}],
			private_rows=[{"public_value": "private row", "private_currency": SENTINEL}],
		).insert()
		self.diff = {
			"changed": [
				["public_value", "before", "visible"],
				["private_currency", 0, SENTINEL],
				["private_nullable", "before", None],
			]
		}
		self.version = frappe.get_doc(
			doctype="Version", ref_doctype=self.parent.name, docname=self.doc.name, data=json.dumps(self.diff)
		).insert(ignore_permissions=True)

	def assert_private_omitted(self, result):
		for fieldname in (*PRIVATE, "private_computed", "private_rows"):
			self.assertNotIn(
				fieldname, result, f"Withheld {fieldname} must not become null, zero or an empty table"
			)
		self.assertEqual(result["public_value"], "visible")
		self.assertIn("public_zero", result)
		self.assertEqual(result["public_zero"], 0)
		self.assertIn("public_nullable", result)
		self.assertIsNone(result["public_nullable"])
		self.assertEqual(len(result["rows"]), 1)
		self.assertEqual(result["rows"][0]["public_value"], "row")
		self.assertNotIn("private_currency", result["rows"][0])
		self.assertNotIn("private_nullable", result["rows"][0])

	def test_native_client_get_omits_withheld_numeric_null_child_and_computed_fields(self):
		frappe.set_user(self.users["reader"])
		self.assert_private_omitted(frappe.client.get(self.doc.doctype, self.doc.name))

	def test_direct_native_filter_serializes_omission_instead_of_defaults(self):
		frappe.set_user(self.users["reader"])
		doc = frappe.get_doc(self.doc.doctype, self.doc.name)
		doc.check_permission("read")
		doc.apply_fieldlevel_read_permissions()
		self.assert_private_omitted(doc.as_dict())
		self.assert_private_omitted(doc.__json__())

	def test_withheld_real_zero_is_omitted_while_public_zero_remains_real(self):
		self.doc.db_set("private_currency", 0, update_modified=False)
		frappe.set_user(self.users["reader"])
		self.assert_private_omitted(frappe.client.get(self.doc.doctype, self.doc.name))

	def test_finance_and_administrator_keep_zero_null_sentinel_and_computed_values(self):
		for user in (self.users["finance"], "Administrator"):
			with self.subTest(user=user):
				frappe.set_user(user)
				result = frappe.client.get(self.doc.doctype, self.doc.name)
				self.assertEqual(result["private_currency"], SENTINEL)
				for field in ("private_float", "private_int", "private_check"):
					self.assertIn(field, result)
					self.assertEqual(result[field], 0)
				self.assertIn("private_nullable", result)
				self.assertIsNone(result["private_nullable"])
				self.assertEqual(result["private_computed"], SENTINEL)
				self.assertEqual(result["rows"][0]["private_currency"], SENTINEL)
				self.assertIsNone(result["rows"][0]["private_nullable"])
				self.assertEqual(len(result["private_rows"]), 1)

	def test_reads_preserve_storage_and_unfiltered_trusted_document(self):
		trusted = frappe.get_doc(self.doc.doctype, self.doc.name)
		frappe.set_user(self.users["reader"])
		frappe.client.get(self.doc.doctype, self.doc.name)
		self.assertEqual(trusted.private_currency, SENTINEL)
		self.assertEqual(trusted.as_dict()["private_currency"], SENTINEL)
		frappe.set_user("Administrator")
		self.assertEqual(frappe.db.get_value(self.doc.doctype, self.doc.name, "private_currency"), SENTINEL)
		self.assertEqual(json.loads(frappe.get_doc("Version", self.version.name).data), self.diff)

	def test_native_query_and_version_controls_preserve_permission_boundaries(self):
		frappe.set_user(self.users["reader"])
		rows = frappe.get_list(
			self.doc.doctype,
			filters={"name": self.doc.name},
			fields=["name", "public_value", "private_currency"],
		)
		self.assertEqual(len(rows), 1)
		self.assertNotIn("private_currency", rows[0])
		doc = frappe.get_doc(self.doc.doctype, self.doc.name)
		doc.check_permission("read")
		versions = get_versions(doc)
		entry = next(v for v in versions if v.name == self.version.name)
		self.assertEqual(json.loads(entry.data), {"changed": [self.diff["changed"][0]]})
		frappe.set_user(self.users["finance"])
		rows = frappe.get_list(
			self.doc.doctype, filters={"name": self.doc.name}, fields=["name", "private_currency"]
		)
		self.assertEqual(rows[0].private_currency, SENTINEL)
		entry = next(
			v
			for v in get_versions(frappe.get_doc(self.doc.doctype, self.doc.name))
			if v.name == self.version.name
		)
		self.assertEqual(json.loads(entry.data), self.diff)

	def test_filtered_document_save_preserves_protected_storage_and_audit(self):
		frappe.set_user(self.users["reader"])
		doc = frappe.get_doc(self.doc.doctype, self.doc.name)
		doc.apply_fieldlevel_read_permissions()
		doc.public_value = "changed public"
		with patch.object(frappe, "in_test", False):
			doc.save()
		self.assertEqual(doc.private_currency, SENTINEL)
		frappe.set_user("Administrator")
		stored = frappe.get_doc(doc.doctype, doc.name)
		self.assertEqual(stored.public_value, "changed public")
		self.assertEqual(stored.private_currency, SENTINEL)
		self.assertEqual(stored.rows[0].private_currency, SENTINEL)
		for data in frappe.get_all(
			"Version", filters={"ref_doctype": doc.doctype, "docname": doc.name}, pluck="data"
		):
			self.assertNotIn(["private_currency", SENTINEL, 0], json.loads(data).get("changed", []))
			self.assertNotIn(["private_currency", SENTINEL, None], json.loads(data).get("changed", []))

	def test_authorized_update_after_in_place_filter_does_not_lose_db_writes(self):
		frappe.set_user(self.users["reader"])
		doc = frappe.get_doc(self.doc.doctype, self.doc.name)
		doc.apply_fieldlevel_read_permissions()
		frappe.set_user(self.users["finance"])
		doc.private_currency = 27
		doc.private_nullable = "finance update"
		doc.save()
		self.assertEqual(frappe.db.get_value(doc.doctype, doc.name, "private_currency"), 27)
		self.assertEqual(frappe.db.get_value(doc.doctype, doc.name, "private_nullable"), "finance update")

	def test_client_supplied_omission_flags_cannot_bypass_storage_or_mandatory_validation(self):
		payload = self.doc.as_dict()
		payload["flags"] = {
			"response_omitted_fields": ["public_value", "private_currency"],
			"ignore_permissions": 1,
		}
		payload["public_value"] = "legitimate public"
		payload["private_currency"] = 999999
		frappe.set_user(self.users["reader"])
		frappe.client.save(payload)
		self.assertEqual(
			frappe.db.get_value(self.doc.doctype, self.doc.name, "public_value"), "legitimate public"
		)
		self.assertEqual(frappe.db.get_value(self.doc.doctype, self.doc.name, "private_currency"), SENTINEL)
		frappe.set_user("Administrator")
		payload = frappe.get_doc(self.doc.doctype, self.doc.name).as_dict()
		payload["flags"] = {"response_omitted_fields": ["public_value"]}
		payload["public_value"] = None
		frappe.set_user(self.users["reader"])
		with self.assertRaises(frappe.MandatoryError):
			frappe.client.save(payload)
