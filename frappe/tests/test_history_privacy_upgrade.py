# Copyright (c) 2026, Frappe Technologies and Contributors
# License: MIT. See LICENSE

import json

import frappe
from frappe.desk.form.load import get_milestones, get_versions
from frappe.model.utils.mask import mask_field_value
from frappe.tests import IntegrationTestCase


class TestHistoryPrivacyUpgrade(IntegrationTestCase):
	"""Actual native metadata, role permissions, Version and Milestone storage."""

	def setUp(self):
		super().setUp()
		from frappe.core.doctype.doctype.test_doctype import new_doctype

		frappe.set_user("Administrator")
		self.addCleanup(frappe.set_user, "Administrator")
		self.child = new_doctype(
			istable=1,
			fields=[
				{"fieldname": "public_value", "label": "Public", "fieldtype": "Data"},
				{"fieldname": "masked_value", "label": "Masked", "fieldtype": "Data", "mask": 1},
				{"fieldname": "private_value", "label": "Private", "fieldtype": "Data", "permlevel": 1},
			],
		).insert()
		self.parent = new_doctype(
			track_changes=1,
			fields=[
				{
					"fieldname": "masked_email",
					"label": "Masked Email",
					"fieldtype": "Data",
					"options": "Email",
					"mask": 1,
				},
				{"fieldname": "public_value", "label": "Public", "fieldtype": "Data"},
				{"fieldname": "masked_value", "label": "Masked", "fieldtype": "Data", "mask": 1},
				{"fieldname": "private_value", "label": "Private", "fieldtype": "Data", "permlevel": 1},
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
				{"role": "All", "read": 1},
				{"role": "System Manager", "read": 1, "mask": 1},
				{"role": "System Manager", "read": 1, "mask": 1, "permlevel": 1},
			],
		).insert()
		self.addCleanup(frappe.delete_doc, "DocType", self.child.name, force=True)
		self.addCleanup(frappe.delete_doc, "DocType", self.parent.name, force=True)
		self.addCleanup(frappe.set_user, "Administrator")
		self.users = {}
		for role in ("reader", "unmask"):
			email = f"history-{role}-{frappe.generate_hash(length=10)}@example.test"
			user = frappe.get_doc(
				doctype="User",
				email=email,
				first_name=role,
				send_welcome_email=0,
				roles=[{"role": "System Manager"}] if role == "unmask" else [],
			).insert()
			self.users[role] = user.name
		self.doc = frappe.get_doc(doctype=self.parent.name, public_value="visible").insert()
		self.diff = {
			"changed": [
				["public_value", "public-before", "public-after"],
				["masked_value", "masked-before", "masked-after"],
				["private_value", "private-before", "private-after"],
				["docstatus", 0, 1],
			],
			"row_changed": [
				[
					"rows",
					0,
					"ROW-1",
					[
						["public_value", "public-before", "public-after"],
						["masked_value", "masked-before", "masked-after"],
						["private_value", "private-before", "private-after"],
					],
				]
			],
			"added": [
				[
					"rows",
					{
						"public_value": "public-added",
						"masked_value": "masked-added",
						"private_value": "private-added",
					},
				]
			],
			"removed": [
				[
					"rows",
					{
						"public_value": "public-removed",
						"masked_value": "masked-removed",
						"private_value": "private-removed",
					},
				]
			],
		}
		self.version = frappe.get_doc(
			doctype="Version",
			ref_doctype=self.parent.name,
			docname=self.doc.name,
			data=json.dumps(self.diff),
		).insert(ignore_permissions=True)

	def read_versions(self, role):
		frappe.set_user(self.users[role])
		self.doc.check_permission("read")
		return get_versions(self.doc)

	def test_masked_parent_version_values_use_native_masking(self):
		versions = self.read_versions("reader")
		data = json.loads(versions[0].data)
		masked = mask_field_value(
			self.parent.get("fields", {"fieldname": "masked_value"})[0], "masked-before"
		)
		self.assertIn(["masked_value", masked, masked], data["changed"])
		self.assertIn(["public_value", "public-before", "public-after"], data["changed"])
		self.assertIn(["docstatus", 0, 1], data["changed"])
		self.assertNotIn("masked-before", versions[0].data)
		self.assertNotIn("private-before", versions[0].data)

	def test_masked_child_version_values_use_parent_permission_context(self):
		data = json.loads(self.read_versions("reader")[0].data)
		self.assertEqual(
			data["row_changed"],
			[
				[
					"rows",
					0,
					"ROW-1",
					[
						["public_value", "public-before", "public-after"],
						["masked_value", "XXXXXXXX", "XXXXXXXX"],
					],
				]
			],
		)
		for section in ("added", "removed"):
			self.assertEqual(data[section][0][1]["masked_value"], "XXXXXXXX")
			self.assertNotIn("private_value", data[section][0][1])

	def test_native_unmask_actor_and_administrator_keep_version_values(self):
		self.assertEqual(json.loads(self.read_versions("unmask")[0].data), self.diff)
		frappe.set_user("Administrator")
		self.assertEqual(json.loads(get_versions(self.doc)[0].data), self.diff)

	def test_version_filter_does_not_mutate_stored_audit(self):
		self.read_versions("reader")
		frappe.set_user("Administrator")
		self.assertEqual(json.loads(frappe.get_doc("Version", self.version.name).data), self.diff)

	def seed_milestones(self):
		for fieldname in ("public_value", "masked_value", "private_value", "removed_field"):
			frappe.get_doc(
				doctype="Milestone",
				reference_type=self.doc.doctype,
				reference_name=self.doc.name,
				track_field=fieldname,
				value=f"{fieldname}-secret",
			).insert(ignore_permissions=True)

	def test_milestones_filter_unreadable_fields_and_mask_values(self):
		self.seed_milestones()
		frappe.set_user(self.users["reader"])
		self.doc.check_permission("read")
		rows = get_milestones(self.doc.doctype, self.doc.name)
		values = {row.track_field: row.value for row in rows}
		self.assertEqual(values, {"public_value": "public_value-secret", "masked_value": "XXXXXXXX"})
		frappe.set_user("Administrator")
		stored = frappe.get_all(
			"Milestone",
			filters={"reference_type": self.doc.doctype, "reference_name": self.doc.name},
			fields=["track_field", "value"],
		)
		self.assertEqual(len(stored), 4)
		self.assertIn("masked_value-secret", [row.value for row in stored])

	def test_native_unmask_actor_keeps_permitted_milestones(self):
		self.seed_milestones()
		frappe.set_user(self.users["unmask"])
		rows = get_milestones(self.doc.doctype, self.doc.name)
		self.assertEqual(
			{row.track_field: row.value for row in rows},
			{
				"public_value": "public_value-secret",
				"masked_value": "masked_value-secret",
				"private_value": "private_value-secret",
			},
		)

	def test_malformed_masked_history_fails_closed_without_breaking_valid_versions(self):
		malformed = frappe.get_doc(
			doctype="Version",
			ref_doctype=self.parent.name,
			docname=self.doc.name,
			data=json.dumps({"changed": [["masked_email", {"bad": "before"}, {"bad": "after"}]]}),
		).insert(ignore_permissions=True)
		versions = self.read_versions("reader")
		self.assertEqual([version.name for version in versions], [self.version.name])
		frappe.set_user("Administrator")
		self.assertIsInstance(json.loads(frappe.get_doc("Version", malformed.name).data), dict)
