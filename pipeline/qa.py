"""Final QA, manifest/review/summary generation, and packaging."""
import csv
import json
import zipfile
from datetime import datetime, timezone

from . import config, imaging

MANIFEST_COLUMNS = [
    "ID", "Account", "Address", "City", "State", "Zip Code",
    "Final Status", "Final Filename", "Source URL", "Source Domain",
    "Facility Website", "Parent Health System", "Confidence Score",
    "Verification Notes", "Manual Review Reason", "Timestamp Processed",
]


def write_manifest(state) -> str:
    rows = state.all_rows()
    path = config.MANIFESTS_DIR / "manifest.csv"
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(MANIFEST_COLUMNS)
        for r in rows:
            w.writerow([
                r["id"], r["account"], r["address"], r["city"], r["state"], r["zip"],
                r["status"], r["filename"] or "", r["source_url"] or "",
                r["source_domain"] or "", r["website"] or "", r["parent_system"] or "",
                r["confidence"] if r["confidence"] is not None else "",
                r["verification_notes"] or "", r["review_reason"] or "",
                r["processed_at"] or "",
            ])
    return str(path)


def write_review_file(state) -> str:
    rows = [r for r in state.all_rows() if r["status"] in ("manual_review", "failed")]
    path = config.REVIEW_DIR / "manual_review.csv"
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["ID", "Account", "City", "State", "Status", "Reason",
                    "Search Attempts", "Best Candidate URLs", "Notes"])
        for r in rows:
            attempts = r["attempts_json"] or "[]"
            cands = json.loads(r["candidates_json"] or "[]")
            cand_urls = "; ".join(c.get("url", "")[:200] for c in cands[:5])
            w.writerow([
                r["id"], r["account"], r["city"], r["state"], r["status"],
                r["review_reason"] or r["error"] or "",
                attempts, cand_urls, r["verification_notes"] or "",
            ])
    return str(path)


def validate(state) -> dict:
    """Run the final QA checks; returns {problems: [...], checks: {...}}."""
    rows = state.all_rows()
    problems = []
    ids = [r["id"] for r in rows]
    if len(ids) != len(set(ids)):
        problems.append("duplicate IDs in state")
    fnames = [r["filename"] for r in rows if r["filename"]]
    if len(fnames) != len(set(fnames)):
        problems.append("duplicate filenames")
    unresolved = [r["id"] for r in rows if r["status"] in ("pending", "processing")]
    if unresolved:
        problems.append(f"{len(unresolved)} unresolved rows (pending/processing): {unresolved[:10]}")
    for r in rows:
        if r["status"] == "completed":
            if not r["filename"]:
                problems.append(f"ID {r['id']}: completed without filename")
                continue
            if r["filename"] != config.filename_for(r["id"]):
                problems.append(f"ID {r['id']}: filename mismatch {r['filename']}")
            p = config.LOGOS_DIR / r["filename"]
            if not p.exists():
                problems.append(f"ID {r['id']}: file missing {r['filename']}")
                continue
            issues = imaging.validate_file(p)
            if issues:
                problems.append(f"ID {r['id']}: {issues}")
            if not r["source_url"]:
                problems.append(f"ID {r['id']}: completed without source evidence")
        elif r["status"] in ("manual_review", "failed"):
            if not (r["review_reason"] or r["error"]):
                problems.append(f"ID {r['id']}: unresolved without documented reason")
    # orphan files
    expected = set(fnames)
    for p in config.LOGOS_DIR.glob("*.png"):
        if p.name not in expected:
            problems.append(f"orphan file in logos/: {p.name}")
    return {"problems": problems, "counts": state.counts()}


def write_summary(state, qa_result) -> str:
    c = state.counts()
    rows = state.all_rows()
    by_source = {}
    for r in rows:
        if r["status"] == "completed" and r["source_domain"]:
            by_source[r["source_domain"]] = by_source.get(r["source_domain"], 0) + 1
    top_sources = sorted(by_source.items(), key=lambda kv: -kv[1])[:15]
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "total_rows": c["total"],
        "completed": c["completed"],
        "manual_review": c["manual_review"],
        "failed": c["failed"],
        "pending": c["pending"] + c["processing"],
        "completion_rate": round(c["completed"] / c["total"], 4) if c["total"] else 0,
        "qa_problems": qa_result["problems"],
        "top_source_domains": top_sources,
        "filename_template": config.FILENAME_TEMPLATE,
    }
    path = config.MANIFESTS_DIR / "summary.json"
    path.write_text(json.dumps(summary, indent=2))
    md = [
        "# Hospital Logo Pipeline — Summary Report",
        f"Generated: {summary['generated_at']}",
        "",
        f"| Metric | Count |", "|---|---|",
        f"| Total rows | {summary['total_rows']} |",
        f"| Completed | {summary['completed']} |",
        f"| Manual review | {summary['manual_review']} |",
        f"| Failed | {summary['failed']} |",
        f"| Pending | {summary['pending']} |",
        "",
        f"Completion rate: **{summary['completion_rate']*100:.1f}%**",
        "",
        "## Top source domains",
        *[f"- {d}: {n}" for d, n in top_sources],
        "",
        f"## QA problems ({len(qa_result['problems'])})",
        *[f"- {p}" for p in qa_result["problems"][:50]],
    ]
    (config.MANIFESTS_DIR / "summary.md").write_text("\n".join(md))
    return str(path)


def make_zip(state) -> str:
    path = config.OUTPUT_DIR / "hospital_logos.zip"
    rows = [r for r in state.all_rows() if r["status"] == "completed" and r["filename"]]
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for r in rows:
            p = config.LOGOS_DIR / r["filename"]
            if p.exists():
                z.write(p, arcname=r["filename"])
    return str(path)
