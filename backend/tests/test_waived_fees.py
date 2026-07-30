"""Waived fees: read from the notes, shown as an explicit $0.00 line.

ThriftBooks' admin notes say "$4 12oz Kona / waived $50 destination fee". The
$50 correctly contributes nothing — and that is exactly the problem, because
with no line for it the notes name a $50 that appears nowhere in the invoice and
the reader can't tell whether it was handled or dropped. First it was flagged
HIGH ("nothing in the invoice uses it"); silencing that alone just moved the
question from "why is this flagged?" to "where did the $50 go?".

The same clause-scoped waiver reading serves both, which is the point of
notes_money existing: two different readings of the same notes is how a figure
ends up flagged and displayed as handled at the same time.
"""
import os

os.environ.setdefault("CRM_PROVIDER", "mock")

from app.core.notes_money import (fee_name, is_waived, stated_amounts,
                                  waived_fees)

THRIFTBOOKS_ADMIN = "$4 12oz Kona / waived $50 destination fee"


# ── the waived line the breakdown renders ────────────────────────────────────

def test_thriftbooks_waived_destination_fee():
    fees = waived_fees(THRIFTBOOKS_ADMIN)
    assert len(fees) == 1
    assert fees[0]["amount"] == 50.0
    assert fees[0]["name"] == "destination fee"
    assert fees[0]["phrase"] == "waived $50 destination fee"


def test_the_rate_in_the_other_clause_is_not_waived():
    """"$4 12oz Kona" sits in its own clause and is a live rate. A whole-blob
    waiver check would have swallowed it."""
    assert [f["amount"] for f in waived_fees(THRIFTBOOKS_ADMIN)] == [50.0]


def test_waiver_after_the_figure_reads_the_same():
    fees = waived_fees("$50 destination fee was waived")
    assert fees[0]["amount"] == 50.0
    assert fees[0]["name"] == "destination fee"


def test_a_live_fee_produces_no_waived_line():
    assert waived_fees("$50 destination fee applies") == []


def test_a_clause_naming_no_fee_still_reports_the_amount():
    """The UI falls back to "Fee (waived)" — better than dropping the line and
    leaving the figure unexplained again."""
    fees = waived_fees("waived $50")
    assert fees[0]["amount"] == 50.0
    assert fees[0]["name"] == ""


def test_the_same_waiver_in_two_note_fields_is_one_line():
    text = "waived $50 destination fee / waived $50 destination fee"
    assert len(waived_fees(text)) == 1


def test_two_different_waived_fees_are_both_shown():
    fees = waived_fees("waived $50 destination fee / comped $25 setup fee")
    assert {f["amount"] for f in fees} == {50.0, 25.0}
    assert {f["name"] for f in fees} == {"destination fee", "setup fee"}


def test_filler_words_are_stripped_from_the_name():
    assert fee_name("we waived the $50 destination fee") == "destination fee"


# ── the gate still catches what it must ──────────────────────────────────────

def test_a_waived_fee_is_not_reported_as_unused():
    assert 50.0 not in stated_amounts(THRIFTBOOKS_ADMIN)


def test_a_dropped_fee_beside_a_waived_one_is_still_reported():
    """The regression the clause scoping exists for: a fixed character window
    puts "waived" within 30 characters of the $99."""
    text = "waived $50 destination fee / $99 setup fee applies"
    amounts = stated_amounts(text)
    assert 99.0 in amounts
    assert 50.0 not in amounts


def test_a_figure_waived_once_and_charged_once_is_still_reported():
    text = "waived $50 destination fee / $50 cleaning fee applies"
    assert 50.0 in stated_amounts(text)


def test_include_waived_returns_everything():
    assert 50.0 in stated_amounts(THRIFTBOOKS_ADMIN, include_waived=True)


def test_a_decimal_amount_is_not_split_into_two_clauses():
    """The period in "$50.00" must not read as a clause boundary, or the waiver
    and the figure land in different clauses and the fee looks live."""
    assert is_waived("waived $50.00 destination fee", 7)
    assert waived_fees("waived $50.00 destination fee")[0]["amount"] == 50.0


def test_a_thousands_comma_is_not_a_clause_boundary():
    assert waived_fees("waived $1,200 setup fee")[0]["amount"] == 1200.0


# ── the wiring: does it reach the stored classification? ─────────────────────

