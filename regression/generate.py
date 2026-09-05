"""
Builds a labelled corpus of synthetic CBE and telebirr receipt screenshots.

Every image records the reference it should yield, so a run is scored rather
than eyeballed. Variation is the point: fonts, sizes, themes, phone
resolutions, JPEG damage, blur, noise, rescaling and crops, so the corpus
shows where reading degrades instead of only that it works on a clean render.

    uv run --group dev python regression/generate.py [count]

Synthetic receipts are not real ones. Tokens here are uniform over all 36
alphanumerics, which manufactures far more O/0 and I/1 collisions than a real
reference carries, so the score this produces is a floor rather than an
estimate. It is for catching regressions between two runs, not for quoting.
"""
import json
import os
import random
import string
import subprocess
import sys
import datetime
from datetime import datetime, timedelta

import qrcode
from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "corpus")
N = int(sys.argv[1]) if len(sys.argv) > 1 else 500
random.seed(20260905)

FONTS = sorted({
    line.split(":")[0].strip()
    for line in subprocess.run(["fc-list", ":", "file"], capture_output=True, text=True).stdout.splitlines()
    if any(k in line for k in ("LiberationSans", "LiberationSerif", "LiberationMono", "DejaVuSans", "NotoSans-"))
})
FONTS = [f for f in FONTS if f.endswith(".ttf") and os.path.exists(f)]
if not FONTS:
    sys.exit("no usable fonts found; install fonts-liberation or fonts-dejavu")

FIRST = ["ABEL", "GELILA", "LEWI", "SARON", "DAWIT", "HANA", "YONAS", "MERON", "BIRUK", "TIGIST",
         "KIRUBEL", "SELAM", "NATNAEL", "RAHEL", "EYOB", "BETELHEM", "SAMUEL", "MAHLET", "ROBEL", "FIKIR"]
LAST = ["GIRMA", "ABEBE", "HAILE", "TESFAYE", "BEKELE", "ALEMU", "KEBEDE", "WOLDE", "MENGISTU",
        "ASSEFA", "NEWAY", "DESTA", "TADESSE", "GEBRE", "MULUGETA"]
ALNUM = string.ascii_uppercase + string.digits
# Measured over 494 CBE and 524 telebirr messages off a real handset. Random
# alphanumerics made a corpus that lies: it produced CBE tails full of vowels,
# which CBE never issues, and so invented failures the box will never meet.
CBE_TAIL = "0123456789BCDFGHJKLMNPQRSTVWXYZ"
TB_MONTHS = "ABCDEFGHIJKL"
TB_DAYS = "0123456789ABCDEFGHIJKLMNOPQRSTUV"
TB_YEARS = {2025: "C", 2026: "D"}


def name():   return f"{random.choice(FIRST)} {random.choice(LAST)}"
def amount(): return f"{random.choice([10,25,50,100,150,200,350,500,800,1250,2000,5000,12500]) + random.choice([0,.5,.25,.75]):,.2f}"
def acct():   return f"1{'*' * random.choice([5, 8])}{random.randint(1000, 9999)}"
def phone():  return f"2519{random.randint(10000000, 99999999)}"
def cbe_token(on: datetime.date) -> str:
    """FT + two-digit year + day of year + a five-character tail.

    The digits are the receipt's own date, because that is what CBE puts
    there. A corpus of random digits would be rejected as impossible dates by
    the very check it is meant to exercise.
    """
    return f"FT{on.year % 100:02d}{on.timetuple().tm_yday:03d}" + "".join(random.choices(CBE_TAIL, k=5))


def tb_token(on: datetime.date) -> str:
    """Year, month and day, then a digit, then six free characters.

    Telebirr opens a token with its own date, checked right on 427 real ones.
    """
    return (TB_YEARS.get(on.year, "D") + TB_MONTHS[on.month - 1] + TB_DAYS[on.day]
            + random.choice("0123456789") + "".join(random.choices(ALNUM, k=6)))


