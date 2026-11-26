"""WooCommerce tax rates audit CLI.

Reads a WooCommerce tax rates CSV export and audits every rate
for the mistakes that make a store charge the wrong tax: the same
rate entered twice for one location, impossible rates, zero rates
on taxable classes, compound rates that stack too high, postcode
patterns that can never match, shipping that is never taxed,
rates with no name, priority conflicts, and malformed rows.

An optional orders file (--orders-file) lists the countries real
orders came from, so a country with orders but no rate is
flagged as a missing rate instead of being found by a customer.

Each problem is a finding with a code, a rule name, a severity
(error or warning), and a message. Errors are the ones that
charge the wrong tax or break the table; warnings are patterns
worth investigating. --check exits 1 when any error is found,
and --strict also fails on warnings.

Rates are handled with Decimal throughout, so effective stacked
rates are exact and never float-rounded.

Zero dependencies, Python 3.10 or newer, standard library only.

Usage:
    python3 woo_tax_doctor.py examples/taxes.csv
    python3 woo_tax_doctor.py taxes.csv --orders-file orders.csv --json --check
"""

import argparse
import csv
import sys
from decimal import Decimal, InvalidOperation

__version__ = "1.0.0"

CENT = Decimal("0.01")
DEFAULT_MAX_RATE = 30.0
ZERO_RATE_CLASS = "zero-rate"

# Header names, normalised by lowercasing and stripping every
# character that is not a letter or digit, mapped to the field
# this tool uses. WooCommerce exports "Country code",
# "Postcode / ZIP", and "Rate %"; a hand-built CSV may use the
# snake_case names instead. Both work.
HEADER_ALIASES = {
    "countrycode": "country",
    "country": "country",
    "billingcountry": "country",
    "shipcountry": "country",
    "shippingcountry": "country",
    "statecode": "state",
    "state": "state",
    "postcodezip": "postcode",
    "postcode": "postcode",
    "zip": "postcode",
    "city": "city",
    "rate": "rate",
    "taxname": "tax_name",
    "priority": "priority",
    "compound": "compound",
    "shipping": "shipping",
    "taxclass": "tax_class",
}

TRUE_VALUES = ("1", "yes", "true", "y")
FALSE_VALUES = ("0", "no", "false", "n", "")


class Finding:
    """One audit finding for one rate row, location, or pattern."""

    def __init__(self, code, rule, severity, message):
        self.code = code
        self.rule = rule
        self.severity = severity
        self.message = message

    def as_dict(self):
        """Return the finding as a plain dict."""
        return {"code": self.code, "rule": self.rule,
                "severity": self.severity, "message": self.message}

    def __str__(self):
        return "%s [%s] %s: %s" % (self.severity.upper(), self.rule,
                                   self.code, self.message)


def normalise_header(name):
    """Lowercase a header and strip every non-alphanumeric char."""
    return "".join(ch for ch in str(name).strip().lower()
                   if ch.isalnum())


def parse_rate(value, code):
    """Parse a rate percent as Decimal, or return None when blank.

    Strips a trailing % and thousands separators. Raises
    ValueError on anything else.
    """
    if value is None:
        return None
    text = str(value).strip().replace("%", "").replace(",", "")
    if not text:
        return None
    try:
        return Decimal(text)
    except InvalidOperation:
        raise ValueError("rate is not a number: %r" % value)


def parse_flag(value, code, field):
    """Parse a 0/1 style flag, or return False when blank.

    Accepts 1, yes, true, y and 0, no, false, n. Raises
    ValueError on anything else.
    """
    if value is None:
        return False
    text = str(value).strip().lower()
    if text in TRUE_VALUES:
        return True
    if text in FALSE_VALUES:
        return False
    raise ValueError("%s is not a 0/1 flag: %r" % (field, value))


def parse_priority(value, code):
    """Parse a priority, defaulting a blank one to 1.

    Raises ValueError when the value is not a positive integer.
    """
    if value is None:
        return 1
    text = str(value).strip()
    if not text:
        return 1
    try:
        priority = int(text)
    except ValueError:
        raise ValueError("priority is not a whole number: %r"
                         % value)
    if priority < 1:
        raise ValueError("priority %d is below 1" % priority)
    return priority


