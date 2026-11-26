"""Tests for woo_tax_doctor."""

import json
import os
import sys
import tempfile
import unittest
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))

import woo_tax_doctor as wtd


def row(**overrides):
    """One tax rate row, with clean defaults."""
    base = {"country": "US", "state": "CA", "postcode": "90210",
            "city": "Beverly Hills", "rate": "9.5",
            "tax_name": "CA State Tax", "priority": "1",
            "compound": "0", "shipping": "1",
            "tax_class": "standard"}
    base.update(overrides)
    return base


def codes(findings, rule=None):
    """Finding rules, optionally filtered to one rule."""
    return [f.rule for f in findings
            if rule is None or f.rule == rule]


def run(rows, max_rate=30.0, orders=None):
    """Audit rows and return (findings, report, summary)."""
    findings, report = wtd.audit(rows, max_rate, orders=orders)
    return findings, report, wtd.summarize(findings, report)


class TestParseHelpers(unittest.TestCase):
    def test_parse_rate_strips_percent_and_commas(self):
        self.assertEqual(wtd.parse_rate("9.5%", "x"),
                         Decimal("9.5"))
        self.assertEqual(wtd.parse_rate("1,025.5", "x"),
                         Decimal("1025.5"))

    def test_parse_rate_blank_is_none(self):
        self.assertIsNone(wtd.parse_rate("", "x"))
        self.assertIsNone(wtd.parse_rate(None, "x"))

    def test_parse_rate_rejects_text(self):
        with self.assertRaises(ValueError):
            wtd.parse_rate("not-a-number", "x")

    def test_parse_flag_values(self):
        for true in ("1", "yes", "true", "y", "YES"):
            self.assertTrue(wtd.parse_flag(true, "x", "compound"))
        for false in ("0", "no", "false", "n", ""):
            self.assertFalse(wtd.parse_flag(false, "x", "compound"))

    def test_parse_flag_rejects_other(self):
        with self.assertRaises(ValueError):
            wtd.parse_flag("maybe", "x", "compound")

    def test_parse_priority_blank_defaults_to_one(self):
        self.assertEqual(wtd.parse_priority("", "x"), 1)
        self.assertEqual(wtd.parse_priority(None, "x"), 1)
        self.assertEqual(wtd.parse_priority("2", "x"), 2)

    def test_parse_priority_rejects_zero_and_text(self):
        with self.assertRaises(ValueError):
            wtd.parse_priority("0", "x")
        with self.assertRaises(ValueError):
            wtd.parse_priority("high", "x")

    def test_normalise_header(self):
        self.assertEqual(wtd.normalise_header("Postcode / ZIP"),
                         "postcodezip")
        self.assertEqual(wtd.normalise_header("Rate %"), "rate")
        self.assertEqual(wtd.normalise_header("Country code"),
                         "countrycode")


class TestPostcodePatterns(unittest.TestCase):
    def test_valid_patterns_pass(self):
        for pattern in ("90210", "902*", "90210...90299",
                        "SW1A 1AA", "*", "", "K1A 0B1"):
            self.assertEqual(wtd.check_postcode(pattern, "x"), [])

    def test_reversed_numeric_range(self):
        findings = wtd.check_postcode("90210...90000", "x")
        self.assertEqual(codes(findings), ["postcode-range-reversed"])
        self.assertEqual(findings[0].severity, "error")

    def test_reversed_alpha_range(self):
        findings = wtd.check_postcode("ZZZ...AAA", "x")
        self.assertEqual(codes(findings), ["postcode-range-reversed"])

    def test_range_with_empty_end(self):
        findings = wtd.check_postcode("90210...", "x")
        self.assertEqual(codes(findings),
                         ["postcode-range-malformed"])

    def test_double_dot_not_range(self):
        findings = wtd.check_postcode("90210..90299", "x")
        self.assertEqual(codes(findings),
                         ["postcode-pattern-invalid"])

    def test_invalid_character(self):
        findings = wtd.check_postcode("9720!", "x")
        self.assertEqual(codes(findings),
                         ["postcode-pattern-invalid"])


