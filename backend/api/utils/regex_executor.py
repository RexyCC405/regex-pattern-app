import re
import html
import pandas as pd
from typing import Dict, List, Tuple, Optional, Any

from .date_normalizer import normalize_date_text, normalize_cell_as_whole, guess_dayfirst


# Helpers
# columns & flags
def text_columns(df: pd.DataFrame) -> List[str]:
    """Return columns that are textual (object/string dtypes)."""
    return [c for c in df.columns if df[c].dtype == "object" or str(df[c].dtype).startswith("string")]


def _auto_columns(df: pd.DataFrame, intent: str, pattern: str, columns: Optional[List[str]]):
    """
    Auto-pick columns when the plan/LLM did not specify:
    - If intent is REPLACE and pattern is '^.*$' (overwrite entire cells): use ALL columns
    - Otherwise: only textual columns to avoid impacting numeric dtypes
    """
    if columns:  # Explicit list provided
        return columns
    if intent.lower() == "replace" and pattern == "^.*$":
        return list(df.columns)
    return text_columns(df)


def _compile_flags(flags: str) -> int:
    """Translate simple string flags into Python `re` flags."""
    f = 0
    if not flags:
        return re.IGNORECASE
    for ch in flags:
        if ch == "i":
            f |= re.IGNORECASE
        elif ch == "m":
            f |= re.MULTILINE
        elif ch == "s":
            f |= re.DOTALL
        elif ch == "x":
            f |= re.VERBOSE
        elif ch == "u":
            # Python 3 is unicode by default
            pass
    return f or re.IGNORECASE


# Row-filter normalization
def _normalize_row_filter(query: str, headers: List[str]) -> str:
    """Normalize a pandas query-like string for row filtering."""
    if not query:
        return query

    q = str(query)

    # "Col"/'Col' -> `Col`
    for col in sorted(headers, key=len, reverse=True):
        q = re.sub(rf'(?<!`)(["\']){re.escape(col)}\1(?!`)', f'`{col}`', q)

    # Bare column names used as identifiers -> `Col`
    for col in sorted(headers, key=len, reverse=True):
        q = re.sub(
            rf'(?<![`"\w]){re.escape(col)}(?=\s*(?:\.str\b|\.astype\b|\.isin\b|\)|\]|\b(?:in|not\s+in)\b|==|!=|>=|<=|>|<))',
            f'`{col}`',
            q
        )

    # Ensure first argument of .str.contains/.match/.fullmatch/.replace is a raw string
    q = re.sub(r'(\.str\.(?:contains|match|fullmatch|replace)\(\s*)([\'\"])', r'\1r\2', q)

    # Fix astype(str) -> astype("string") so df.query doesn't need a name 'str'
    # Also normalize astype("str") -> astype("string")
    q = re.sub(r'\.astype\(\s*str\s*\)', ".astype('string')", q)
    q = re.sub(r'\.astype\(\s*["\']str["\']\s*\)', ".astype('string')", q)

    # Strip unsupported 'case=' kwarg from .str.startswith/.endswith (pandas doesn't support it)
    def _strip_case_for_prefix(m: re.Match) -> str:
        inner = m.group("inner")
        inner2 = re.sub(r'\s*,\s*case\s*=\s*(?:True|False)', '', inner)
        inner2 = re.sub(r'case\s*=\s*(?:True|False)\s*,\s*', '', inner2)
        return f"{m.group('prefix')}({inner2})"

    q = re.sub(
        r'(?P<prefix>\.str\.(?:startswith|endswith))\(\s*(?P<inner>[^)]*?)\)',
        _strip_case_for_prefix,
        q,
    )

    ## TODO: add other normalisations???

    return q

def _to_string(s: pd.Series) -> pd.Series:
    """Coerce a Series to pandas' string dtype."""
    return s.astype("string")


def _to_numeric(s: pd.Series) -> pd.Series:
    """Coerce a Series to numeric (NaN on failure)."""
    return pd.to_numeric(s, errors="coerce")


