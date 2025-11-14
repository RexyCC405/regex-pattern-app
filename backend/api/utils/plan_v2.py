import os
import re
import json
import traceback
from typing import List, Optional, Tuple, Dict, Any, Sequence

from pydantic import BaseModel, Field

try:
    # pip install google-genai
    from google import genai  # type: ignore
except Exception:
    genai = None  # gracefully degrade to fallback

try:
    # pip install json-repair
    import json_repair  # type: ignore
    _HAS_JSON_REPAIR = True
except Exception:
    _HAS_JSON_REPAIR = False


# Pydantic schema
class RegexPlan(BaseModel):
    """ Regex Plan Schema"""
    intent: str = Field(..., description="'find' or 'replace'")
    pattern: str = Field(..., description="Single Python-compatible regex string")
    flags: str = Field(default="i", description="Regex flags like 'i', 'im', 'iu', 'ms'")
    columns: Optional[List[str]] = Field(default=None, description="Limit to these columns (optional)")
    replacement: Optional[str] = Field(default=None, description="Required for replace; null/omitted for find")
    row_filter: Optional[str] = Field(default=None, description="Pandas query string, e.g. \"Name == 'James'\"")

    def normalize(self) -> "RegexPlan":
        """ Return a normalized copy of the plan"""
        # allow only safe flags; keep 'u'/'U' too
        allowed = "imsluxUu"
        safe = "".join(ch for ch in (self.flags or "i") if ch in allowed) or "i"

        rep = (self.replacement or "").strip() or None

        rep = (self.replacement or "").strip() or None
        # Avoid pandas NA strings like "NA", "N/A", etc.
        if rep == "NA" or rep == "N/A" or rep == "NaN" or rep == "nan" or rep == "na":
            rep = "N/A (missing)"

        # Ensure pattern is a non-empty string; fall back to match-all
        pat = (self.pattern or "").strip() or "^.*$"
        return self.copy(update={"flags": safe, "replacement": rep, "pattern": pat})

# simple fallback rules
FALLBACKS: Dict[str, str] = {
    "email": r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,7}",
    "phone": r"\+?\d[\d\-\s()]{7,}\d",
    "date": r"(?:\d{4}[/-]\d{1,2}[/-]\d{1,2}|\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{4}\.\d{1,2}\.\d{1,2})",
    "url": r"https?://[^\s]+",
    "postcode": r"\b\d{4,6}\b",
}


def _guess_pattern(instr: str) -> str:
    """Simple keyword pattern guesser"""
    l = (instr or "").lower()
    for k, v in FALLBACKS.items():
        if k in l or (k.endswith("s") and k[:-1] in l):
            return v
    return FALLBACKS["email"]


def _rule_plan(instruction: str, headers: List[str]) -> Tuple[RegexPlan, str]:
    """ Simple rule-based plan generator (fallback)"""
    l = (instruction or "").lower()
    intent = "replace" if any(w in l for w in ["replace", "redact", "mask", "anonym"]) else "find"
    pattern = _guess_pattern(l)
    # Detect "date normalization" keywords
    if any(k in l for k in ["normalize date", "normalise date", "standardize date","standardise date", "unify date"]):
      intent = "replace"
      pattern = FALLBACKS["date"]
      # Default ISO, dayfirst is auto-inferred; if user says "YYYY/MM/DD" etc., LLM will override
      replacement = "__DATE_NORMALIZE__(YYYY-MM-DD; dayfirst=auto)"
      # Prefer columns that look like date-related fields
      cols = [h for h in headers if any(x in h.lower() for x in ["date", "time", "dob", "birth", "created", "updated", "dt", "timestamp"])]
      columns = cols or None
      return RegexPlan(intent=intent, pattern=pattern, flags="i", columns=columns,
                      replacement=replacement, row_filter=None), "fallback"
    
    pattern = _guess_pattern(l)

    # prefer columns which look like the common fields
    cols = [
        h
        for h in headers
        if any(k in h.lower() for k in ["email", "mail", "phone", "mobile", "date", "time", "url", "link", "note", "comment"])
    ]
    columns = cols or None

    # simple where clause
    m = re.search(
        r"""(?i)\bwhere\s+([A-Za-z0-9_ ]+)\s*(?:=|==|:|\bis\b|\bequals?\b)\s*(['"].+?['"]|\d+)""",
        instruction or "",
    )
    row_filter = None

    if m:
        col = m.group(1).strip()
        val = m.group(2).strip()
        if any(col.lower() == h.lower() for h in headers):
            row_filter = f"{col} == {val}"

    return (
        RegexPlan(intent=intent, pattern=pattern, flags="i", columns=columns, replacement=None, row_filter=row_filter),
        "fallback",
    )