def check_postcode(pattern, code):
    """Check one postcode pattern. Returns a list of Findings.

    WooCommerce postcode patterns are an exact postcode, a
    wildcard such as 902*, or a range such as 90210...90299.
    A range with its start above its end can never match, a
    range with an empty end cannot match, and characters outside
    letters, digits, wildcard, dots, dash, and space are not
    part of any postcode.
    """
    findings = []
    text = (pattern or "").strip()
    if not text or text == "*":
        return findings
    if "..." in text:
        start, end = text.split("...", 1)
        start = start.strip()
        end = end.strip()
        if not start or not end:
            findings.append(Finding(
                code, "postcode-range-malformed", "error",
                "postcode range %r has an empty end" % pattern))
        elif start.isdigit() and end.isdigit():
            if int(start) > int(end):
                findings.append(Finding(
                    code, "postcode-range-reversed", "error",
                    "postcode range %s...%s starts above its end"
                    % (start, end)))
        elif start > end:
            findings.append(Finding(
                code, "postcode-range-reversed", "error",
                "postcode range %s...%s starts above its end"
                % (start, end)))
        return findings
    if ".." in text:
        findings.append(Finding(
            code, "postcode-pattern-invalid", "error",
            "postcode pattern %r has '..' instead of a '...' range"
            % pattern))
        return findings
    for char in text:
        if not (char.isalnum() or char in "*.- "):
            findings.append(Finding(
                code, "postcode-pattern-invalid", "error",
                "postcode pattern %r contains %r, which no "
                "postcode contains" % (pattern, char)))
            return findings
    return findings


def location_label(country, state, postcode, city):
    """Build a short location label for messages."""
    parts = [country or "*"]
    if state:
        parts.append(state)
    if postcode:
        parts.append(postcode)
    if city:
        parts.append(city)
    return "/".join(parts)