class TestRowRules(unittest.TestCase):
    def test_clean_row_has_no_findings(self):
        findings, _, summary = run([row()])
        self.assertEqual(findings, [])
        self.assertEqual(summary["errors"], 0)

    def test_rate_over_100_is_error(self):
        findings, _, _ = run([row(rate="125.0")])
        self.assertEqual(codes(findings), ["invalid-rate-range"])
        self.assertEqual(findings[0].severity, "error")

    def test_negative_rate_is_error(self):
        findings, _, _ = run([row(rate="-4.0")])
        self.assertEqual(codes(findings), ["invalid-rate-range"])

    def test_rate_exactly_100_is_allowed(self):
        findings, _, _ = run([row(rate="100")])
        self.assertNotIn("invalid-rate-range", codes(findings))

    def test_high_rate_above_max_is_warning(self):
        findings, _, _ = run([row(rate="35.0")])
        self.assertEqual(codes(findings), ["high-rate"])
        self.assertEqual(findings[0].severity, "warning")

    def test_high_rate_at_max_is_not_flagged(self):
        findings, _, _ = run([row(rate="30.0")])
        self.assertEqual(findings, [])

    def test_unparseable_rate_is_error(self):
        findings, _, _ = run([row(rate="not-a-number")])
        self.assertEqual(codes(findings), ["invalid-rate"])

    def test_missing_rate_is_error(self):
        findings, _, _ = run([row(rate="")])
        self.assertEqual(codes(findings), ["missing-rate"])

    def test_zero_rate_on_standard_is_warning(self):
        findings, _, _ = run([row(rate="0")])
        self.assertEqual(codes(findings),
                         ["zero-rate-taxable-class"])
        self.assertEqual(findings[0].severity, "warning")

    def test_zero_rate_on_zero_rate_class_is_clean(self):
        findings, _, _ = run([row(rate="0",
                                  tax_class="zero-rate")])
        self.assertEqual(findings, [])

    def test_zero_rate_on_reduced_rate_is_warning(self):
        findings, _, _ = run([row(rate="0",
                                  tax_class="reduced-rate")])
        self.assertEqual(codes(findings),
                         ["zero-rate-taxable-class"])

    def test_missing_tax_name_is_warning(self):
        findings, _, _ = run([row(tax_name="")])
        self.assertEqual(codes(findings), ["missing-tax-name"])

    def test_missing_country_is_error(self):
        findings, _, _ = run([row(country="")])
        self.assertEqual(codes(findings), ["missing-country"])

    def test_wildcard_country_is_clean(self):
        findings, _, _ = run([row(country="*")])
        self.assertEqual(findings, [])

    def test_three_letter_country_is_error(self):
        findings, _, _ = run([row(country="USA")])
        self.assertEqual(codes(findings), ["invalid-country-code"])

    def test_invalid_state_character_is_warning(self):
        findings, _, _ = run([row(state="C@")])
        self.assertEqual(codes(findings), ["invalid-state-code"])

    def test_invalid_priority_is_error(self):
        findings, _, _ = run([row(priority="0")])
        self.assertEqual(codes(findings), ["invalid-priority"])

    def test_invalid_compound_flag_is_error(self):
        findings, _, _ = run([row(compound="maybe")])
        self.assertEqual(codes(findings), ["invalid-compound"])

    def test_invalid_shipping_flag_is_error(self):
        findings, _, _ = run([row(shipping="maybe")])
        self.assertIn("invalid-shipping", codes(findings))
        # The bad flag reads as off, so no rate taxes shipping.
        self.assertIn("shipping-tax-missing", codes(findings))

    def test_duplicate_same_rate_is_error(self):
        findings, _, _ = run([row(), row()])
        self.assertEqual(codes(findings), ["duplicate-rate-row"])
        self.assertEqual(findings[0].severity, "error")

    def test_same_place_different_priority_is_clean(self):
        findings, _, _ = run([row(), row(priority="2",
                                         tax_name="County Tax")])
        self.assertEqual(findings, [])

    def test_same_place_different_class_is_clean(self):
        findings, _, _ = run([row(), row(tax_class="reduced-rate",
                                         rate="7.0")])
        self.assertEqual(findings, [])

    def test_priority_conflict_different_rate(self):
        findings, _, _ = run([row(), row(rate="6.0")])
        self.assertEqual(codes(findings), ["priority-conflict"])

    def test_duplicate_detection_is_case_insensitive(self):
        findings, _, _ = run([row(), row(city="beverly hills")])
        self.assertEqual(codes(findings), ["duplicate-rate-row"])


