"""Tiny markdown -> email-ready standalone HTML (headings, bold, italics, lists, tables, links). No dependencies."""
import re, html


def _inline(s):
    s = html.escape(s)
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
    s = re.sub(r"_(.+?)_", r"<i>\1</i>", s)
    s = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r'<a href="\2">\1</a>', s)
    s = re.sub(r"\[S(\d+)\]", r'<sup><a href="#s\1">S\1</a></sup>', s)
    return s


def to_html(md, title=""):
    out, in_list, in_table = [], False, False
    for line in md.splitlines():
        if line.startswith("|"):
            cells = [c.strip() for c in line.strip("|").split("|")]
            if set("".join(cells)) <= set("-: "):
                continue
            if not in_table:
                out.append("<table>")
                in_table = True
                out.append("<tr>" + "".join(f"<th>{_inline(c)}</th>" for c in cells) + "</tr>")
                continue
            rid = re.match(r"S(\d+)", cells[0])
            out.append(f'<tr id="s{rid.group(1)}">' if rid else "<tr>")
            out.append("".join(f"<td>{_inline(c)}</td>" for c in cells) + "</tr>")
            continue
        if in_table:
            out.append("</table>")
            in_table = False
        stripped = line.strip()
        if re.match(r"^(\s*)- ", line):
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append("<li style='margin-left:%dpx'>%s</li>" % ((len(line) - len(line.lstrip())) * 8, _inline(stripped[2:])))
            continue
        if in_list:
            out.append("</ul>")
            in_list = False
        m = re.match(r"^(#+) (.*)", line)
        if m:
            n = len(m.group(1))
            out.append(f"<h{n}>{_inline(m.group(2))}</h{n}>")
        elif re.match(r"^\d+\. ", stripped):
            out.append(f"<p>{_inline(stripped)}</p>")
        elif stripped:
            out.append(f"<p>{_inline(stripped)}</p>")
    if in_list:
        out.append("</ul>")
    if in_table:
        out.append("</table>")
    css = ("body{font-family:Arial,Helvetica,sans-serif;max-width:860px;margin:24px auto;color:#222;line-height:1.5;padding:0 12px}"
           "h1{font-size:24px}h2{font-size:18px;border-bottom:1px solid #ddd;padding-bottom:4px;margin-top:28px}"
           "table{border-collapse:collapse;font-size:12px}td,th{border:1px solid #ddd;padding:4px 6px;vertical-align:top}th{background:#f5f5f5}")
    return f"<!doctype html><html><head><meta charset='utf-8'><title>{html.escape(title)} brief</title><style>{css}</style></head><body>{''.join(out)}</body></html>"