# Clause parsers (fallbacks)
def _mask_from_clauses(df: pd.DataFrame, q: str) -> Optional[pd.Series]:
    """
    Best-effort AND-only clause parser. If nothing is recognized, return None.
    Supported:
      - `col` == 'value'                              (case-insensitive fallback)
      - `col` >= 123 / > 123 / <= 123 / < 123
      - `col`.str.contains('value', case=?, na=?)
      - `col`.str.match('rx', case=?, na=?)
      - `col`.str.fullmatch('rx', case=?, na=?)
      - `col`.str.startswith('prefix', na=?)
      - `col`.str.endswith('suffix', na=?)
      - Optional `.astype(str|"str"|"string")` before `.str.*` is supported.
    NOTE: This parser ANDs all recognized clauses. OR must be handled by a higher-level splitter.
    """
    mask: Optional[pd.Series] = None

    def _and(acc: Optional[pd.Series], cur: pd.Series) -> pd.Series:
        return cur if acc is None else (acc & cur)

    # Equality compare with string literal (case-insensitive)
    for col, quote, val in re.findall(r'`([^`]+)`\s*==\s*([\'"])(.*?)\2', q):
        if col in df.columns:
            s = _to_string(df[col])
            cur = s.str.casefold().eq(val.casefold()).fillna(False)
            mask = _and(mask, cur)

    # Numeric comparisons
    for col, op, val in re.findall(r'`([^`]+)`\s*(>=|<=|>|<)\s*([0-9]+(?:\.[0-9]+)?)', q):
        if col in df.columns:
            s = _to_numeric(df[col])
            if op == '>=':
                cur = s.notna() & (s >= float(val))
            elif op == '<=':
                cur = s.notna() & (s <= float(val))
            elif op == '>':
                cur = s.notna() & (s > float(val))
            else:
                cur = s.notna() & (s < float(val))
            mask = _and(mask, cur)

    # Unified pattern for .str.* ops (with optional astype, optional case/na)
    str_op_pat = re.compile(
        r'`(?P<col>[^`]+)`\s*'
        r'(?:\.astype\(\s*(?:str|["\'](?:str|string)["\'])\s*\))?\s*'
        r'\.str\.(?P<op>contains|match|fullmatch|startswith|endswith)\('
        r'\s*r?(?P<q>["\'])(?P<pat>.*?)(?P=q)'
        r'(?:\s*,\s*case\s*=\s*(?P<case>True|False))?'
        r'(?:\s*,\s*na\s*=\s*(?P<na>True|False))?'
        r'\s*\)',
        re.I
    )

    for m in str_op_pat.finditer(q):
        col = m.group("col")
        op = m.group("op").lower()
        pat = m.group("pat")
        case_s = (m.group("case") or "").lower()
        na_s = (m.group("na") or "").lower()
        if col not in df.columns:
            continue

        # defaults: forgiving (case-insensitive, na=False)
        case = False if case_s == "" else (case_s == "true")
        na = False if na_s == "" else (na_s == "true")

        s = _to_string(df[col])
        try:
            if op == "contains":
                cur = s.str.contains(pat, case=case, na=na).fillna(False)
            elif op == "match":
                cur = s.str.match(pat, case=case, na=na).fillna(False)
            elif op == "fullmatch":
                cur = s.str.fullmatch(pat, case=case, na=na).fillna(False)
            elif op == "startswith":
                if case:
                    cur = s.str.startswith(pat, na=na).fillna(False)
                else:
                    cur = s.str.lower().str.startswith(pat.lower(), na=na).fillna(False)
            else:  # endswith
                if case:
                    cur = s.str.endswith(pat, na=na).fillna(False)
                else:
                    cur = s.str.lower().str.endswith(pat.lower(), na=na).fillna(False)
            mask = _and(mask, cur)
        except Exception:
            # If any op errors, we simply skip that clause
            print(f"[CLAUSE_EVAL_SKIP] failed to eval .str.{op} on column: {col}")
            continue

    return mask


def _has_string_equality(q: str) -> bool:
    """Detect if expression contains any `col == 'literal'` string equalities."""
    return bool(re.search(r'`[^`]+`\s*==\s*([\'"])(.*?)\1', q))


def _split_top_level(expr: str, word: str = "or") -> List[str]:
    """
    Split an expression by a top-level logical word (default 'or'), respecting quotes and parentheses.
    Returns non-empty trimmed parts.
    """
    out: List[str] = []
    buf: List[str] = []
    depth = 0
    in_s, in_d = False, False
    i, n = 0, len(expr)

    while i < n:
        ch = expr[i]
        # Escapes inside quotes
        if ch == "\\" and i + 1 < n:
            buf.append(expr[i:i+2]); i += 2; continue
        if not in_d and ch == "'":
            in_s = not in_s; buf.append(ch); i += 1; continue
        if not in_s and ch == '"':
            in_d = not in_d; buf.append(ch); i += 1; continue
        if not in_s and not in_d:
            if ch == "(":
                depth += 1
            elif ch == ")" and depth > 0:
                depth -= 1
            m = re.match(rf'(?i)\b{word}\b', expr[i:])
            if depth == 0 and m:
                out.append("".join(buf).strip()); buf = []
                i += m.end()
                continue
        buf.append(ch); i += 1

    out.append("".join(buf).strip())
    return [p for p in out if p]


