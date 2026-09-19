# Fictional demonstration dataset

`sample_sales.csv` contains 107 fictional monthly region/product aggregate rows
from January 2025 through June 2026. Regenerate it with
`python scripts/generate_samples.py`. The generator is deterministic.

Grain: one monthly region/product aggregate, not a transaction or unique customer.
Currency is an arbitrary consistent demo unit. `customer_count` is a segment count:
summing it is NOT a global distinct-customer count. `date` labels a calendar month.

Deliberate issues: a missing South period, one blank profit cell, one duplicate,
an East sales outlier, declining recent North sales, and latest South sales growth
with falling profit. These are test patterns, not observations about a real company.

Duplicates are preserved by default. No missing value is imputed, and a missing
calendar period is not interpreted as zero. Recent periods are anchored to the
maximum observed date, not the machine's current date.
