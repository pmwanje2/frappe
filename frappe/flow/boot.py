# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See LICENSE

from frappe.flow import is_disabled


def boot_session(bootinfo):
	if is_disabled():
		return
	from frappe.flow.knowledge.extract import FILE_EXTENSIONS

	bootinfo.flow_enabled = True
	# Single source of truth for file types the ingest pipeline can extract
	bootinfo.flow_supported_file_types = sorted(FILE_EXTENSIONS)