# Query evaluators
def _eval_basic_query(df: pd.DataFrame, q: str) -> Tuple[pd.Series, str, str]:
    """
    Evaluate a single expression using:
      1) df.query(q, engine="python")
      2) retry with 'and/or' -> '&/|' rewrite
      3) fallback AND-clause parser (case-insensitive for string equality)

    Returns (mask, path_tag, used_query_or_hint).
    """
    # As-is
    try:
        idx = df.query(q, engine="python").index
        mask = df.index.to_series().isin(idx)
        if int(mask.sum()) == 0 and _has_string_equality(q):
            # Zero-hit but expression contains string equalities -> try CI fallback
            m_ci = _mask_from_clauses(df, q)
            if m_ci is not None:
                print(f"[ROW_FILTER_RESULT] path=query_zero_and_fallback, rows={int(m_ci.sum())}/{len(df)}, indices_head={list(df.index[m_ci][:10])}")
                return m_ci, "query_zero_and_fallback", q
        print(f"[ROW_FILTER_RESULT] path=query, rows={int(mask.sum())}/{len(df)}, indices_head={list(idx[:10])}")
        return mask, "query", q
    except Exception as e1:
        print(f"[ROW_FILTER_QUERY_FAIL_1] {e1} -> retry with &/|")

    # with &/| rewrite
    rew = re.sub(r'(?i)\bor\b', '|', re.sub(r'(?i)\band\b', '&', q))
    try:
        idx = df.query(rew, engine="python").index
        mask = df.index.to_series().isin(idx)
        if int(mask.sum()) == 0 and _has_string_equality(q):
            # Zero-hit after rewrite -> try CI fallback
            m_ci = _mask_from_clauses(df, q)
            if m_ci is not None:
                print(f"[ROW_FILTER_RESULT] path=query_rewrite_zero_and_fallback, rows={int(m_ci.sum())}/{len(df)}, indices_head={list(df.index[m_ci][:10])}")
                print(f"[ROW_FILTER_QUERY_REWRITE] {rew}")
                return m_ci, "query_rewrite_zero_and_fallback", rew
        print(f"[ROW_FILTER_RESULT] path=query_rewrite, rows={int(mask.sum())}/{len(df)}, indices_head={list(idx[:10])}")
        print(f"[ROW_FILTER_QUERY_REWRITE] {rew}")
        return mask, "query_rewrite", rew
    except Exception as e2:
        print(f"[ROW_FILTER_QUERY_FAIL_2] {e2} -> fallback AND-clauses")

    # AND-only fallback (CI for string equality and full `.str.*` ops)
    m = _mask_from_clauses(df, q)
    if m is not None:
        print(f"[ROW_FILTER_RESULT] path=fallback_and, rows={int(m.sum())}/{len(df)}, indices_head={list(df.index[m][:10])}")
        return m, "fallback_and", q

    # Nothing recognized: all True to avoid breaking the pipeline
    print("[ROW_FILTER_FALLBACK_ALL_TRUE] nothing recognized (single expression)")
    return pd.Series(True, index=df.index), "all_false", q


def _mask_from_expr_with_or(df: pd.DataFrame, q: str) -> Optional[pd.Series]:
    """
    OR-aware evaluator (without rownum):
      - Split by top-level OR into parts
      - For each part, evaluate using _eval_basic_query
      - OR the parts together
    Returns a boolean mask, or None if nothing could be parsed.
    """
    parts = _split_top_level(q, "or")
    if not parts:
        m, _path, _used = _eval_basic_query(df, q)
        return m

    acc: Optional[pd.Series] = None
    any_part = False
    for part in parts:
        part = part.strip() or "True"
        m, _path, _used = _eval_basic_query(df, part)
        if m is not None:
            any_part = True
            acc = m if acc is None else (acc | m)

    if not any_part:
        return None
    return acc


# Row number support

# Matches: __rownum__ == 123  |  row == 123  |  row_number == 123  | index == 123
_ROWNUM_RE = re.compile(r'(?i)(?:`?__rownum__`?|row(?:_?number)?|index)\s*==\s*(\d+)')