def audit_row(row, seen_keys, max_rate):
    """Audit one CSV row. Returns (findings, parsed row info).

    The parsed info is a dict with the normalised fields, used
    by the cross-row checks in audit().
    """
    findings = []
    country = str(row.get("country") or "").strip().upper()
    state = str(row.get("state") or "").strip().upper()
    postcode = str(row.get("postcode") or "").strip().upper()
    city = str(row.get("city") or "").strip()
    tax_class = str(row.get("tax_class") or "").strip().lower() \
        or "standard"
    tax_name = str(row.get("tax_name") or "").strip()
    label = location_label(country, state, postcode, city)
    code = "%s %s%%" % (label, row.get("rate") or "?")
    display = code

    if not country:
        findings.append(Finding(
            display, "missing-country", "error",
            "country code is empty, so this rate applies nowhere "
            "on purpose"))
    elif country != "*" and (len(country) != 2
                             or not country.isalpha()):
        findings.append(Finding(
            display, "invalid-country-code", "error",
            "country code %r is not a 2-letter code or '*'"
            % country))

    if state and state != "*":
        for char in state:
            if not (char.isalnum() or char in "- "):
                findings.append(Finding(
                    display, "invalid-state-code", "warning",
                    "state code %r contains %r" % (state, char)))
                break

    findings.extend(check_postcode(postcode, display))

    if not tax_name:
        findings.append(Finding(
            display, "missing-tax-name", "warning",
            "no tax name, so customers see a blank tax line at "
            "checkout"))

    try:
        rate = parse_rate(row.get("rate"), display)
    except ValueError as exc:
        findings.append(Finding(display, "invalid-rate", "error",
                                str(exc)))
        rate = None
    if rate is None and row.get("rate") is not None \
            and not str(row.get("rate")).strip():
        findings.append(Finding(display, "missing-rate", "error",
                                "rate is empty"))
    if rate is not None:
        if rate < 0 or rate > 100:
            findings.append(Finding(
                display, "invalid-rate-range", "error",
                "rate %s%% is outside 0 to 100" % rate))
        elif rate > Decimal(str(max_rate)):
            findings.append(Finding(
                display, "high-rate", "warning",
                "rate %s%% is above the %.1f%% threshold, check "
                "it is not a typo" % (rate, max_rate)))
        if rate == 0 and tax_class != ZERO_RATE_CLASS:
            findings.append(Finding(
                display, "zero-rate-taxable-class", "warning",
                "rate is 0%% on the %s class, so orders in %s "
                "are never taxed" % (tax_class, label)))

    try:
        priority = parse_priority(row.get("priority"), display)
    except ValueError as exc:
        findings.append(Finding(display, "invalid-priority",
                                "error", str(exc)))
        priority = None
    try:
        compound = parse_flag(row.get("compound"), display,
                              "compound")
    except ValueError as exc:
        findings.append(Finding(display, "invalid-compound",
                                "error", str(exc)))
        compound = False
    try:
        shipping = parse_flag(row.get("shipping"), display,
                              "shipping")
    except ValueError as exc:
        findings.append(Finding(display, "invalid-shipping",
                                "error", str(exc)))
        shipping = False

    # Duplicate rows and priority conflicts share one key: the
    # same place, class, and priority. The same rate twice is a
    # duplicate; a different rate at the same priority means the
    # two rows fight over which one applies.
    if country and priority is not None:
        key = (country, state, postcode, city.lower(), tax_class,
               priority)
        if key in seen_keys:
            first_rate, first_code = seen_keys[key]
            if rate is not None and first_rate is not None \
                    and rate == first_rate:
                findings.append(Finding(
                    display, "duplicate-rate-row", "error",
                    "same location, class, and priority as %s "
                    "with the same rate %s%%"
                    % (first_code, rate)))
            else:
                findings.append(Finding(
                    display, "priority-conflict", "error",
                    "same location, class, and priority as %s "
                    "but a different rate, so only one of them "
                    "can apply" % first_code))
        else:
            seen_keys[key] = (rate, display)

    info = {"country": country, "state": state,
            "postcode": postcode, "city": city,
            "tax_class": tax_class, "tax_name": tax_name,
            "rate": rate, "priority": priority,
            "compound": compound, "shipping": shipping,
            "label": label, "code": display}
    return findings, info


