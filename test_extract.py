"""
Fixtures are shaped like real receipts but carry no real account or name.

The wrapped-link cases are the ones that matter: CBE breaks its receipt link
mid-token across two lines, and OCR reads it back that way.

    python3 test_extract.py
"""
from extract import extract, join_wrapped_urls

# How OCR actually reads a CBE SMS screenshot: the link splits after "/v2".
CBE_SMS = """Dear TEST NAME your Account 1*****0000 has been debited with ETB
250.00 Service Charge ETB 2.00 VAT ETB 0.30 on 05/09/2026 at 10:12 with Ref
FT26241X7KQ3 transferred to OTHER NAME. Your current balance is ETB 100.00.
Thanks for Banking with CBE.
https://mbreciept.cbe.com.et/v2
-hfHCxGxayyn332GrsZRI for
feedback:
https://forms.gle/kGNGQpG3mQCCk"""

CBE_SMS_LEGACY_ONLY = """Ref FT26241X7KQ3 transferred to OTHER NAME.
https://apps.cbe.com.et:100/?id=FT26241X7KQ322967116"""

TELEBIRR_SMS = """Dear Test, You have transferred ETB 800.00 to Other Name
(251900000000) on 05/09/2026 10:12:00. Your transaction number is DGS2BT1W54.
Your current balance is ETB 1,240.55. Thank you for using telebirr."""


def check(label, got, want):
    assert got == want, f"{label}\n  got:  {got!r}\n  want: {want!r}"
    print(f"  ok  {label}")


def main():
    r = extract(CBE_SMS, "cbe")
    check("cbe: wrapped link is rejoined",
          r["reference"], "https://mbreciept.cbe.com.et/v2-hfHCxGxayyn332GrsZRI")
    check("cbe: the FT token is still reported", r["tokens"], ["FT26241X7KQ3"])

    r = extract(CBE_SMS_LEGACY_ONLY, "cbe")
    check("cbe: a retired link is never the reference", r["reference"], None)
    check("cbe: but it is named as legacy",
          r["legacy"], ["https://apps.cbe.com.et:100/?id=FT26241X7KQ322967116"])
    check("cbe: a bare FT number is not evidence", r["reference"], None)

    r = extract(TELEBIRR_SMS, "telebirr")
    check("telebirr: the token is the reference", r["reference"], "DGS2BT1W54")

    r = extract("Ref FT 26241X7KQ3 transferred", "cbe")
    check("cbe: OCR's space after FT is closed up", r["tokens"], ["FT26241X7KQ3"])

    r = extract(TELEBIRR_SMS, "cbe")
    check("a telebirr receipt read as cbe finds nothing", r["reference"], None)

    r = extract(CBE_SMS, "unknown")
    check("an unknown provider finds nothing", r["reference"], None)

    check("an unwrapped link is left alone",
          join_wrapped_urls("see https://example.com/a and stop"),
          "see https://example.com/a and stop")

    # The five digits after FT are a day of year, so a misread that puts a
    # letter or an impossible day there is structurally not a reference.
    r = extract("Ref FT26999BCDFG transferred", "cbe")
    check("cbe: day 999 is a misread, not a reference", r["tokens"], [])
    r = extract("Ref FT26000BCDFG transferred", "cbe")
    check("cbe: day 000 is a misread too", r["tokens"], [])
    r = extract("Ref FT26236DZD16 transferred", "cbe")
    check("cbe: a real day of year survives", r["tokens"], ["FT26236DZD16"])
    r = extract("Ref FT27366BCDFG transferred", "cbe")
    check("cbe: day 366 needs a leap year", r["tokens"], [])
    r = extract("Ref FT28366BCDFG transferred", "cbe")
    check("cbe: 2028 has one", r["tokens"], ["FT28366BCDFG"])
    r = extract("Ref FT26236DZD1 and FT26236DZD16 seen", "cbe")
    check("cbe: the four-character read is discarded", r["tokens"], ["FT26236DZD16"])

    # Measured over 494 CBE and 524 telebirr messages off a real handset.
    # CBE puts no vowel in a tail, so an O or an I there was never in the image.
    r = extract("Ref FT26236DZDI6 transferred", "cbe")
    check("cbe: I in a tail is a one", r["tokens"], ["FT26236DZD16"])
    r = extract("Ref FT26236DZDO6 transferred", "cbe")
    check("cbe: O in a tail is a zero", r["tokens"], ["FT26236DZD06"])
    r = extract("Ref FT26236DZDA6 transferred", "cbe")
    check("cbe: a vowel that is not a homoglyph is dropped", r["tokens"], [])
    r = extract("Ref FT26236DZD1 transferred", "cbe")
    check("cbe: a four-character tail is not a reference", r["tokens"], [])

    # Telebirr does use both O and 0, but not in the same place: its fourth
    # character is always a digit and its third is never a zero.
    r = extract("transaction number is DHTO9Z7NCY.", "telebirr")
    check("telebirr: O in the digit slot is a zero", r["reference"], "DHT09Z7NCY")
    r = extract("https://transactioninfo.ethiotelecom.et/receipt/DHTO9Z7NCY", "telebirr")
    check("telebirr: and the link is repaired with it",
          r["reference"], "https://transactioninfo.ethiotelecom.et/receipt/DHT09Z7NCY")
    r = extract("transaction number is DHT09Z7NCY.", "telebirr")
    check("telebirr: a correct token is left alone", r["reference"], "DHT09Z7NCY")
    # A telebirr token opens with its own date: year, month, day.
    r = extract("transaction number is DBV12BCDFG.", "telebirr")
    check("telebirr: February has no 31st", r["reference"], None)
    r = extract("transaction number is DMA12BCDFG.", "telebirr")
    check("telebirr: there is no thirteenth month", r["reference"], None)
    r = extract("transaction number is DHT09Z7NCY.", "telebirr")
    check("telebirr: a real August token is fine", r["reference"], "DHT09Z7NCY")

    # A QR payload arrives as its own line and needs no joining.
    r = extract("https://mbreciept.cbe.com.et/v2-hfHCxGxdqOQNRK57GBpT", "cbe")
    check("a QR link is taken whole",
          r["reference"], "https://mbreciept.cbe.com.et/v2-hfHCxGxdqOQNRK57GBpT")
    print("all passed")


if __name__ == "__main__":
    main()
