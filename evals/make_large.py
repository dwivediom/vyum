"""Generates a 10M+ token test PDF ("Northwind Logistics Master Archive") and its question set.

Contents: the real GDPR text, thousands of supplier agreements with identical clause structure but different
values (hard distractors), monthly operations logs, and 20 unique "needle" facts planted at known depths.
Tokens are counted exactly with BGE-M3's tokenizer (XLM-RoBERTa) as the text is written.

Usage: python evals/make_large.py [--tokens 520000] [--out evals/large]"""
import argparse
import json
import random
import sys
import textwrap
import time
from pathlib import Path

import pypdfium2 as pdfium
from huggingface_hub import snapshot_download
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen.canvas import Canvas
from tokenizers import Tokenizer

HERE = Path(__file__).parent
R = random.Random(7)

SUPPLIER_A = ["Halvorsen", "Lindqvist", "Bergström", "Nordvik", "Baltic", "Vester", "Kowalski", "Aalto", "Brandt",
              "Solberg", "Hedlund", "Mäkinen", "Dahl", "Kristiansen", "Novak", "Jansen", "Holm", "Eklund", "Rasmussen",
              "Virtanen", "Lund", "Sørensen", "Nilsen", "Strand", "Moberg", "Falk", "Wójcik", "de Vries", "Krause"]
SUPPLIER_B = ["Freight AS", "Components AB", "Metall Oy", "Logistics GmbH", "Packaging Sp. z o.o.", "Marine ApS",
              "Industri AS", "Polymers BV", "Cold Chain AB", "Electrics Oy", "Steelworks GmbH", "Pallets AS"]
LAWS = [("Norway", "Oslo"), ("Sweden", "Stockholm"), ("Denmark", "Copenhagen"), ("Finland", "Helsinki"),
        ("Germany", "Hamburg"), ("Poland", "Gdańsk"), ("England and Wales", "London"), ("the Netherlands", "Rotterdam")]
CITIES = ["Oslo", "Bergen", "Trondheim", "Gothenburg", "Malmö", "Aarhus", "Turku", "Hamburg", "Gdynia", "Rotterdam"]
DEPOTS = ["Bergen", "Oslo", "Trondheim", "Kristiansand", "Drammen", "Fredrikstad", "Hamar", "Lillehammer", "Molde"]
EVENTS = [
    "A routine forklift inspection was completed and {n} units passed without remarks.",
    "Inbound volume was {n} pallets, {p}% above the weekly average.",
    "Two loading bays were closed for {h} hours for floor cleaning.",
    "The cold room held a stable temperature between 2 and 6 °C throughout the shift.",
    "Outbound shipments to {c} left {h} hours behind schedule because of road works.",
    "The fire alarm system was tested and all {n} detectors responded.",
    "A pallet of packaging film was found damaged and written off.",
    "Staffing was {n} people on the day shift and {h} on the night shift.",
    "The yard was cleared of snow before {h}:00 and all gates stayed open.",
    "Customs documents for {n} export consignments were checked without errors.",
    "Electricity use was {n} kWh, in line with the monthly plan.",
    "A new label printer was installed at bay {h} and tested with {n} labels.",
]
BOILER = [
    "Each party shall perform its obligations under this Agreement in good faith and with reasonable care.",
    "Notices under this Agreement shall be in writing and sent to the addresses stated in the preamble.",
    "No delay in exercising a right under this Agreement operates as a waiver of that right.",
    "The Supplier shall comply with all applicable laws, including export control and sanctions rules.",
    "This clause survives the expiry or termination of this Agreement for the period stated.",
    "The parties shall meet at least once per {q} to review performance under this Agreement.",
    "Any amendment to this Agreement is valid only if made in writing and signed by both parties.",
    "The Supplier shall keep accurate records of deliveries for at least {y} years.",
    "Neither party may assign this Agreement without the prior written consent of the other party.",
    "If any provision of this Agreement is held invalid, the remaining provisions stay in full force.",
    "The Buyer may audit the Supplier's compliance with this clause once per calendar year on {d} days' notice.",
    "Invoices shall state the purchase order number, the delivery note number and the agreed unit price.",
]

