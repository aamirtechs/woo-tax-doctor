# woo-tax-doctor

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://github.com/aamirtechs/woo-tax-doctor/blob/main/LICENSE) [![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](https://github.com/aamirtechs/woo-tax-doctor/issues)

A zero-dependency Python CLI that audits a WooCommerce tax rates
CSV export for the mistakes that make a store charge the wrong
tax: the same rate entered twice for one location, impossible
rates, zero rates on taxable classes, compound rates that stack
too high, postcode patterns that can never match, shipping that
is never taxed, rates with no name, priority conflicts, and
malformed rows.

WooCommerce ships a troubleshooting guide for core taxes because
manual rate tables go wrong so often: rates that apply to the
wrong place, compound rates that stack, priorities that fight,
and postcodes that never match. The usual answer is reading the
whole table by hand, row by row. This tool is the offline audit:
it reads the exported CSV, flags every problem with a rule name
and a severity, and, with `--orders-file`, flags the countries
real orders came from that have no rate at all. It never touches
your store.

## Install

Requires Python 3.10 or newer. No dependencies beyond the standard
library.

```bash
pip install woo-tax-doctor
```

Or run it straight from the source checkout:

```bash
python3 woo_tax_doctor.py examples/taxes.csv
```

## Input format

A CSV export with a header row. Columns used: `Country code`,
`State code`, `Postcode / ZIP`, `City`, `Rate %`, `Tax name`,
`Priority`, `Compound`, `Shipping`, `Tax class`. Snake_case
headers (`country_code`, `postcode`, `rate`, `tax_name`) work
too, and extra columns are ignored, so a full WooCommerce
export works as-is. Rates accept a trailing `%` and thousands
separators. Compound and shipping accept 1, yes, true, y and
0, no, false, n. A UTF-8 BOM header is handled. Rates are
handled with Decimal throughout, so stacked compound rates are
exact and never float-rounded.

```csv
Country code,State code,Postcode / ZIP,City,Rate %,Tax name,Priority,Compound,Shipping,Tax class
US,CA,90210,Beverly Hills,9.5,CA State Tax,1,0,1,standard
GB,,SW1A 1AA,London,20.0,UK VAT,1,0,0,standard
DE,,10115,Berlin,19.0,German VAT,1,0,0,standard
```

## What it checks

Errors, the ones that charge the wrong tax or break the table:

* `duplicate-rate-row`: the same location, tax class, and
  priority entered twice with the same rate.
* `priority-conflict`: the same location, class, and priority
  with a different rate, so only one of them can apply.
* `invalid-rate-range`: a rate below 0 or above 100 percent.
* `invalid-rate`, `missing-rate`: an unparseable or empty rate.
* `invalid-priority`: a priority that is not a whole number
  of 1 or more.
* `postcode-range-reversed`: a range such as 90210...90000
  whose start is above its end, so it can never match.
* `postcode-range-malformed`, `postcode-pattern-invalid`:
  a range with an empty end, `..` instead of `...`, or a
  character no postcode contains.
* `missing-country`, `invalid-country-code`: an empty country
  code, or one that is not a 2-letter code or `*`.
* `invalid-compound`, `invalid-shipping`: a compound or
  shipping flag that is not a 0/1 value.
* `missing-rate-for-order-country`: with `--orders-file`, a
  country real orders came from that no rate covers.

Warnings, patterns worth investigating:

* `zero-rate-taxable-class`: a 0 percent rate on a taxable
  class (standard or reduced-rate), so orders there are never
  taxed. A 0 percent rate on the `zero-rate` class is clean.
* `high-rate`: a rate above `--max-rate` (default 30.0),
  usually a typo such as 35 entered for 3.5.
* `compound-stack-high`: compound rates in one place and class
  that stack to an effective rate above `--max-rate`. The
  effective rate is the base rates plus the compound rates
  applied on top of the subtotal plus the base rates.
* `multiple-compound-rates`: more than one compound rate in
  one place and class, so the stacking order decides the tax.
* `missing-tax-name`: no tax name, so customers see a blank
  tax line at checkout.
* `shipping-tax-missing`: no rate applies to shipping, so
  shipping is never taxed on any order.
* `invalid-state-code`: a state code containing a character
  no state code contains.

The report also prints two tables after the findings: rates by
country (count and highest rate) and rates by tax class. With
`--orders-file` it adds an orders-by-country table showing
whether each country is covered by a rate.

## Usage

```bash
python3 woo_tax_doctor.py examples/taxes.csv
```

Captured output:

