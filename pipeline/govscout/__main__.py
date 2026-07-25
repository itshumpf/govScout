"""GovScout CLI — fetch, filter, score, store, and report on solicitations.

Usage:
    python -m govscout demo [--config PATH] [--csv [PATH]] [--json [PATH]] [--digest]
    python -m govscout fetch (--api-key KEY | env SAM_API_KEY) [--config PATH] [--csv [PATH]] [--json [PATH]] [--digest]
    python -m govscout digest [--config PATH]
    python -m govscout init-config [PATH]
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from . import __version__
from .config import Config, load_config, save_config
from .report import export_csv, export_json, render_digest
from .sources.base import Source
from .sources.samgov import API_KEY_HELP, SamGovError, SamGovSource
from .sources.sample import SampleSource
from .store import Store


def _run_pipeline(
    source: Source,
    config: Config,
    csv_path: str | None,
    json_path: str | None,
    want_digest: bool,
    source_label: str,
) -> int:
    """Shared fetch -> normalize -> store -> report pipeline for demo/fetch."""
    raw = source.fetch(config.psc_codes, config.days_back)
    sols = [source.normalize(record) for record in raw]
    unique = {sol.sol_number for sol in sols}

    with Store(config.db_path) as store:
        new, updated = store.upsert_many(sols)
        total = store.count()

    dupes = len(sols) - len(unique)
    print(
        f"Source: {source.name} | fetched {len(raw)} records "
        f"({dupes} duplicate{'s' if dupes != 1 else ''} collapsed)"
    )
    print(f"Store:  {new} new, {updated} updated — {total} total in {config.db_path}")

    if csv_path:
        out = export_csv(sols, csv_path)
        print(f"CSV:    wrote {len(sols)} rows to {out}")
    if json_path:
        out = export_json(sols, json_path, source_label, stored=total)
        print(f"JSON:   wrote {len(sols)} records to {out}")
    if want_digest:
        print()
        print(render_digest(sols))
    return 0


def _default_export(config: Config, stem: str, ext: str) -> str:
    return str(Path(config.output_dir) / f"{stem}.{ext}")


def _export_path(value: str | None, config: Config, stem: str, ext: str) -> str | None:
    """Resolve an optional --csv/--json argument (empty string = default path)."""
    if value == "":
        return _default_export(config, stem, ext)
    return value or None


def cmd_demo(args: argparse.Namespace) -> int:
    """Run the full pipeline offline against bundled sample data."""
    config = load_config(args.config)
    return _run_pipeline(
        SampleSource(),
        config,
        csv_path=_export_path(args.csv, config, "demo", "csv"),
        json_path=_export_path(args.json, config, "demo", "json"),
        want_digest=args.digest,
        source_label="demo",
    )


def cmd_fetch(args: argparse.Namespace) -> int:
    """Run the pipeline against the live SAM.gov Opportunities API."""
    api_key = args.api_key or os.environ.get("SAM_API_KEY")
    if not api_key:
        print(API_KEY_HELP, file=sys.stderr)
        return 2
    config = load_config(args.config)
    try:
        return _run_pipeline(
            SamGovSource(api_key, max_pages=config.max_pages),
            config,
            csv_path=_export_path(args.csv, config, "fetch", "csv"),
            json_path=_export_path(args.json, config, "fetch", "json"),
            want_digest=args.digest,
            source_label="sam.gov",
        )
    except SamGovError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


def cmd_digest(args: argparse.Namespace) -> int:
    """Render a digest from the existing local database."""
    config = load_config(args.config)
    with Store(config.db_path) as store:
        sols = store.all()
    if not sols:
        print(f"No solicitations in {config.db_path} — run 'demo' or 'fetch' first.")
        return 0
    print(render_digest(sols))
    return 0


def cmd_init_config(args: argparse.Namespace) -> int:
    """Write an example config file the user can edit."""
    path = args.path or "config.json"
    target = Path(path)
    if target.exists():
        print(f"Refusing to overwrite existing {target}", file=sys.stderr)
        return 1
    save_config(Config(psc_codes=["5340", "5962"]), target)
    print(f"Wrote {target} — edit psc_codes/days_back, then run 'fetch' or 'demo'.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Build the argparse CLI parser with demo/fetch/digest/init-config subcommands."""
    parser = argparse.ArgumentParser(
        prog="govscout",
        description="Monitor U.S. federal solicitations, detect pricing signals, export reports.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--config", metavar="PATH", default=None, help="JSON config file (default: config.json if present)")
        p.add_argument(
            "--csv",
            metavar="PATH",
            nargs="?",
            const="",
            default=None,
            help="export results to CSV (PATH optional; defaults to out/<command>.csv)",
        )
        p.add_argument(
            "--json",
            metavar="PATH",
            nargs="?",
            const="",
            default=None,
            help="export dashboard-ready JSON (PATH optional; defaults to out/<command>.json)",
        )
        p.add_argument("--digest", action="store_true", help="print a tiered console digest")

    p_demo = sub.add_parser("demo", help="offline demo using bundled sample data")
    add_common(p_demo)
    p_demo.set_defaults(func=cmd_demo)

    p_fetch = sub.add_parser("fetch", help="live mode via SAM.gov Opportunities API v2")
    add_common(p_fetch)
    p_fetch.add_argument("--api-key", metavar="KEY", default=None, help="api.data.gov key (or set SAM_API_KEY)")
    p_fetch.set_defaults(func=cmd_fetch)

    p_digest = sub.add_parser("digest", help="render digest from the existing database")
    p_digest.add_argument("--config", metavar="PATH", default=None, help="JSON config file")
    p_digest.set_defaults(func=cmd_digest)

    p_init = sub.add_parser("init-config", help="write an example config file")
    p_init.add_argument("path", nargs="?", default=None, help="target path (default: config.json)")
    p_init.set_defaults(func=cmd_init_config)

    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns a process exit code."""
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