# 20 needles: unique facts that appear exactly once. Kind "log" goes into an operations-log entry,
# kind "clause" becomes a special clause in one supplier agreement (the question names that agreement).
NEEDLES = [
    ("log", "On inspection at the Tromsø depot, technicians recorded that the backup generator had been replaced on 14 March 2021 at a cost of NOK 412,750.",
     "When was the backup generator at the Tromsø depot replaced, and what did it cost?", [["14 March 2021"], ["412,750"]]),
    ("clause", "The Supplier shall keep a spare-parts stock of 3,250 units at its Gdańsk warehouse at all times.",
     "How many spare-parts units must the supplier keep at its Gdańsk warehouse under Supplier Agreement No. {no}?", [["3,250", "3250"]]),
    ("log", "The access code for vault 7 at the Narvik depot was changed to QX-4471-B after the annual security review.",
     "What is the access code for vault 7 at the Narvik depot?", [["QX-4471-B"]]),
    ("clause", "The Buyer receives a rebate of 2.35% on all orders shipped by rail from Kiruna.",
     "What rebate does the buyer receive on rail shipments from Kiruna under Supplier Agreement No. {no}?", [["2.35%"]]),
    ("log", "Night supervisor Ingrid Solheim at the Bodø depot reported that the roof membrane above hall C was replaced in a single weekend.",
     "Who was the night supervisor at the Bodø depot who reported the roof membrane replacement?", [["Ingrid Solheim"]]),
    ("clause", "Deliveries to the Svalbard site must be made in insulated containers rated for minus 40 °C.",
     "What temperature rating must containers for the Svalbard site meet under Supplier Agreement No. {no}?", [["40"]]),
    ("log", "The Ålesund depot's cold-storage compressor failed at 03:40 and 1,180 kg of salmon was moved to the Molde site.",
     "How much salmon was moved to Molde when the Ålesund cold-storage compressor failed?", [["1,180", "1180"]]),
    ("clause", "The Supplier shall assign a dedicated account manager, Mr Tomasz Zieliński, for the duration of this Agreement.",
     "Who is the dedicated account manager under Supplier Agreement No. {no}?", [["Zieliński", "Zielinski"]]),
    ("log", "The Stavanger depot passed its ISO 14001 surveillance audit with auditor reference NV-2207-981.",
     "What was the auditor reference for the Stavanger depot's ISO 14001 surveillance audit?", [["NV-2207-981"]]),
    ("clause", "A penalty of EUR 1,875 applies for each day a delivery to the Hammerfest terminal is late.",
     "What is the daily penalty for late delivery to the Hammerfest terminal under Supplier Agreement No. {no}?", [["1,875", "1875"]]),
    ("log", "The forklift fleet at the Tromsø depot was converted to hydrogen fuel cells, with 14 trucks delivered by Hyster in June.",
     "How many hydrogen fuel-cell forklifts were delivered to the Tromsø depot?", [["14"]]),
    ("clause", "Packaging must contain at least 68% recycled fibre, verified by an annual third-party certificate.",
     "What minimum share of recycled fibre must packaging contain under Supplier Agreement No. {no}?", [["68%"]]),
    ("log", "At the Narvik depot a ransomware alert on the warehouse system was contained within 47 minutes and no data was lost.",
     "How quickly was the ransomware alert at the Narvik depot contained?", [["47 minutes"]]),
    ("clause", "The Supplier guarantees a maximum lead time of 11 business days for orders placed before 1 October.",
     "What maximum lead time does the supplier guarantee for orders placed before 1 October under Supplier Agreement No. {no}?", [["11 business days", "11"]]),
    ("log", "The Bodø depot installed 312 solar panels on the north warehouse roof, rated at 128 kWp in total.",
     "How many solar panels were installed at the Bodø depot?", [["312"]]),
    ("clause", "Disputes under this Agreement shall first be referred to mediation at the Arctic Trade Chamber in Tromsø.",
     "Where must disputes first be referred to mediation under Supplier Agreement No. {no}?", [["Arctic Trade Chamber"]]),
    ("log", "A spill of 260 litres of hydraulic oil at the Ålesund depot was cleaned up by the contractor Vestmiljø AS.",
     "Which contractor cleaned up the hydraulic oil spill at the Ålesund depot?", [["Vestmiljø"]]),
    ("clause", "The Supplier shall carry cyber insurance with a minimum cover of EUR 6,400,000.",
     "What minimum cyber insurance cover must the supplier carry under Supplier Agreement No. {no}?", [["6,400,000", "6.4 million"]]),
    ("log", "The Stavanger depot's new automated cold store holds 9,600 pallet positions and opened on 2 September 2023.",
     "How many pallet positions does the new automated cold store at the Stavanger depot hold?", [["9,600", "9600"]]),
    ("clause", "The Buyer may return unsold seasonal goods within 120 days for a full credit note.",
     "Within how many days may the buyer return unsold seasonal goods under Supplier Agreement No. {no}?", [["120"]]),
]
UNANSWERABLE = [
    "What is the late payment interest under Supplier Agreement No. 09999?",
    "What is the warranty period under Supplier Agreement No. 09871?",
    "When was the backup generator at the Bergen depot replaced?",
    "What is the access code for vault 12 at the Narvik depot?",
    "How many electric trucks did Northwind buy for the Hamar depot in 2022?",
    "What penalty applies for late delivery to the Kirkenes terminal?",
    "Who was the night supervisor at the Molde depot?",
    "What is the auditor reference for the Bergen depot's ISO 9001 audit?",
]


