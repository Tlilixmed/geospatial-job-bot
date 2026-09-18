"""Plain-language front end for the Telegram commands (English and French, typo tolerant).

interpret("i want the top 5 matching offers") -> ("jobs", "5")
interpret("select jobs in threshold 60-70")   -> ("range", "60 70")
interpret("waht command you can do")          -> ("help", "")
interpret("lidar jobs in montreal")           -> ("search", "lidar montreal")

Deterministic rules only: intent keywords are matched on an accent-folded, typo-corrected copy of the
message, while free-text arguments (a company to mute, search words) are taken from the original words.
"""
from __future__ import annotations

import re
from difflib import get_close_matches
from typing import Callable

from ..utils.text import fold

# Words worth repairing when mistyped. Deliberately limited to command vocabulary so that search terms and
# company names are never "corrected".
VOCAB = [
    "what", "which", "command", "commands", "help", "status", "weekly", "summary", "threshold", "between", "above",
    "below", "under", "over", "least", "score", "scored", "pause", "resume", "continue", "stop", "alerts",
    "notifications", "mute", "unmute", "muted", "block", "ignore", "hide", "unhide", "remove", "restore", "applied",
    "apply", "applications", "explain", "details", "internship", "internships", "interns", "include", "exclude",
    "without", "matching", "matches", "offers", "jobs", "latest", "newest", "recent", "search", "find", "show", "select",
    "location", "locations", "country", "countries", "prefer", "refresh", "settings", "range", "minimum",
    "seuil", "offres", "meilleures", "montre", "cherche", "trouve", "arrete", "reprends", "candidatures", "postule",
    "pourquoi", "explique", "stages", "stagiaires", "bloque", "cache", "statut", "resume", "aide", "commandes",
    # short real words listed so they are never "corrected" into a neighbour (last -> least, more -> mute...)
    "last", "list", "run", "scan", "reset", "again", "back", "more", "most", "best", "rate", "note", "mode", "fine",
    "interesting", "relevant", "available", "please", "current",
]
NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
                "ten": 10, "fifteen": 15, "twenty": 20, "thirty": 30, "un": 1, "deux": 2, "trois": 3, "quatre": 4,
                "cinq": 5, "dix": 10, "quinze": 15, "vingt": 20, "trente": 30}
# Words that carry no search meaning: removed before a sentence is treated as a search.
FILLER = set("""
i i'd id im iwant we you me my mine your it its this that these those there here the a an some any all every
please pls plz thanks thank hi hello hey ok okay so then now just also only really
want wanna need would could can may might will shall should like love get got have has do does did is are was be been
show give send list tell find search look looking see select display fetch bring pull check
for of in at on to from with by about into near around as than and or but not no yes
top best better good great latest newest recent new current currently today available open matching matches match
matched relevant related suitable interesting
job jobs offer offers offre offres opening openings position positions poste postes emploi emplois role roles
result results opportunity opportunities posting postings vacancy vacancies annonce annonces
je j j'ai tu il nous vous moi toi veux veut voudrais aimerais peux peut pouvez peut-etre svp stp merci bonjour salut
montre montrez montre-moi donne donnez donne-moi envoie envoyez affiche affichez cherche cherchez trouve trouvez liste
le la les l un une des du de d au aux en dans sur sous pour par avec sans et ou mais pas plus moins
meilleur meilleure meilleurs meilleures dernier derniere derniers dernieres nouveau nouvelle nouveaux nouvelles
actuel actuelle actuels actuelles disponible disponibles correspondant correspondantes quelles quels quel quelle
what which who how when where whats
""".split())

RANGE_RE = re.compile(r"(?<![\d.])(\d{1,3})\s*(?:-|–|to|and|a|et|until|jusqu\s?a)\s*(\d{1,3})(?![\d.])")
ABOVE_RE = re.compile(r"\b(?:above|over|at least|minimum|min|more than|greater than|higher than|from|superieure?s? a|"
                      r"au moins|plus de|au dessus de|>=?)\s*(\d{1,3})\b")
BELOW_RE = re.compile(r"\b(?:below|under|at most|maximum|max|less than|lower than|up to|inferieure?s? a|moins de|"
                      r"en dessous de|<=?)\s*(\d{1,3})\b")
SCORE_WORDS_RE = re.compile(r"\b(?:threshold|seuil|scores?|scored|scoring|rated|rating|points?|range|percent|%|notes?)\b")


def _tokens(text: str) -> tuple[list[str], list[str]]:
    """Return (original tokens, intent tokens): same length, the second folded and typo-corrected."""
    cleaned = re.sub(r"[“”\"`´«»!?;()\[\]{}]", " ", text.replace("’", "'"))
    original = [tok.strip(".:") for tok in cleaned.split()]   # commas stay: they separate list arguments
    original = [tok for tok in original if tok.strip(",")]
    fixed = []
    for tok in original:
        word = fold(tok.strip(","))
        if len(word) >= 4 and word.isalpha() and word not in VOCAB and word not in FILLER:
            # conservative: same first letter, at most one character of length difference, very close spelling
            close = [c for c in get_close_matches(word, VOCAB, n=3, cutoff=0.8)
                     if c[0] == word[0] and abs(len(c) - len(word)) <= 1]
            if close:
                word = close[0]
        fixed.append(word)
    return original, fixed


