# Changelog

## 1.0.0 - 2026-11-14

Initial release.

* Tax rates audit over a WooCommerce tax rates CSV export:
  duplicate rate rows and priority conflicts for the same
  location and class, rates over 100 percent or negative,
  zero rates on taxable classes, compound rates that stack
  above a threshold, postcode patterns that never match,
  shipping tax enabled with no shipping rate, rates with no
  tax name, and malformed rows.
* Orders coverage with --orders-file: countries that real
  orders came from but no rate covers are flagged.
* Rates handled with Decimal throughout, so stacked compound
  rates are exact and never float-rounded.
* Findings carry a code, rule name, severity (error or warning),
  and message; errors and warnings are counted in the summary.
* Text and --json output, --out file output, --max-rate
  threshold, and a --check CI gate that exits 1 on errors
  (and on warnings with --strict).
* Zero dependencies, Python 3.10 or newer.
