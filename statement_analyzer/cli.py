"""Command-line interface for the statement analyzer.

Usage (from the project folder):
    python -m statement_analyzer summary statement_sample.csv
    python -m statement_analyzer monthly statement_sample.csv --month 2026-03
"""

import argparse
import logging
import re
import sys
from pathlib import Path

try:
    from .categoriser import DEFAULT_RULES_PATH, RulesError, categorise_all, group_by_category, load_rules
    from .loader import LoaderError, load_statement
except ImportError:  # run directly as `python statement_analyzer/cli.py`
    from categoriser import DEFAULT_RULES_PATH, RulesError, categorise_all, group_by_category, load_rules
    from loader import LoaderError, load_statement


def month_arg(value):
    """argparse type for --month: accepts YYYY-MM and returns it unchanged."""
    match = re.fullmatch(r"(\d{4})-(\d{2})", value)
    if not match or not 1 <= int(match.group(2)) <= 12:
        raise argparse.ArgumentTypeError(f"invalid month {value!r}, expected YYYY-MM (e.g. 2026-03)")
    return value


def money(amount):
    return f"{amount:,.2f}"


def print_report(title, transactions):
    """Print money in/out, net, and a per-category breakdown."""
    print(title)
    print("=" * len(title))

    if not transactions:
        print("No transactions.")
        return

    money_in = sum(t["amount"] for t in transactions if t["amount"] > 0)
    money_out = sum(t["amount"] for t in transactions if t["amount"] < 0)
    print(f"Transactions: {len(transactions)}")
    print(f"Money in:     {money(money_in):>15}")
    print(f"Money out:    {money(money_out):>15}")
    print(f"Net:          {money(money_in + money_out):>15}")
    print()

    groups = group_by_category(transactions)
    totals = {cat: sum(t["amount"] for t in txns) for cat, txns in groups.items()}
    # Biggest spending first, income at the bottom.
    width = max(len("Category"), *(len(c) for c in totals))
    print(f"{'Category':<{width}}  {'Count':>5}  {'Total':>15}")
    print(f"{'-' * width}  {'-' * 5}  {'-' * 15}")
    for cat in sorted(totals, key=totals.get):
        print(f"{cat:<{width}}  {len(groups[cat]):>5}  {money(totals[cat]):>15}")


def cmd_summary(args, transactions):
    if transactions:
        first = min(t["date"] for t in transactions).date()
        last = max(t["date"] for t in transactions).date()
        title = f"Summary: {Path(args.file).name} ({first} to {last})"
    else:
        title = f"Summary: {Path(args.file).name}"
    print_report(title, transactions)


def cmd_monthly(args, transactions):
    in_month = [t for t in transactions if t["date"].strftime("%Y-%m") == args.month]
    print_report(f"Monthly report: {args.month} ({Path(args.file).name})", in_month)


def build_parser():
    parser = argparse.ArgumentParser(
        prog="python -m statement_analyzer",
        description="Analyse a bank statement (CSV or JSON) by spending category.",
        epilog="Categories come from rules.json. Dates are in Nairobi time (EAT, UTC+3).",
    )
    parser.add_argument(
        "--rules",
        default=DEFAULT_RULES_PATH,
        metavar="PATH",
        help="rules file (default: rules.json in the project folder)",
    )
    parser.add_argument(
        "-q", "--quiet", action="store_true", help="don't print warnings about skipped rows"
    )
    sub = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    summary = sub.add_parser(
        "summary",
        help="totals for the whole statement",
        description="Show money in, money out, net, and totals per category for the whole statement.",
    )
    summary.add_argument("file", metavar="FILE", help="statement file (.csv or .json)")
    summary.set_defaults(func=cmd_summary)

    monthly = sub.add_parser(
        "monthly",
        help="totals for one month",
        description="Show money in, money out, net, and totals per category for one month.",
    )
    monthly.add_argument("file", metavar="FILE", help="statement file (.csv or .json)")
    monthly.add_argument(
        "--month", required=True, type=month_arg, metavar="YYYY-MM", help="month to report on, e.g. 2026-03"
    )
    monthly.set_defaults(func=cmd_monthly)

    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.ERROR if args.quiet else logging.WARNING,
        format="warning: %(message)s",
    )

    try:
        rules = load_rules(args.rules)
        transactions = categorise_all(load_statement(args.file), rules)
    except (LoaderError, RulesError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    args.func(args, transactions)
    return 0


if __name__ == "__main__":
    sys.exit(main())