def test_the_pipeline_records_waived_fees_on_the_classification():
    """The display can only show what got stored. Read deterministically in
    _normalize_classification rather than asked of the classifier, so it doesn't
    depend on the model choosing to mention it."""
    from app.core.pipeline import _normalize_classification

    cleaned = {
        "ADMIN_NOTES": THRIFTBOOKS_ADMIN,
        "EVENT_NOTES_HTML": "<p><strong>EVENT TYPE </strong>Invoice</p>",
        "DRIVER_NOTES": "Served 31 Konas. Send invoice",
    }
    cls = _normalize_classification(
        {"EVENT_TYPE": "package", "BILLING_MODEL": "PACKAGE_PER_SERVING",
         "RATE_PER_SERVING": 4, "UNITS_SERVED_TOTAL": 31}, cleaned
    )
    assert cls["WAIVED_FEES"] == [
        {"amount": 50.0, "name": "destination fee",
         "phrase": "waived $50 destination fee"}
    ]


def test_no_waiver_leaves_the_field_absent():
    """Absent rather than [] — the breakdown renders nothing either way, and an
    empty list on every event is noise in the stored classification."""
    from app.core.pipeline import _normalize_classification

    cls = _normalize_classification(
        {"EVENT_TYPE": "package", "BILLING_MODEL": "PACKAGE_PER_SERVING"},
        {"ADMIN_NOTES": "$4 12oz Kona", "EVENT_NOTES_HTML": "", "DRIVER_NOTES": ""},
    )
    assert "WAIVED_FEES" not in cls


# ── the invoice document: charged in full, discounted straight back off ──────
#
# Brett, 2026-07-30: "There is no destination fee in and of itself... we use that
# terminology so that the customer realizes we did give them a discount. What we
# typically do is on the invoice, we will show the full price and then we'll
# subtract out the difference to bring it to what we eventually quoted them."
# The admin confirmed: "We include the Destination Fee on the invoice, then
# deduct the same amount under the Discount line."

def _thriftbooks_payload():
    from app.core import billing, invoice_builder

    cls = {
        "EVENT_ID": "be6ba87e37b648f99aee4f7d620e64a2",
        "EVENT_NAME": "ThriftBooks",
        "EVENT_TYPE": "package", "BILLING_MODEL": "PACKAGE_PER_SERVING",
        "RATE_PER_SERVING": 4, "UNITS_SERVED_TOTAL": 31, "TAXABLE": "YES",
        "PAYMENT_METHOD": "CHECK",
        "WAIVED_FEES": [{"amount": 50.0, "name": "destination fee",
                         "phrase": "waived $50 destination fee"}],
    }
    calc = billing.calculate_invoice(cls)
    payload = invoice_builder.build_invoice_payload(
        {**cls, "calculations": calc},
        {"ADMIN_NOTES": THRIFTBOOKS_ADMIN, "EVENT_NAME": "ThriftBooks"},
        {},
    )
    return payload, calc


def test_the_invoice_shows_the_fee_and_takes_it_back_off():
    payload, _ = _thriftbooks_payload()
    names = [i["name"] for i in payload["clientInvoiceItems"]]
    assert "Destination Fee" in names
    assert "Destination Fee Discount" in names
    # Order matters to a reader: the charge, then the credit against it.
    assert names.index("Destination Fee") < names.index("Destination Fee Discount")


def test_the_pair_nets_to_zero():
    """The whole point: the client SEES a $50 discount and owes exactly what was
    quoted. If these two lines didn't cancel, we'd be billing a fee the office
    told the customer they weren't paying."""
    payload, _ = _thriftbooks_payload()
    fee = next(i for i in payload["clientInvoiceItems"] if i["name"] == "Destination Fee")
    credit = next(i for i in payload["clientInvoiceItems"]
                  if i["name"] == "Destination Fee Discount")
    assert fee["amount"] == 50.0
    assert credit["amount"] == -50.0
    assert fee["amount"] + credit["amount"] == 0


