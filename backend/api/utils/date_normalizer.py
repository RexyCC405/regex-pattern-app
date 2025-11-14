import re
from datetime import datetime
from typing import Optional, Tuple, Any

try:
    # Prefer dateparser (more intelligent: multi-language, Chinese "年/月/日", fuzzy strings, etc.)
    import dateparser  # type: ignore
    _HAS_DATEPARSER = True
except Exception:
    _HAS_DATEPARSER = False

try:
    # Fallback: dateutil
    from dateutil import parser as du_parser  # type: ignore
    _HAS_DATEUTIL = True
except Exception:
    _HAS_DATEUTIL = False


# —— Common date tokens: digits with slash/dash/dot; Chinese; compact 8-digit 20240512 —— #
DATE_TOKEN_RX = re.compile(
    r"""(?xi)
    (?:                                    # Typical: 2024-5-1 / 2024/05/01 / 2024.05.01
        \b\d{4}[-/.]\d{1,2}[-/.]\d{1,2}\b
    )|
    (?:                                    # Typical: 1/5/24 or 05/12/2024 (ambiguity decided by dayfirst)
        \b\d{1,2}[-/]\d{1,2}[-/]\d{2,4}\b
    )|
    (?:                                    # Compact: 20240501
        \b\d{8}\b
    )|
    (?:                                    # Chinese: 2024年5月1日 ("日" is optional)
        \b\d{4}年\d{1,2}月\d{1,2}日?\b
    )
    """,
)

_ORDINAL_RX = re.compile(r"(\b\d{1,2})(st|nd|rd|th)\b", re.IGNORECASE)

def _to_strftime(fmt: str) -> str:
    """Map a human format like 'YYYY-MM-DD HH:mm:ss' to a strftime pattern."""
    # Simple safe mapping (extend when needed)
    mapping = {
        "YYYY": "%Y", "YY": "%y",
        "MM": "%m", "DD": "%d",
        "HH": "%H", "mm": "%M", "ss": "%S",
    }
    out = fmt
    for k, v in mapping.items():
        out = out.replace(k, v)
    return out

def _compact8_to_iso(s: str) -> Optional[datetime]:
    # 20240501 → 2024-05-01
    if re.fullmatch(r"\d{8}", s):
        y, m, d = int(s[:4]), int(s[4:6]), int(s[6:])
        try:
            return datetime(y, m, d)
        except ValueError:
            return None
    return None

def _parse_one(token: str, dayfirst: Optional[bool]) -> Optional[datetime]:
    """Robust parsing: dateparser > dateutil > simple fallback."""
    # Remove ordinal suffix 1st/2nd/3rd/4th
    token = _ORDINAL_RX.sub(r"\1", token)

    # Normalize Chinese → standard separators
    # (some parsers can handle this directly; this is a light normalization)
    token = token.replace("年", "-").replace("月", "-").replace("日", "")

    # Compact 8-digit
    dt = _compact8_to_iso(token)
    if dt:
        return dt

    if _HAS_DATEPARSER:
        # settings: PREFER_DAY_OF_MONTH=first can reduce ambiguity;
        # languages are detected automatically
        settings = {
            "DATE_ORDER": "DMY" if dayfirst else "MDY",
            "PREFER_DAY_OF_MONTH": "first",
            "REQUIRE_PARTS": ["day", "month", "year"],
        }
        try:
            d = dateparser.parse(token, settings=settings)
            if d:
                return d
        except Exception:
            pass

    if _HAS_DATEUTIL:
        try:
            return du_parser.parse(token, dayfirst=bool(dayfirst), yearfirst=False, fuzzy=True)
        except Exception:
            pass

    # Final fallback: a few common formats
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d",
                "%d/%m/%Y", "%m/%d/%Y",
                "%d/%m/%y", "%m/%d/%y",
                "%d-%m-%Y", "%m-%d-%Y",
                "%d-%b-%Y", "%d-%B-%Y",
                "%b %d, %Y", "%B %d, %Y"):
        try:
            return datetime.strptime(token, fmt)
        except Exception:
            continue
    return None

def guess_dayfirst(samples: list[str]) -> Optional[bool]:
    """
    Roughly guess ambiguity from samples (if any first/second segment > 12, prefer dayfirst).

    Returns True/False; if it cannot be inferred, returns None (use default).
    """
    ambiguous_hits = 0
    dayfirst_votes = 0
    for s in samples[:200]:
        m = re.search(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})\b", s)
        if not m:
            continue
        a, b = int(m.group(1)), int(m.group(2))
        if a <= 12 and b <= 12:
            ambiguous_hits += 1
            # Look for other tokens > 12 as hints
            if re.search(r"\b(13|14|15|1[6-9]|2[0-9]|3[01])\b", s):
                # If 13–31 is present, cast a vote for dayfirst
                dayfirst_votes += 1
    if ambiguous_hits == 0:
        return None
    return True if dayfirst_votes >= max(1, ambiguous_hits // 3) else None

def normalize_date_text(
    text: str,
    out_fmt: str = "YYYY-MM-DD",
    dayfirst: Optional[bool] = None,
) -> tuple[str, int]:
    """
    Replace each "date chunk" in a block of text with out_fmt.

    Returns (new_text, number_of_replacements).
    """
    if not text:
        return text, 0

    fmt = _to_strftime(out_fmt)
    count = 0

    def _repl(m: re.Match) -> str:
        nonlocal count
        token = m.group(0)
        dt = _parse_one(token, dayfirst=dayfirst)
        if dt is None:
            return token
        count += 1
        return dt.strftime(fmt)

    new_text = DATE_TOKEN_RX.sub(_repl, str(text))
    return new_text, count

def normalize_cell_as_whole(
    value: Any,
    out_fmt: str = "YYYY-MM-DD",
    dayfirst: Optional[bool] = None,
) -> tuple[str, int]:
    """
    Treat the entire cell as a date.

    On success: return normalized string and 1;
    on failure: return original string and 0.

    Also includes simple handling of Excel serial dates (1900-based).
    """
    fmt = _to_strftime(out_fmt)

    # Excel serial (1900-based, float/int)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        # Rough threshold: Excel counts from 1899-12-30 (Windows);
        # restrict to a reasonable range
        n = float(value)
        if 10_000 <= n <= 80_000:
            base = datetime(1899, 12, 30)
            try:
                dt = base + timedelta(days=n)
                return dt.strftime(fmt), 1
            except Exception:
                pass

    s = "" if value is None else str(value)
    dt = _parse_one(s, dayfirst=dayfirst)
    if dt is None:
        return s, 0
    return dt.strftime(fmt), 1
