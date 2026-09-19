"""Generate fictional monthly region/product aggregates; no real business data."""

import csv
from pathlib import Path


DESTINATION = Path(__file__).resolve().parents[1] / "data" / "samples" / "sample_sales.csv"
FIELDS = ["date", "region", "product", "category", "sales_amount", "order_count", "customer_count", "cost", "profit"]


def sample_rows() -> list[dict]:
    rows = []
    for month_index in range(18):
        year, month = 2025 + month_index // 12, month_index % 12 + 1
        for region_index, region in enumerate(("North", "South", "East")):
            if region == "South" and month_index == 6:
                continue  # Deliberate missing calendar period, not zero sales.
            for product_index, product in enumerate(("Widget", "Gadget")):
                sales = 1000 + 150 * region_index + 300 * product_index + 40 * month_index
                if region == "North" and month_index >= 15:
                    sales = round(sales * (1.0, .8, .65)[month_index - 15], 2)
                if region == "East" and month_index == 15 and product == "Widget":
                    sales = 18000  # Deliberate row-level outlier.
                margin = .20 - .02 * product_index
                if region == "South" and month_index == 17:
                    margin = .04  # Increasing sales but decreasing profit.
                profit = round(sales * margin, 2)
                row = {"date": f"{year}-{month:02d}-01", "region": region, "product": product,
                       "category": "Hardware", "sales_amount": f"{sales:.2f}",
                       "order_count": max(1, int(sales / 25)), "customer_count": max(1, int(sales / 40)),
                       "cost": f"{sales - profit:.2f}", "profit": f"{profit:.2f}"}
                if region == "North" and month_index == 1 and product == "Widget":
                    row["profit"] = ""  # Deliberate missing value, never imputed.
                rows.append(row)
    rows.append(dict(rows[0]))  # Deliberate duplicate; retained unless explicitly removed.
    return rows


def write_samples(path: Path = DESTINATION) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(sample_rows())


if __name__ == "__main__":
    write_samples()
    print(f"Wrote {len(sample_rows())} fictional rows to {DESTINATION}")