def when():
    d = datetime(2026, 9, 5) - timedelta(days=random.randint(0, 120), minutes=random.randint(0, 1440))
    return d.strftime("%d/%m/%Y"), d.strftime("%H:%M"), d.strftime("%H:%M:%S"), d.date()


def wrap(draw, text, font, width):
    """Wrap on spaces, and inside a word too: a receipt link has no spaces to break on."""
    out = []
    for para in text.split("\n"):
        if not para:
            out.append("")
            continue
        line = ""
        for word in para.split(" "):
            while draw.textlength(word, font=font) > width:
                cut = len(word)
                while cut > 1 and draw.textlength(word[:cut], font=font) > width:
                    cut -= 1
                if line:
                    out.append(line)
                    line = ""
                out.append(word[:cut])
                word = word[cut:]
            trial = f"{line} {word}".strip()
            if draw.textlength(trial, font=font) <= width or not line:
                line = trial
            else:
                out.append(line)
                line = word
        out.append(line)
    return out


def body_for(kind):
    """The text, plus the reference the box is expected to answer with."""
    date, hm, hms, on = when()
    if kind.startswith("cbe"):
        tok = cbe_token(on)
        # Half carry the link CBE still resolves, half the retired apps host.
        # A retired link is never the reference, and neither is a bare FT
        # number, so those images expect nothing back.
        live = random.random() < 0.5
        link = (f"https://mbreciept.cbe.com.et/v2-{''.join(random.choices(string.ascii_letters + string.digits, k=20))}"
                if live else f"https://apps.cbe.com.et:100/?id={tok}{random.randint(10000000, 99999999)}")
        if kind == "cbe_sms":
            text = (f"Dear {name()} your Account {acct()} has been debited with ETB {amount()} "
                    f"Service Charge ETB 2.00 VAT ETB 0.30 on {date} at {hm} with Ref {tok} "
                    f"transferred to {name()}. Your Current Balance is ETB {amount()}.\n"
                    f"Thanks for Banking with CBE.\n{link}")
        elif kind == "cbe_ussd":
            text = (f"Transaction completed.\nRef: {tok}\nAmount: ETB {amount()}\n"
                    f"To: {name()}\nDate: {date} {hm}\nBalance: ETB {amount()}")
            link = None
        else:
            text = (f"Transaction Completed Successfully!\n\nTransaction Summary\n\n"
                    f"ETB {amount()} has been debited from {name()} for {name()} "
                    f"on {date} {hms} with transaction ID: {tok}. Reason: MB Transfer")
        return text, "cbe", (link if link and "mbreciept" in link else None), tok, link
    tok = tb_token(on)
    link = f"https://transactioninfo.ethiotelecom.et/receipt/{tok}"
    if kind == "tb_sms":
        text = (f"Dear {name().title().split()[0]}, You have transferred ETB {amount()} to "
                f"{name().title()}({phone()}) on {date} {hms}. Your transaction number is {tok}. "
                f"Your current balance is ETB {amount()}. Thank you for using telebirr.\n{link}")
        return text, "telebirr", link, tok, link
    text = (f"Transaction Successful\n\nTransaction Number\n{tok}\n\nAmount\nETB {amount()}\n\n"
            f"Receiver Name\n{name().title()}\n\nReceiver Phone\n{phone()}\n\nDate\n{date} {hms}")
    return text, "telebirr", tok, tok, None


# Sampled, not cycled: a short run must keep the same mix as a long one.
KINDS = ["cbe_sms", "cbe_app", "cbe_ussd", "tb_sms", "tb_app"]
WEIGHTS = [30, 18, 10, 28, 14]
RES = [(720, 1280), (1080, 1920), (1080, 2400), (1440, 3200), (828, 1792), (1170, 2532)]


