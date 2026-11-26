# Good first issues

Seeded issues for new contributors. File each one on GitHub with the
`good first issue` and `help wanted` labels.

## 1. Add a per-country effective rate column

Title: Show each country's highest effective rate, not just its highest row

Description: The rates-by-country table shows the highest single
rate per country. Add a second column for the highest effective
rate in that country, where a compound stack applies, so a store
owner can see at a glance where the tax a customer actually pays
is highest. Keep the max single-rate column as it is.

Difficulty: Easy

Acceptance criteria: the country table in text output shows both
rates; JSON country rows gain an effective_max_rate field; a test
checks both values for the sample file (GB effective 44.00%);
README documents the columns.

## 2. Flag rates that shadow a more specific row

Title: Warn when a wildcard row sits above a specific row

Description: WooCommerce matches the most specific rate first, so
a wildcard postcode row and a specific postcode row for the same
place and class can hide each other. Add a warning when a row
with postcode `*` or a blank postcode shares a country, state,
city, class, and priority with a row that names a postcode.

Difficulty: Medium

Acceptance criteria: a sample file with a wildcard row and a
specific row for the same place gets the new warning; a file
with only specific rows stays clean; a test covers both cases;
README documents the rule.

## 3. Support a known-rates reference file

Title: Let stores check rates against a reference table

Description: Add an optional --reference flag that takes a CSV
of expected rates (country, state, postcode, city, tax_class,
rate) from a tax authority or accountant. Flag any audited rate
that differs from the reference for the same place and class,
so a store can catch a rate that drifted from the published
figure.

Difficulty: Medium

Acceptance criteria: a reference file flags the sample New York
rate as differing; matching rates stay clean; a missing
reference file exits 2 with a clear error; a test covers both
cases; README documents the flag.

## 4. Add a monthly orders-coverage trend

Title: Show orders by country and month in the summary

Description: Add a small table after the orders coverage table
showing orders per month (by order date, when the orders file
has an order_date column), with count and covered share, so a
store owner can see whether uncovered countries are a new
problem or an old one.

Difficulty: Easy

Acceptance criteria: the table appears in text output with the
correct months for the sample orders file; JSON output gains a
months field; a test checks the rows; README documents the
table.
