"""Generate a Barclays-style UK CSV (Money Out / Money In columns)."""
import csv
from pathlib import Path

OUTPUT = Path(__file__).parent / "barclays_sample.csv"


HEADERS = ["Date", "Description", "Money Out", "Money In", "Balance"]
ROWS = [
    ["01/02/2026", "TESCO STORES 1234", "45.20", "", "2454.80"],
    ["02/02/2026", "SALARY ACME LTD", "", "3500.00", "5954.80"],
    ["05/02/2026", "DIRECT DEBIT BRITISH GAS", "120.00", "", "5834.80"],
    ["10/02/2026", "AMAZON.CO.UK", "67.99", "", "5766.81"],
    ["15/02/2026", "REFUND ASOS", "", "32.50", "5799.31"],
]

with open(OUTPUT, "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(HEADERS)
    w.writerows(ROWS)
print(f"Wrote {OUTPUT}")
