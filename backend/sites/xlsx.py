"""
A tiny .xlsx reader using only Python's built-in zip and XML tools.

An .xlsx file is really a zip of XML files. We only need plain cell values
(text and numbers) from one sheet, so this avoids adding openpyxl.
"""

import re
import zipfile
import xml.etree.ElementTree as ET

# The XML namespaces Excel uses
NS = {
    "m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
}
REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"


def _col_index(cell_ref):
    """Turn a cell reference like 'AB12' into a 0-based column number (27)."""
    letters = re.match(r"[A-Z]+", cell_ref).group(0)
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch) - ord("A") + 1)
    return n - 1


def _shared_strings(z):
    """Excel stores most text once in a shared list; cells point into it."""
    if "xl/sharedStrings.xml" not in z.namelist():
        return []
    root = ET.fromstring(z.read("xl/sharedStrings.xml"))
    out = []
    for si in root.findall("m:si", NS):
        # A string can be split into several formatted runs; join them.
        out.append("".join(t.text or "" for t in si.iter(f"{{{NS['m']}}}t")))
    return out


def _sheet_path(z, sheet_name):
    """Find which XML file inside the zip holds the sheet called sheet_name."""
    wb = ET.fromstring(z.read("xl/workbook.xml"))
    rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
    targets = {r.get("Id"): r.get("Target") for r in rels.findall(f"{{{REL_NS}}}Relationship")}
    for s in wb.find("m:sheets", NS):
        if s.get("name") == sheet_name:
            target = targets[s.get(f"{{{NS['r']}}}id")]
            return target.lstrip("/") if target.startswith("/") else "xl/" + target
    raise KeyError(f"no sheet named {sheet_name!r}")


def read_rows(path, sheet_name):
    """Yield each row of a sheet as a list of values (str, float or None)."""
    with zipfile.ZipFile(path) as z:
        strings = _shared_strings(z)
        sheet_xml = z.read(_sheet_path(z, sheet_name))
    root = ET.fromstring(sheet_xml)
    for row in root.iter(f"{{{NS['m']}}}row"):
        values = []
        for c in row.findall("m:c", NS):
            col = _col_index(c.get("r"))
            while len(values) < col:          # empty cells are left out of the XML
                values.append(None)
            kind = c.get("t")
            v = c.find("m:v", NS)
            if kind == "s":                   # shared string
                val = strings[int(v.text)] if v is not None else None
            elif kind == "inlineStr":
                t = c.find(".//m:t", NS)
                val = t.text if t is not None else None
            elif v is None:
                val = None
            elif kind in ("str", "e"):        # formula text or error
                val = v.text
            else:                             # number
                try:
                    val = float(v.text)
                except ValueError:
                    val = v.text
            values.append(val)
        yield values
