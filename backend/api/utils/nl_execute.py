import os
import pandas as pd
from typing import Dict, Any, Tuple

from .plan_v2 import plan_with_llm
from .regex_executor import execute_plan


def get_sample_rows_for_llm(df):
    """ Get <= 2 random sample rows for LLM context"""
    if df.empty:
        return []
    n = min(2, len(df))
    # random rows; you can also use df.head(n) if you prefer deterministic
    sample_df = df.sample(n=n, random_state=42)
    return sample_df.to_dict(orient="records")


def nl_execute(
    df: pd.DataFrame,
    instruction: str,
    want_download: bool = True,
    media_root: str = None,
    file_tag: str = "executed"
) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """NL instruction -> plan → execute -> (new_df, payload)"""
    headers = df.columns.tolist()
    sample_rows = get_sample_rows_for_llm(df)
    plan, source, raw = plan_with_llm(instruction, headers, sample_rows=sample_rows)

    new_df, payload = execute_plan(
        df=df,
        intent=plan.intent,
        pattern=plan.pattern,
        flags=plan.flags,
        columns=plan.columns,
        replacement=plan.replacement,
        row_filter=plan.row_filter,
        head_n=1000,
    )

    # plan source & raw LLM response
    payload["plan_source"] = source
    payload["plan_raw"] = raw if source == "llm" else None

    # expose regex source & intent to frontend (for "Show Regex/intent" button)
    payload["regex_source"] = source
    payload["intent"] = {
        "intent": plan.intent,
        "replacement": plan.replacement,
    }

    # Optional export (views.execute may rewrite this as an absolute URL)
    if want_download:
        media_root = media_root or os.path.join(os.path.dirname(os.path.dirname(__file__)), "media")
        os.makedirs(media_root, exist_ok=True)
        out_name = f"{file_tag}.csv"
        out_path = os.path.join(media_root, out_name)
        new_df.to_csv(out_path, index=False)

    return new_df, payload
