"""
Finds the payment reference in text that came off a receipt.

The provider is told to us, never guessed. Deciding which bank a screenshot
belongs to is a different and harder problem, and a wrong guess here would
hand a buyer someone else's grammar; the caller already knows what the buyer
picked.

Links beat bare tokens. Both banks put the receipt link in their SMS and the
link carries the token verbatim, while the same token in the body is where OCR
confuses O/0 and l/I.

This mirrors Negarit's shared/extract-reference.ts. The two are separate
implementations of one grammar and will drift if only one is changed; the
fixtures in test_extract.py are the cheapest place to notice.
"""
import datetime
import itertools
import re

URL_RE = re.compile(r"https?://[^\s<>\"')\]]+")
# A wrapped link continues on a line that is all URL characters, or starts with
# one no sentence starts with. Real CBE receipts wrap mid-token exactly here.
CONTINUATION = re.compile(r"\n(?:([\w\-.~%/?=&#:]+)(?=\n|$)|([\-._/?=&%~][\w\-.~%/?=&#:]*))")

# Measured over 494 CBE and 524 telebirr messages from a real handset.
#
# CBE never puts a vowel in the tail, so an O or an I in a reference is not a
# reference that will fail to verify, it is a misread we can correct. Telebirr
# does use both O and 0, but not everywhere: its fourth character is always a
# digit and its third is never a zero, which is enough to fix the confusion
# exactly where it happens.
CBE_TAIL_ALPHABET = set("0123456789BCDFGHJKLMNPQRSTVWXYZ")
HOMOGLYPH_TO_DIGIT = {"O": "0", "I": "1"}
# A telebirr token opens with its own date: one letter for the year, one for
# the month, one for the day. Checked against the message that carried it on
# 427 real tokens, and right every time. The fourth character is always a
# digit but tracks nothing we could find.
TELEBIRR_MONTHS = "ABCDEFGHIJKL"
TELEBIRR_DAYS = "0123456789ABCDEFGHIJKLMNOPQRSTUV"   # index is the day, so 0 is unused
TELEBIRR_POSITIONS = [
    set("ABCDEFGHIJKLMNOPQRSTUVWXYZ"),          # 0, year: C is 2025 and D is 2026
    set(TELEBIRR_MONTHS),                       # 1, month
    set(TELEBIRR_DAYS[1:]),                     # 2, day, so never a zero
    set("0123456789"),                          # 3
]
DAYS_IN_MONTH = (31, 29, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)


def repair_cbe(token: str) -> str:
    """O is a zero and I is a one, because CBE's alphabet holds neither."""
    return token[:2] + "".join(HOMOGLYPH_TO_DIGIT.get(c, c) for c in token[2:])


# OCR reads the letter for the digit about twice as often as the reverse, so
# when two candidates both pass the check, the digit reading goes first.
CONFUSABLE = {"O": "0", "0": "O", "I": "1", "1": "I", "S": "5", "5": "S"}
PREFERRED = "015"


def telebirr_checksum_ok(token: str) -> bool:
    """The fourth character is the last decimal digit of the six that follow it.

    Positions 4 to 9 are one base-36 counter and position 3 is that number mod
    10. True on 492 of 492 real tokens, against a one-in-ten chance. Found by
    Fable 5.1; per-character schemes all sat at chance because the check is
    over the value, not over the digits.
    """
    if len(token) != 10 or not token[3].isdigit():
        return False
    try:
        return int(token[4:], 36) % 10 == int(token[3])
    except ValueError:
        return False


def repair_telebirr_checksum(token: str) -> list[str]:
    """Tokens worth submitting, best first. Empty means a misread we cannot fix.

    A single O/0, I/1 or S/5 flip anywhere in the free positions always moves
    the check digit, so a wrong token is caught before it is submitted rather
    than after the bank refuses it. Most of the time exactly one flip restores
    it, and that is the answer with no lookup at all.
    """
    if len(token) != 10:
        return [token]
    if telebirr_checksum_ok(token):
        return [token]
    slots = [i for i in range(4, 10) if token[i] in CONFUSABLE]
    for count in range(1, len(slots) + 1):      # fewest flips first
        found = []
        for chosen in itertools.combinations(slots, count):
            candidate = list(token)
            for i in chosen:
                candidate[i] = CONFUSABLE[candidate[i]]
            candidate = "".join(candidate)
            if telebirr_checksum_ok(candidate):
                found.append(candidate)
        if found:
            found.sort(key=lambda c: -sum(1 for i in slots if c[i] in PREFERRED))
            return found
    return []


def repair_telebirr(token: str) -> str:
    """Fix only the positions whose alphabet decides the answer."""
    out = list(token)
    for i, allowed in enumerate(TELEBIRR_POSITIONS):
        if i >= len(out) or out[i] in allowed:
            continue
        swap = {"O": "0", "0": "O", "I": "1", "1": "I", "S": "5", "5": "S"}.get(out[i])
        if swap and swap in allowed:
            out[i] = swap
    return "".join(out)


TB_RECEIPT = re.compile(r"ethiotelecom\.et/\S*?/?([A-Za-z0-9]{8,12})/?$", re.I)
TB_CANONICAL = "https://transactioninfo.ethiotelecom.et/receipt/"


