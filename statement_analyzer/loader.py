"""Load bank statement transactions from CSV or JSON files.

Every loader returns a list of transactions in the same shape:

    {"date": datetime (Africa/Nairobi), "description": str, "amount": float}

Negative amounts are money going out (debits); positive amounts are money in (credits).
If the file has a "type" column (IN/OUT), it decides the sign instead.

Rows that can't be parsed are skipped and logged as warnings with their line number,
so one bad row doesn't stop the whole statement from loading.
"""

import csv
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

try:
    from zoneinfo import ZoneInfo

    NAIROBI = ZoneInfo("Africa/Nairobi")
except Exception:  # zoneinfo data missing (common on Windows without `pip install tzdata`)
    # Nairobi is UTC+3 all year round (no daylight saving), so a fixed offset is exact.
    NAIROBI = timezone(timedelta(hours=3), "EAT")

# Tried in order after ISO 8601. Day-first comes before month-first, so 05/11/2026 = 5 Nov.
DATE_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y/%m/%d",
    "%d/%m/%Y %I:%M %p",     # 02/01/2026 7:06 PM
    "%d/%m/%Y %I:%M:%S %p",
    "%d/%m/%Y %H:%M:%S",
    "%d/%m/%Y %H:%M",
    "%d/%m/%Y",
    "%d-%m-%Y %H:%M",
    "%d-%m-%Y",
    "%m/%d/%Y",
    "%d %b %Y",   # 05 Nov 2026
    "%d %B %Y",   # 05 November 2026
    "%B %d %Y",   # March 14 2026
    "%B %d, %Y",  # March 14, 2026
    "%b %d %Y",   # Mar 14 2026
    "%b %d, %Y",  # Mar 14, 2026
)

# Alternative column/key names we accept, mapped to our standard field names.
FIELD_ALIASES = {
    "date": ("date", "transaction_date", "txn_date", "posted", "datetime", "timestamp"),
    "description": ("description", "details", "narration", "merchant", "memo"),
    "amount": ("amount", "value", "amt"),
    "type": ("type", "direction"),
}

# Values of the optional "type" column, mapped to the sign they give the amount.
DIRECTIONS = {"IN": 1, "CREDIT": 1, "CR": 1, "OUT": -1, "DEBIT": -1, "DR": -1}


class LoaderError(Exception):
    """Raised when a statement file can't be read or a record is invalid."""


def parse_date(value):
    """Parse a date/datetime string into a timezone-aware Nairobi datetime.

    Values without a timezone are taken to be Nairobi local time. Values that
    carry one (e.g. '2026-11-01T07:00:00Z') are converted to Nairobi time.
    Date-only values become midnight Nairobi time.
    """
    text = str(value).strip()
    parsed = None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        for fmt in DATE_FORMATS:
            try:
                parsed = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue
    if parsed is None:
        raise LoaderError(f"unrecognised date format: {value!r}")

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=NAIROBI)
    return parsed.astimezone(NAIROBI)


def parse_amount(value):
    """Parse an amount such as '1,250.00', '-45.5', '(45.00)' or 45.5 into a float."""
    if isinstance(value, bool):
        raise LoaderError(f"invalid amount: {value!r}")
    if isinstance(value, (int, float)):
        return float(value)
    cleaned = str(value).strip().replace(",", "")
    # Treat accounting-style "(45.00)" as negative.
    if cleaned.startswith("(") and cleaned.endswith(")"):
        cleaned = "-" + cleaned[1:-1]
    try:
        return float(cleaned)
    except ValueError:
        raise LoaderError(f"invalid amount: {value!r}") from None


def _has_field(record, field):
    """True if the record has a key for this field at all (even if it's blank)."""
    keys = {str(k).strip().lower() for k in record if k is not None}
    return any(alias in keys for alias in FIELD_ALIASES[field])


def _get_field(record, field):
    """Find a field in a record using its aliases (case-insensitive)."""
    lowered = {str(k).strip().lower(): v for k, v in record.items() if k is not None}
    for alias in FIELD_ALIASES[field]:
        value = lowered.get(alias)
        if value is not None and str(value).strip() != "":
            return value
    raise LoaderError(f"missing '{field}'")


def _apply_direction(record, amount):
    """Use the IN/OUT "type" column, if the file has one, to set the amount's sign."""
    if not _has_field(record, "type"):
        return amount
    direction = str(_get_field(record, "type")).strip().upper()
    if direction not in DIRECTIONS:
        raise LoaderError(f"unknown type {direction!r} (expected IN or OUT)")
    return abs(amount) * DIRECTIONS[direction]