class TestCrossRowRules(unittest.TestCase):
    def test_no_shipping_rate_anywhere_is_warning(self):
        findings, _, _ = run([row(shipping="0"),
                              row(country="GB", state="",
                                  postcode="SW1A 1AA",
                                  city="London", rate="20.0",
                                  tax_name="UK VAT", shipping="0")])
        self.assertEqual(codes(findings), ["shipping-tax-missing"])

    def test_shipping_rate_present_is_clean(self):
        findings, _, _ = run([row()])
        self.assertEqual(findings, [])

    def test_compound_stack_above_threshold(self):
        rows = [row(rate="20.0"),
                row(priority="2", compound="1", rate="20.0",
                    tax_name="County Tax")]
        findings, _, _ = run(rows)
        stack = [f for f in findings
                 if f.rule == "compound-stack-high"]
        self.assertEqual(len(stack), 1)
        self.assertIn("44.00%", stack[0].message)

    def test_compound_stack_below_threshold_is_clean(self):
        rows = [row(rate="5.0"),
                row(priority="2", compound="1", rate="5.0",
                    tax_name="County Tax")]
        findings, _, _ = run(rows)
        self.assertEqual(codes(findings, "compound-stack-high"), [])

    def test_multiple_compound_rates_flagged(self):
        rows = [row(rate="5.0"),
                row(priority="2", compound="1", rate="5.0",
                    tax_name="County Tax"),
                row(priority="3", compound="1", rate="5.0",
                    tax_name="City Tax")]
        findings, _, _ = run(rows)
        self.assertIn("multiple-compound-rates", codes(findings))

    def test_compound_math_is_decimal_exact(self):
        rows = [row(rate="10.0"),
                row(priority="2", compound="1", rate="10.0",
                    tax_name="County Tax")]
        findings, _, _ = run(rows, max_rate=20.0)
        stack = [f for f in findings
                 if f.rule == "compound-stack-high"]
        self.assertEqual(len(stack), 1)
        self.assertIn("21.00%", stack[0].message)


class TestOrdersCoverage(unittest.TestCase):
    def test_country_with_orders_and_no_rate_is_error(self):
        orders = [{"country": "JP"}, {"country": "JP"},
                  {"country": "US"}]
        findings, report, _ = run([row()], orders=orders)
        self.assertEqual(codes(findings,
                               "missing-rate-for-order-country"),
                         ["missing-rate-for-order-country"])
        self.assertEqual(report["orders"], [
            {"country": "JP", "orders": 2, "covered": False},
            {"country": "US", "orders": 1, "covered": True},
        ])

    def test_wildcard_rate_covers_every_country(self):
        orders = [{"country": "JP"}]
        findings, _, _ = run([row(country="*")], orders=orders)
        self.assertEqual(findings, [])

    def test_no_orders_file_means_no_orders_report(self):
        _, report, _ = run([row()])
        self.assertIsNone(report["orders"])


class TestReportAndSummary(unittest.TestCase):
    def test_country_and_class_tables(self):
        rows = [row(), row(country="GB", state="",
                           postcode="SW1A 1AA", city="London",
                           rate="20.0", tax_name="UK VAT",
                           tax_class="reduced-rate")]
        _, report, summary = run(rows)
        self.assertEqual(report["rates"], 2)
        self.assertEqual(report["countries"], [
            {"country": "GB", "rates": 1, "max_rate": "20.0"},
            {"country": "US", "rates": 1, "max_rate": "9.5"},
        ])
        self.assertEqual(report["classes"], [
            {"tax_class": "reduced-rate", "rates": 1,
             "max_rate": "20.0"},
            {"tax_class": "standard", "rates": 1,
             "max_rate": "9.5"},
        ])
        self.assertEqual(summary, {"rates": 2, "findings": 0,
                                   "errors": 0, "warnings": 0})

    def test_summary_counts(self):
        rows = [row(rate="125.0"),
                row(rate="0", postcode="90211", city="Los Angeles")]
        _, _, summary = run(rows)
        self.assertEqual(summary["findings"], 2)
        self.assertEqual(summary["errors"], 1)
        self.assertEqual(summary["warnings"], 1)

    def test_text_output_mentions_clean(self):
        findings, report = wtd.audit([row()], 30.0)
        text = wtd.format_text(findings, report,
                               wtd.summarize(findings, report))
        self.assertIn("All tax rates clean.", text)
        self.assertIn("Rates: 1, findings: 0", text)

    def test_text_output_has_tables(self):
        findings, report = wtd.audit([row()], 30.0)
        text = wtd.format_text(findings, report,
                               wtd.summarize(findings, report))
        self.assertIn("Rates by country:", text)
        self.assertIn("Rates by tax class:", text)
        self.assertIn("US", text)


