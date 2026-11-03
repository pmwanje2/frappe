# Copyright (c) 2025, Frappe Technologies and Contributors
# See license.txt

from unittest.mock import patch

import frappe
from frappe.core.doctype.doctype.test_doctype import new_doctype
from frappe.model.delete_doc import get_dynamic_linked_docs, get_linked_docs
from frappe.tests import IntegrationTestCase

# On IntegrationTestCase, the doctype test records and all
# link-field test record dependencies are recursively loaded
# Use these module variables to add/remove to/from that list
EXTRA_TEST_RECORD_DEPENDENCIES = []  # eg. ["User"]
IGNORE_TEST_RECORD_DEPENDENCIES = []  # eg. ["User"]


class IntegrationTestWorkspaceSidebar(IntegrationTestCase):
	"""
	Integration tests for WorkspaceSidebar.
	Use this class for testing interactions between multiple components.
	"""

	def make_report(self):
		report = frappe.get_doc(
			{
				"doctype": "Report",
				"report_name": "Archived sidebar report " + frappe.generate_hash(length=10),
				"ref_doctype": "ToDo",
				"report_type": "Query Report",
				"is_standard": "No",
				"query": "select 1",
			}
		).insert()
		self.addCleanup(frappe.delete_doc, "Report", report.name, ignore_permissions=True)
		return report

	def make_archive(self, report):
		# Only migration may write the old navigation store. Exercise normal deletion
		# after that flag is gone, as when retiring a report after an upgrade.
		with patch.dict(frappe.flags, in_patch=True):
			archive = frappe.get_doc(
				{
					"doctype": "Workspace Sidebar",
					"title": "Archived navigation " + frappe.generate_hash(length=10),
					"standard": 0,
					"items": [
						{"type": "Link", "link_type": "Report", "link_to": report.name, "label": "Old report"}
					],
				}
			).insert()
		self.addCleanup(frappe.delete_doc, "Workspace Sidebar", archive.name, ignore_permissions=True)
		return archive

	def test_archived_navigation_does_not_block_report_retirement(self):
		report = self.make_report()
		archive = self.make_archive(report)
		archive.reload()
		before = archive.as_dict()

		frappe.delete_doc("Report", report.name)

		self.assertFalse(frappe.db.exists("Report", report.name))
		self.assertEqual(frappe.get_doc("Workspace Sidebar", archive.name).as_dict(), before)

	def test_business_reference_still_blocks_report_retirement(self):
		report = self.make_report()
		self.make_archive(report)
		reference_type = new_doctype(
			fields=[{"fieldname": "report", "fieldtype": "Link", "options": "Report"}]
		).insert()
		self.addCleanup(frappe.delete_doc, "DocType", reference_type.name, force=True)
		reference = frappe.get_doc({"doctype": reference_type.name, "report": report.name}).insert()
		self.addCleanup(frappe.delete_doc, reference.doctype, reference.name)
		# The schema was created inside this test, after earlier report deletions
		# populated the request-local link-field inventory.
		frappe.flags.link_fields = {}

		links = get_linked_docs(report) + get_dynamic_linked_docs(report)
		self.assertEqual(
			{(link["reference_doctype"], link["reference_docname"]) for link in links},
			{(reference.doctype, reference.name)},
		)
		with self.assertRaises(frappe.LinkExistsError):
			frappe.delete_doc("Report", report.name)

		self.assertTrue(frappe.db.exists("Report", report.name))
		self.assertTrue(frappe.db.exists(reference.doctype, reference.name))

	def test_archived_navigation_does_not_grant_delete_permission(self):
		report = self.make_report()
		self.make_archive(report)
		frappe.set_user("Guest")
		try:
			with self.assertRaises(frappe.PermissionError):
				frappe.delete_doc("Report", report.name)
		finally:
			frappe.set_user("Administrator")

		self.assertTrue(frappe.db.exists("Report", report.name))

	def test_archived_navigation_remains_read_only(self):
		report = self.make_report()
		archive = self.make_archive(report)
		archive.title = "Changed archive title"

		with self.assertRaisesRegex(frappe.ValidationError, "archive"):
			archive.save()