def _remove_rownum_clauses(expr: str) -> Tuple[str, List[int]]:
    """
    Remove all rownum clauses from an expression and return (expr_without_rownum, rownums).
    Rownums are returned as a list of ints in the order they appear.
    """
    rownums: List[int] = []

    def _collect(m: re.Match) -> str:
        try:
            rownums.append(int(m.group(1)))
        except Exception:
            print(f"[ROWNUM_PARSE_FAIL] invalid rownum: {m.group(1)}")
            pass
        return " True "  # keep boolean structure valid

    expr_wo = _ROWNUM_RE.sub(_collect, expr)
    return expr_wo, rownums


def _mask_group_with_rownum(df: pd.DataFrame, group_expr: str) -> Tuple[pd.Series, str]:
    """
    Evaluate one OR group possibly containing a rownum clause.
    Semantics:
      - If the group has ONLY rownum(s): pick the N-th row(s) globally.
      - If the group has filters + rownum(s): pick the N-th row(s) within that group's base set.
      - If the group has no rownum: return the group's filter mask.
      - Multiple rownums in a single group are supported (union).
    """
    expr_wo_rn, rownums = _remove_rownum_clauses(group_expr)
    expr_wo_rn = (expr_wo_rn or "").strip() or "True"

    base_mask, path, used = _eval_basic_query(df, expr_wo_rn)

    # No rownum in this group -> just the base mask
    if not rownums:
        print(f"[ROW_FILTER_GROUP_PATH] {path} :: '{group_expr}'")
        return base_mask, path

    # Rownum only (expr reduced to True) -> pick global rows
    if expr_wo_rn.lower() == "true":
        picks = []
        n_rows = len(df)
        for n in rownums:
            pos = n - 1
            if 0 <= pos < n_rows:
                picks.append(df.index[pos])
        out_mask = df.index.to_series().isin(picks) if picks else pd.Series(False, index=df.index)
        print(f"[ROWNUM_GLOBAL_ONLY] ns={rownums} -> rows={int(out_mask.sum())}")
        print(f"[ROW_FILTER_GROUP_PATH] rownum_only :: '{group_expr}'")
        return out_mask, "rownum_only"

    # Rownum WITHIN the group's base set
    idxs = list(df.index[base_mask])
    picks = []
    for n in rownums:
        pos = n - 1
        if 0 <= pos < len(idxs):
            picks.append(idxs[pos])
    out_mask = df.index.to_series().isin(picks) if picks else pd.Series(False, index=df.index)
    print(f"[ROWNUM_WITHIN_GROUP] ns={rownums}, base_rows={len(idxs)}, selected_count={int(out_mask.sum())}")
    print(f"[ROW_FILTER_GROUP_PATH] {path}+rownum :: '{group_expr}'")
    return out_mask, f"{path}+rownum"


def _mask_from_query_with_rownum(df: pd.DataFrame, norm: str) -> pd.Series:
    """
    Evaluate the full (possibly OR-ed) expression, honoring __rownum__ inside each OR group.
    Final mask is the OR-union of per-group masks.
    """
    parts = _split_top_level(norm, "or") or [norm]
    acc: Optional[pd.Series] = None
    for part in parts:
        m, tag = _mask_group_with_rownum(df, part)
        acc = m if acc is None else (acc | m)
    if acc is None:
        return pd.Series(True, index=df.index)
    return acc


# Main row-filter builder
def _ensure_mask_from_query(df: pd.DataFrame, query: Optional[str]) -> pd.Series:
    """
    Build a boolean mask from a pandas query-like string, with **per-OR-group row-number semantics**.
    Pipeline:
      1) Normalize the filter (backticks, raw strings, etc.)
      2) Split by top-level OR; for each group:
         - Strip any __rownum__ clauses
         - Evaluate the remaining filter (df.query -> rewrite -> AND-fallback)
         - If the group had rownum(s):
             · If the remaining filter is 'True' -> pick N-th global row(s)
             · Else -> pick N-th row(s) within the base set
      3) OR all per-group masks together and return.
    """
    if not query or not str(query).strip():
        return pd.Series(True, index=df.index)

    norm = _normalize_row_filter(query, df.columns)
    print(f"[ROW_FILTER_NORMALIZED] {norm}")

    mask = _mask_from_query_with_rownum(df, norm)
    print(f"[ROW_FILTER_RESULT] path=or_groups, rows={int(mask.sum())}/{len(df)}, indices_head={list(df.index[mask][:10])}")

    # soft-recall guess when the entire query is a single column == 'value' condition
    if mask.sum() == 0:
        m = re.fullmatch(
            r"""\s*`?([A-Za-z0-9_ ]+)`?\s*==\s*['"](.+?)['"]\s*""",
            str(query)
        )
        if m:
            col, val = m.group(1).strip(), m.group(2)
            if col in df.columns:
                lhs = _to_string(df[col]).str.casefold()
                rhs = str(val).casefold()
                return (lhs.notna() & (lhs == rhs)).fillna(False)

    return mask