def main():
    os.makedirs(OUT, exist_ok=True)
    for stale in os.listdir(OUT):
        os.remove(os.path.join(OUT, stale))
    manifest = []
    for i in range(N):
        kind = random.choices(KINDS, weights=WEIGHTS)[0]
        text, provider, expect, token, link = body_for(kind)
        width, height = random.choice(RES)
        dark = (provider == "telebirr") if random.random() < 0.75 else (random.random() < 0.5)
        img = Image.new("RGB", (width, height), (18, 18, 18) if dark else (255, 255, 255))
        draw = ImageDraw.Draw(img)
        fg = (232, 232, 232) if dark else (24, 24, 24)
        font_px = max(20, int(width * random.uniform(0.030, 0.046)))
        font = ImageFont.truetype(random.choice(FONTS), font_px)
        margin, y = int(width * 0.055), int(height * 0.03)
        if random.random() < 0.7:  # status bar clutter, like a real screenshot
            small = ImageFont.truetype(font.path, int(font_px * 0.8))
            draw.text((margin, y), "12:51", font=small, fill=fg)
            draw.text((width - margin - draw.textlength("4G  87%", font=small), y), "4G  87%", font=small, fill=fg)
            y += int(font_px * 2.0)
        for line in wrap(draw, text, font, width - 2 * margin):
            draw.text((margin, y), line, font=font, fill=fg)
            y += int(font_px * 1.45)

        # A CBE app receipt prints its live link as a QR and nothing else.
        has_qr = kind == "cbe_app"
        if has_qr:
            payload = f"https://mbreciept.cbe.com.et/v2-{''.join(random.choices(string.ascii_letters + string.digits, k=20))}"
            qr = qrcode.make(payload).convert("RGB")
            side = int(width * 0.45)
            qr = qr.resize((side, side), Image.NEAREST)
            img.paste(qr, (margin, min(y + font_px, height - side - margin)))
            expect, link = payload, payload

        tier, ops = "clean", []
        if i / N >= 0.60:
            tier = "degraded"
            if random.random() < 0.55:
                f = random.uniform(0.35, 0.75)
                ops.append(f"scale{f:.2f}")
                img = img.resize((int(img.width * f), int(img.height * f)), Image.LANCZOS)
            if random.random() < 0.40:
                b = random.uniform(0.6, 1.8)
                ops.append(f"blur{b:.1f}")
                img = img.filter(ImageFilter.GaussianBlur(b))
            if random.random() < 0.35:
                ops.append("crop")
                img = img.crop((0, 0, img.width, int(img.height * random.uniform(0.55, 0.85))))
            if random.random() < 0.30:
                ops.append("noise")
                px = img.load()
                for _ in range(int(img.width * img.height * 0.02)):
                    x, yy = random.randrange(img.width), random.randrange(img.height)
                    px[x, yy] = tuple(max(0, min(255, c + random.randint(-45, 45))) for c in px[x, yy])

        if i / N >= 0.80:
            q = random.choice([70, 50, 35, 25])
            ops.append(f"jpegq{q}")
            fname = f"{i:04d}_{kind}.jpg"
            img.save(os.path.join(OUT, fname), "JPEG", quality=q)
        else:
            fname = f"{i:04d}_{kind}.png"
            img.save(os.path.join(OUT, fname), "PNG")

        manifest.append({"file": fname, "kind": kind, "provider": provider, "expect": expect,
                         "token": token, "link": link, "has_qr": has_qr, "tier": tier, "ops": ops,
                         "res": list(img.size), "dark": dark, "font_px": font_px})

    with open(os.path.join(HERE, "manifest.json"), "w") as fh:
        json.dump(manifest, fh, indent=1)
    print(f"wrote {len(manifest)} images to {OUT}")
    from collections import Counter
    print("kinds:", dict(Counter(m["kind"] for m in manifest)))
    print("tiers:", dict(Counter(m["tier"] for m in manifest)))
    print("with a reference to find:", sum(1 for m in manifest if m["expect"]))


if __name__ == "__main__":
    main()
