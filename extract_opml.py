from aopmlengine import OPMLDocument, Outline, build_opml_from_html
import sqlite3, time

def export_archive_to_opml(db_path="search_time_machine.db", out_path="archive_export.opml"):
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("SELECT id, title, url, captured_at, snippet, clean_html FROM archive_pages ORDER BY captured_at DESC")
    rows = cur.fetchall()
    conn.close()

    doc = OPMLDocument(
        title="AI Navigator Archive",
        date_created=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        meta={"generator": "AI Navigator Reader Mode + FunKit AOPML Engine"}
    )

    for id_, title, url, captured_at, snippet, html in rows:
        # Build inner outline for each page
        article_node = Outline(title or f"Snapshot {id_}", _attrs={
            "url": url or "",
            "captured_at": captured_at or "",
            "_local_id": str(id_),
        })
        if snippet:
            article_node.add(Outline(f"Snippet: {snippet[:200]}…"))
        if html:
            subdoc = build_opml_from_html(title or "Document", html)
            for c in subdoc.outlines:
                article_node.add(c)
        doc.add(article_node)

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(doc.to_xml())
    print(f"Wrote {out_path}")