# Validation + utilities
def _log_raw(tag: str, raw: str, max_len: int = 2000) -> None:
    """ Log debugging raw llm output"""
    print(f"[{tag}] {raw[:max_len]}" + (" …[truncated]" if len(raw) > max_len else ""))

def _format_sample_rows(sample_rows: Optional[Sequence[Dict[str, Any]]], max_rows: int = 2) -> str:
    """
    Render up to `max_rows` sample rows as compact JSON for the prompt.
    This is purely for context; the model must not copy values into the output.
    """
    if not sample_rows:
        return "None"
    try:
        subset = list(sample_rows)[:max_rows]
        return json.dumps(subset, ensure_ascii=False)
    except Exception:
        # Fall back to a simple repr if something goes wrong
        return repr(sample_rows[:max_rows])
    

def _align_columns(plan: RegexPlan, headers: List[str]) -> RegexPlan:
    """Map case-insensitive columns to canonical header names; keep only known."""
    if not plan.columns:
        return plan
    canonical: Dict[str, str] = {h.lower(): h for h in headers}
    mapped: List[str] = []
    unknown: List[str] = []
    for c in plan.columns:
        key = c.lower()
        if key in canonical:
            mapped.append(canonical[key])
        else:
            unknown.append(c)
    # If all unknown, keep them to trigger validator error.
    if mapped:
        return plan.copy(update={"columns": mapped})
    return plan

class PlanValidationError(Exception):
    """Error raised when a plan fails val"""
    def __init__(self, errors: List[str]):
        self.errors = errors
        super().__init__("\n".join(errors))


def _validate_plan(plan: RegexPlan, headers: List[str]) -> None:
    """Validate a RegexPlan; raise PlanValidationError on failure"""
    errors: List[str] = []

    # intent
    if plan.intent not in ("find", "replace"):
        errors.append("intent must be 'find' or 'replace'.")

    # regex compiles (flags checked separately; compile without flags to catch syntax)
    try:
        re.compile(plan.pattern)
    except Exception as e:
        errors.append(f"pattern not compilable: {e}")

    # flags
    for f in plan.flags:
        if f not in "imsluxUu":
            errors.append(f"unsupported flag '{f}'")

    # columns exist
    if plan.columns:
        legit = {h.lower() for h in headers}
        unknown = [c for c in plan.columns if c.lower() not in legit]
        if unknown:
            errors.append(f"unknown columns: {unknown}")

    # replacement required for replace
    if plan.intent == "replace" and not plan.replacement:
        errors.append("replacement is required for replace intent.")

    # row_filter sanity: verify backticked column names exist
    if plan.row_filter:
        ticks = re.findall(r"`([^`]+)`", plan.row_filter)
        legit_names = set(headers)
        for t in ticks:
            if t not in legit_names:
                errors.append(f"row_filter references unknown column `{t}`")

    if errors:
        raise PlanValidationError(errors)


