# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# License: MIT. See LICENSE
"""Site-free regression using real get_versions and native permission methods.

Only storage, session and metadata inputs are doubled. DB-backed form loading
and Version creation are covered by TestVersionCurrencyPermissions.
"""

import copy
import json
import unittest
from contextlib import ExitStack
from types import MethodType, SimpleNamespace
from unittest.mock import patch

import frappe
import frappe.share
from frappe.desk.form.load import get_versions
from frappe.model.document import Document
from frappe.model.meta import Meta


class TestVersionPayloadPermissions(unittest.TestCase):
	def setUp(self):
		self.context = ExitStack()
		self.addCleanup(self.context.close)
		self.roles = ["Maintenance"]
		self.context.enter_context(
			patch.object(frappe.local, "session", frappe._dict(user="maintenance@example.test"), create=True)
		)
		self.context.enter_context(
			patch.object(frappe, "get_roles", side_effect=lambda user=None: self.roles)
		)
		self.context.enter_context(patch.object(frappe.share, "get_shared", return_value=[]))
		fields = [
			frappe._dict(fieldname="description", fieldtype="Data", permlevel=0),
			frappe._dict(fieldname="amount", fieldtype="Currency", permlevel=1),
			frappe._dict(fieldname="items", fieldtype="Table", permlevel=0, options="Test Version Item"),
			frappe._dict(
				fieldname="private_items", fieldtype="Table", permlevel=1, options="Test Version Item"
			),
		]
		self.meta = SimpleNamespace(
			name="Test Version Currency",
			track_changes=1,
			fields=fields,
			permissions=[
				frappe._dict(role="Maintenance", read=1, permlevel=0),
				frappe._dict(role="Finance", read=1, permlevel=1),
			],
			istable=0,
			get_table_fields=lambda **kwargs: fields[2:],
			get_masked_fields=lambda **kwargs: [],
			get=lambda key, default=None: getattr(self.meta, key, default),
		)
		for name in ("get_permissions", "get_permlevel_access"):
			setattr(self.meta, name, MethodType(getattr(Meta, name), self.meta))
		self.child_meta = SimpleNamespace(fields=fields[:2], get_masked_fields=lambda **kwargs: [])
		self.context.enter_context(
			patch.object(
				frappe,
				"get_meta",
				side_effect=lambda dt: self.child_meta if dt == "Test Version Item" else self.meta,
			)
		)
		self.doc = Document.__new__(Document)
		self.doc.doctype, self.doc.name = self.meta.name, "TEST-VERSION-1"
		self.diff = {
			"changed": [
				["description", "before", "after"],
				["amount", "731.29", "842.37"],
				["docstatus", 0, 1],
			],
			"row_changed": [
				["items", 0, "ROW-1", [["description", "before", "after"], ["amount", "731.29", "842.37"]]],
				["private_items", 0, "ROW-2", [["amount", "731.29", "842.37"]]],
			],
			"added": [["items", {"description": "added row", "amount": "842.37"}]],
			"removed": [["items", {"description": "removed row", "amount": "731.29"}]],
		}
		self.stored = [
			frappe._dict(
				name="VERSION-1",
				owner="finance@example.test",
				creation="2026-11-04",
				data=json.dumps(self.diff),
			)
		]
		self.context.enter_context(
			patch.object(frappe, "get_all", side_effect=lambda *args, **kwargs: copy.deepcopy(self.stored))
		)

	def test_get_versions_strips_fields_without_read_permlevel(self):
		versions = get_versions(self.doc)
		self.assertEqual(len(versions), 1)
		data = json.loads(versions[0].data)
		self.assertEqual(data["changed"], [["description", "before", "after"], ["docstatus", 0, 1]])
		for secret in ('"amount"', "731.29", "842.37", "private_items"):
			self.assertNotIn(secret, versions[0].data)
		self.assertEqual(json.loads(self.stored[0].data), self.diff)

	def test_get_versions_keeps_permitted_fields(self):
		self.roles.append("Finance")
		self.assertEqual(json.loads(get_versions(self.doc)[0].data), self.diff)

	def test_get_versions_filters_child_changes_additions_and_removals(self):
		data = json.loads(get_versions(self.doc)[0].data)
		self.assertEqual(data["row_changed"], [["items", 0, "ROW-1", [["description", "before", "after"]]]])
		self.assertEqual(data["added"], [["items", {"description": "added row"}]])
		self.assertEqual(data["removed"], [["items", {"description": "removed row"}]])

	def test_get_versions_omits_unparseable_data_for_all_readers(self):
		for user in ("maintenance@example.test", "Administrator"):
			for raw in ('{"changed": "842.37"', "[]", "null"):
				with self.subTest(user=user, raw=raw):
					frappe.session.user = user
					self.stored[0].data = raw
					self.assertEqual(get_versions(self.doc), [])

	def test_get_versions_omits_malformed_diff_sections(self):
		for section, value in (
			("changed", 842.37),
			("row_changed", [["items", 0, "ROW-1", 842.37]]),
			("added", [["items", {"description": "ok"}], None]),
			("removed", 842.37),
		):
			with self.subTest(section=section):
				self.stored[0].data = json.dumps({section: value})
				try:
					versions = get_versions(self.doc)
				except (TypeError, KeyError, IndexError, AttributeError) as exc:
					self.fail(f"Malformed historical data must be omitted, not break form loading: {exc}")
				self.assertEqual(versions, [])

	def test_get_versions_omits_unparseable_data_without_permission_rows(self):
		self.meta.permissions = []
		self.stored[0].data = '{"changed": "842.37"'
		self.assertEqual(get_versions(self.doc), [])

	def test_get_versions_preserves_administrator_data(self):
		frappe.session.user = "Administrator"
		self.assertEqual(json.loads(get_versions(self.doc)[0].data), self.diff)

	def test_get_versions_does_not_return_untracked_history(self):
		self.meta.track_changes = 0
		self.assertEqual(get_versions(self.doc), [])