# Display-regex extraction
_MATCH_ALL_RE = re.compile(r'^\s*\^?\.\*\$?\s*$')


def _loose_token_regex(token: str) -> str:
    """
    Create a tolerant regex from a human token:
      - Escape the token
      - Allow flexible separators for spaces: ' ' -> '[-_.:/\\s]*'
    """
    esc = re.escape(token)
    esc = re.sub(r'\\\s+', r'[-_.:/\\s]*', esc)
    return esc


def _display_regex_and_columns_from_row_filter(row_filter: Optional[str]) -> Tuple[Optional[str], List[str]]:
    """
    Inspect a (normalized) row_filter and derive:
      - A display regex that ORs textual tokens
      - The list of columns referenced in the clauses
    Supports optional `.astype(str|"...")` before `.str.*`, same as _mask_from_clauses.
    """
    if not row_filter:
        return None, []

    q = str(row_filter)
    tokens: List[str] = []
    cols: List[str] = []

    # .astype(...)? followed by .str.contains/.match/.fullmatch/.startswith/.endswith('value', ...)
    for col, op, _, val in re.findall(
        r'`([^`]+)`\s*'
        r'(?:\.astype\(\s*(?:str|["\'](?:str|string)["\'])\s*\))?\s*'
        r'\.str\.(contains|match|fullmatch|startswith|endswith)\('
        r'\s*([\'"])(.*?)\2.*?\)',
        q
    ):
        cols.append(col)
        if val:
            if op == "startswith":
                tokens.append("^" + re.escape(val))
            elif op == "endswith":
                tokens.append(re.escape(val) + "$")
            elif op in ("match", "fullmatch"):
                # keep full regex as-is
                tokens.append(val)
            else:
                tokens.append(_loose_token_regex(val))

    # Equality with string literal
    for col, _, val in re.findall(r'`([^`]+)`\s*==\s*([\'"])(.*?)\2', q):
        cols.append(col)
        if val:
            tokens.append(re.escape(val))

    # Numeric comparisons (columns only)
    for col, _val in re.findall(r'`([^`]+)`\s*(?:>=|<=|>|<)\s*([0-9]+(?:\.[0-9]+)?)', q):
        cols.append(col)

    # Deduplicate columns preserving order
    seen = set()
    cols = [c for c in cols if not (c in seen or seen.add(c))]

    if tokens:
        display_rx = r'(?:' + '|'.join(tokens) + r')'
        return display_rx, cols

    return None, cols


# Highlight helpers
def _highlight_html(text: Any, rx: re.Pattern) -> Tuple[str, int]:
    """
    Wrap matches in <mark> with HTML-escaped text. Return (html, count).
    """
    if text is None or (isinstance(text, float) and pd.isna(text)):
        return "", 0
    s = str(text)
    parts: List[str] = []
    last = 0
    count = 0
    for m in rx.finditer(s):
        count += 1
        parts.append(html.escape(s[last:m.start()]))
        parts.append("<mark>")
        parts.append(html.escape(s[m.start():m.end()]))
        parts.append("</mark>")
        last = m.end()
    parts.append(html.escape(s[last:]))
    return "".join(parts), count