def audit(rows, max_rate, orders=None):
    """Audit all CSV rows.

    Returns (findings, report) where report holds the per-country
    table, the per-class table, and the orders coverage when an
    orders file was given.
    """
    findings = []
    seen_keys = {}
    infos = []
    any_shipping = False

    for row in rows:
        row_findings, info = audit_row(row, seen_keys, max_rate)
        findings.extend(row_findings)
        infos.append(info)
        if info["shipping"]:
            any_shipping = True

    if infos and not any_shipping:
        findings.append(Finding(
            "file", "shipping-tax-missing", "warning",
            "no rate applies to shipping, so shipping is never "
            "taxed on any order"))

    # Compound stacking: within one place and class, compound
    # rates apply on top of the subtotal plus the other rates,
    # so the effective rate is higher than the sum. Flag a group
    # whose effective rate passes the --max-rate threshold, and
    # flag groups with more than one compound rate.
    groups = {}
    for info in infos:
        key = (info["country"], info["state"], info["postcode"],
               info["city"].lower(), info["tax_class"])
        groups.setdefault(key, []).append(info)
    for key in sorted(groups):
        members = groups[key]
        compound_rows = [m for m in members if m["compound"]]
        if not compound_rows:
            continue
        valid = [m for m in members if m["rate"] is not None]
        base = sum((m["rate"] for m in valid if not m["compound"]),
                   Decimal("0"))
        compound_sum = sum((m["rate"] for m in valid
                            if m["compound"]), Decimal("0"))
        label = members[0]["label"]
        if len(compound_rows) > 1:
            findings.append(Finding(
                label, "multiple-compound-rates", "warning",
                "%d compound rates apply to %s, so the stacking "
                "order decides the tax" % (len(compound_rows),
                                           label)))
        effective = base + compound_sum * (Decimal("1")
                                           + base / Decimal("100"))
        if effective > Decimal(str(max_rate)):
            findings.append(Finding(
                label, "compound-stack-high", "warning",
                "compound rates stack to an effective %s%% in "
                "%s, above the %.1f%% threshold"
                % (effective.quantize(CENT), label, max_rate)))

    # Orders coverage: every country with orders needs at least
    # one rate row for that country, or a wildcard "*" row.
    orders_report = None
    if orders is not None:
        countries = {}
        for order in orders:
            code = str(order.get("country") or "").strip().upper()
            if code:
                countries[code] = countries.get(code, 0) + 1
        covered = {code for code in countries
                   if any(info["country"] == code
                          or info["country"] == "*"
                          for info in infos)}
        for code in sorted(countries):
            if code not in covered:
                findings.append(Finding(
                    code, "missing-rate-for-order-country", "error",
                    "no tax rate covers %s, but %d order%s came "
                    "from there"
                    % (code, countries[code],
                       "" if countries[code] == 1 else "s")))
        orders_report = [{
            "country": code,
            "orders": countries[code],
            "covered": code in covered,
        } for code in sorted(countries)]

    countries = {}
    classes = {}
    for info in infos:
        country = info["country"] or "(blank)"
        entry = countries.setdefault(
            country, {"rates": 0, "max_rate": None})
        entry["rates"] += 1
        if info["rate"] is not None:
            if entry["max_rate"] is None \
                    or info["rate"] > entry["max_rate"]:
                entry["max_rate"] = info["rate"]
        entry = classes.setdefault(
            info["tax_class"], {"rates": 0, "max_rate": None})
        entry["rates"] += 1
        if info["rate"] is not None:
            if entry["max_rate"] is None \
                    or info["rate"] > entry["max_rate"]:
                entry["max_rate"] = info["rate"]

    country_rows = [{
        "country": country,
        "rates": countries[country]["rates"],
        "max_rate": str(countries[country]["max_rate"])
        if countries[country]["max_rate"] is not None else "-",
    } for country in sorted(countries)]
    class_rows = [{
        "tax_class": tax_class,
        "rates": classes[tax_class]["rates"],
        "max_rate": str(classes[tax_class]["max_rate"])
        if classes[tax_class]["max_rate"] is not None else "-",
    } for tax_class in sorted(classes)]

    report = {
        "rates": len(infos),
        "countries": country_rows,
        "classes": class_rows,
        "orders": orders_report,
    }
    return findings, report


def summarize(findings, report):
    """Count findings by severity."""
    errors = sum(1 for f in findings if f.severity == "error")
    warnings = sum(1 for f in findings if f.severity == "warning")
    return {"rates": report["rates"],
            "findings": len(findings), "errors": errors,
            "warnings": warnings}


def rate_str(value):
    """Format a rate for the tables, with a % sign."""
    if value == "-":
        return "-"
    return "%s%%" % value


def format_text(findings, report, summary):
    """Render findings, tables, and the summary as text."""
    lines = [str(f) for f in findings]
    lines.append("")
    lines.append("Rates by country:")
    for row in report["countries"]:
        lines.append("  %-10s %3d %s  max %s"
                     % (row["country"][:10], row["rates"],
                        "rate" if row["rates"] == 1 else "rates",
                        rate_str(row["max_rate"])))
    lines.append("")
    lines.append("Rates by tax class:")
    for row in report["classes"]:
        lines.append("  %-14s %3d %s  max %s"
                     % (row["tax_class"][:14], row["rates"],
                        "rate" if row["rates"] == 1 else "rates",
                        rate_str(row["max_rate"])))
    if report["orders"] is not None:
        lines.append("")
        lines.append("Orders by country (covered by a rate?):")
        for row in report["orders"]:
            lines.append("  %-10s %3d %s  %s"
                         % (row["country"][:10], row["orders"],
                            "order" if row["orders"] == 1
                            else "orders",
                            "covered" if row["covered"]
                            else "NOT COVERED"))
    lines.append("")
    lines.append("Rates: %d, findings: %d, errors: %d, warnings: %d"
                 % (summary["rates"], summary["findings"],
                    summary["errors"], summary["warnings"]))
    if summary["errors"] == 0 and summary["warnings"] == 0:
        lines.append("All tax rates clean.")
    return "\n".join(lines) + "\n"


