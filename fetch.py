#!/usr/bin/env python3
"""Sweep PubMed for LigaSys-relevant new indexing.

Reads queries.yaml, hits NCBI E-utilities, dedupes against data/seen.json,
writes data/results.json for the dashboard to render.

Env:
  NCBI_EMAIL   - required by NCBI's usage policy (set as repo secret/var)
  NCBI_API_KEY - optional; raises rate limit 3/sec -> 10/sec
  LOOKBACK_DAYS - default 30
"""

import json
import os
import sys
import time
import pathlib
import datetime as dt
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

import yaml

BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
ROOT = pathlib.Path(__file__).parent
DATA = ROOT / "data"
EMAIL = os.environ.get("NCBI_EMAIL", "")
API_KEY = os.environ.get("NCBI_API_KEY", "")
LOOKBACK = int(os.environ.get("LOOKBACK_DAYS", "30"))
RETMAX = 60

# NCBI asks for <=3 req/sec without a key, <=10 with one.
DELAY = 0.12 if API_KEY else 0.40


def _common():
    p = {"tool": "ligasys-pubmed-watch", "db": "pubmed"}
    if EMAIL:
        p["email"] = EMAIL
    if API_KEY:
        p["api_key"] = API_KEY
    return p


def _get(endpoint, params, retries=3):
    url = f"{BASE}/{endpoint}?" + urllib.parse.urlencode(params)
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=45) as r:
                return r.read()
        except Exception as e:
            if attempt == retries - 1:
                raise
            wait = 2 ** attempt
            print(f"  retry in {wait}s ({e})", file=sys.stderr)
            time.sleep(wait)


def esearch(term):
    p = _common()
    p.update({
        "term": " ".join(term.split()),
        "retmax": RETMAX,
        "sort": "date",
        "datetype": "edat",          # Entrez date = when PubMed indexed it
        "reldate": LOOKBACK,
        "retmode": "json",
    })
    time.sleep(DELAY)
    data = json.loads(_get("esearch.fcgi", p))
    return data.get("esearchresult", {}).get("idlist", [])


def _text(node, path):
    el = node.find(path)
    return "".join(el.itertext()).strip() if el is not None else ""


def efetch(pmids):
    if not pmids:
        return {}
    p = _common()
    p.update({"id": ",".join(pmids), "retmode": "xml"})
    time.sleep(DELAY)
    root = ET.fromstring(_get("efetch.fcgi", p))

    out = {}
    for art in root.findall(".//PubmedArticle"):
        pmid = _text(art, ".//PMID")
        if not pmid:
            continue

        abstract = " ".join(
            "".join(seg.itertext()).strip()
            for seg in art.findall(".//Abstract/AbstractText")
        ).strip()

        authors = []
        for a in art.findall(".//Author")[:4]:
            last, initials = _text(a, "LastName"), _text(a, "Initials")
            if last:
                authors.append(f"{last} {initials}".strip())
        if len(art.findall(".//Author")) > 4:
            authors.append("et al.")

        doi = ""
        for aid in art.findall('.//ArticleId[@IdType="doi"]'):
            doi = (aid.text or "").strip()
            break

        pubtypes = [
            "".join(pt.itertext()).strip()
            for pt in art.findall(".//PublicationType")
        ]

        out[pmid] = {
            "pmid": pmid,
            "title": _text(art, ".//ArticleTitle") or "(no title)",
            "journal": _text(art, ".//Journal/ISOAbbreviation")
                       or _text(art, ".//Journal/Title"),
            "year": _text(art, ".//JournalIssue/PubDate/Year")
                    or _text(art, ".//JournalIssue/PubDate/MedlineDate")[:4],
            "authors": authors,
            "abstract": abstract,
            "doi": doi,
            "pubtypes": pubtypes,
            "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
        }
    return out


def main():
    if not EMAIL:
        print("WARNING: NCBI_EMAIL unset. NCBI policy asks that you identify "
              "yourself; unidentified traffic can get throttled or blocked.",
              file=sys.stderr)

    queries = yaml.safe_load((ROOT / "queries.yaml").read_text())
    DATA.mkdir(exist_ok=True)

    seen_path = DATA / "seen.json"
    seen = set(json.loads(seen_path.read_text())) if seen_path.exists() else set()
    first_run = not seen_path.exists()

    hits = {}   # pmid -> record
    for q in queries:
        print(f"[{q['id']}] searching...", file=sys.stderr)
        try:
            ids = esearch(q["term"])
        except Exception as e:
            print(f"  FAILED: {e}", file=sys.stderr)
            continue
        print(f"  {len(ids)} in last {LOOKBACK}d", file=sys.stderr)

        for chunk_start in range(0, len(ids), 50):
            chunk = ids[chunk_start:chunk_start + 50]
            try:
                recs = efetch(chunk)
            except Exception as e:
                print(f"  fetch FAILED: {e}", file=sys.stderr)
                continue
            for pmid, rec in recs.items():
                if pmid in hits:
                    # Same paper matched by more than one query — keep both tags.
                    hits[pmid]["matched"].append(
                        {"id": q["id"], "label": q["label"], "bucket": q["bucket"]}
                    )
                else:
                    rec["matched"] = [
                        {"id": q["id"], "label": q["label"], "bucket": q["bucket"]}
                    ]
                    rec["is_new"] = pmid not in seen
                    hits[pmid] = rec

    # A paper is "fieldnotes" only if it matched *no* research query.
    for rec in hits.values():
        buckets = {m["bucket"] for m in rec["matched"]}
        rec["bucket"] = "research" if "research" in buckets else "fieldnotes"

    if first_run:
        for rec in hits.values():
            rec["is_new"] = False

    records = sorted(
        hits.values(),
        key=lambda r: (not r["is_new"], r["title"].lower()),
    )

    payload = {
        "generated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "lookback_days": LOOKBACK,
        "first_run": first_run,
        "queries": [
            {"id": q["id"], "label": q["label"], "bucket": q["bucket"]}
            for q in queries
        ],
        "counts": {
            "total": len(records),
            "new": sum(1 for r in records if r["is_new"]),
            "research": sum(1 for r in records if r["bucket"] == "research"),
            "fieldnotes": sum(1 for r in records if r["bucket"] == "fieldnotes"),
        },
        "records": records,
    }

    (DATA / "results.json").write_text(json.dumps(payload, indent=1))
    seen_path.write_text(json.dumps(sorted(seen | set(hits)), indent=0))

    c = payload["counts"]
    print(f"\n{c['total']} papers ({c['new']} new) — "
          f"{c['research']} research / {c['fieldnotes']} field notes",
          file=sys.stderr)


if __name__ == "__main__":
    main()
