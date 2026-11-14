"""Command line entry point: ``python -m radar <command>``.

Commands
  stock    check stock of the watchlist products (every few hours)
  catalog  snapshot of all product URLs in the sitemap (daily)
  stores   addresses, coordinates and opening hours of all stores (weekly)
  postcodes centre point of every 4-digit postcode area, CBS/PDOK (weekly)
  reference stores + postcodes
  all      catalog + stock
  sample   write sample data (no network needed)
  check    show the configuration

Examples
  python -m radar check
  python -m radar stock --dry-run
  python -m radar sample --days 30 --out ./landing_local
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys

from radar import postcodes, rituals
from radar.config import Config, load_config
from radar.http_client import PoliteSession
from radar.landing import LandingWriter
from radar.matching import matcher_from_config
from radar.sample_data import write_sample_data
from radar.util import new_run_id, utc_now

log = logging.getLogger("radar")


def _add_common(parser: argparse.ArgumentParser, *, top_level: bool) -> None:
    # The same options are accepted before and after the command name. On the
    # sub-commands the defaults are SUPPRESSed so they don't overwrite values
    # given before the command.
    def default(value):
        return value if top_level else argparse.SUPPRESS

    parser.add_argument("--config", default=default(os.environ.get("RADAR_CONFIG", "config/radar.toml")),
                        help="path to radar.toml (default: config/radar.toml)")
    parser.add_argument("--out", default=default(os.environ.get("RADAR_LANDING_PATH")),
                        help="landing target: a local folder or a /Volumes/... path (default: landing.volume_path)")
    parser.add_argument("--dry-run", action="store_true", default=default(False),
                        help="collect, but print a summary instead of writing")
    parser.add_argument("-v", "--verbose", action="store_true", default=default(False))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m radar", description="Rituals Radar stock collectors")
    _add_common(parser, top_level=True)
    common = argparse.ArgumentParser(add_help=False)
    _add_common(common, top_level=False)

    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("stock", parents=[common], help="check stock of the watchlist products")
    sub.add_parser("catalog", parents=[common], help="snapshot of all product URLs in the sitemap")
    sub.add_parser("stores", parents=[common], help="addresses and coordinates of all stores")
    sub.add_parser("postcodes", parents=[common], help="centre of every 4-digit postcode area (CBS/PDOK)")
    sub.add_parser("reference", parents=[common], help="stores + postcodes")
    sub.add_parser("all", parents=[common], help="catalog + stock")
    sample = sub.add_parser("sample", parents=[common], help="write sample data (no network)")
    sample.add_argument("--days", type=int, default=30)
    sample.add_argument("--seed", type=int, default=42)
    sub.add_parser("check", parents=[common], help="show the configuration")
    return parser


class Runner:
    def __init__(self, cfg: Config, writer: LandingWriter | None):
        self.cfg = cfg
        self.writer = writer
        self.matcher = matcher_from_config(cfg)
        self.now = utc_now().replace(microsecond=0)
        self.run_id = new_run_id()
        self.failures: list[str] = []

    def _emit(self, dataset: str, records: list[dict]) -> None:
        if self.writer is None:
            print(f"[dry-run] {dataset}: {len(records)} records")
            if records:
                print(json.dumps(records[0], indent=2, ensure_ascii=False))
            return
        self.writer.write(dataset, records, run_id=self.run_id, when=self.now)

    def catalog(self) -> None:
        try:
            session = rituals.make_session(self.cfg)
            records = rituals.fetch_catalog(self.cfg, session, self.matcher, now=self.now, run_id=self.run_id)
            self._emit("rituals_catalog", records)
        except Exception:
            log.exception("Catalog collection failed")
            self.failures.append("catalog")

    def stock(self) -> None:
        try:
            session = rituals.make_session(self.cfg)
            records = rituals.collect_stock(self.cfg, session, self.matcher, now=self.now, run_id=self.run_id)
            self._emit("rituals_stock", records)  # failed checks are landed too (-> quarantine table)
            if records and all(r["parse_status"] not in ("ok", "not_found") for r in records):
                log.error("Every product check failed - is rituals.com blocking us or did the page change?")
                self.failures.append("stock")
        except Exception:
            log.exception("Stock collection failed")
            self.failures.append("stock")


    def stores(self) -> None:
        try:
            session = rituals.make_session(self.cfg)
            self._emit("rituals_stores", rituals.collect_stores(self.cfg, session, now=self.now, run_id=self.run_id))
        except Exception:
            log.exception("Store collection failed")
            self.failures.append("stores")

    def postcodes(self) -> None:
        try:
            session = PoliteSession(self.cfg.site.user_agent, delay_seconds=1.0, respect_robots_txt=False)
            self._emit("reference_pc4", postcodes.collect_postcodes(session, now=self.now, run_id=self.run_id))
        except Exception:
            log.exception("Postcode collection failed")
            self.failures.append("postcodes")


def _check(cfg: Config, out: str | None) -> None:
    present = lambda name: "set" if os.environ.get(name) else "missing"  # noqa: E731
    target = out or cfg.volume_path
    print(f"Config:            {cfg.path}")
    print(f"Landing target:    {target} (mode: {LandingWriter(target).mode})")
    print(f"Site:              {cfg.site.base_url}/{cfg.site.locale} (delay {cfg.site.delay_seconds}s)")
    print(f"Watchlist:         {len(cfg.watchlist.products)} products, collections={list(cfg.watchlist.collections)}")
    print(f"Collections:       {', '.join(cfg.collections)}")
    print(f"Databricks upload: DATABRICKS_HOST {present('DATABRICKS_HOST')}, "
          f"DATABRICKS_TOKEN {present('DATABRICKS_TOKEN')}")


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s", stream=sys.stderr)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    cfg = load_config(args.config)

    if args.command == "check":
        _check(cfg, args.out)
        return 0

    writer = None if args.dry_run else LandingWriter(args.out or cfg.volume_path)

    if args.command == "sample":
        if writer is None:
            print("--dry-run is not supported for 'sample'; use --out ./some_folder instead")
            return 2
        counts = write_sample_data(cfg, writer, days=args.days, seed=args.seed)
        for key, count in sorted(counts.items()):
            print(f"{key:<20} {count:>6} records")
        return 0

    runner = Runner(cfg, writer)
    log.info("Run %s started (%s)", runner.run_id, args.command)
    if args.command in ("catalog", "all"):
        runner.catalog()
    if args.command in ("stock", "all"):
        runner.stock()
    if args.command in ("stores", "reference"):
        runner.stores()
    if args.command in ("postcodes", "reference"):
        runner.postcodes()

    if runner.failures:
        log.error("Run %s finished with failures: %s", runner.run_id, ", ".join(runner.failures))
        return 1
    log.info("Run %s finished OK", runner.run_id)
    return 0