class Writer:
    """Streams blocks to a PDF (page by page, constant memory) and a text file, counting tokens exactly."""

    def __init__(self, out: Path, tok: Tokenizer):
        self.tok = tok
        self.tokens = 0
        self.words = 0
        self.txt = open(out / "northwind_master_archive.txt", "w")
        self.c = Canvas(str(out / "northwind_master_archive.pdf"), pagesize=A4, pageCompression=1)
        self.y = 0
        self._page_start()

    def _page_start(self):
        self.c.setFont("Helvetica", 7.5)
        self.c.drawString(50, 812, "Northwind Logistics AS · Master Archive · Confidential")
        self.c.drawString(50, 22, f"Page {self.c.getPageNumber()}")
        self.y = 790

    def _line(self, text, font="Helvetica", size=9.5, gap=12):
        if self.y < 40:
            self.c.showPage()
            self._page_start()
        self.c.setFont(font, size)
        self.c.drawString(50, self.y, text)
        self.y -= gap

    @property
    def page(self):
        return self.c.getPageNumber()

    def block(self, text: str, heading=False) -> int:
        """Writes a paragraph or heading; returns the page its first line landed on."""
        if heading:
            self.y -= 6
        if self.y < 40 + (24 if heading else 0):
            self.c.showPage()
            self._page_start()
        first = self.page
        for i, line in enumerate(textwrap.wrap(text, 98 if not heading else 80) or [""]):
            self._line(line, "Helvetica-Bold" if heading else "Helvetica", 10 if heading else 9.5, 13 if heading else 12)
        self.y -= 4
        self.txt.write(("\n# " if heading else "\n") + text + "\n")
        self.tokens += len(self.tok.encode(text, add_special_tokens=False).ids)
        self.words += len(text.split())
        return first

    def close(self):
        self.c.save()
        self.txt.close()


