"""End-to-end demo-mode smoke test (fully offline, tmp working dir)."""

import csv
import json

import pytest

from govscout.__main__ import _run_pipeline, main
from govscout.config import Config
from govscout.report import TIER_HIGH, TIER_MEDIUM
from govscout.sources.sample import SampleSource
from govscout.store import Store


@pytest.fixture
def workdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return tmp_path


class TestDemoPipeline:
    def test_demo_runs_offline_end_to_end(self, workdir, capsys):
        csv_path = workdir / "out" / "demo.csv"
        rc = main(["demo", "--csv", str(csv_path), "--digest"])
        assert rc == 0

        out = capsys.readouterr().out
        assert "fetched 10 records" in out
        assert "1 duplicate collapsed" in out
        assert "9 new" in out
        assert "pricing signals" in out

        # Dedupe: 10 sample records, 9 unique solicitation numbers.
        with Store(workdir / "govscout.db") as store:
            assert store.count() == 9

        # CSV has header + 10 rows (the duplicate is written as fetched).
        with csv_path.open(newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        assert len(rows) == 10
        assert {"sol_number", "pricing_score", "nsns", "quantities"} <= set(rows[0])

    def test_demo_is_idempotent_via_upsert(self, workdir, capsys):
        assert main(["demo"]) == 0
        capsys.readouterr()
        assert main(["demo"]) == 0
        out = capsys.readouterr().out
        # All 10 records (incl. the deliberate duplicate) upsert over existing rows.
        assert "0 new, 10 updated" in out
        with Store(workdir / "govscout.db") as store:
            assert store.count() == 9

    def test_demo_psc_filter_via_config(self, workdir, capsys):
        (workdir / "config.json").write_text(
            '{"psc_codes": ["5340"], "days_back": 30, "db_path": "filtered.db", "output_dir": "out"}'
        )
        assert main(["demo"]) == 0
        capsys.readouterr()
        with Store(workdir / "filtered.db") as store:
            sols = store.all()
        assert len(sols) == 1  # 5340 appears twice but is one unique solicitation
        assert sols[0].sol_number == "SPE4A7-25-R-0412"

    def test_demo_extraction_and_scoring_sane(self, workdir):
        main(["demo"])
        with Store(workdir / "govscout.db") as store:
            sols = {s.sol_number: s for s in store.all()}
        bracket = sols["SPE4A7-25-R-0412"]
        assert bracket.pricing_score == 100
        assert bracket.nsns == ["5340-01-234-5678"]
        assert bracket.part_numbers == ["BACB10FM4"]
        assert bracket.quantities  # amendment bumped qty; at least one quantity present
        it_rfp = sols["W91QVN-25-R-0045"]
        assert it_rfp.pricing_score == 0
        assert it_rfp.nsns == []


class TestJsonExport:
    def test_demo_json_export_contract(self, workdir, capsys):
        json_path = workdir / "out" / "dash.json"
        rc = main(["demo", "--json", str(json_path)])
        assert rc == 0
        assert "JSON:" in capsys.readouterr().out

        data = json.loads(json_path.read_text(encoding="utf-8"))  # valid JSON
        assert set(data) == {"generated_at", "source", "stats", "solicitations"}
        assert data["source"] == "demo"
        # ISO-8601 timestamp, parseable and timezone-aware.
        from datetime import datetime

        assert datetime.fromisoformat(data["generated_at"]).tzinfo is not None

        stats = data["stats"]
        assert set(stats) == {
            "fetched",
            "stored",
            "with_pricing_signals",
            "high",
            "medium",
            "low",
            "by_weekday",
            "by_fsc",
        }
        assert stats["fetched"] == 10
        assert stats["stored"] == 9  # unique rows in the DB after dedupe

        sols = data["solicitations"]
        assert len(sols) == stats["fetched"]

    def test_demo_json_tiers_counted_correctly(self, workdir):
        json_path = workdir / "dash.json"
        main(["demo", "--json", str(json_path)])
        data = json.loads(json_path.read_text(encoding="utf-8"))
        stats, sols = data["stats"], data["solicitations"]

        expected = {"high": 0, "medium": 0, "low": 0}
        for sol in sols:
            score = sol["pricing_score"]
            if score >= TIER_HIGH:
                expected["high"] += 1
            elif score >= TIER_MEDIUM:
                expected["medium"] += 1
            else:
                expected["low"] += 1
        assert {t: stats[t] for t in expected} == expected
        assert sum(expected.values()) == stats["fetched"]
        # Sample data exercises all three tiers.
        assert all(n > 0 for n in expected.values())
        assert stats["with_pricing_signals"] == sum(
            1 for s in sols if s["pricing_flags"]
        )

    def test_demo_json_solicitation_rows(self, workdir):
        json_path = workdir / "dash.json"
        main(["demo", "--json", str(json_path)])
        sols = json.loads(json_path.read_text(encoding="utf-8"))["solicitations"]

        # Sorted by pricing_score descending.
        scores = [s["pricing_score"] for s in sols]
        assert scores == sorted(scores, reverse=True)

        for sol in sols:
            # to_row() fields are all present; pricing_flags is a real list.
            assert isinstance(sol["pricing_flags"], list)
            assert {
                "sol_number",
                "title",
                "agency",
                "psc_code",
                "posted_date",
                "response_deadline",
                "url",
                "nsns",
                "part_numbers",
                "quantities",
                "pricing_score",
                "description",
                "fetched_at",
            } <= set(sol)

        top = sols[0]
        assert top["sol_number"] == "SPE4A7-25-R-0412"
        assert "online_pricing" in top["pricing_flags"]

    def test_demo_json_default_path(self, workdir):
        assert main(["demo", "--json"]) == 0
        data = json.loads((workdir / "out" / "demo.json").read_text(encoding="utf-8"))
        assert data["stats"]["fetched"] == 10


class TestOtherCommands:
    def test_digest_from_existing_db(self, workdir, capsys):
        main(["demo"])
        capsys.readouterr()
        assert main(["digest"]) == 0
        out = capsys.readouterr().out
        assert "GovScout digest" in out
        assert "HIGH priority" in out

    def test_digest_empty_db(self, workdir, capsys):
        assert main(["digest"]) == 0
        assert "No solicitations" in capsys.readouterr().out

    def test_init_config(self, workdir, capsys):
        assert main(["init-config"]) == 0
        assert (workdir / "config.json").exists()
        assert main(["init-config"]) == 1  # refuses to overwrite

    def test_fetch_without_key_errors_gracefully(self, workdir, capsys, monkeypatch):
        monkeypatch.delenv("SAM_API_KEY", raising=False)
        assert main(["fetch"]) == 2
        err = capsys.readouterr().err
        assert "api.data.gov" in err

    def test_fetch_respects_cross_run_rate_limit_backoff(self, workdir, capsys, monkeypatch):
        """cmd_fetch wires config.rate_limit_state_path through to SamGovSource —
        a recent 429 recorded there must block a second `fetch` without it
        ever touching the network, same as the direct SamGovSource tests
        prove at the unit level."""
        from govscout import ratelimit
        from govscout.sources.samgov import SamGovSource

        monkeypatch.setenv("SAM_API_KEY", "x")
        state_path = workdir / "rate_limit_state.json"
        (workdir / "config.json").write_text(
            json.dumps({"rate_limit_state_path": str(state_path)}), encoding="utf-8"
        )
        ratelimit.record_429(state_path)  # simulate a 429 from an earlier run

        calls = []
        real_get = SamGovSource._get

        def spying_get(self, params):
            calls.append(1)
            return real_get(self, params)

        monkeypatch.setattr(SamGovSource, "_get", spying_get)

        assert main(["fetch"]) == 1
        assert calls == []  # blocked before any request was made
        assert "cooling down" in capsys.readouterr().err


class TestSyncCommand:
    """CLI wiring for `sync` — the coverage-ledger mechanism end to end,
    with SamGovSource stubbed so nothing touches the network."""

    class _FakeSamGovSource:
        """Stub SamGovSource: same constructor/fetch_range/normalize shape."""

        def __init__(self, api_key, session=None, max_pages=1, state_path=None):
            self.api_key = api_key
            self.max_pages = max_pages
            self.state_path = state_path

        def fetch_range(self, psc_codes, posted_from, posted_to):
            code = psc_codes[0]
            return [
                {
                    "sol_number": f"{code}-{posted_from.isoformat()}-1",
                    "title": "Bracket",
                    "agency": "DLA Aviation",
                    "psc_code": code,
                    "posted_date": posted_from.isoformat(),
                    "response_deadline": "2099-01-01",
                    "description": "",
                    "url": None,
                    "attachments": [],
                }
            ]

        def normalize(self, raw):
            from govscout.sources.base import enrich_solicitation

            return enrich_solicitation(raw)

    def test_sync_without_key_errors_gracefully(self, workdir, capsys, monkeypatch):
        monkeypatch.delenv("SAM_API_KEY", raising=False)
        assert main(["sync"]) == 2
        assert "api.data.gov" in capsys.readouterr().err

    def test_sync_without_tracked_codes_errors_gracefully(self, workdir, capsys, monkeypatch):
        monkeypatch.setenv("SAM_API_KEY", "x")
        (workdir / "config.json").write_text(json.dumps({"tracked_codes": []}), encoding="utf-8")
        assert main(["sync"]) == 2
        assert "tracked_codes" in capsys.readouterr().err

    def test_sync_writes_ledger_and_json_end_to_end(self, workdir, monkeypatch):
        monkeypatch.setenv("SAM_API_KEY", "x")
        monkeypatch.setattr("govscout.__main__.SamGovSource", self._FakeSamGovSource)
        config = {
            "tracked_codes": ["5340"],
            "lookback_months": 2,
            "ledger_path": "coverage_ledger.csv",
            "default_slices_per_run": 1,
            "max_pages": 1,
        }
        (workdir / "config.json").write_text(json.dumps(config), encoding="utf-8")
        json_path = str(workdir / "dashboard.json")

        rc = main(["sync", "--json", json_path])
        assert rc == 0

        ledger_rows = list(csv.DictReader((workdir / "coverage_ledger.csv").open(encoding="utf-8")))
        assert len(ledger_rows) == 2  # lookback_months=2, one code
        fetched = [r for r in ledger_rows if r["last_fetched"]]
        assert len(fetched) == 1  # default_slices_per_run=1 — only one advanced

        data = json.loads((workdir / "dashboard.json").read_text(encoding="utf-8"))
        assert len(data["solicitations"]) == 1
        assert data["solicitations"][0]["slice_key"] in {r["code"] + ":" + r["year"] + "-" + r["month"].zfill(2) for r in fetched}

    def test_sync_rerun_selects_the_other_slice_next(self, workdir, monkeypatch):
        monkeypatch.setenv("SAM_API_KEY", "x")
        monkeypatch.setattr("govscout.__main__.SamGovSource", self._FakeSamGovSource)
        config = {
            "tracked_codes": ["5340"],
            "lookback_months": 2,
            "ledger_path": "coverage_ledger.csv",
            "default_slices_per_run": 1,
            "max_pages": 1,
        }
        (workdir / "config.json").write_text(json.dumps(config), encoding="utf-8")

        main(["sync", "--json", str(workdir / "dashboard.json")])
        first_fetched = {r["code"] + r["year"] + r["month"] for r in csv.DictReader((workdir / "coverage_ledger.csv").open(encoding="utf-8")) if r["last_fetched"]}

        main(["sync", "--json", str(workdir / "dashboard.json")])
        second_fetched = {r["code"] + r["year"] + r["month"] for r in csv.DictReader((workdir / "coverage_ledger.csv").open(encoding="utf-8")) if r["last_fetched"]}

        # Second run advances the OTHER slice, not the same one again —
        # proves nulls-first selection is really driving successive runs.
        assert second_fetched != first_fetched
        assert len(second_fetched) == 2  # both slices now fetched across the two runs


class TestAccumulateWiring:
    """Exercises _run_pipeline's accumulate=True path (the same wiring cmd_fetch
    uses) through the real CLI pipeline, offline via SampleSource, to prove
    successive "weekday group" runs build up rather than replace the board.

    The bundled sample fixtures carry 2025 response deadlines, so "today" is
    pinned to a date in that window — otherwise the freshness pass (correctly)
    drops every fixture row as expired, which isn't what these tests probe.
    """

    def test_successive_runs_with_different_psc_groups_accumulate(self, workdir, monkeypatch):
        import datetime as _dt

        monkeypatch.setattr("govscout.report._utc_today", lambda: _dt.date(2025, 3, 1))
        config = Config(days_back=30, db_path="a.db", output_dir="out", max_pages=1)
        json_path = str(workdir / "dashboard.json")

        # "Monday": one PSC group.
        rc = _run_pipeline(
            SampleSource(),
            config,
            psc_codes=["5340"],
            csv_path=None,
            json_path=json_path,
            want_digest=False,
            source_label="sam.gov",
            accumulate=True,
        )
        assert rc == 0
        data = json.loads((workdir / "dashboard.json").read_text(encoding="utf-8"))
        first_ids = {s["sol_number"] for s in data["solicitations"]}
        assert first_ids  # non-empty

        # "Tuesday": a different PSC group, same dashboard file.
        rc = _run_pipeline(
            SampleSource(),
            config,
            psc_codes=["5962"],
            csv_path=None,
            json_path=json_path,
            want_digest=False,
            source_label="sam.gov",
            accumulate=True,
        )
        assert rc == 0
        data = json.loads((workdir / "dashboard.json").read_text(encoding="utf-8"))
        second_ids = {s["sol_number"] for s in data["solicitations"]}

        # Tuesday's group is additive, not a replacement of Monday's.
        assert first_ids < second_ids
        assert second_ids - first_ids  # Tuesday actually added something new

    def test_plain_export_json_still_overwrites_for_demo_mode(self, workdir):
        # accumulate=False (demo's default) must keep the old snapshot behavior.
        config = Config(days_back=30, db_path="a.db", output_dir="out", max_pages=1)
        json_path = str(workdir / "demo.json")
        _run_pipeline(
            SampleSource(), config, psc_codes=["5340"], csv_path=None,
            json_path=json_path, want_digest=False, source_label="demo",
        )
        first = json.loads((workdir / "demo.json").read_text(encoding="utf-8"))
        first_ids = {s["sol_number"] for s in first["solicitations"]}
        assert first_ids == {"SPE4A7-25-R-0412"}  # the 5340 fixture (incl. its amendment record)

        _run_pipeline(
            SampleSource(), config, psc_codes=["5962"], csv_path=None,
            json_path=json_path, want_digest=False, source_label="demo",
        )
        second = json.loads((workdir / "demo.json").read_text(encoding="utf-8"))
        second_ids = {s["sol_number"] for s in second["solicitations"]}
        # Overwritten, not merged: only the second run's PSC group is present.
        assert second_ids == {"SPE4A6-25-R-1187"}
        assert second_ids != first_ids