# ---------- Prompts ----------
SYSTEM = """
Convert a natural-language instruction about table text operations into STRICT JSON only (no prose).
Return exactly:
{
  "intent": "find" | "replace",
  "row_filter": "<pandas query string>",
  "pattern": "<single Python-compatible regex>",
  "flags": "i" | "im" | "m" | "s" | "iu" | ...,
  "columns": ["<one or more headers from the given list>"], // optional
  "replacement": string or null, // required if intent='replace'; else null/omit
}

Recall-first rules:
- If the row filters cover all conditions to find the targets, then simply set pattern '^.*$'.
- For complex conditions, prefer row_filter + pattern='^.*$' over complex regexes.
- Prefer '.*token.*' for cell-level contains; for multi-word tokens allow flexible separators with '[-_.:/\\s]*'.
- Whole-cell overwrite: use row_filter to target rows and '^.*$' as the pattern in chosen columns.
- Keep one simple regex; avoid lookbehinds. Default flags: 'iu'; add 'm' or 's' only if clearly needed.
- Use ONLY provided headers. If a field is named, prefer that column.
- If replacement is unspecified for 'replace', set 'REDACTED'.
- If targeting a partial person/id, use Name.str.contains('Jane', case=False, na=False) in row_filter *only if really needed*; otherwise prefer pattern+columns.
- Wrap columns with spaces/punctuation in backticks in row_filter.
- If the instruction asks to normalize/standardize dates, set intent='replace', pattern=<date regex>, and replacement="__DATE_NORMALIZE__(YYYY-MM-DD; dayfirst=auto)" (format and dayfirst may vary per user request).
- If the user asks for the Nth row/record (e.g., "the 555th row"), set pattern '^.*$' and row_filter "__rownum__ == 555" (1-based).

- DO NOT use ANY of the following in row_filter (VERY IMPORTANT)::
  * .str.startswith(...), .str.endswith(...)
  * Q, Q.parse, Q[..., Q(...), or any Q-based API
  * .query(...), .loc[..., .iloc[...
  * re.compile, lambda, list/dict comprehensions, try/except, for/while, np., pd., os., eval, exec
  * indexing or slicing beyond .isin([...])
- If you are unsure how to express a complex condition with this limited language, prefer a simpler row_filter (or omit it) and use pattern + columns instead.

You may also be given up to 2 SAMPLE_ROWS as JSON records. They show typical values for each column.
Use them only to understand data formats and semantics (e.g., how dates/emails look), but do NOT copy any values from SAMPLE_ROWS into the output.
Output ONLY the JSON object, no explanations.
"""

REPAIRER = """
You are fixing a STRICT-JSON plan for a regex task. You will be given:
1) HEADERS
2) INSTRUCTION
3) THE BROKEN JSON
4) VALIDATION ERRORS

Produce a corrected JSON that obeys the same schema and rules as the system prompt. No prose, JSON only.
"""

CRITIC = """
You are a validator. Given HEADERS, INSTRUCTION, and a CANDIDATE JSON plan:
- If the plan fully complies with the schema and rules, return:
  {"decision":"PASS"}
- Otherwise, return a corrected plan JSON that complies (no extra keys, no prose).
"""


# ---------- LLM helpers ----------
def _gemini_client():
    """ Gemini client setup"""
    if genai is None:
        raise ImportError(
            "google-genai SDK not available. Install with: pip install -U google-genai"
        )
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise EnvironmentError("GEMINI_API_KEY is not set.")
    client = genai.Client(api_key=api_key)
    model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
    gen_cfg = {"response_mime_type": "application/json"}
    return client, model, gen_cfg


def _plan_from_raw(raw: str) -> RegexPlan:
    """
    Parse an LLM JSON string into a RegexPlan.
    1) try strict JSON -> Pydantic
    2) if that fails and json-repair is available, repair then parse
    """
    try:
        return RegexPlan.model_validate_json(raw).normalize()
    except Exception:
        if _HAS_JSON_REPAIR:
            try:
                obj = json_repair.loads(raw)  # -> Python object (dict)
                return RegexPlan.model_validate(obj).normalize()
            except Exception:
                pass
        # Re-raise a standardized error
        raise


def _maybe_repaired(raw: str) -> str:
    """Return a best-effort repaired JSON string for prompting the repair LLM."""
    if _HAS_JSON_REPAIR:
        try:
            return json_repair.repair_json(raw)
        except Exception:
            pass
    return raw