def _has(text: str, pattern: str) -> re.Match | None:
    return re.search(pattern, text)


def _number(tokens: list[str], low: int, high: int) -> int | None:
    for tok in tokens:
        value = int(tok) if tok.isdigit() else NUMBER_WORDS.get(tok)
        if value is not None and low <= value <= high:
            return value
    return None


def _content(original: list[str], fixed: list[str], drop: set[str] = frozenset()) -> str:
    """Original words that are neither filler, numbers nor intent keywords: the search phrase."""
    keep = [o.strip(",") for o, f in zip(original, fixed)
            if f not in FILLER and f not in drop and not f.isdigit() and f not in NUMBER_WORDS and len(f) > 1]
    return " ".join(keep)


def _after(original: list[str], fixed: list[str], keywords: set[str], skip: set[str]) -> str:
    """Original words following the last intent keyword, minus leading filler such as 'jobs from'."""
    last = max((i for i, f in enumerate(fixed) if f in keywords), default=-1)
    rest = list(zip(original[last + 1:], fixed[last + 1:]))
    while rest and (rest[0][1] in skip or rest[0][1] in FILLER):
        rest.pop(0)
    while rest and rest[-1][1] in {"please", "pls", "svp", "stp", "again", "anymore", "jobs", "offers", "offres"}:
        rest.pop()
    return " ".join(o for o, _ in rest).strip(", ")