```
ERROR [duplicate-rate-row] US/CA/90210/Beverly Hills 9.5%: same location, class, and priority as US/CA/90210/Beverly Hills 9.5% with the same rate 9.5%
ERROR [invalid-rate-range] US/NY/10001/New York 125.0%: rate 125.0% is outside 0 to 100
ERROR [invalid-rate-range] US/TX/75001/Dallas -4.0%: rate -4.0% is outside 0 to 100
WARNING [zero-rate-taxable-class] US/FL/33101/Miami 0%: rate is 0% on the standard class, so orders in US/FL/33101/Miami are never taxed
WARNING [missing-tax-name] US/WA/98101/Seattle 8.5%: no tax name, so customers see a blank tax line at checkout
ERROR [invalid-rate] US/IL/60601/Chicago not-a-number%: rate is not a number: 'not-a-number'
ERROR [priority-conflict] US/OH/43085/Columbus 6.0%: same location, class, and priority as US/OH/43085/Columbus 7.5% but a different rate, so only one of them can apply
ERROR [invalid-priority] US/AZ/85001/Phoenix 5.6%: priority 0 is below 1
ERROR [postcode-range-reversed] US/CO/90210...90000/Denver 4.5%: postcode range 90210...90000 starts above its end
WARNING [high-rate] US/MI/48201/Detroit 35.0%: rate 35.0% is above the 30.0% threshold, check it is not a typo
ERROR [missing-country] * 5.0%: country code is empty, so this rate applies nowhere on purpose
ERROR [postcode-pattern-invalid] US/OR/9720!/Portland 5.0%: postcode pattern '9720!' contains '!', which no postcode contains
WARNING [compound-stack-high] GB/SW1A 1AA/London: compound rates stack to an effective 44.00% in GB/SW1A 1AA/London, above the 30.0% threshold

Rates by country:
  (blank)      1 rate  max 5.0%
  AU           1 rate  max 10.0%
  CA           1 rate  max 13.0%
  DE           2 rates  max 19.0%
  FR           1 rate  max 20.0%
  GB           2 rates  max 20.0%
  US          15 rates  max 125.0%

Rates by tax class:
  reduced-rate     1 rate  max 7.0%
  standard        22 rates  max 125.0%

Rates: 23, findings: 13, errors: 9, warnings: 4
```

The sample has 23 rates and 13 findings. The story it tells:
the Beverly Hills rate is entered twice, New York is set to
125 percent, Texas is negative, Florida charges no tax at all,
Ohio has two different rates fighting at priority 1, Colorado
has a postcode range that runs backwards, and London's
compound rates stack to an effective 44 percent.

Use it as a CI gate: `--check` exits 1 when any error is found,
and 0 otherwise. On the sample above it exits 1:

```bash
python3 woo_tax_doctor.py examples/taxes.csv --check
```

On `examples/clean.csv`, five clean rates, it exits 0:

```

Rates by country:
  DE           2 rates  max 19.0%
  FR           1 rate  max 20.0%
  GB           1 rate  max 20.0%
  US           1 rate  max 9.5%

Rates by tax class:
  reduced-rate     1 rate  max 7.0%
  standard         4 rates  max 20.0%

Rates: 5, findings: 0, errors: 0, warnings: 0
All tax rates clean.
```

With `--strict`, `--check` also fails on warnings: a file with
warnings only (one zero rate on a taxable class, and no rate
taxing shipping) exits 0 under `--check` and 1 under
`--check --strict`. `--json` emits the summary, country and
class tables, orders coverage, and findings as JSON, and
`--out` writes the report to a file.

Check the countries your orders actually came from:

```bash
python3 woo_tax_doctor.py examples/taxes.csv --orders-file examples/orders.csv
```

The orders file needs an `order_id` and a `billing_country`
column (`country`, `ship_country`, and `shipping_country`
work too). The sample orders include 2 orders from JP, which
no rate covers, so the run adds one more finding and ends at
14 findings, 10 errors and 4 warnings:

```
ERROR [missing-rate-for-order-country] JP: no tax rate covers JP, but 2 orders came from there

Orders by country (covered by a rate?):
  DE           1 order  covered
  GB           2 orders  covered
  JP           2 orders  NOT COVERED
  US           3 orders  covered

Rates: 23, findings: 14, errors: 10, warnings: 4
```

Raising the threshold changes the verdict: `--max-rate 50` on
the sample drops the high-rate and compound-stack warnings and
leaves 11 findings, 9 errors and 2 warnings.

## Options

| Flag | Meaning |
| --- | --- |
| `--orders-file` | Orders CSV with a billing country column, to flag countries with orders but no rate |
| `--max-rate` | Flag rates above this percent, and compound stacks above it (default: 30.0) |
| `--json` | Emit JSON instead of text |
| `--check` | Exit 1 if any error is found |
| `--strict` | With --check, also exit 1 on warnings |
| `--out` | Write the report to a file |

## Edge cases

* A rate at exactly `--max-rate` is not flagged; it must be
  strictly above the threshold. A rate of exactly 100 percent
  is allowed; only rates outside 0 to 100 are errors.
* A 0 percent rate on the `zero-rate` class is clean; a 0
  percent rate on any other class is a warning.
* A duplicate is only a duplicate at the same location, class,
  and priority. The same rate at a different priority is a
  legitimate stacked rate, and the same place under a different
  tax class is a different rate.
* A blank country is an error, not a wildcard. Use `*` for a
  rate that applies everywhere; a `*` country row covers every
  country in the orders check.
* A blank priority defaults to 1, the WooCommerce default.
* A blank postcode, state, or city means the rate applies to
  all postcodes, states, or cities in that country.
* Postcode ranges compare numerically when both ends are
  digits, and alphabetically otherwise.
* A compound stack is only checked in a place and class that
  has at least one compound rate; plain rates never trigger it.
* Rows with an unparseable rate are excluded from the country
  and class maximums but still count in the rate totals.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

66 tests, all passing: every rule, error and warning severities,
postcode patterns, compound stacking math with Decimal, orders
coverage, header aliases and BOM handling, summary counts, JSON
output shape, and `--check` and `--strict` exit codes.

## License

MIT. See [LICENSE](LICENSE).
