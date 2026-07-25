# GovScout Dashboard

A static [Astro](https://astro.build) site that renders the output of the
GovScout CLI pipeline (bundled in this repo at [`pipeline/`](pipeline)):
federal solicitations scored for pricing signals, with NSN / part-number /
quantity line items extracted for quoting.

Dark theme, amber accent, zero client-side JavaScript dependencies —
plain `.astro` components plus a small vanilla `<script>` for filtering.

## Features

- **Stat-card row** — fetched count, pricing-signal count, and
  HIGH / MEDIUM / LOW tier counts straight from the pipeline's `stats` block.
- **Solicitation cards grouped by tier** (High ≥ 70, Medium 40–69, Low < 40),
  each with pricing-score badge, pricing-signal chips, agency / sol # /
  PSC / posted date / response deadline, extracted NSN / P/N / qty line
  items, and an outbound link to the posting when present.
- **Client-side filters** (no framework): text search across title /
  agency / sol # / NSN, PSC-code dropdown, tier buttons.
- **"DEMO DATA" banner** rendered automatically when the JSON's `source`
  field is `"demo"`; disappears on its own when the data comes from a live
  `fetch` run (`source: "sam.gov"`).

## Data flow

The site reads a committed data file at `src/data/dashboard.json` produced
by the GovScout CLI (`--json` flag on the `demo` and `fetch` commands):

```json
{
  "generated_at": "<ISO timestamp>",
  "source": "demo | sam.gov",
  "stats": { "fetched", "stored", "with_pricing_signals", "high", "medium", "low" },
  "solicitations": [ { "...to_row() fields", "pricing_flags": ["..."] } ]
}
```

### Refreshing the data

The GovScout Python pipeline lives in this repo at `pipeline/` (a copy of
the original `../project` repo — see `pipeline/README.md`). It writes
straight into `src/data/dashboard.json`:

```bash
cd pipeline
pip install -r requirements.txt

# offline sample data
python -m govscout demo --json ../src/data/dashboard.json

# or live SAM.gov data (needs SAM_API_KEY; pipeline/config.json auto-loads
# and caps a run to one request — see pipeline/README.md's quota notes)
SAM_API_KEY=your_key_here python -m govscout fetch --json ../src/data/dashboard.json
```

Then commit the result:

```bash
git add src/data/dashboard.json
git commit -m "Refresh dashboard data"
```

**Automated daily refresh:** `.github/workflows/refresh.yml` runs this
automatically once a day (plus on-demand via the Actions tab's "Run
workflow" button) and commits the result for you. It needs a repo secret:

1. Go to **Settings → Secrets and variables → Actions** on GitHub.
2. Add a secret named `SAM_API_KEY` with your api.data.gov key.

If the fetch fails (rate limit, missing key, network error) the workflow
fails visibly but does **not** commit — the last-good `dashboard.json`
stays in place. Note: GitHub disables scheduled workflows after 60 days
of repo inactivity; push any commit (or re-enable it from the Actions
tab) to reactivate the schedule if daily runs silently stop.

Either way, rebuild (`npm run build`) and redeploy — that's it. The demo
banner, tier counts, cards, and filter options all regenerate from the
JSON at build time.

## Local development

```bash
npm install
npm run dev       # local dev server with hot reload
npm run build     # static build -> dist/
npm run preview   # serve the built dist/ locally
```

Requires Node.js 18+ (built and tested on Node 20).

## Deploy: Cloudflare Pages

1. Push this repo to GitHub/GitLab and import it in
   **Cloudflare Pages → Create a project**.
2. Build settings:

   | Setting | Value |
   | --- | --- |
   | Framework preset | `Astro` (or `None`) |
   | Build command | `npm run build` |
   | Build output directory | `dist` |
   | Node version | 18+ (set `NODE_VERSION=20` env var if needed) |

3. Deploy. Every commit that refreshes `src/data/dashboard.json`
   triggers a new build with the updated pipeline output.

The site is fully static — no server, no environment variables, no
runtime dependencies.