def interpret(text: str, is_code: Callable[[str], bool] = lambda token: False) -> tuple[str, str]:
    """Map a free-text message to (command, argument). Always returns something: search is the fallback."""
    original, fixed = _tokens(text)
    t = " ".join(fixed)
    codes = [tok for tok in fixed if re.fullmatch(r"[0-9a-f]{5}", tok) and is_code(tok)]
    code = codes[0] if codes else ""

    if not fixed:
        return "help", ""
    # "ai summary", "what does the AI think of the top 5", "second opinion": before help/weekly, which share words
    if not code and (_has(t, r"\b(ai|ia|a\.i\.?|artificial intelligence|second opinion|deuxieme avis|avis de l ia)\b")
                     or _has(t, r"\b(fit|fits)\b.*\b(me|best|profile)\b")):
        return "ai", str(_number(fixed, 1, 25) or "")
    if _has(t, r"\b(help|aide|commands?|commandes?|menu|options|manual|guide)\b") or \
            _has(t, r"\bwhat\b.*\b(can|do|does|could)\b") or _has(t, r"\bque (?:peux|sais|fais)\b") or \
            _has(t, r"\bhow (?:do|does|to)\b.*\b(work|use)\b") or _has(t, r"\bcomment (?:ca marche|t utiliser|utiliser)\b"):
        return "help", ""
    if _has(t, r"\b(weekly|hebdo\w*|summary|recap|bilan|recapitulatif)\b"):
        return "weekly", ""

    # internships on/off
    if _has(t, r"\b(intern|interns|internships?|stages?|stagiaires?|trainees?|alternances?)\b"):
        if _has(t, r"\b(exclude|excluding|without|no|hide|disable|remove|skip|stop|sans|exclu\w*|enleve\w*|retire\w*|off|non|pas)\b"):
            return "interns", "off"
        if _has(t, r"\b(include|including|with|show|allow|enable|add|avec|inclu\w*|ajoute\w*|accepte\w*|on|oui)\b"):
            return "interns", "on"

    # things that need a job code
    if code:
        if _has(t, r"\b(unhide|restore|bring back|reaffiche\w*|remets?)\b"):
            return "unhide", code
        if _has(t, r"\b(pitch|draft|cover ?letter|letter|motivation|lettre|write|redige\w*|ecris)\b"):
            return "pitch", code
        if _has(t, r"\b(appl(?:y|ied|ying|ication)|postule\w*|candidat\w*|sent my cv|envoye\w*)\b"):
            return "applied", code
        if _has(t, r"\b(hide|remove|dismiss|discard|drop|delete|skip|not interested|pas interesse\w*|cache\w*|supprime\w*|"
                   r"enleve\w*|retire\w*|ignore)\b"):
            return "hide", code
        return "why", code  # "why a3f9c", "explain a3f9c", "tell me about a3f9c" or the bare code
    if _has(t, r"\b(applications?|candidatures?|applied|apply|applying|postule\w*)\b"):
        return "applied", ""

    if _has(t, r"\b(status|statut|etat|health|alive|uptime|settings|parametres|configuration|config)\b") or \
            _has(t, r"\b(last|previous|derniere?)\s+(run|scan|execution|recherche)\b") or \
            _has(t, r"\b(are you|is it|tu es|ca)\s+(ok|there|working|running|alive|marche|fonctionne)\b"):
        return "status", ""

    # score ranges: "between 60 and 70", "threshold 60-70", "above 80", "moins de 65"
    setting = _has(t, r"\b(set|change|put|raise|lower|increase|decrease|make|update|mets?|mettre|regle\w*|fixe\w*|"
                      r"augmente\w*|baisse\w*|modifie\w*)\b")
    if not setting:
        m = RANGE_RE.search(t)
        if m and (SCORE_WORDS_RE.search(t) or _has(t, r"\b(between|entre|from|de)\b")):
            lo, hi = sorted((int(m.group(1)), int(m.group(2))))
            if hi <= 100:
                return "range", f"{lo} {hi}"
        above, below = ABOVE_RE.search(t), BELOW_RE.search(t)
        if above and int(above.group(1)) <= 100 and (SCORE_WORDS_RE.search(t) or int(above.group(1)) >= 41):
            return "range", f"{int(above.group(1))} 100"
        if below and int(below.group(1)) <= 100 and (SCORE_WORDS_RE.search(t) or int(below.group(1)) >= 41):
            return "range", f"0 {int(below.group(1))}"
    if _has(t, r"\b(threshold|seuil|cut ?off|minimum score|score minimum)\b"):
        numbers = [tok for tok in re.findall(r"\d{1,3}", t) if 0 < int(tok) <= 100]
        return "threshold", " ".join(numbers[:2])  # no number -> the command answers with the current values

    # pause / resume / run
    if _has(t, r"\b(pause|quiet|shut up|tais|silence)\b") or fixed == ["stop"] or \
            _has(t, r"\b(stop|arrete\w*|suspend\w*|halt|hold)\b.*\b(alerts?|alertes?|notifications?|notifs?|messages?|sending|"
                    r"bot|envoi\w*|everything|tout)\b"):
        return "pause", ""
    if _has(t, r"\b(unmuted?|reactive\w*)\b") or _has(t, r"\bshow\b.*\bagain\b"):
        return "unmute", _after(original, fixed, {"unmute", "unmuted", "show", "reactive", "reactiver"}, {"again"}).replace(" again", "")
    if _has(t, r"\b(muted|mutes)\b") or _has(t, r"\bwhat\b.*\bmute"):
        return "muted", ""
    mute_words = {"mute", "block", "ignore", "bloque", "bloquer", "silence", "exclude", "exclus", "exclure"}
    if any(f in mute_words for f in fixed) or _has(t, r"\b(stop showing|no more|do not show|don't show|dont show|"
                                                      r"ne (?:plus )?montre\w*(?: plus| pas)?|plus d[e']? ?offres? de)\b"):
        target = _after(original, fixed, mute_words | {"showing", "more", "show", "montre", "montrer", "plus", "de"},
                        {"jobs", "offers", "offres", "from", "at", "by", "de", "chez", "company", "entreprise", "the"})
        return "mute", target
    if _has(t, r"\b(resume|unpause|continue|restart|reprends?|reprendre|relance\w*|reactive\w*|wake up|go on|start again)\b"):
        return "resume", ""
    if _has(t, r"\b(run|scan|refresh|crawl|check|search|update|lance\w*|actualise\w*|cherche)\b.*\b(now|again|maintenant|"
               r"tout de suite|right away)\b") or _has(t, r"\b(start|launch|trigger|do|lance\w*)\s+(a |an |une |un )?(new )?"
                                                          r"(run|scan|search|crawl|recherche)\b"):
        return "run", ""

    # preferred locations
    place_word = _has(t, r"\b(locations?|countr(?:y|ies)|pays|lieux?|places?|regions?)\b")
    if place_word and _has(t, r"\b(reset|default|clear|anywhere|partout|reinitialise\w*)\b"):
        return "locations", "reset"
    if (setting and place_word) or _has(t, r"\bprefer\w*\b"):
        target = _after(original, fixed, {"locations", "location", "countries", "country", "pays", "lieux", "places",
                                          "prefer", "preferred", "prefere"}, {"to", "as", "are", "is", "a", ":"})
        return "locations", ", ".join(p.strip() for p in re.split(r",| and | et |/", target) if p.strip())

    # listing versus searching
    drop = {"high", "haute", "hautes", "strong", "excellent", "tier"}
    content = _content(original, fixed, drop)
    wants_list = _has(t, r"\b(top|best|latest|newest|recent|new|current|meilleure?s?|dernier\w*|nouve\w*|show|give|send|"
                         r"list|want|need|montre\w*|donne\w*|envoie\w*|affiche\w*|veux|voudrais|iwant|select|display)\b") or \
        _has(t, r"\b(jobs?|offers?|offres?|match\w*|openings?|positions?|postes?|emplois?|results?|opportunit\w*)\b")
    if content:
        return "search", content
    if wants_list:
        count = _number(fixed, 1, 40)
        command = "high" if _has(t, r"\b(high|hautes?|strong|excellent|top tier|best only)\b") else "jobs"
        return command, str(count) if count else ""
    return "help", ""