class TestCsvReading(unittest.TestCase):
    def write(self, content):
        handle = tempfile.NamedTemporaryFile(
            "w", suffix=".csv", delete=False, encoding="utf-8")
        handle.write(content)
        handle.close()
        self.addCleanup(os.unlink, handle.name)
        return handle.name

    def test_woocommerce_export_headers(self):
        path = self.write(
            "Country code,State code,Postcode / ZIP,City,Rate %,"
            "Tax name,Priority,Compound,Shipping,Tax class\n"
            "US,CA,90210,Beverly Hills,9.5,CA State Tax,1,0,1,"
            "standard\n")
        rows = wtd.read_rates(path)
        self.assertEqual(rows[0]["country"], "US")
        self.assertEqual(rows[0]["postcode"], "90210")
        self.assertEqual(rows[0]["rate"], "9.5")

    def test_snake_case_headers(self):
        path = self.write(
            "country_code,state_code,postcode,city,rate,tax_name,"
            "priority,compound,shipping,tax_class\n"
            "GB,,SW1A 1AA,London,20.0,UK VAT,1,0,0,standard\n")
        rows = wtd.read_rates(path)
        self.assertEqual(rows[0]["country"], "GB")

    def test_bom_header(self):
        path = self.write(
            "\ufeffCountry code,Rate %\nUS,9.5\n")
        rows = wtd.read_rates(path)
        self.assertEqual(rows[0]["country"], "US")

    def test_missing_rate_column_is_error(self):
        path = self.write("Country code,City\nUS,Miami\n")
        with self.assertRaises(ValueError):
            wtd.read_rates(path)

    def test_orders_billing_country_header(self):
        path = self.write("order_id,billing_country\n1,JP\n")
        orders = wtd.read_orders(path)
        self.assertEqual(orders[0]["country"], "JP")

    def test_orders_without_country_column_is_error(self):
        path = self.write("order_id,total\n1,50.00\n")
        with self.assertRaises(ValueError):
            wtd.read_orders(path)


class TestCli(unittest.TestCase):
    def write(self, content):
        handle = tempfile.NamedTemporaryFile(
            "w", suffix=".csv", delete=False, encoding="utf-8")
        handle.write(content)
        handle.close()
        self.addCleanup(os.unlink, handle.name)
        return handle.name

    def test_sample_file_counts(self):
        here = os.path.dirname(os.path.dirname(
            os.path.abspath(__file__)))
        findings, report = wtd.audit(
            wtd.read_rates(os.path.join(
                here, "examples", "taxes.csv")), 30.0)
        summary = wtd.summarize(findings, report)
        self.assertEqual(summary, {"rates": 23, "findings": 13,
                                   "errors": 9, "warnings": 4})

    def test_clean_file_passes(self):
        here = os.path.dirname(os.path.dirname(
            os.path.abspath(__file__)))
        self.assertEqual(wtd.main([
            os.path.join(here, "examples", "clean.csv"),
            "--check"]), 0)

    def test_sample_file_fails_check(self):
        here = os.path.dirname(os.path.dirname(
            os.path.abspath(__file__)))
        self.assertEqual(wtd.main([
            os.path.join(here, "examples", "taxes.csv"),
            "--check"]), 1)

    def test_warnings_file_check_vs_strict(self):
        here = os.path.dirname(os.path.dirname(
            os.path.abspath(__file__)))
        path = os.path.join(here, "examples", "warnings.csv")
        self.assertEqual(wtd.main([path, "--check"]), 0)
        self.assertEqual(wtd.main([path, "--check", "--strict"]), 1)

    def test_orders_file_flags_japan(self):
        here = os.path.dirname(os.path.dirname(
            os.path.abspath(__file__)))
        self.assertEqual(wtd.main([
            os.path.join(here, "examples", "taxes.csv"),
            "--orders-file",
            os.path.join(here, "examples", "orders.csv"),
            "--check"]), 1)

    def test_json_output_shape(self):
        here = os.path.dirname(os.path.dirname(
            os.path.abspath(__file__)))
        out = tempfile.NamedTemporaryFile(
            "w", suffix=".json", delete=False)
        out.close()
        self.addCleanup(os.unlink, out.name)
        self.assertEqual(wtd.main([
            os.path.join(here, "examples", "clean.csv"),
            "--json", "--out", out.name]), 0)
        with open(out.name, encoding="utf-8") as handle:
            data = json.load(handle)
        self.assertEqual(data["summary"]["errors"], 0)
        self.assertEqual(data["summary"]["rates"], 5)
        self.assertEqual(data["findings"], [])
        self.assertEqual(len(data["countries"]), 4)

    def test_max_rate_flag_changes_verdict(self):
        here = os.path.dirname(os.path.dirname(
            os.path.abspath(__file__)))
        rows = wtd.read_rates(os.path.join(
            here, "examples", "taxes.csv"))
        findings, report = wtd.audit(rows, 50.0)
        summary = wtd.summarize(findings, report)
        self.assertEqual(summary["findings"], 11)
        self.assertEqual(summary["errors"], 9)
        self.assertEqual(summary["warnings"], 2)
        self.assertNotIn("high-rate", codes(findings))
        self.assertNotIn("compound-stack-high", codes(findings))

    def test_bad_max_rate_exits_2(self):
        self.assertEqual(wtd.main(["x.csv", "--max-rate", "150"]),
                         2)

    def test_missing_file_exits_2(self):
        self.assertEqual(wtd.main(["no-such-file.csv"]), 2)


if __name__ == "__main__":
    unittest.main()
