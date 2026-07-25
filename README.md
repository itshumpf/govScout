# GovScout Dashboard

A static [Astro](https://astro.build) site that renders the output of the
[GovScout](../project) CLI pipeline: federal solicitations scored for
pricing signals, with NSN / part-number / quantity line items extracted
for quoting.

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

From the `govscout` project directory (the sibling `project/` repo):

```bash
# offline sample data
python -m govscout demo --json out/dash.json

# or live SAM.gov data (needs SAM_API_KEY)
python -m govscout fetch --json out/dash.json
```

Then copy it into this site and commit:

```bash
cp out/dash.json src/data/dashboard.json
git add src/data/dashboard.json
git commit -m "Refresh dashboard data"
```

Rebuild (`npm run build`) and redeploy — that's it. The demo banner, tier
counts, cards, and filter options all regenerate from the JSON at build
time.

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
