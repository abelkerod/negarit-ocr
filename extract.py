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
import re

URL_RE = re.compile(r"https?://[^\s<>\"')\]]+")
# A wrapped link continues on a line that is all URL characters, or starts with
# one no sentence starts with. Real CBE receipts wrap mid-token exactly here.
CONTINUATION = re.compile(r"\n(?:([\w\-.~%/?=&#:]+)(?=\n|$)|([\-._/?=&%~][\w\-.~%/?=&#:]*))")

PROVIDERS = {
    # FT + YYDDD + 4-6 alnum, e.g. FT25123ABC45.
    "cbe": {
        "token": re.compile(r"\bFT\d{5}[A-Z0-9]{4,6}\b"),
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
            links.append(url)
        elif spec["legacy"] and spec["legacy"](host):
            legacy.append(url)

    tokens = []
    for tok in spec["token"].findall(normalize(joined)):
        if tok not in tokens:
            tokens.append(tok)

    reference = links[0] if links else (tokens[0] if spec["token_is_evidence"] and tokens else None)
    return {"links": links, "tokens": tokens, "legacy": legacy, "reference": reference}