def read_csv(path, aliases=None):
    """Read a CSV export, normalising headers via aliases.

    Returns a list of row dicts keyed by the canonical field
    names. Extra columns are ignored. Raises ValueError when
    the file has no header row or a required column is missing.
    """
    with open(path, newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError("%s has no header row" % path)
        mapping = {}
        for name in reader.fieldnames:
            key = HEADER_ALIASES.get(normalise_header(name))
            if key:
                mapping[name] = key
        rows = []
        for raw in reader:
            row = {}
            for name, key in mapping.items():
                row[key] = raw.get(name)
            rows.append(row)
        return rows, mapping


def read_rates(path):
    """Read a tax rates CSV export. Returns a list of row dicts."""
    rows, mapping = read_csv(path)
    for required in ("country", "rate"):
        if required not in mapping.values():
            raise ValueError(
                "%s has no %s column" % (path, required))
    return rows


def read_orders(path):
    """Read an orders CSV. Returns a list of row dicts."""
    rows, mapping = read_csv(path)
    if "country" not in mapping.values():
        raise ValueError(
            "%s has no billing country column" % path)
    return rows


def parse_args(argv):
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Audit a WooCommerce tax rates CSV export.")
    parser.add_argument("input", help="tax rates CSV export file")
    parser.add_argument("--orders-file", default=None,
                        help="orders CSV with a billing country "
                        "column, to flag countries with orders "
                        "but no rate")
    parser.add_argument("--max-rate", type=float,
                        default=DEFAULT_MAX_RATE,
                        help="flag rates above this percent, and "
                        "compound stacks above it (default: 30.0)")
    parser.add_argument("--json", action="store_true",
                        help="emit JSON instead of text")
    parser.add_argument("--check", action="store_true",
                        help="exit 1 if any error is found")
    parser.add_argument("--strict", action="store_true",
                        help="with --check, also exit 1 on warnings")
    parser.add_argument("--out", default=None,
                        help="write the report to a file")
    return parser.parse_args(argv)


def main(argv=None):
    """Entry point."""
    args = parse_args(argv if argv is not None else sys.argv[1:])
    if args.max_rate < 0 or args.max_rate > 100:
        print("ERROR: --max-rate must be between 0 and 100, got %r"
              % args.max_rate, file=sys.stderr)
        return 2
    try:
        rows = read_rates(args.input)
    except OSError as exc:
        print("ERROR: cannot read %s: %s" % (args.input, exc),
              file=sys.stderr)
        return 2
    except ValueError as exc:
        print("ERROR: %s" % exc, file=sys.stderr)
        return 2
    orders = None
    if args.orders_file:
        try:
            orders = read_orders(args.orders_file)
        except OSError as exc:
            print("ERROR: cannot read %s: %s"
                  % (args.orders_file, exc), file=sys.stderr)
            return 2
        except ValueError as exc:
            print("ERROR: %s" % exc, file=sys.stderr)
            return 2
    findings, report = audit(rows, args.max_rate, orders=orders)
    summary = summarize(findings, report)
    if args.json:
        import json
        text = json.dumps(
            {"summary": summary, "countries": report["countries"],
             "classes": report["classes"],
             "orders": report["orders"],
             "findings": [f.as_dict() for f in findings]},
            indent=2)
    else:
        text = format_text(findings, report, summary)
    if args.out:
        try:
            with open(args.out, "w", encoding="utf-8") as handle:
                handle.write(text)
        except OSError as exc:
            print("ERROR: cannot write %s: %s" % (args.out, exc),
                  file=sys.stderr)
            return 2
    else:
        sys.stdout.write(text)
    if args.check and (summary["errors"]
                       or (args.strict and summary["warnings"])):
        return 1
    return 0


def main_entry():
    """Console script entry point."""
    sys.exit(main())


if __name__ == "__main__":
    main_entry()
