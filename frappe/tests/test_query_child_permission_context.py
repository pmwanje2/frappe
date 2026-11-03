"""Site-free checks for native child-field permission context.

Use the real query engine and Meta permission inheritance. Only metadata,
effective permitted-field lookup, and exception presentation are substituted;
no database, HTTP request, token or deployed service is involved.
"""

import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import patch

import frappe
from frappe.database.query import Engine
from frappe.model.meta import Meta


class Metadata(SimpleNamespace):
	get_permissions = Meta.get_permissions

	def get(self, key, default=None):
		return getattr(self, key, default)

	def get_table_fields(self):
		return self.table_fields


class TestChildPermissionContext(unittest.TestCase):
	def setUp(self):
		context = ExitStack()
		self.addCleanup(context.close)
		self.parent = Metadata(
			name="Parent", istable=False, permissions=[{"read": 1}],
			table_fields=[SimpleNamespace(fieldname="lines", options="Line")],
		)
		self.child = Metadata(name="Line", istable=True, permissions=[], table_fields=[])
		self.other = Metadata(name="Other", istable=False, permissions=[{"read": 1}], table_fields=[])
		self.metadata = {"Parent": self.parent, "Line": self.child, "Other": self.other}
		self.allowed = {
			("Parent", None): {"name", "lines", "public_value"},
			("Line", "Parent"): {"name", "public_value"},
			("Other", None): {"name", "public_value"},
		}
		context.enter_context(patch.object(frappe, "get_meta", side_effect=self.metadata.__getitem__))
		context.enter_context(patch.object(frappe, "only_has_select_perm", return_value=False))
		context.enter_context(patch("frappe.database.query._", side_effect=lambda text: text))
		context.enter_context(patch("frappe.database.query.get_permitted_fields", side_effect=self.permitted))
		context.enter_context(patch.object(frappe, "throw", side_effect=self.throw))
		self.engine = Engine()
		self.engine.doctype = "Parent"
		self.engine.parent_doctype = None
		self.engine.user = "reader@example.invalid"
		self.engine.apply_permissions = True
		self.engine.permitted_fields_cache = {}

	def permitted(self, *, doctype, parenttype, **kwargs):
		return self.allowed.get((doctype, parenttype), set())

	@staticmethod
	def throw(msg=None, exc=frappe.ValidationError, **kwargs):
		raise exc(msg)

	def test_contextless_child_selection_cannot_skip_inherited_field_permissions(self):
		with self.assertRaises(frappe.PermissionError):
			self.engine.check_select_field_permission("Line", "private_value")

	def test_contextless_child_filter_cannot_skip_inherited_field_permissions(self):
		with self.assertRaises(frappe.PermissionError):
			self.engine.check_filter_field_permission("Line", "private_value")

	def test_contextless_child_public_field_remains_readable(self):
		self.engine.check_select_field_permission("Line", "public_value")

	def test_explicit_native_parent_context_remains_readable(self):
		self.engine.check_select_field_permission("Line", "public_value", "Parent")

	def test_explicit_native_parent_context_still_denies_private_field(self):
		with self.assertRaises(frappe.PermissionError):
			self.engine.check_select_field_permission("Line", "private_value", "Parent")

	def test_unrelated_root_cannot_supply_child_authority(self):
		self.engine.doctype = "Other"
		with self.assertRaises(frappe.PermissionError):
			self.engine.check_select_field_permission("Line", "public_value")

	def test_ambiguous_table_relationship_is_not_guessed(self):
		self.parent.table_fields.append(SimpleNamespace(fieldname="other_lines", options="Line"))
		with self.assertRaises(frappe.PermissionError):
			self.engine.check_select_field_permission("Line", "public_value")

	def test_hidden_parent_table_field_cannot_be_avoided(self):
		self.allowed[("Parent", None)] = {"name", "public_value"}
		with self.assertRaises(frappe.PermissionError):
			self.engine.check_select_field_permission("Line", "public_value")

	def test_native_child_root_uses_its_declared_parent_context(self):
		self.engine.doctype = "Line"
		self.engine.parent_doctype = "Parent"
		self.engine.check_select_field_permission("Line", "public_value")
		with self.assertRaises(frappe.PermissionError):
			self.engine.check_select_field_permission("Line", "private_value")

	def test_child_root_without_a_parent_fails_closed(self):
		self.engine.doctype = "Line"
		with self.assertRaises(frappe.PermissionError):
			self.engine.check_select_field_permission("Line", "public_value")

	def test_select_only_parent_does_not_gain_child_read(self):
		with patch.object(frappe, "only_has_select_perm", return_value=True):
			with self.assertRaises(frappe.PermissionError):
				self.engine.check_filter_field_permission("Line", "public_value")

	def test_ordinary_parent_field_checks_are_unchanged(self):
		self.engine.check_select_field_permission("Parent", "public_value")
		with self.assertRaises(frappe.PermissionError):
			self.engine.check_select_field_permission("Parent", "private_value")

	def test_ordinary_metadata_without_permission_rows_is_unchanged(self):
		self.other.permissions = []
		self.engine.check_select_field_permission("Other", "public_value")

	def test_explicit_permission_bypass_behavior_is_not_changed(self):
		self.engine.apply_permissions = False
		self.engine.check_select_field_permission("Line", "private_value")


if __name__ == "__main__":
	unittest.main()
