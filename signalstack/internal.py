"""Internal intelligence: CSV / Excel / PDF / text uploads -> recurring objections, lost-deal reasons, product requests, patterns."""
import os, json, hashlib
from datetime import datetime, timezone
import pandas as pd
import db, llm, research


def extract_text(path):
    ext = os.path.splitext(path)[1].lower()
    if ext in (".csv", ".tsv"):
        df = pd.read_csv(path, sep="\t" if ext == ".tsv" else ",", on_bad_lines="skip")
        return _df_text(df)
    if ext in (".xlsx", ".xls"):
        parts = []
        for name, df in pd.read_excel(path, sheet_name=None).items():
            parts.append(f"## Sheet {name}\n" + _df_text(df))
        return "\n".join(parts)
    if ext == ".pdf":
        from pypdf import PdfReader
        return "\n".join((p.extract_text() or "") for p in PdfReader(path).pages)
    with open(path, "r", errors="ignore") as f:
        return f.read()


def _df_text(df, max_rows=150):
    head = f"{len(df)} rows; columns: {list(df.columns)}\n"
    stats = ""
    for col in df.columns:
        if df[col].dtype == object and 1 < df[col].nunique() <= 25:
            stats += f"Counts for '{col}': {df[col].value_counts().head(12).to_dict()}\n"
    return head + stats + df.head(max_rows).to_csv(index=False)


def analyze_upload(cid, path, filename):
    text = extract_text(path)[:14000]
    prof = research.profile_brief(cid)
    out = llm.ask(f"""Analyse this internal company file ({filename}) for the company below.
{prof}

Extract ONLY patterns actually present in the file (quote short customer phrases verbatim where they exist). Return JSON:
{{"items":[{{"type":"objection"|"lost_deal_reason"|"product_request"|"sales_pattern"|"emerging_risk"|"operational_issue"|"other",
 "finding": str (what the data shows, with counts if the data has them),
 "evidence": str (short verbatim quote or row summary from the file),
 "why_it_matters": str (interpretation, start with 'Interpretation:'),
 "relevance": 0-10, "impact": 0-10, "urgency": 0-10, "suggested_action": str}}], "file_summary": str}}
If the file has no useful patterns, return an empty items list.

FILE CONTENT:
{text}""", temperature=0.2)
    src = db.q("select * from sources where company_id=? and category='internal'", (cid,), one=True)
    n = 0
    for it in out.get("items", []):
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        title = f"[{it.get('type')}] {it.get('finding','')[:150]}"
        hsh = hashlib.sha1((filename + title).encode()).hexdigest()
        analysis = {"what_happened": it.get("finding"), "why_it_matters": it.get("why_it_matters"), "type": "neutral",
                    "business_area": it.get("type"), "suggested_action": it.get("suggested_action"), "evidence_strength": 8}
        rel, imp, urg = (float(it.get(k, 0) or 0) for k in ("relevance", "impact", "urgency"))
        sc = round(100 * (0.30 * rel / 10 + 0.25 * imp / 10 + 0.15 * urg / 10 + 0.07 + 0.10 + 0.10), 1)
        try:
            db.x("""insert into records(company_id,source_id,category,title,url,publisher,published,discovered,snippet,hash,analysis,
                    relevance,impact,urgency,credibility,recency,novelty,evidence_strength,score,verified,kind)
                    values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                 (cid, src["id"] if src else None, "internal", title, f"file://{filename}", "Internal upload", now, now,
                  it.get("evidence", "")[:1000], hsh, json.dumps(analysis), rel, imp, urg, 0.7, 1.0, 1.0, 8, sc,
                  "verified (internal data)", "internal"))
            n += 1
        except Exception:
            pass
    return n, out.get("file_summary", "")