def agreement(w: Writer, no: int, needle: str | None):
    sup = f"{R.choice(SUPPLIER_A)} {R.choice(SUPPLIER_B)}"
    law, court = R.choice(LAWS)
    v = {"payment": R.choice([14, 21, 30, 45, 60, 75, 90]), "interest": R.choice([0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0, 2.25, 2.5]),
         "warranty": R.choice([6, 12, 18, 24, 30, 36, 48]), "notice": R.choice([30, 60, 90, 120, 180]),
         "cap": R.randrange(250, 9000) * 1000, "law": law, "delivery": R.randint(5, 30), "insurance": R.randrange(5, 120) * 1_000_000,
         "price": round(R.uniform(0.8, 95), 2), "cur": R.choice(["EUR", "USD", "NOK", "SEK"]), "start": R.randint(2012, 2024)}
    pages = {}
    w.block(f"SUPPLIER AGREEMENT NO. {no:05d} — {sup.upper()} AND NORTHWIND LOGISTICS AS", heading=True)
    clauses = [
        ("1. Parties", f"This Supplier Agreement No. {no:05d} is made between {sup} (the Supplier) and Northwind Logistics AS (the Buyer)."),
        ("2. Term", f"This Agreement starts on 1 {R.choice(['January', 'April', 'July', 'October'])} {v['start']} and runs for {R.randint(1, 5)} years."),
        ("3. Pricing", f"The unit price is {v['cur']} {v['price']:.2f} per unit, fixed for the first contract year."),
        ("4. Payment", f"The Buyer shall pay each invoice within {v['payment']} days of the invoice date. Late payments carry interest of {v['interest']}% per month."),
        ("5. Delivery", f"The Supplier shall deliver each order within {v['delivery']} business days, {R.choice(['FCA', 'DAP', 'DDP', 'EXW'])} {R.choice(CITIES)} (Incoterms 2020)."),
        ("6. Warranty", f"The Supplier warrants each item against defects for {v['warranty']} months from delivery."),
        ("7. Insurance", f"The Supplier shall maintain liability insurance of at least EUR {v['insurance']:,}."),
        ("8. Limitation of liability", f"Each party's total liability under this Agreement is capped at EUR {v['cap']:,}."),
        ("9. Termination", f"Either party may terminate this Agreement for convenience with {v['notice']} days' written notice."),
        ("10. Governing law", f"This Agreement is governed by the laws of {law}. The courts of {court} have exclusive jurisdiction."),
    ]
    if needle:
        clauses.append(("11. Special conditions", needle))
    for title, text in clauses:
        w.block(title, heading=True)
        filler = " ".join(R.choice(BOILER).format(q=R.choice(["quarter", "half-year"]), y=R.randint(3, 10), d=R.choice([14, 30]))
                          for _ in range(R.randint(2, 5)))
        pages[title] = w.block(f"{text} {filler}")
    return v, pages


