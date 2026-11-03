"""Site-free checks for native child-field permission context.

Use the real query engine, permitted-field provider and Meta permission methods.
Only site metadata, user roles and exception presentation are substituted;
no database, HTTP request, token or deployed service is involved.
"""

import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import patch

import frappe
from frappe.database.query import Engine
from frappe.model import default_fields, table_fields
from frappe.model.meta import Meta


class Metadata(SimpleNamespace):
	get_permissions = Meta.get_permissions
	get_permlevel_access = Meta.get_permlevel_access
	get_permitted_fieldnames = Meta.get_permitted_fieldnames
	get_fieldnames_with_value = Meta.get_fieldnames_with_value
	get_table_fields = Meta.get_table_fields
	default_fields = Meta.default_fields

	def get(self, key, default=None):
		return getattr(self, key, default)

	@property
	def _table_fields(self):
		return [df for df in self.fields if df.fieldtype in table_fields]

	@property
	def _non_computed_table_fields(self):
		return [df for df in self._table_fields if not df.is_virtual]

	def get_valid_columns(self):
		return [*default_fields, *self.get_fieldnames_with_value()]

	def get_field(self, fieldname):
		return next((df for df in self.fields if df.fieldname == fieldname), None)


def field(fieldname, fieldtype="Data", **kwargs):
	return frappe._dict(fieldname=fieldname, fieldtype=fieldtype, permlevel=0, is_virtual=0, **kwargs)


class TestChildPermissionContext(unittest.TestCase):
	def setUp(self):
		context = ExitStack()
		self.addCleanup(context.close)
		permissions = [frappe._dict(role="Reader", read=1, permlevel=0)]
		self.parent = Metadata(
			name="Parent",
			istable=False,
			permissions=permissions,
			fields=[field("lines", "Table", options="Line"), field("public_value"), field("private_value")],
		)
		self.parent.fields[-1].permlevel = 1
		self.child = Metadata(
			name="Line", istable=True, permissions=[], fields=[field("public_value"), field("private_value")]
		)
		self.child.fields[-1].permlevel = 1
		self.other = Metadata(
			name="Other", istable=False, permissions=permissions, fields=[field("public_value")]
		)
		self.metadata = {"Parent": self.parent, "Line": self.child, "Other": self.other}
		context.enter_context(patch.object(frappe, "get_meta", side_effect=self.metadata.__getitem__))
		context.enter_context(patch.object(frappe, "get_roles", return_value=["Reader"]))
		context.enter_context(patch.object(frappe, "only_has_select_perm", return_value=False))
		context.enter_context(patch("frappe.database.query._", side_effect=lambda text: text))
		context.enter_context(patch.object(frappe, "throw", side_effect=self.throw))
		self.engine = Engine()
		self.engine.doctype = "Parent"
		self.engine.parent_doctype = None
		self.engine.user = "reader@example.invalid"
		self.engine.apply_permissions = True
		self.engine.permitted_fields_cache = {}

	@staticmethod
	def throw(msg=None, exc=frappe.ValidationError, **kwargs):
		raise exc(msg)

	def assert_accessible(self, check, *args):
		try:
			check(*args)
		except frappe.PermissionError as exc:
			self.fail(f"Authorized native table access was rejected: {exc}")

	def test_contextless_child_selection_cannot_skip_inherited_field_permissions(self):
		with self.assertRaises(frappe.PermissionError):
			self.engine.check_select_field_permission("Line", "private_value")

	def test_contextless_child_filter_cannot_skip_inherited_field_permissions(self):
		with self.assertRaises(frappe.PermissionError):
			self.engine.check_filter_field_permission("Line", "private_value")

	def test_contextless_child_public_field_remains_readable(self):
		self.assert_accessible(self.engine.check_select_field_permission, "Line", "public_value")

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
		self.parent.fields.append(field("other_lines", "Table", options="Line"))
		with self.assertRaises(frappe.PermissionError):
			self.engine.check_select_field_permission("Line", "public_value")

	def test_computed_relationship_does_not_hide_qualified_child_ambiguity(self):
		computed = field("computed_lines", "Table", options="Line")
		computed.is_virtual = 1
		self.parent.fields.append(computed)
		for check in (self.engine.check_select_field_permission, self.engine.check_filter_field_permission):
			with self.subTest(check=check.__name__):
				with self.assertRaises(frappe.PermissionError):
					check("Line", "public_value")

	def test_computed_only_relationship_cannot_authorize_stored_child_query(self):
		self.parent.get_field("lines").is_virtual = 1
		for check in (self.engine.check_select_field_permission, self.engine.check_filter_field_permission):
			with self.subTest(check=check.__name__):
				with self.assertRaises(frappe.PermissionError):
					check("Line", "public_value")

	def test_child_optional_fields_keep_existing_bypass(self):
		self.engine.doctype = "Other"
		self.engine.check_select_field_permission("Line", "_user_tags")

	def test_hidden_parent_table_field_cannot_be_avoided(self):
		self.parent.get_field("lines").permlevel = 1
		with self.assertRaises(frappe.PermissionError):
			self.engine.check_select_field_permission("Line", "public_value")

	def test_native_table_types_allow_authorized_permlevels_for_select_and_filter(self):
		for fieldtype in ("Table", "Table MultiSelect"):
			for permlevel in (0, 1):
				for check in (
					self.engine.check_select_field_permission,
					self.engine.check_filter_field_permission,
				):
					with self.subTest(fieldtype=fieldtype, permlevel=permlevel, check=check.__name__):
						self.parent.get_field("lines").fieldtype = fieldtype
						self.parent.get_field("lines").permlevel = permlevel
						self.parent.permissions = [
							frappe._dict(role="Reader", read=1, permlevel=p) for p in (0, 1)
						]
						self.engine.permitted_fields_cache.clear()
						self.assert_accessible(check, "Line", "public_value")

	def test_native_table_types_deny_unauthorized_permlevel_for_select_and_filter(self):
		for fieldtype in ("Table", "Table MultiSelect"):
			for check in (
				self.engine.check_select_field_permission,
				self.engine.check_filter_field_permission,
			):
				with self.subTest(fieldtype=fieldtype, check=check.__name__):
					self.parent.get_field("lines").fieldtype = fieldtype
					self.parent.get_field("lines").permlevel = 1
					with self.assertRaises(frappe.PermissionError):
						check("Line", "public_value")

	def test_inferred_child_with_empty_parent_permissions_fails_closed(self):
		self.parent.permissions = []
		for fieldname in ("public_value", "private_value"):
			for check in (
				self.engine.check_select_field_permission,
				self.engine.check_filter_field_permission,
			):
				with self.subTest(fieldname=fieldname, check=check.__name__):
					with self.assertRaises(frappe.PermissionError):
						check("Line", fieldname)

	def test_native_child_root_uses_its_declared_parent_context(self):
		self.engine.doctype = "Line"
		self.engine.parent_doctype = "Parent"
		self.assert_accessible(self.engine.check_select_field_permission, "Line", "public_value")
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
