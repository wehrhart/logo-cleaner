"""Workbook ingestion and schema validation."""
import openpyxl

from . import config, util

EXPECTED_HEADER = ["ID", "Account", "Address", "City", "State", "Zip Code"]


def read_workbook(path=None):
    """Read the Excel workbook; validate schema; return list of row dicts."""
    path = path or config.WORKBOOK_PATH
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb[wb.sheetnames[0]]
    rows = ws.iter_rows(values_only=True)
    header = [str(c).strip() if c is not None else "" for c in next(rows)]
    if header[: len(EXPECTED_HEADER)] != EXPECTED_HEADER:
        raise ValueError(f"Unexpected header: {header!r}; expected {EXPECTED_HEADER}")

    out, problems = [], []
    seen_ids = set()
    for i, r in enumerate(rows, start=2):
        if r is None or all(c in (None, "") for c in r):
            continue
        rid, account, address, city, state, zip_code = (list(r) + [None] * 6)[:6]
        if rid is None:
            problems.append(f"row {i}: missing ID")
            continue
        rid = int(rid)
        if rid in seen_ids:
            problems.append(f"row {i}: duplicate ID {rid}")
            continue
        seen_ids.add(rid)
        if not account or not str(account).strip():
            problems.append(f"row {i} (ID {rid}): missing Account")
            continue
        out.append({
            "id": rid,
            "account": str(account).strip(),
            "address": str(address).strip() if address is not None else "",
            "city": str(city).strip() if city is not None else "",
            "state": str(state).strip().upper() if state is not None else "",
            "zip": util.zip5(zip_code),
        })
    return out, problems
