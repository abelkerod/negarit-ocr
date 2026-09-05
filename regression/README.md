# Regression suite

Generates labelled receipt screenshots, sends them to a deployed box, and
scores the `reference` that comes back against what the image was built to
contain.

    uv sync --group dev
    uv run --group dev python regression/generate.py 500
    OCR_URL=https://your-box OCR_SECRET=... uv run --group dev python regression/run.py 4

Throughput against concurrency instead of accuracy:

    OCR_URL=... OCR_SECRET=... uv run --group dev python regression/run.py sweep

The corpus and its results are gitignored. Regenerate rather than commit them:
the seed is fixed, so the same count gives the same images.

## What it is for

Comparing two runs. Deploy a change, run it again, see what moved.

## What it is not for

Quoting a number. Tokens here are uniform over all 36 alphanumerics, which
manufactures far more O/0 and I/1 collisions than a real CBE or telebirr
reference carries, so the score is a floor and not an estimate of real
accuracy. Synthetic renders also lack the subpixel antialiasing and
compression history of a real screenshot, and the degradation tiers are
guesses at the damage a real re-shared image has taken.

Two numbers this corpus got wrong before it was fixed, as a warning about the
rest: it once scored CBE link recovery at 0% because long links overflowed the
canvas instead of wrapping, and it once showed dark mode as 14 points worse
than light when the real cause was telebirr's longer tokens. Check that a
finding survives contact with a real receipt before acting on it.

## Reading the output

`by engine` is the one to watch. A CBE app receipt answers from its QR in
about a tenth of a second and is either exactly right or absent, because the
payload is checksummed. Everything else goes through OCR, where a miss is
usually one character: `0` read as `O` is the single most common failure, and
those show up under `answered wrong` rather than `answered nothing`.

A wrong answer is worse than none. For telebirr the bare token is submittable
evidence, so a misread token becomes a lookup that fails at the bank.