def month_log(w: Writer, year: int, month: str, needle: str | None):
    w.block(f"OPERATIONS LOG — {month.upper()} {year}", heading=True)
    days = list(range(1, 29))
    needle_day = R.choice(days) if needle else None
    page = None
    for day in days:
        depot = R.choice(DEPOTS)
        events = " ".join(R.choice(EVENTS).format(n=R.randint(3, 900), p=R.randint(2, 30), h=R.randint(1, 12), c=R.choice(CITIES))
                          for _ in range(R.randint(3, 6)))
        if day == needle_day:
            page = w.block(f"{day} {month} {year}. {needle} {events}")
        else:
            w.block(f"{day} {month} {year} — {depot} depot. {events}")
    return page


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tokens", type=int, default=520_000)
    ap.add_argument("--out", default=str(HERE / "large"))
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    tok = Tokenizer.from_file(str(Path(snapshot_download("BAAI/bge-reranker-v2-m3", allow_patterns=["tokenizer.json"])) / "tokenizer.json"))
    w = Writer(out, tok)
    t0 = time.time()

    w.block("NORTHWIND LOGISTICS AS — MASTER ARCHIVE", heading=True)
    w.block("This archive collects the reference regulation, every supplier agreement and the monthly operations logs of Northwind Logistics AS.")
    w.block("PART I — SUPPLIER AGREEMENTS AND OPERATIONS LOGS", heading=True)
    needles = list(NEEDLES)
    depths = [0.02 + i * (0.95 / (len(needles) - 1)) for i in range(len(needles))]
    agreements, questions, placed = {}, [], []
    months = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
    no, mi = 1, 0
    per_block = 25 if a.tokens >= 5_000_000 else 4
    while w.tokens < a.tokens:
        for _ in range(per_block):
            needle = None
            if needles and needles[0][0] == "clause" and w.tokens >= depths[len(placed)] * a.tokens:
                needle = needles.pop(0)
            v, pages = agreement(w, no, needle[1] if needle else None)
            agreements[no] = (v, pages)
            if needle:
                placed.append(no)
                questions.append({"q": needle[2].format(no=f"{no:05d}"), "expect": needle[3], "doc": "northwind",
                                  "page": [pages["11. Special conditions"]], "depth": w.tokens, "type": "needle"})
            no += 1
        needle = None
        if needles and needles[0][0] == "log" and w.tokens >= depths[len(placed)] * a.tokens:
            needle = needles.pop(0)
        page = month_log(w, 2015 + mi // 12, months[mi % 12], needle[1] if needle else None)
        if needle:
            placed.append(needle[1][:30])
            questions.append({"q": needle[2], "expect": needle[3], "doc": "northwind", "page": [page],
                              "depth": w.tokens, "type": "needle"})
        mi += 1
        if mi % (20 if per_block == 25 else 40) == 0:
            print(f"  {w.tokens / 1e6:5.2f}M tokens · {w.page:,} pages · {no - 1:,} agreements · {time.time() - t0:.0f}s", flush=True)

    w.block("APPENDIX — REFERENCE REGULATION", heading=True)
    gdpr = pdfium.PdfDocument(str(HERE / "real" / "gdpr_regulation_2016_679.pdf"))
    for i in range(len(gdpr)):
        for para in gdpr[i].get_textpage().get_text_range().split("\r\n\r\n"):
            para = " ".join(para.split())
            if para:
                w.block(para, heading=bool(len(para) < 60 and (para.startswith("Article ") or para.isupper())))

    # distractor questions: one clause of ten random agreements spread across the archive
    kinds = [("4. Payment", "What is the late payment interest under Supplier Agreement No. {no}?", lambda v: [[f"{v['interest']}%"]]),
             ("6. Warranty", "How long is the warranty under Supplier Agreement No. {no}?", lambda v: [[f"{v['warranty']} months"]]),
             ("10. Governing law", "Which law governs Supplier Agreement No. {no}?", lambda v: [[v["law"]]]),
             ("9. Termination", "How much notice is needed to terminate Supplier Agreement No. {no} for convenience?", lambda v: [[f"{v['notice']} days"]]),
             ("8. Limitation of liability", "What is the liability cap under Supplier Agreement No. {no}?", lambda v: [[f"{v['cap']:,}"]])]
    for i, n in enumerate(sorted(R.sample(range(1, no), 10))):
        title, q, expect = kinds[i % len(kinds)]
        v, pages = agreements[n]
        questions.append({"q": q.format(no=f"{n:05d}"), "expect": expect(v), "doc": "northwind", "page": [pages[title]],
                          "depth": round(n / no, 3), "type": "distractor"})
    questions += [{"q": q, "unanswerable": True, "type": "unanswerable"} for q in UNANSWERABLE]
    assert no - 1 < 9871, "unanswerable agreement numbers must not exist"

    for q in questions:
        if q.get("type") == "needle":
            q["depth"] = round(q["depth"] / w.tokens, 3)
    w.close()
    (HERE / "questions_large.json").write_text(json.dumps(questions, indent=1, ensure_ascii=False))
    stats = {"tokens": w.tokens, "words": w.words, "pages": w.page, "agreements": no - 1, "log_months": mi,
             "needles": len(placed), "questions": len(questions), "seconds": round(time.time() - t0)}
    (out / "stats.json").write_text(json.dumps(stats, indent=1))
    print(json.dumps(stats, indent=1))
    if len(placed) != len(NEEDLES):
        sys.exit(f"only {len(placed)} of {len(NEEDLES)} needles placed")


if __name__ == "__main__":
    main()
