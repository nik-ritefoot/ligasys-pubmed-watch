#!/usr/bin/env python3
"""Generate Atom feeds from results.json.

Emits three feeds into data/:
  feed.xml            — everything
  feed-research.xml   — research bucket only
  feed-fieldnotes.xml — fieldnotes bucket only

GUID is the PMID, so each paper appears exactly once across all time and the
reader's read/unread state sticks to it. A paper only enters a feed on the
sweep where is_new is true (first sight of that PMID) — genuine repeats never
re-emit, first-appearance papers do regardless of any "reprint" label.

A feed is a cumulative snapshot, not an append log — a reader that polls
infrequently must still see everything since it last checked. So we keep a
rolling history in data/feed_history.json: every first-sight paper is recorded
with the timestamp it first appeared, and each feed re-emits the trailing
FEED_WINDOW_DAYS of that history. Miss a poll for a week, catch up in one pull.

Env:
  SITE_URL - base URL of the Pages site, e.g.
             https://nik-ritefoot.github.io/ligasys-pubmed-watch
             Used for self-links. Falls back to a relative reference.
  FEED_WINDOW_DAYS - trailing window each feed carries. Default 45.
"""

import html
import json
import os
import pathlib
import datetime as dt

WINDOW = int(os.environ.get("FEED_WINDOW_DAYS", "45"))

ROOT = pathlib.Path(__file__).parent
DATA = ROOT / "data"
SITE = os.environ.get("SITE_URL", "").rstrip("/")

# A stable namespace for building GUIDs. urn:pmid: is unofficial but widely
# understood and guarantees uniqueness without depending on the site URL.
def guid(pmid):
    return f"urn:pmid:{pmid}"


def esc(s):
    return html.escape(str(s or ""), quote=True)


def entry_xml(r):
    tags = "".join(
        f'<category term="{esc(m["id"])}" label="{esc(m["label"])}"/>'
        for m in r["matched"]
    )
    tags += f'<category term="bucket:{esc(r["bucket"])}"/>'

    authors = "".join(f"<author><name>{esc(a)}</name></author>"
                      for a in r["authors"])

    # Build a readable HTML summary: citation line + abstract + links.
    cite = " · ".join(filter(None, [
        ", ".join(r["authors"]), r.get("journal"), str(r.get("year") or "")
    ]))
    doi_line = (f'<p>doi: <a href="https://doi.org/{esc(r["doi"])}">'
                f'{esc(r["doi"])}</a></p>') if r.get("doi") else ""
    body = (
        f"<p><em>{esc(cite)}</em></p>"
        f"<p>{esc(r.get('abstract') or 'No abstract indexed.')}</p>"
        f"{doi_line}"
        f'<p><a href="{esc(r["url"])}">View on PubMed</a></p>'
    )

    # first_seen is recorded in history the run this PMID first appeared.
    seen = r["first_seen"]

    return f"""  <entry>
    <title>{esc(r['title'])}</title>
    <id>{guid(r['pmid'])}</id>
    <link href="{esc(r['url'])}" rel="alternate"/>
    <updated>{esc(seen)}</updated>
    <published>{esc(seen)}</published>
    {authors}
    {tags}
    <content type="html">{esc(body)}</content>
  </entry>"""


def build(records, title, slug):
    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    self_link = f'{SITE}/data/{slug}.xml' if SITE else f'data/{slug}.xml'
    entries = "\n".join(entry_xml(r) for r in records)
    feed = f"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>{esc(title)}</title>
  <id>{esc(self_link)}</id>
  <link href="{esc(self_link)}" rel="self"/>
  <updated>{now}</updated>
  <generator>ligasys-pubmed-watch</generator>
{entries}
</feed>
"""
    (DATA / f"{slug}.xml").write_text(feed, encoding="utf-8")
    return len(records)


def main():
    payload = json.loads((DATA / "results.json").read_text())
    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    hist_path = DATA / "feed_history.json"
    history = {}
    if hist_path.exists():
        history = {e["pmid"]: e for e in json.loads(hist_path.read_text())}

    # Record any first-sight paper we haven't already logged. is_new means the
    # PMID was unseen before this sweep — that's the reprint rule: first sight
    # of a PMID enters history regardless of any "reprint" label; a PMID we've
    # logged before is never re-added, so genuine repeats don't resurface.
    for r in payload["records"]:
        if r.get("is_new") and r["pmid"] not in history:
            entry = {k: r[k] for k in (
                "pmid", "title", "journal", "year", "authors",
                "abstract", "doi", "url", "matched", "bucket")}
            entry["first_seen"] = now
            history[r["pmid"]] = entry

    # Prune history well past the window so the file can't grow without bound.
    cutoff = (dt.datetime.now(dt.timezone.utc)
              - dt.timedelta(days=WINDOW * 4)).strftime("%Y-%m-%dT%H:%M:%SZ")
    history = {p: e for p, e in history.items() if e["first_seen"] >= cutoff}

    hist_path.write_text(
        json.dumps(sorted(history.values(),
                          key=lambda e: e["first_seen"], reverse=True),
                   indent=1),
        encoding="utf-8")

    # Each feed carries the trailing window, newest first.
    win_cutoff = (dt.datetime.now(dt.timezone.utc)
                  - dt.timedelta(days=WINDOW)).strftime("%Y-%m-%dT%H:%M:%SZ")
    windowed = sorted(
        (e for e in history.values() if e["first_seen"] >= win_cutoff),
        key=lambda e: e["first_seen"], reverse=True)

    n_all = build(windowed, "LigaSys — PubMed Watch", "feed")
    n_res = build([r for r in windowed if r["bucket"] == "research"],
                  "LigaSys — PubMed Watch (Research)", "feed-research")
    n_fn = build([r for r in windowed if r["bucket"] == "fieldnotes"],
                 "LigaSys — PubMed Watch (Field Notes)", "feed-fieldnotes")

    print(f"feeds: {n_all} in {WINDOW}d window "
          f"({n_res} research / {n_fn} field notes), "
          f"{len(history)} in history")


if __name__ == "__main__":
    main()