# Main executor
def execute_plan(
    df: pd.DataFrame,
    intent: str,
    pattern: str,
    flags: str = "i",
    columns: Optional[List[str]] = None,
    replacement: Optional[str] = None,
    row_filter: Optional[str] = None,
    head_n: int = 1000
) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """
    Execute 'find' or 'replace' across selected columns with an optional row_filter.
    Returns (new_df, payload). For 'find', new_df == df; for 'replace', new_df is modified.
    """
    # row filter -> mask
    # Coalesce empty pattern for FIND to match-all (LLM may emit null)
    if (not pattern) and intent.lower() == "find":
        pattern = "^.*$"
    mask = _ensure_mask_from_query(df, row_filter)

    # choose base columns
    cols = _auto_columns(df, intent, pattern, columns)

    # Normalize row_filter for UI + regex derivation
    norm_row_filter = _normalize_row_filter(row_filter, df.columns) if row_filter else None

    # decide the regex actually used for matching
    use_pattern = pattern or ""
    regex_source = "regex"

    row_filter_only_hits = False

    display_regex: Optional[str] = None
    display_regex_source: Optional[str] = None
    display_columns: List[str] = list(cols)  # default: actual applied columns

    if intent.lower() == "find":
        # Try to derive tokens/columns from the row_filter, for complex cases
        disp_rx, disp_cols = _display_regex_and_columns_from_row_filter(norm_row_filter)

        # If the LLM did NOT supply a meaningful pattern (empty or match-all),
        # we can safely adopt the row_filter-derived regex/columns.
        if (not use_pattern or _MATCH_ALL_RE.match(use_pattern)) and disp_rx:
            row_filter_only_hits = True
            use_pattern = disp_rx
            regex_source = "row_filter-derived"
            # Only override cols if the plan didn't explicitly specify "columns"
            if columns is None and disp_cols:
                cols = [c for c in disp_cols if c in df.columns]

        if disp_rx:
            display_regex = disp_rx
            display_regex_source = "row_filter-derived"
        else:
            display_regex = use_pattern
            display_regex_source = regex_source

        # Else: pattern is non-trivial -> we respect it and the explicit columns.
        # could still UNION disp_rx for UI-only, but we don't change semantics.

    # TODO:
    # Our "row_filter-derived regex" logic works well for FIND but is confusing for REPLACE.
    # For FIND, pattern "^.*$" is treated as "no real pattern", so we replace it with a
    # regex inferred from row_filter (e.g. (?:Female|United\ States|Male|Great\ Britain)),
    # and auto-pick the referenced text columns. That’s fine for display + highlighting.
    #
    # For REPLACE, doing the same breaks expectations: callers often use pattern "^.*$"
    # to mean "clobber whole cell values in the selected columns for rows matching
    # row_filter". Overriding "^.*$" with a row_filter-derived regex means we only
    # replace when the cell literally contains those tokens, which fails when the
    # selected columns (e.g. Id, Age) never contain "Female"/"Male"/etc, so total
    # replacements = 0 even though the row_filter matched.

    if intent.lower() == "replace":
        # Default display: just show the actual pattern
        if use_pattern:
            display_regex = use_pattern
            display_regex_source = "regex"

        # Don’t derive anything special for DATE_NORMALIZE mode
        sentinel = str(replacement or "")
        is_date_normalize = sentinel.startswith("__DATE_NORMALIZE__(") and sentinel.endswith(")")

        # If not DATE_NORMALIZE, we *may* derive a nicer display regex from row_filter
        if not is_date_normalize and norm_row_filter:
            disp_rx, disp_cols = _display_regex_and_columns_from_row_filter(norm_row_filter)

            # Only treat empty / match-all pattern as "no meaningful pattern"
            if (not pattern or _MATCH_ALL_RE.match(pattern)) and disp_rx:
                display_regex = disp_rx
                display_regex_source = "row_filter-derived"

                # Only tighten display columns if caller didn't explicitly pass "columns"
                if columns is None and disp_cols:
                    display_columns = [c for c in disp_cols if c in df.columns]

    # Safety: ensure a non-empty pattern before compiling for REPLACE mode too
    if not use_pattern:
        use_pattern = "^.*$"

    rx = re.compile(use_pattern, _compile_flags(flags))

    # ---------- FIND ----------
    if intent.lower() == "find":
        total = 0
        per_col: Dict[str, int] = {}
        changed_rows: set[int] = set()

        # Count matches in masked rows only
        for c in cols:
            ser = _to_string(df.loc[mask, c])
            counts = ser.str.count(rx).fillna(0)
            n = int(counts.sum())
            per_col[c] = n
            total += n
            if n:
                for idx in counts[counts > 0].index:
                    try:
                        changed_rows.add(int(idx))
                    except Exception:
                        # Skip non-integer index labels
                        print(f"[INDEX_LABEL_SKIP] non-integer index label: {idx}")
                        pass

        rows_with_hits = len(changed_rows)

        # Prepare up to 50 HTML-highlighted examples
        examples: List[Dict[str, Any]] = []
        for ridx in sorted(changed_rows)[:50]:
            ex: Dict[str, Any] = {"_index": int(ridx)}
            row_has_any = False
            for c in cols:
                raw = df.at[ridx, c] if (ridx in df.index and c in df.columns) else None
                html_text, cell_cnt = _highlight_html(raw, rx)
                if cell_cnt > 0:
                    ex[c] = {"count": int(cell_cnt), "html": html_text}
                    row_has_any = True
            if row_has_any:
                examples.append(ex)

        if row_filter_only_hits:
            changed_rows = set()
            for lbl in df.index[mask]:
                try:
                    changed_rows.add(int(lbl))
                except Exception:
                    print(f"[INDEX_LABEL_SKIP] non-integer index label in mask: {lbl}")
                    pass
            rows_with_hits = len(changed_rows)
        else:
            rows_with_hits = len(changed_rows)

        head_hits = [i for i in sorted(changed_rows) if i < head_n]
        print("head_hits:", head_hits)

        # Safer mask index export (ints only)
        mask_idx_ints: List[int] = []
        for lbl in list(df.index[mask])[:2000]:
            try:
                mask_idx_ints.append(int(lbl))
            except Exception:
                print(f"[INDEX_LABEL_SKIP] non-integer index label in mask: {lbl}")
                pass

        result_rows_description = (
            "Result rows = rows where row_filter is true."
            if row_filter_only_hits
            else (
                "Result rows = rows where "
                "row_filter is true AND at least one of the selected columns matches the pattern."
            )
        )
        
        payload = {
            "mode": "find",
            "regex": use_pattern,                      # may be row_filter-derived or a union
            "regex_source": regex_source,              # "regex" | "row_filter-derived" | "regex|row_filter-derived"
            "display_regex": display_regex,
            "display_regex_source": display_regex_source,
            "flags": flags,
            "columns_applied": cols,
            "row_filter": row_filter,
            "row_filter_normalized": norm_row_filter,  # helpful for UI and debugging
            "mask_row_indices": mask_idx_ints,
            "stats": {
                "total_matches": total,
                "per_column": per_col,
                "rows_with_hits": rows_with_hits,
                "changed_row_indices": [int(i) for i in sorted(changed_rows)[:2000]],
                "head_hit_row_indices": head_hits,
            },
            "examples": examples,
            "columns": list(cols),
            "head": df.head(head_n).to_dict(orient="records"),

            # For UI
            "result_rows_description": result_rows_description,
            "result_rows_count": rows_with_hits,
            "result_rows_indices": [int(i) for i in sorted(changed_rows)[:2000]],
        }
        return df, payload

    # ---------- SPECIAL: DATE NORMALIZE ----------
    # Replacement sentinel format: __DATE_NORMALIZE__(YYYY-MM-DD; dayfirst=auto)
    sentinel = str(replacement or "")
    if intent.lower() == "replace" and sentinel.startswith("__DATE_NORMALIZE__(") and sentinel.endswith(")"):
        # Parse options
        inside = sentinel[len("__DATE_NORMALIZE__("):-1].strip()  # e.g. "YYYY-MM-DD; dayfirst=auto"
        out_fmt = "YYYY-MM-DD"
        dayfirst_opt: Optional[bool] = None  # None=auto; True/False=explicit

        if inside:
            parts = [p.strip() for p in re.split(r"[;,]", inside) if p.strip()]
            for p in parts:
                if re.fullmatch(r"[YMDHms:/._\- ]+", p):
                    out_fmt = p
                elif p.lower().startswith("dayfirst"):
                    m = re.search(r"dayfirst\s*=\s*(auto|true|false)", p, re.I)
                    if m:
                        val = m.group(1).lower()
                        if val == "true":
                            dayfirst_opt = True
                        elif val == "false":
                            dayfirst_opt = False
                        else:
                            dayfirst_opt = None

        # Columns + mask
        cols = _auto_columns(df, intent, pattern, columns)
        mask = _ensure_mask_from_query(df, row_filter)

        # Guess dayfirst if auto
        if dayfirst_opt is None:
            samples: List[str] = []
            for c in cols:
                ser = _to_string(df.loc[mask, c]).dropna()
                samples.extend([str(x) for x in ser.head(200).tolist()])
            g = guess_dayfirst(samples)
            dayfirst_use = g if g is not None else False  # default to month-first if unsure
        else:
            dayfirst_use = dayfirst_opt

        # Execute (whole-cell or substring mode)
        out = df.copy()
        total = 0
        per_col: Dict[str, int] = {}
        changed_rows: set[int] = set()

        whole_cell = bool(re.fullmatch(r'^\^?\.\*\$?$', pattern or ""))

        for c in cols:
            ser_all = out[c].astype("string")
            target = ser_all.loc[mask]

            if whole_cell:
                def _apply_whole(val):
                    new, cnt = normalize_cell_as_whole(val, out_fmt=out_fmt, dayfirst=dayfirst_use)
                    return new, cnt

                changed = 0
                new_vals = []
                for v in target.tolist():
                    nv, cnt = _apply_whole(v)
                    new_vals.append(nv)
                    changed += cnt

                per_col[c] = int(changed)
                total += int(changed)
                ser_all.loc[mask] = new_vals
                out[c] = ser_all
                if changed:
                    changed_rows.update(target.index.tolist())
            else:
                rx_sub = re.compile(pattern or "^.*$", _compile_flags(flags))
                hit_mask = _to_string(target).str.contains(rx_sub).fillna(False)
                sub_index = target[hit_mask].index

                changed = 0
                new_vals = []
                for v in target.loc[sub_index].tolist():
                    s = "" if v is None else str(v)
                    new_s, cnt = normalize_date_text(s, out_fmt=out_fmt, dayfirst=dayfirst_use)
                    new_vals.append(new_s)
                    changed += cnt

                per_col[c] = int(changed)
                total += int(changed)
                ser_all.loc[sub_index] = new_vals
                out[c] = ser_all
                if changed:
                    try:
                        changed_rows.update(int(x) for x in sub_index)
                    except Exception:
                        print(f"[INDEX_LABEL_SKIP] non-integer index label in date normalize: {sub_index}")
                        pass

        # Safer mask index export (ints only)
        mask_idx_ints: List[int] = []
        for lbl in list(df.index[mask])[:2000]:
            try:
                mask_idx_ints.append(int(lbl))
            except Exception:
                print(f"[INDEX_LABEL_SKIP] non-integer index label in mask: {lbl}")
                pass

        payload = {
            "mode": "replace",
            "regex": pattern,
            "regex_source": "regex",
            "flags": flags,
            "columns_applied": cols,
            "row_filter": row_filter,
            "row_filter_normalized": norm_row_filter,
            "mask_row_indices": mask_idx_ints,
            "replacements": int(total),
            "per_column": per_col,
            "changed_row_indices": [int(i) for i in sorted(changed_rows)[:2000]],
            "head_hit_row_indices": [i for i in sorted(changed_rows) if i < head_n],
            "head": out.head(head_n).to_dict(orient="records"),

            "result_rows_description": (
                "Result rows = rows where "
                "row_filter is true AND at least one of the selected columns matches the pattern."
            ),
            "result_rows_count": len(changed_rows),
            "result_rows_indices": [int(i) for i in sorted(changed_rows)[:2000]],
        }
        return out, payload

    # ---------- PLAIN REPLACE ----------
    rep = "" if replacement is None else str(replacement)
    out = df.copy()
    total = 0
    per_col: Dict[str, int] = {}
    changed_rows: set[int] = set()

    rx_replace = re.compile(use_pattern, _compile_flags(flags))

    for c in cols:
        col_all = out[c].astype("string")
        s_mask = col_all.loc[mask]
        before = s_mask.str.count(rx_replace).fillna(0)
        n = int(before.sum())
        per_col[c] = n
        total += n
        if n:
            col_all.loc[mask] = s_mask.str.replace(rx_replace, rep, regex=True)
            out[c] = col_all
            for lbl in before[before > 0].index:
                try:
                    changed_rows.add(int(lbl))
                except Exception:
                    print(f"[INDEX_LABEL_SKIP] non-integer index label in replace: {lbl}")
                    pass

    # Safer mask index export (ints only)
    mask_idx_ints: List[int] = []
    for lbl in list(df.index[mask])[:2000]:
        try:
            mask_idx_ints.append(int(lbl))
        except Exception:
            print(f"[INDEX_LABEL_SKIP] non-integer index label in mask: {lbl}")
            pass

    payload = {
        "mode": "replace",
        "regex": use_pattern,
        "regex_source": regex_source,
        "display_regex": display_regex,
        "display_regex_source": display_regex_source,
        "display_columns": display_columns,
        "flags": flags,
        "columns_applied": cols,
        "row_filter": row_filter,
        "row_filter_normalized": norm_row_filter,
        "mask_row_indices": mask_idx_ints,
        "replacements": total,
        "per_column": per_col,
        "changed_row_indices": [int(i) for i in sorted(changed_rows)[:2000]],
        "head_hit_row_indices": [i for i in sorted(changed_rows) if i < head_n],
        "head": out.head(head_n).to_dict(orient="records"),

        "result_rows_description": (
                "Result rows = rows where "
                "row_filter is true AND at least one of the selected columns matches the pattern."
            ),
        "result_rows_count": len(changed_rows),
        "result_rows_indices": [int(i) for i in sorted(changed_rows)[:2000]],
    }
    return out, payload