def normalise(record):
    """Convert a raw dict (from CSV or JSON) into a standard transaction."""
    if not isinstance(record, dict):
        raise LoaderError(f"expected an object, got {type(record).__name__}")
    return {
        "date": parse_date(_get_field(record, "date")),
        "description": str(_get_field(record, "description")).strip(),
        "amount": _apply_direction(record, parse_amount(_get_field(record, "amount"))),
    }


def _normalise_all(numbered_records, source):
    """Normalise (line_number, record) pairs, skipping and logging bad ones."""
    transactions = []
    skipped = 0
    for line_no, record in numbered_records:
        try:
            transactions.append(normalise(record))
        except LoaderError as exc:
            skipped += 1
            logger.warning("%s line %d: skipped row (%s)", source, line_no, exc)
    if skipped:
        logger.warning("%s: loaded %d rows, skipped %d", source, len(transactions), skipped)
    return transactions


def load_csv(path):
    """Load transactions from a CSV file with a header row."""
    path = Path(path)
    try:
        with path.open(newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            if not reader.fieldnames:
                raise LoaderError(f"{path} is empty or has no header row")
            # reader.line_num is the file line the row ended on (1 = header).
            rows = [
                (reader.line_num, row)
                for row in reader
                if any((v or "").strip() for v in row.values() if isinstance(v, str))
            ]
    except FileNotFoundError:
        raise LoaderError(f"File not found: {path}") from None
    return _normalise_all(rows, path.name)


def _skip_ws(text, pos):
    while pos < len(text) and text[pos] in " \t\r\n":
        pos += 1
    return pos


def _find_transactions_array(text, decoder):
    """Return the index of the '[' that opens the transactions list.

    Handles a top-level list, or a top-level object with a "transactions" key.
    """
    pos = _skip_ws(text, 0)
    if text.startswith("[", pos):
        return pos
    if not text.startswith("{", pos):
        return None
    pos = _skip_ws(text, pos + 1)
    while pos < len(text) and text[pos] != "}":
        key, pos = decoder.raw_decode(text, pos)
        pos = _skip_ws(text, pos) + 1  # skip ':'
        pos = _skip_ws(text, pos)
        if key == "transactions":
            return pos if text.startswith("[", pos) else None
        _, pos = decoder.raw_decode(text, pos)  # skip this value
        pos = _skip_ws(text, pos)
        if text.startswith(",", pos):
            pos = _skip_ws(text, pos + 1)
    return None


def _json_records_with_lines(text):
    """Yield (line_number, record) for each item in the transactions list."""
    decoder = json.JSONDecoder()
    start = _find_transactions_array(text, decoder)
    if start is None:
        raise LoaderError("must contain a list of transactions")
    pos = _skip_ws(text, start + 1)
    while pos < len(text) and text[pos] != "]":
        line_no = text.count("\n", 0, pos) + 1
        record, pos = decoder.raw_decode(text, pos)
        yield line_no, record
        pos = _skip_ws(text, pos)
        if text.startswith(",", pos):
            pos = _skip_ws(text, pos + 1)


def load_json(path):
    """Load transactions from a JSON file.

    Accepts either a list of transactions, or an object with a
    "transactions" key holding that list.
    """
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        raise LoaderError(f"File not found: {path}") from None
    try:
        json.loads(text)  # validate the whole file first for a clear error message
    except json.JSONDecodeError as exc:
        raise LoaderError(f"{path} is not valid JSON: {exc}") from None
    try:
        records = list(_json_records_with_lines(text))
    except LoaderError as exc:
        raise LoaderError(f"{path} {exc}") from None
    return _normalise_all(records, path.name)


LOADERS = {".csv": load_csv, ".json": load_json}


def load_statement(path):
    """Load a statement, picking the loader from the file extension."""
    suffix = Path(path).suffix.lower()
    loader = LOADERS.get(suffix)
    if loader is None:
        supported = ", ".join(LOADERS)
        raise LoaderError(f"Unsupported file type '{suffix}'. Supported: {supported}")
    return loader(path)


def main(argv=None):
    """Run as a script: load a statement and print what was read."""
    import sys

    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("usage: python statement_analyzer/loader.py FILE", file=sys.stderr)
        return 2
    logging.basicConfig(level=logging.WARNING, format="warning: %(message)s")
    try:
        transactions = load_statement(args[0])
    except LoaderError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"Loaded {len(transactions)} transactions from {Path(args[0]).name}\n")
    print(f"{'Date (Nairobi)':<25}  {'Amount':>12}  Description")
    for txn in transactions[:10]:
        print(f"{txn['date'].isoformat():<25}  {txn['amount']:>12,.2f}  {txn['description']}")
    if len(transactions) > 10:
        print(f"... and {len(transactions) - 10} more")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
