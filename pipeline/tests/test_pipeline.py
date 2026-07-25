"""End-to-end demo-mode smoke test (fully offline, tmp working dir)."""

import csv
import json

import pytest

from govscout.__main__ import main
from govscout.report import TIER_HIGH, TIER_MEDIUM
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
