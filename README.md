# LigaSys — PubMed Watch

Daily sweep of newly-indexed PubMed records relevant to LigaSys, split into
**Research** (injury mechanisms + epidemiology) and **Field Notes** (blog-worthy).

- `queries.yaml` — the search terms. Edit this; nothing is hardcoded.
- `fetch.py` — hits NCBI E-utilities, dedupes against `data/seen.json`, writes `data/results.json`.
- `index.html` — static dashboard, reads `data/results.json`.
- `.github/workflows/sweep.yml` — runs daily at 13:00 UTC, commits results, deploys Pages.

## Deploy

1. **Create the repo** (public is simplest — Pages on private repos needs a paid plan):
   ```bash
   cd pubmed-watch
   git init && git add . && git commit -m "Initial commit"
   gh repo create ligasys-pubmed-watch --public --source=. --push
   ```

2. **Settings → Pages → Source: GitHub Actions.**

3. **Settings → Actions → General → Workflow permissions: Read and write.**

4. **Settings → Secrets and variables → Actions:**
   - Variables tab → New variable → `NCBI_EMAIL` = your email.
     NCBI's usage policy asks that automated traffic identify itself.
   - Secrets tab (optional) → `NCBI_API_KEY`. Free from
     https://account.ncbi.nlm.nih.gov/settings/ — raises the rate limit from
     3 to 10 requests/sec. Not required at this volume.

5. **Actions tab → PubMed sweep → Run workflow** to trigger the first run.

Dashboard lands at `https://<you>.github.io/ligasys-pubmed-watch/`.

## Run locally

```bash
pip install pyyaml
NCBI_EMAIL=you@example.com python fetch.py
python -m http.server 8000    # then open localhost:8000
```

## Notes

- **First run establishes a baseline** — everything is marked seen, nothing NEW.
  The second run onward flags genuinely new indexing.
- `data/seen.json` is what makes NEW work. It's committed on purpose. Delete it
  to reset the baseline.
- Searches use `datetype=edat` — *when PubMed indexed it*, not publication date.
  That's what you want for a watch: it catches papers as they enter the index.
- A paper matching both a research and a fieldnotes query is filed under
  research, but keeps both tags.
- `LOOKBACK_DAYS` (default 30) is the search window, not the NEW window. Daily
  runs with a 30d window means a paper has ~30 chances to be caught even if a
  run fails.
- GitHub disables scheduled workflows on repos with no activity for 60 days.
  It emails you first.
- Scheduled Actions run on a best-effort queue and can be delayed at peak times.