def repair_telebirr_link(url: str) -> str:
    """Rebuild the link around its token, keeping nothing OCR read of the rest.

    Everything before the token is the same on every telebirr receipt, so
    reading it is a chance to be wrong for no gain: one real screenshot came
    back saying "recelpt". Only the last path segment carries information, and
    it carries the same misread as the loose token, so it is repaired too.

    Repairing only the loose token would leave the link wrong and still
    preferred, which is the worst outcome: a confident answer that fails at
    the bank.
    """
    m = TB_RECEIPT.search(url.rstrip("."))
    if not m:
        return url
    fixed = repair_telebirr(m.group(1).upper())
    best = repair_telebirr_checksum(fixed)
    return TB_CANONICAL + (best[0] if best else fixed)


def plausible_telebirr(token: str) -> bool:
    if len(token) != 10:
        return True  # 12- and 16-character forms exist; only the common one is pinned down
    if not all(c in allowed for c, allowed in zip(token, TELEBIRR_POSITIONS)):
        return False
    # The date it opens with has to be one that happened.
    month = TELEBIRR_MONTHS.index(token[1]) + 1
    return TELEBIRR_DAYS.index(token[2]) <= DAYS_IN_MONTH[month - 1]


def plausible_cbe(token: str) -> bool:
    """The five digits after FT are a date, not a serial.

    CBE runs Temenos T24, where an FT reference is FT + two-digit year + day of
    year. Every real receipt we hold decodes to the date printed on its own
    face. So a day outside 1-366 is not a reference that failed to verify, it is
    a misread: O in a digit slot, or a dropped character shifting the rest left.
    Rejecting it here costs a lookup we would have spent to learn the same thing.
    """
    if len(token) != 12 or not set(token[7:]) <= CBE_TAIL_ALPHABET:
        return False  # every real tail is five characters and holds no vowel
    year, day = int(token[2:4]), int(token[4:7])
    if not 1 <= day <= 366:
        return False
    if day == 366:  # only a leap year has one
        y = 2000 + year
        return y % 4 == 0 and (y % 100 != 0 or y % 400 == 0)
    return True


PROVIDERS = {
    # FT + YY + DDD (day of year) + a tail. Every real receipt we hold has a
    # five-character tail; the wider 4-6 is kept because the sample is small,
    # but a five is preferred over a four or six when both are on offer.
    "cbe": {
        "token": re.compile(r"\bFT\d{5}[A-Z0-9]{4,6}\b"),
        "valid": plausible_cbe,
        "repair": repair_cbe,
        "checksum": None,
        "repair_link": None,
        "prefer": lambda t: len(t) == 12,
        "link": lambda host: host == "mbreciept.cbe.com.et",
        # CBE retired the host these key, so they are named but never offered.
        "legacy": lambda host: host == "cbe.com.et" or host.endswith(".cbe.com.et"),
        "token_is_evidence": False,
    },
    # 10 uppercase alnum with both letters and digits, e.g. AB12CD34EF.
    "telebirr": {
        "token": re.compile(r"\b(?=[A-Z0-9]{10}\b)(?=[A-Z0-9]*\d)(?=[A-Z0-9]*[A-Z])[A-Z0-9]{10}\b"),
        "link": lambda host: host.endswith("ethiotelecom.et"),
        "legacy": None,
        "valid": plausible_telebirr,
        "repair": repair_telebirr,
        "checksum": repair_telebirr_checksum,
        "repair_link": repair_telebirr_link,
        "prefer": None,
        "token_is_evidence": True,
    },
}


def join_wrapped_urls(text: str) -> str:
    """SMS apps and OCR wrap long links across lines; rejoin them."""
    out, pos = [], 0
    for match in URL_RE.finditer(text):
        out.append(text[pos:match.start()])
        url, idx = match.group(0), match.end()
        while True:
            cont = CONTINUATION.match(text, idx)
            if not cont:
                break
            url += cont.group(1) or cont.group(2)
            idx = cont.end()
        out.append(url)
        pos = match.end()
    out.append(text[pos:])
    return "".join(out)


def host_of(url: str) -> str:
    m = re.match(r"https?://([^/:?#]+)", url, re.I)
    return m.group(1).lower() if m else ""


def normalize(text: str) -> str:
    # OCR sometimes inserts a space after "FT".
    return re.sub(r"\bFT\s+(?=\d{5})", "FT", text.upper())


def extract(text: str, provider: str) -> dict:
    """Links, tokens, and the one worth submitting, for a known provider."""
    spec = PROVIDERS.get(provider)
    if spec is None:
        return {"links": [], "tokens": [], "legacy": [], "reference": None}

    joined = join_wrapped_urls(text)
    links, legacy = [], []
    for url in URL_RE.findall(joined):
        host = host_of(url)
        if spec["link"](host):
            if spec.get("repair_link"):
                url = spec["repair_link"](url)
            links.append(url)
        elif spec["legacy"] and spec["legacy"](host):
            legacy.append(url)

    tokens = []
    for tok in spec["token"].findall(normalize(joined)):
        # Repair before judging: the alphabet says which character was meant,
        # so a reference is only impossible once that has been applied.
        if spec.get("repair"):
            tok = spec["repair"](tok)
        if spec.get("checksum"):
            fixed = spec["checksum"](tok)
            if not fixed:
                continue  # the check says this was misread and no flip restores it
            tok = fixed[0]
        if tok in tokens:
            continue
        if spec.get("valid") and not spec["valid"](tok):
            continue  # structurally impossible, so a misread rather than a miss
        tokens.append(tok)
    if spec.get("prefer"):
        tokens.sort(key=lambda t: not spec["prefer"](t))

    reference = links[0] if links else (tokens[0] if spec["token_is_evidence"] and tokens else None)
    return {"links": links, "tokens": tokens, "legacy": legacy, "reference": reference}