def _llm_generate_once(
    client,
    model,
    gen_cfg,
    instruction: str,
    headers: List[str],
    sample_rows: Optional[Sequence[Dict[str, Any]]] = None,
) -> Tuple[RegexPlan, str]:
    """ Single LLM call to generate a plan"""
    sample_text = _format_sample_rows(sample_rows)
    prompt = (
        f"{SYSTEM}\n"
        f"HEADERS: {headers}\n"
        f"SAMPLE_ROWS (up to 2): {sample_text}\n"
        f"INSTRUCTION: {instruction}\n"
    )
    resp = client.models.generate_content(model=model, contents=prompt, config=gen_cfg)  # type: ignore[attr-defined]
    raw = (getattr(resp, "text", None) or "").strip()
    _log_raw("LLM_GENERATE_RAW", raw)
    plan = _plan_from_raw(raw)
    return plan, raw


def _llm_repair(
    client,
    model,
    gen_cfg,
    instruction: str,
    headers: List[str],
    bad_raw: str,
    errors: List[str],
    sample_rows: Optional[Sequence[Dict[str, Any]]] = None,
) -> Tuple[RegexPlan, str]:
    """ Single LLM call to repair a bad plan"""
    repaired_hint = _maybe_repaired(bad_raw)
    _log_raw("LLM_AUTOREPAIR_HINT", repaired_hint)
    sample_text = _format_sample_rows(sample_rows)
    repair_prompt = (
        f"{REPAIRER}\n\n"
        f"HEADERS: {headers}\n"
        f"SAMPLE_ROWS (up to 2): {sample_text}\n"
        f"INSTRUCTION: {instruction}\n"
        f"THE BROKEN JSON (raw):\n{bad_raw}\n"
        f"THE BROKEN JSON (auto-repaired attempt):\n{repaired_hint}\n"
        f"\nVALIDATION ERRORS:\n{errors}\n"
    )
    resp = client.models.generate_content(model=model, contents=repair_prompt, config=gen_cfg)  # type: ignore[attr-defined]
    raw = (getattr(resp, "text", None) or "").strip()
    plan = _plan_from_raw(raw)
    _log_raw("LLM_REPAIR_RAW", raw)
    return plan, raw


def _critic_review(
    client,
    model,
    gen_cfg,
    instruction: str,
    headers: List[str],
    candidate_raw: str,
    sample_rows: Optional[Sequence[Dict[str, Any]]] = None,
) -> Optional[Tuple[RegexPlan, str]]:
    """ Single LLM call to review a plan"""
    sample_text = _format_sample_rows(sample_rows)
    critic_prompt = (
        f"{CRITIC}\n"
        f"HEADERS: {headers}\n"
        f"SAMPLE_ROWS (up to 2): {sample_text}\n"
        f"INSTRUCTION: {instruction}\n"
        f"CANDIDATE:\n{candidate_raw}\n"
    )
    resp = client.models.generate_content(model=model, contents=critic_prompt, config=gen_cfg)  # type: ignore[attr-defined]
    raw = (getattr(resp, "text", None) or "").strip()
    _log_raw("LLM_CRITIC_RAW", raw)

    # If critic returns {"decision":"PASS"}, accept candidate
    try:
        obj = json.loads(raw)
        if isinstance(obj, dict) and obj.get("decision") == "PASS":
            return None
    except Exception:
        # Not a PASS marker; continue and try to parse as a plan
        pass

    try:
        plan = _plan_from_raw(raw)
        return plan, raw
    except Exception:
        return None


