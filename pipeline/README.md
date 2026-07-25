# GovScout

> This copy lives inside the `govscout-site` repo (`pipeline/`) and is the
> source of truth for the live site's data — see
> `.github/workflows/refresh.yml`, which runs `python -m govscout fetch`
> here and commits the JSON straight into `../src/data/dashboard.json`.

**Federal solicitation monitoring with pricing-signal detection — from raw opportunity feed to a prioritized, quotable pipeline in one command.**

GovScout pulls U.S. federal solicitations (SAM.gov Opportunities API v2), filters them by PSC/FSC code and recency, detects which ones have actionable pricing signals, extracts the fields a quoter actually needs (NSN, part number, quantity), dedupes them into SQLite, and exports CSV reports plus a tiered console digest.

It was built to mirror a real freelance job scope: *monitor DLA/DIBBS-style RFQs, identify solicitations that support online pricing, and extract NSN/part/qty line-item data for quoting.*

- **Demo mode runs 100% offline** on bundled sample data — no API key, no network.
- **Live mode** queries the SAM.gov Opportunities API v2 with a free api.data.gov key.
- Pure, unit-tested detection/extraction logic; pluggable source interface for future adapters (e.g. authenticated DIBBS).

---

## Problem

Small defense contractors live or die by how fast they find *quotable* solicitations. The raw firehose (SAM.gov, DIBBS) mixes thousands of notices — IT services RFPs, construction, grants — with the small minority that matter to a parts supplier: DLA-style RFQs for specific NSNs where pricing can be submitted (often automatically) online.

Doing this by hand means a buyer skimming dozens of postings a day, copying NSNs and quantities into a spreadsheet, and guessing which notices have pricing history available. It is slow, error-prone, and duplicates pile up every time a solicitation is amended.

**The job to be done:** given a stream of solicitations, answer three questions automatically —

1. *Is this in my lane?* (PSC/FSC filter)
2. *Can I price it online?* (pricing-signal detection)
3. *What exactly am I quoting?* (NSN / part number / quantity extraction)

## Approach

```
                        ┌─────────────────────┐
  SAM.gov API v2 ─────▶ │  sources/samgov.py  │ ─┐
  (live, api.data.gov)  └─────────────────────┘  │   raw dicts
                        ┌─────────────────────┐  │
  sample JSON ────────▶ │  sources/sample.py  │ ─┘
  (offline demo)        └─────────────────────┘
                                   │
                                   ▼  normalize (sources/base.py)
              ┌───────────────────────────────────────────┐
              │ extract.py: NSN / part number / quantity  │
              │ detect.py:  pricing-score + flags (0–100) │
              └───────────────────────────────────────────┘
                                   │ Solicitation records
                                   ▼
                        ┌─────────────────────┐     ┌────────────────┐
                        │ store.py (SQLite)   │ ──▶ │ report.py      │
                        │ upsert + dedupe on  │     │ CSV export +   │
                        │ sol_number          │     │ tiered digest  │
                        └─────────────────────┘     └────────────────┘
```

The pipeline is four small stages, each independently testable:

| Stage | Module | Responsibility |
|---|---|---|
| **Source** | `sources/base.py`, `sources/samgov.py`, `sources/sample.py` | Pluggable adapters behind one `fetch()/normalize()` interface. Demo and live run the *same* pipeline. |
| **Extract** | `extract.py` | Pure regex extractors: NSN (`####-##-###-####`), part numbers after `P/N`-style labels, quantities after `qty/each/ea` labels. Deduped, order-preserving. |
| **Detect** | `detect.py` | Pure scoring function: weighted keyword families + attachment bonus, capped at 100. |
| **Store / Report** | `store.py`, `report.py` | SQLite upsert keyed on `sol_number` (amendments update, never duplicate); CSV export and a tiered console digest. |

### Detection logic

`score_pricing(text, attachments) -> (score, flags)` looks for four keyword families, weighted by how strongly they predict quotability:

| Flag | Example keywords | Weight |
|---|---|---|
| `online_pricing` | "online pricing", "enter your quote", "automated evaluation" | 40 |
| `historical_pricing` | "historical pricing", "price history", "prior award", "last paid" | 25 |
| `quote_requested` | "request for quotation", "rfq", "quote due" | 20 |
| `competitive` | "competitive", "full and open" | 10 |
| `pricing_attachment` | attachment named like `pricing_worksheet.xlsx` | +15 |

