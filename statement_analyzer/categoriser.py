"""Sort transactions into categories using regex rules from rules.json.

To add or change a category, edit rules.json (in the project folder) -
no code changes needed. Two formats are accepted.

Simple - one regex per category:

    {
      "Groceries": "SUPERMARKET|GENERAL STORES",
      "Airtime": "^Bought airtime"
    }

Extended - several patterns per category and a custom default:

    {
      "default": "Uncategorised",
      "rules": [
        {"category": "Groceries", "patterns": ["supermarket", "general stores"]},
        ...
      ]
    }

- Patterns are Python regular expressions matched anywhere in the
  transaction description, ignoring case.
- Rules are checked top to bottom and the first match wins, so put more
  specific rules above general ones (e.g. "Safaricom Home" before "Safaricom").
- Transactions that match nothing get the "default" category.
"""

import json
import re
from pathlib import Path

# rules.json lives in the project folder, one level above this package.
DEFAULT_RULES_PATH = Path(__file__).resolve().parent.parent / "rules.json"
DEFAULT_CATEGORY = "Uncategorised"


class RulesError(Exception):
    """Raised when rules.json is missing or malformed."""


class Rules:
    """Compiled categorisation rules."""

    def __init__(self, rules, default=DEFAULT_CATEGORY):
        # rules: list of (category, [compiled regex, ...]) in priority order
        self.rules = rules
        self.default = default

    def categorise(self, description):
        """Return the category for a description string."""
        text = description or ""
        for category, patterns in self.rules:
            if any(p.search(text) for p in patterns):
                return category
        return self.default

    @property
    def categories(self):
        """All category names, in rule order, plus the default."""
        names = list(dict.fromkeys(category for category, _ in self.rules))
        if self.default not in names:
            names.append(self.default)
        return names


def load_rules(path=DEFAULT_RULES_PATH):
    """Load and compile rules from a JSON file.

    Raises RulesError with a clear message if the file or any rule is invalid,
    so a typo in rules.json is caught before any transactions are processed.
    """
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        raise RulesError(f"Rules file not found: {path}") from None
    except json.JSONDecodeError as exc:
        raise RulesError(f"{path} is not valid JSON: {exc}") from None

    if not isinstance(data, dict):
        raise RulesError(f"{path} must be a JSON object")
    if "rules" not in data:
        # Simple format: {"Category": "regex" or ["regex", ...], ...}
        data = {
            "default": DEFAULT_CATEGORY,
            "rules": [{"category": cat, "patterns": pats} for cat, pats in data.items()],
        }
    if not isinstance(data["rules"], list):
        raise RulesError(f'{path}: "rules" must be a list')

    default = data.get("default", DEFAULT_CATEGORY)
    if not isinstance(default, str) or not default.strip():
        raise RulesError(f'{path}: "default" must be a non-empty string')

    compiled = []
    for index, rule in enumerate(data["rules"], start=1):
        where = f"{path.name} rule {index}"
        if not isinstance(rule, dict):
            raise RulesError(f"{where}: each rule must be an object")

        category = rule.get("category")
        if not isinstance(category, str) or not category.strip():
            raise RulesError(f'{where}: "category" must be a non-empty string')
        where = f"{where} ({category})"

        patterns = rule.get("patterns")
        if isinstance(patterns, str):
            patterns = [patterns]
        if not isinstance(patterns, list) or not patterns:
            raise RulesError(f'{where}: "patterns" must be a non-empty list of strings')

        regexes = []
        for pattern in patterns:
            if not isinstance(pattern, str):
                raise RulesError(f"{where}: pattern {pattern!r} is not a string")
            try:
                regexes.append(re.compile(pattern, re.IGNORECASE))
            except re.error as exc:
                raise RulesError(f"{where}: invalid regex {pattern!r}: {exc}") from None

        compiled.append((category.strip(), regexes))

    return Rules(compiled, default.strip())


def categorise_all(transactions, rules=None):
    """Return copies of the transactions with a "category" key added.

    Loads rules.json from next to this file if no rules are given.
    """
    if rules is None:
        rules = load_rules()
    return [
        {**txn, "category": rules.categorise(txn.get("description", ""))}
        for txn in transactions
    ]


def group_by_category(transactions):
    """Group categorised transactions into {category: [transactions]}."""
    groups = {}
    for txn in transactions:
        groups.setdefault(txn["category"], []).append(txn)
    return groups


def main(argv=None):
    """Run as a script: categorise a statement and show the result."""
    import logging
    import sys

    try:
        from .loader import LoaderError, load_statement
    except ImportError:  # run directly as `python statement_analyzer/categoriser.py`
        from loader import LoaderError, load_statement

    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("usage: python statement_analyzer/categoriser.py FILE", file=sys.stderr)
        return 2
    logging.basicConfig(level=logging.ERROR, format="warning: %(message)s")
    try:
        rules = load_rules()
        transactions = categorise_all(load_statement(args[0]), rules)
    except (LoaderError, RulesError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"Rules: {DEFAULT_RULES_PATH.name} ({len(rules.rules)} categories)\n")
    print(f"{'Category':<15}  {'Count':>5}  Example")
    for category, txns in sorted(group_by_category(transactions).items(), key=lambda kv: -len(kv[1])):
        print(f"{category:<15}  {len(txns):>5}  {txns[0]['description']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