# Public entrypoint
def plan_with_llm(
    instruction: str,
    headers: List[str],
    sample_rows: Optional[Sequence[Dict[str, Any]]] = None,
) -> Tuple[RegexPlan, str, str]:
    """
    Return (plan, source, raw_text)
    source: 'llm' | 'llm_repair' | 'llm_critic' | 'fallback'

    Environment:
      - LLM_PROVIDER: 'gemini' to enable LLM; anything else -> rule-based fallback
      - GEMINI_API_KEY: required if LLM_PROVIDER='gemini'
      - GEMINI_MODEL: optional (default 'gemini-2.5-flash')
      - MAX_LLM_ATTEMPTS: int, default 2 (first try + one repair)
      - ENABLE_CRITIC: '1' to enable critic escalation
    """
    provider = os.getenv("LLM_PROVIDER", "rule_based").lower()
    if provider != "gemini" or genai is None or not os.getenv("GEMINI_API_KEY"):
        # pure rule-based path
        print("using rule based fallback")
        plan, src = _rule_plan(instruction, headers)
        return plan.normalize(), src, ""
    
    MAX_ATTEMPTS = max(1, int(os.getenv("MAX_LLM_ATTEMPTS", "2")))
    ENABLE_CRITIC = os.getenv("ENABLE_CRITIC", "0") == "0" # default to disable to save token costs

    try:
        print("using gemini")
        client, model, gen_cfg = _gemini_client()

        # attempt 1
        try:
            plan, raw = _llm_generate_once(client, model, gen_cfg, instruction, headers, sample_rows)
            plan = _align_columns(plan, headers)
            _validate_plan(plan, headers)
            # Always run critic by default
            if ENABLE_CRITIC:
                review = _critic_review(client, model, gen_cfg, instruction, headers, raw, sample_rows)
                if review:
                    cplan, craw = review
                    cplan = _align_columns(cplan, headers)
                    _validate_plan(cplan, headers)
                    return cplan, "llm_critic", craw
            return plan, "llm", raw
        except PlanValidationError as ve1:
            first_errors = ve1.errors
            last_raw = locals().get("raw", "")
        except Exception as e:
            first_errors = [f"first call error: {e}"]
            last_raw = locals().get("raw", "")

        # attempts 2..N via repair
        errors = first_errors
        for attempt in range(2, MAX_ATTEMPTS + 1):
            try:
                plan, raw = _llm_repair(
                    client, model, gen_cfg, instruction, headers, last_raw or "N/A", errors, sample_rows
                )
                plan = _align_columns(plan, headers)
                _validate_plan(plan, headers)
                # Always run critic by default
                if ENABLE_CRITIC:
                    review = _critic_review(client, model, gen_cfg, instruction, headers, raw, sample_rows)
                    if review:
                        cplan, craw = review
                        cplan = _align_columns(cplan, headers)
                        _validate_plan(cplan, headers)
                        return cplan, "llm_critic", craw
                return plan, "llm_repair", raw
            except PlanValidationError as veN:
                # 'raw' may be undefined if _llm_repair raised before assignment
                last_raw, errors = locals().get("raw", last_raw or ""), veN.errors
            except Exception as e:
                last_raw, errors = locals().get("raw", last_raw or ""), [f"repair call error: {e}"]

        # optional critic escalation
        if ENABLE_CRITIC:
            try:
                critic = _critic_review(client, model, gen_cfg, instruction, headers, last_raw or "{}", sample_rows)
                if critic:
                    plan, raw = critic
                    plan = _align_columns(plan, headers)
                    _validate_plan(plan, headers)
                    return plan, "llm_critic", raw
            except Exception:
                # ignore critic failure, fall through
                pass

        # final fallback
        print("LLM planning failed, falling back to rules.")
        print("errors seen:", errors)
        plan, src = _rule_plan(instruction, headers)
        return plan.normalize(), src, ""

    except Exception as e:
        print("LLM planning failed, falling back to rules.")
        print(f"[{type(e).__name__}] {e}")
        traceback.print_exc()
        plan, src = _rule_plan(instruction, headers)
        return plan.normalize(), src, ""


# Optional quick test
# if __name__ == "__main__":
#     headers = ["Name", "Email", "Phone Number", "Notes"]
#     instruction = "Replace emails in Notes where Name equals 'Alice' with REDACTED"
#     plan, source, raw = plan_with_llm(instruction, headers)
#     print("SOURCE:", source)
#     print("PLAN:", plan.model_dump())
#     print("RAW:", raw)