def test_the_total_is_unchanged_by_the_presentation():
    """$124 of servings + 6% tax + 4% processing = $136.40, with or without the
    two extra lines. KonaOS recomputes the total from the line items, so a pair
    that didn't cancel would silently change what the client is charged.

    Compared against the same event with no waiver rather than against a literal:
    the line items also carry the processing fee, so the invariant is "adding the
    presentation moves nothing", not any particular sum.
    """
    from app.core import billing, invoice_builder

    def build(with_waiver: bool):
        cls = {
            "EVENT_ID": "e1", "EVENT_NAME": "ThriftBooks",
            "EVENT_TYPE": "package", "BILLING_MODEL": "PACKAGE_PER_SERVING",
            "RATE_PER_SERVING": 4, "UNITS_SERVED_TOTAL": 31, "TAXABLE": "YES",
            "PAYMENT_METHOD": "CHECK",
        }
        if with_waiver:
            cls["WAIVED_FEES"] = [{"amount": 50.0, "name": "destination fee"}]
        calc = billing.calculate_invoice(cls)
        p = invoice_builder.build_invoice_payload(
            {**cls, "calculations": calc}, {"EVENT_NAME": "ThriftBooks"}, {}
        )
        return p, calc

    shown, calc = build(True)
    plain, _ = build(False)

    assert calc["SUBTOTAL"] == 124.00
    assert shown["grandTotal"] == plain["grandTotal"] == 136.40
    assert shown["subTotal"] == plain["subTotal"] == 124.00
    assert shown["taxAmount"] == plain["taxAmount"]
    assert round(sum(i["amount"] for i in shown["clientInvoiceItems"]), 2) == \
           round(sum(i["amount"] for i in plain["clientInvoiceItems"]), 2)
    # And it really did add the two lines rather than quietly doing nothing.
    assert len(shown["clientInvoiceItems"]) == len(plain["clientInvoiceItems"]) + 2


def test_both_lines_are_taxable_so_the_tax_base_nets_out_too():
    payload, _ = _thriftbooks_payload()
    for name in ("Destination Fee", "Destination Fee Discount"):
        item = next(i for i in payload["clientInvoiceItems"] if i["name"] == name)
        assert item["taxable"] is True, name


def test_no_waiver_adds_no_lines():
    from app.core import billing, invoice_builder

    cls = {
        "EVENT_ID": "e1", "EVENT_NAME": "No waiver",
        "EVENT_TYPE": "package", "BILLING_MODEL": "PACKAGE_PER_SERVING",
        "RATE_PER_SERVING": 4, "UNITS_SERVED_TOTAL": 31, "TAXABLE": "YES",
    }
    calc = billing.calculate_invoice(cls)
    payload = invoice_builder.build_invoice_payload(
        {**cls, "calculations": calc}, {"EVENT_NAME": "No waiver"}, {}
    )
    assert not any("Discount" in i["name"] for i in payload["clientInvoiceItems"])


def test_an_unnamed_waiver_still_reads_as_the_destination_fee():
    """"waived $50" with no fee named. Destination fee is the standing term for
    this discount in the business, so it beats a bare "Fee"."""
    from app.core.invoice_builder import _waived_fee_label

    assert _waived_fee_label({"amount": 50.0, "name": ""}) == "Destination Fee"
    assert _waived_fee_label({"amount": 50.0, "name": "travel fee"}) == "Travel Fee"


def test_a_waiver_does_not_bleed_across_note_fields():
    """The bug this test was written for: the note fields were joined with a
    space, so admin notes ending "waived $50 destination fee" and driver notes
    beginning "$50 cleaning fee charged" became ONE clause — and the waiver
    covered a fee nobody waived. Separate fields are separate thoughts."""
    from app.core.invariants import _notes_text
    from app.core.pipeline import _normalize_classification

    cleaned = {
        "ADMIN_NOTES": "waived $50 destination fee",
        "DRIVER_NOTES": "$60 cleaning fee charged",
        "EVENT_NOTES_HTML": "",
    }
    # The gate must still see the $60 as a live, unaccounted-for figure.
    assert 60.0 in stated_amounts(_notes_text(cleaned))
    # And the display must not claim the $60 was waived.
    waived = _normalize_classification(
        {"EVENT_TYPE": "package", "BILLING_MODEL": "PACKAGE_PER_SERVING"}, cleaned
    )["WAIVED_FEES"]
    assert [f["amount"] for f in waived] == [50.0]
    assert waived[0]["name"] == "destination fee"


def test_html_notes_are_read_too():
    """Event notes arrive as HTML; a waiver inside markup must still be seen."""
    from app.core.pipeline import _normalize_classification

    cls = _normalize_classification(
        {"EVENT_TYPE": "package", "BILLING_MODEL": "PACKAGE_PER_SERVING"},
        {"EVENT_NOTES_HTML": "<p>waived <strong>$50</strong> destination fee</p>",
         "ADMIN_NOTES": "", "DRIVER_NOTES": ""},
    )
    assert cls["WAIVED_FEES"][0]["amount"] == 50.0
