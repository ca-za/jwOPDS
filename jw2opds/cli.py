from __future__ import annotations

import argparse
import logging

from .config import load_config
from .sync import run_sync


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="jw2opds",
        description="Generate a static OPDS catalog of jw.org EPUB publications.",
    )
    parser.add_argument("--config", default="config.yaml", help="Path to config.yaml")
    parser.add_argument(
        "--lang",
        action="append",
        help="Only sync this language (locale or jw code). Repeatable. "
        "Overrides the 'languages' list in config.yaml.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-check every candidate publication, ignoring the recheck_days cache.",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )

    cfg = load_config(args.config)
    run_sync(cfg, only_languages=args.lang, force=args.force)


if __name__ == "__main__":
    main()
