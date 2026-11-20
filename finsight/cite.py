"""Citation check: every claim in an answer must cite a source that supports it.

A sentence is a claim. It fails when it has no citation, cites a source that doesn't exist, states a number
that isn't in its cited sources, quotes text that isn't in them word for word, or (for explanation
sentences with no numbers) uses key words that mostly don't appear in them."""
import re

SCALE = {"trillion": 1e12, "billion": 1e9, "bn": 1e9, "b": 1e9, "million": 1e6, "mm": 1e6, "m": 1e6,
         "thousand": 1e3, "k": 1e3}
NUM = re.compile(r"(?<![\w.])(-?\$?-?\d[\d,]*\.?\d*)\s*(%|percent|percentage points?|pp|trillion|billion|bn|million|mm|thousand|[bmk](?![a-z]))?",
                 re.I)
CITE = re.compile(r"\[S(\d+)\]")


def _is_label(token, text, start):
    """Years, fiscal labels and citation ids are not claims."""
    v = token.replace("$", "").replace(",", "")
    if re.fullmatch(r"(19|20)\d\d", v) and "$" not in token:
        return True
    before = text[max(0, start - 2):start]
    return bool(re.search(r"(FY|Q|S|\[S|H)$", before, re.I))


def numbers(text):
    """(token, [(value, tolerance), ...]) for each claim-like number in text."""
    out = []
    for m in NUM.finditer(text):
        tok, unit = m.group(1).rstrip("."), (m.group(2) or "").lower()
        if _is_label(tok, text, m.start()):
            continue
        raw = tok.replace("$", "").replace(",", "")
        try:
            x = float(raw)
        except ValueError:
            continue
        half = 0.5 * 10 ** -(len(raw.split(".")[1]) if "." in raw else 0)  # rounding of the shown digits
        if unit in ("%", "percent") or unit.startswith("percentage") or unit == "pp":
            cands = [(x, half), (x / 100, half / 100)]
        else:
            k = SCALE.get(unit, 1)
            cands = [(x * k, half * k)]
        out.append((m.group(0).strip(), cands))
    return out


def _source_values(text):
    vals = []
    for m in re.finditer(r"-?\d[\d,]*\.?\d*(?:e[+-]?\d+)?", text, re.I):
        try:
            vals.append(float(m.group(0).replace(",", "")))
        except ValueError:
            pass
    # MD&A tables are usually "in millions"; allow that scale too.
    return vals + [v * 1e6 for v in vals] + [v * 1e3 for v in vals]


def _matches(cands, source_vals):
    return any(abs(c - s) <= max(tol, abs(c) * 0.005) for c, tol in cands for s in source_vals)


STOP = set("""about above after again against also among because been before being below between both could
during each from further have having here into itself more most other over same should since some such than
that their them then there these they this those through under until very were what when where which while
whose will with would year years fiscal company company's quarter quarters compared increase increased
decrease decreased primarily mainly mostly driven higher lower result results due""".split())
QUOTE = re.compile(r"[\"“”]([^\"“”]{12,})[\"“”]")
MIN_SUPPORT = 0.5


def _norm(text):
    return " ".join(text.replace("’", "'").lower().split())


def _stems(text):
    return {w[:6] for w in re.findall(r"[a-z][a-z'\-]{3,}", _norm(text)) if w not in STOP}


def support(sentence, source_text):
    """Share of the sentence's key words that appear in the source (crude 6-letter stems)."""
    words = _stems(sentence)
    return len(words & _stems(source_text)) / len(words) if words else 1.0


def check(answer, sources):
    """sources: {id: text}. Returns a list of problems; empty means every number is cited and found."""
    problems = []
    # models write citations as 【S1】 and use narrow no-break spaces; normalize before checking
    answer = answer.replace("【", "[").replace("】", "]").replace("\u202f", " ").replace("\xa0", " ").replace("**", "")
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", answer):
        plain = CITE.sub("", sentence).strip()
        if len(plain.split()) < 4 and not numbers(plain):
            continue  # headings, "ANSWER:" stubs and fragments are not claims
        nums = numbers(plain)
        ids = [int(i) for i in CITE.findall(sentence)]
        missing = [i for i in ids if i not in sources]
        if missing:
            problems.append(f"cites a source that does not exist: {missing}")
        found = [i for i in ids if i in sources]
        if not found:
            problems.append(f"uncited claim: {plain[:120]}")
            continue
        cited = "\n".join(sources[i] for i in found)
        for q in QUOTE.findall(plain):
            if _norm(q) not in _norm(cited):
                problems.append(f"quote not found in cited sources {found}: {q[:80]!r}")
        if not nums and support(plain, cited) < MIN_SUPPORT:
            problems.append(f"cited sources {found} do not support: {plain[:120]}")
        vals = [v for i in found for v in _source_values(sources[i])]
        for text, cands in nums:
            if not _matches(cands, vals):
                problems.append(f"number {text!r} not found in cited sources {found}")
    return problems