Scores add up and cap at 100; the digest tiers them **High (≥70) / Medium (40–69) / Low (<40)**. Matching is case-insensitive with word boundaries (so "rfq" can't match inside other tokens), and the function is pure — no I/O — which is why the test suite can pin down its exact behavior.

## Results

`python -m govscout demo --csv out/demo.csv --digest` (offline, zero setup):

```
Source: sample | fetched 10 records (1 duplicate collapsed)
Store:  9 new, 1 updated — 9 total in govscout.db
CSV:    wrote 10 rows to out/demo.csv

================================================================
GovScout digest: 10 new solicitations, 8 with pricing signals
================================================================

HIGH priority (score >= 70) — 3
----------------------------------------------------------------
  [100] SPE4A7-25-R-0412 — Bracket, Structural Component, Aircraft (DLA Aviation)  NSN: 5340-01-234-5678  P/N: BACB10FM4  qty: 250
  [100] SPE4A7-25-R-0412 — Bracket, Structural Component, Aircraft (AMENDMENT 0001) (DLA Aviation)  NSN: 5340-01-234-5678  P/N: BACB10FM4  qty: 300
  [ 80] SPE4A7-25-R-0510 — Connector, Plug, Electrical, Circular (DLA Aviation)  NSN: 5935-01-789-0123  P/N: MS3476L14-19S  qty: 500

MEDIUM priority (score 40-69) — 2
----------------------------------------------------------------
  [ 60] SPE8EF-25-T-2210 — Screw, Cap, Hexagon Head (DLA Troop Support)  NSN: 5305-00-153-8234  P/N: MS90725-8  qty: 10000
  [ 45] SPE4A6-25-R-1187 — Microcircuit, Digital (CMOS Hex Inverter) (DLA Maritime)  NSN: 5962-01-456-7890  P/N: SN74HC04N  qty: 48
  ...
```

The bundled sample (10 records, 9 unique) deliberately includes an **amended duplicate** — note the two `SPE4A7-25-R-0412` rows above: the amendment updates the stored row in place instead of creating a duplicate. An IT services RFP and an engineering-services notice correctly score **0**, proving the detector doesn't cry wolf on non-hardware solicitations.

CSV excerpt (`out/demo.csv`):

```csv
sol_number,title,agency,psc_code,posted_date,...,nsns,part_numbers,quantities,pricing_score,pricing_flags,...
SPE4A7-25-R-0412,"Bracket, Structural Component, Aircraft",DLA Aviation,5340,2025-02-18,...,5340-01-234-5678,BACB10FM4,250,100,"online_pricing; historical_pricing; quote_requested; competitive; pricing_attachment",...
SPE4A6-25-R-1187,"Microcircuit, Digital (CMOS Hex Inverter)",DLA Maritime,5962,2025-02-20,...,5962-01-456-7890,SN74HC04N,48,45,historical_pricing; quote_requested,...
```

## How to run

Requires Python 3.11+.

```bash
pip install -r requirements.txt

# 1. Offline demo — no key, no network
python -m govscout demo --csv out/demo.csv --digest

# 2. Digest of whatever is already in the local DB
python -m govscout digest

# 3. Live mode (free key: https://api.data.gov/signup/ or SAM.gov Workspace > API Key)
export SAM_API_KEY=your_key_here
python -m govscout fetch --csv out/live.csv --digest
#   ...or per-invocation:
python -m govscout fetch --api-key your_key_here

# 4. Configure filters (PSC/FSC codes, lookback window, paths)
python -m govscout init-config        # writes config.json
python -m govscout fetch --config config.json --digest
```

Example `config.json`:

```json
{ "psc_codes": ["5340", "5962"], "days_back": 30, "db_path": "govscout.db", "output_dir": "out", "max_pages": 1 }
```

### Tests

```bash
pytest -q     # 58 tests: detection, extraction, storage/dedupe, end-to-end demo pipeline, SAM.gov pagination cap
```

No test touches the network; the demo-mode smoke test runs the whole CLI against a temp directory.

## Design notes

- **Same interface for demo and live.** `SampleSource` and `SamGovSource` both implement `Source.fetch()/normalize()`, so the offline demo exercises the real pipeline — not a scripted mock of it.
- **Dedupe at the storage layer.** `UNIQUE(sol_number)` + upsert means amendments update in place and reruns are idempotent; `first_seen` is preserved so `new_since()` can power incremental digests.
- **Polite live client.** 0.5 s between pages, 30 s timeouts, exponential backoff on 429/5xx, clear error (with signup instructions) when the API key is missing.
- **Quota-capped pagination.** `SamGovSource` stops after `max_pages` pages (100 records each); `Config.max_pages` defaults to 1 so a normal `fetch` run spends exactly one request. Personal api.data.gov keys without an entity role are limited to ~10 requests/day — one request per run leaves headroom for retries/manual runs. An entity role raises the quota to ~1000/day; raise `max_pages` in `config.json` once you have one.
- **Pure core logic.** Detection and extraction are side-effect-free functions with pinned unit tests — the part of the system most likely to evolve per client is the part easiest to change safely.

## Roadmap

- **DIBBS authenticated adapter** — new `Source` subclass for DLA's Internet Bid Board System (quote status, award history behind login).
- **Email/Slack alerts** — scheduled `fetch` + `new_since()` digests pushed to the buyer.
- **Dashboard** — lightweight web UI over the SQLite store (score trends, win/loss tracking per NSN).
- **Smarter extraction** — line-item tables (multiple NSN/qty pairs per notice) and CLIN parsing.
