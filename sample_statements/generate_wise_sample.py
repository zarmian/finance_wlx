"""Generate a Wise CSV sample matching the user's real data structure."""
import csv
from pathlib import Path

OUTPUT = Path(__file__).parent / "welux_wise_sample.csv"

HEADERS = [
    "Date started (UTC)", "Date completed (UTC)", "ID", "Type", "State",
    "Description", "Reference", "Payer", "Card number", "Card label",
    "Card state", "Orig currency", "Orig amount", "Payment currency",
    "Amount", "Total amount", "Exchange rate", "Fee", "Fee currency",
    "Balance", "Account", "Beneficiary account number",
    "Beneficiary sort code or routing number", "Beneficiary IBAN",
    "Beneficiary BIC", "MCC", "Related transaction id", "Spend program",
]


# Real-shaped sample rows — drawn from the actual sheet
ROWS = [
    # Card payments (CARD_PAYMENT — should auto-route)
    ["18/02/2026", "2026-02-19", "test-001", "CARD_PAYMENT", "COMPLETED",
     "London City Airport", "", "Waleed Ahmed", "516760******4844", "Virtual",
     "ACTIVE", "GBP", "8", "GBP", "-8", "-8", "", "0", "GBP",
     "4717.69", "GBP Main", "", "", "", "", "7523", "", ""],
    ["18/02/2026", "2026-02-19", "test-002", "CARD_PAYMENT", "COMPLETED",
     "Apcoa Parking Uk", "", "Waleed Ahmed", "516760******9898", "welux chauffeurs ltd",
     "ACTIVE", "GBP", "7", "GBP", "-7", "-7", "", "0", "GBP",
     "4710.69", "GBP Main", "", "", "", "", "7523", "", ""],
    ["17/02/2026", "2026-02-18", "test-003", "CARD_PAYMENT", "COMPLETED",
     "Shell Petroleum", "", "Waleed Ahmed", "516760******9898", "welux chauffeurs ltd",
     "ACTIVE", "GBP", "55", "GBP", "-55", "-55", "", "0", "GBP",
     "4655.69", "GBP Main", "", "", "", "", "5541", "", ""],
    ["17/02/2026", "2026-02-18", "test-004", "CARD_PAYMENT", "COMPLETED",
     "Pod Point Charging", "", "Waleed Ahmed", "516760******9898", "welux chauffeurs ltd",
     "ACTIVE", "GBP", "23.50", "GBP", "-23.50", "-23.50", "", "0", "GBP",
     "4632.19", "GBP Main", "", "", "", "", "5734", "", ""],
    ["17/02/2026", "2026-02-18", "test-005", "CARD_PAYMENT", "COMPLETED",
     "Netflix.com", "", "Waleed Ahmed", "516760******9898", "welux chauffeurs ltd",
     "ACTIVE", "GBP", "18.99", "GBP", "-18.99", "-18.99", "", "0", "GBP",
     "4613.20", "GBP Main", "", "", "", "", "4899", "", ""],

    # TOPUPs (incoming)
    ["17/02/2026", "2026-02-17", "test-101", "TOPUP", "COMPLETED",
     "Money added from BLACKLANE HAVN UK", "BLACKLANE 01/26 C2", "", "", "",
     "", "GBP", "323.11", "GBP", "323.11", "323.11", "", "0", "GBP",
     "4936.31", "GBP Main", "", "", "", "", "", "", ""],
    ["17/02/2026", "2026-02-17", "test-102", "TOPUP", "COMPLETED",
     "Money added from HARIS SERVICES LTD", "Job payment", "", "", "",
     "", "GBP", "690", "GBP", "690", "690", "", "0", "GBP",
     "5626.31", "GBP Main", "", "", "", "", "", "", ""],
    ["16/02/2026", "2026-02-16", "test-103", "TOPUP", "COMPLETED",
     "Money added from ENT MULT SER LTD", "WX21VZN RENT", "", "", "",
     "", "GBP", "370", "GBP", "370", "370", "", "0", "GBP",
     "5996.31", "GBP Main", "", "", "", "", "", "", ""],
    ["15/02/2026", "2026-02-15", "test-104", "TOPUP", "COMPLETED",
     "Money added from A UDDIN", "CAR RENT", "", "", "",
     "", "GBP", "370", "GBP", "370", "370", "", "0", "GBP",
     "6366.31", "GBP Main", "", "", "", "", "", "", ""],
    ["14/02/2026", "2026-02-14", "test-105", "TOPUP", "COMPLETED",
     "Money added from VINTAGE LUXURY CHAUFFEURS LONDON LTD", "Loan return", "", "", "",
     "", "GBP", "2900", "GBP", "2900", "2900", "", "0", "GBP",
     "9266.31", "GBP Main", "", "", "", "", "", "", ""],
    ["13/02/2026", "2026-02-13", "test-106", "TOPUP", "COMPLETED",
     "Money added from MONTCLARES LTD", "Heathrow job", "", "", "",
     "", "GBP", "550", "GBP", "550", "550", "", "0", "GBP",
     "9816.31", "GBP Main", "", "", "", "", "", "", ""],

    # TRANSFERs out (wages, suppliers)
    ["18/02/2026", "2026-02-18", "test-201", "TRANSFER", "COMPLETED",
     "To Hafiz Raza", "Jan wages", "Waleed Ahmed", "", "", "",
     "GBP", "976", "GBP", "-976", "-976.20", "", "-0.2", "GBP",
     "8840.11", "GBP Main", "47239381", "90128", "", "", "", "", ""],
    ["18/02/2026", "2026-02-18", "test-202", "TRANSFER", "COMPLETED",
     "To Imran Niazi", "Jan wages", "Waleed Ahmed", "", "", "",
     "GBP", "976", "GBP", "-976", "-976.20", "", "-0.2", "GBP",
     "7863.91", "GBP Main", "684481", "206788", "", "", "", "", ""],
    ["18/02/2026", "2026-02-18", "test-203", "TRANSFER", "COMPLETED",
     "To Zaryab Rashid", "Wages", "Waleed Ahmed", "", "", "",
     "GBP", "976", "GBP", "-976", "-976.20", "", "-0.2", "GBP",
     "6887.71", "GBP Main", "63189198", "202728", "", "", "", "", ""],
    ["10/02/2026", "2026-02-10", "test-204", "TRANSFER", "COMPLETED",
     "To Waleed Ahmed", "Loan", "Waleed Ahmed", "", "", "",
     "GBP", "900", "GBP", "-900", "-900", "", "0", "GBP",
     "5987.71", "GBP Main", "39904763", "90128", "", "", "", "", ""],
    ["10/02/2026", "2026-02-10", "test-205", "TRANSFER", "COMPLETED",
     "To 1st nationwide security Ltd", "VclassJan", "Waleed Ahmed", "", "", "",
     "GBP", "1496", "GBP", "-1496", "-1496.20", "", "-0.2", "GBP",
     "4491.51", "GBP Main", "41448654", "90129", "", "", "", "", ""],
    ["09/02/2026", "2026-02-09", "test-206", "TRANSFER", "COMPLETED",
     "Dvla-wx21vzn", "73368653", "", "", "", "",
     "GBP", "54.25", "GBP", "-54.25", "-54.25", "", "0", "GBP",
     "4437.26", "GBP Main", "23709310", "208045", "", "", "", "", ""],
    ["09/02/2026", "2026-02-09", "test-207", "TRANSFER", "COMPLETED",
     "Dvla-wr24mry", "73778440", "", "", "", "",
     "GBP", "54.25", "GBP", "-54.25", "-54.25", "", "0", "GBP",
     "4383.01", "GBP Main", "23709310", "208045", "", "", "", "", ""],
    ["08/02/2026", "2026-02-09", "test-208", "TRANSFER", "COMPLETED",
     "Uk Fuels Limited", "2617763", "", "", "", "",
     "GBP", "6.30", "GBP", "-6.30", "-6.30", "", "0", "GBP",
     "4376.71", "GBP Main", "20390720", "205385", "", "", "", "", ""],
    ["08/02/2026", "2026-02-08", "test-209", "TRANSFER", "COMPLETED",
     "Tfl Congestn Chrge", "2.50E+17", "", "", "", "",
     "GBP", "126", "GBP", "-126", "-126", "", "0", "GBP",
     "4250.71", "GBP Main", "1394088", "400250", "", "", "", "", ""],

    # FEE
    ["08/02/2026", "2026-02-08", "test-301", "FEE", "COMPLETED",
     "Wise Business Fee", "Basic plan fee", "", "", "", "",
     "GBP", "10", "GBP", "-10", "-10", "", "0", "GBP",
     "4240.71", "GBP Main", "", "", "", "", "", "", ""],
]


def main():
    with open(OUTPUT, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(HEADERS)
        w.writerows(ROWS)
    print(f"Wrote {len(ROWS)} rows to {OUTPUT}")


if __name__ == "__main__":
    main()
