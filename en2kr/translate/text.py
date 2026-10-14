"""Provider-agnostic text handling around machine translation.

Glossary: game terms are swapped for 【Korean】 before translation so any
engine keeps them and still attaches natural Korean particles around them;
the brackets are removed afterwards (tolerating engines that rewrite them).
Slang: English shorthand is expanded before translation ("gg" -> "good game").
"""
import re

_OPEN = "【"
_CLOSE = "】"
_ANY_OPEN = r"[【\[「『〔<(（]"
_ANY_CLOSE = r"[】\]」』〕>)）]"


def _term_regex(keys, plurals):
    keys = sorted(keys, key=len, reverse=True)  # "Raider Hatch Key" before "Raider"
    suffix = r"(?:'s|s|es)?" if plurals else ""
    return re.compile(r"(?<![\w-])(" + "|".join(re.escape(k) for k in keys) + r")" + suffix + r"(?![\w-])",
                      re.IGNORECASE)


class Glossary:
    def __init__(self, entries):
        self._map = {}
        for k, v in (entries or {}).items():
            k, v = str(k).strip(), str(v).strip()
            if k and v:
                self._map[k.lower()] = v
        self._re = _term_regex(self._map, plurals=True) if self._map else None

    def protect(self, text):
        """Return (text with terms replaced by 【Korean】, list of Korean terms used)."""
        if not self._re:
            return text, []
        used = []

        def sub(m):
            kor = self._map[m.group(1).lower()]
            used.append(kor)
            return f"{_OPEN}{kor}{_CLOSE}"

        return self._re.sub(sub, text), used

    @staticmethod
    def restore(text, used):
        for kor in sorted(set(used), key=len, reverse=True):
            text = re.sub(_ANY_OPEN + r"\s*" + re.escape(kor) + r"\s*" + _ANY_CLOSE, kor, text)
        return text.replace(_OPEN, "").replace(_CLOSE, "")


class Slang:
    def __init__(self, entries):
        self._map = {str(k).strip().lower(): str(v).strip() for k, v in (entries or {}).items()
                     if str(k).strip() and str(v).strip()}
        self._re = _term_regex(self._map, plurals=False) if self._map else None

    def expand(self, text):
        if not self._re:
            return text
        return self._re.sub(lambda m: self._map[m.group(1).lower()], text)
