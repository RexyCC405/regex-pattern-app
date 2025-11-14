import os
import re
import uuid
from pathlib import Path
import pandas as pd
import numpy as np
import math
from collections.abc import Mapping, Sequence

from django.conf import settings
from django.core.files import File
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from rest_framework.decorators import api_view, parser_classes
from rest_framework.parsers import MultiPartParser, JSONParser
from django.db.models import Value
from django.db.models.functions import Lower

from .models import UploadedFile
from .utils.nl_execute import nl_execute


def next_unique_numeric_name(original_name: str, ext=".csv") -> str:
    """ Given an original file name, generate the next unique name with numeric suffix."""
    stem = Path(original_name).stem

    # Backward compatible with historical '-chained' suffix
    if stem.endswith("-chained"):
        stem = stem[:-len("-chained")]

    # Strip existing '-<number>' suffix if present
    m = re.search(r"-(\d+)$", stem)
    base = stem[:m.start()] if m else stem

    # Consider only existing names like 'base-<number>.ext' (case-insensitive)
    pattern_prefix = f"{base.lower()}-"
    existing = list(
        UploadedFile.objects
        .annotate(on_lower=Lower("original_name"))
        .filter(on_lower__startswith=pattern_prefix,
                on_lower__endswith=ext.lower())
        .values_list("original_name", flat=True)
    )

    used_nums = set()
    for name in existing:
        s = Path(name).stem
        mm = re.search(r"-(\d+)$", s)
        if mm:
            used_nums.add(int(mm.group(1)))

    nxt = (max(used_nums) + 1) if used_nums else 1
    return f"{base}-{nxt}{ext}"

def _read_csv_with_fallbacks(path: str) -> pd.DataFrame:
    """ Read CSV with multiple fallbacks for encoding and separators. """
    encodings = ("utf-8", "utf-8-sig", "cp1252", "latin1")

    read_kwargs = dict(
        keep_default_na=False,   # don't treat 'NA', 'N/A', etc. as NaN
        na_filter=False,         # optionally: don't infer NA at all
    )

    # Fast path
    for enc in encodings:
        try:
            return pd.read_csv(path, encoding=enc, **read_kwargs)
        except UnicodeDecodeError:
            continue
        except pd.errors.ParserError:
            break

    # Sniffer
    for enc in encodings:
        try:
            return pd.read_csv(path, sep=None, engine="python", encoding=enc, **read_kwargs)
        except Exception:
            continue

    # Common separators
    for sep in (",", ";", "\t", "|"):
        for enc in encodings:
            try:
                return pd.read_csv(path, sep=sep, engine="python", encoding=enc, **read_kwargs)
            except Exception:
                continue

    # Last resort
    for enc in encodings:
        try:
            return pd.read_csv(path, sep=None, engine="python", on_bad_lines="skip", encoding=enc, **read_kwargs)
        except Exception:
            continue

    raise pd.errors.ParserError("Could not parse CSV with any known settings")


def _read_df(path: str):
    """ Read a dataframe from path"""
    name = os.path.basename(path).lower()
    if name.endswith((".xlsx", ".xls")):
        return pd.read_excel(path), True
    # CSV / other text formats
    df = _read_csv_with_fallbacks(path)
    return df, False


def _head_records(df: pd.DataFrame, n=1000):
    """ Return first n rows as list-of-dicts, with NaN/NaT/Inf replaced with None for JSON safety. """
    head = df.head(n).reset_index(drop=True)

    # Replace NaN / NaT / ±Inf with None so JSON is valid
    head = head.replace([pd.NA, pd.NaT, np.inf, -np.inf], None)
    head = head.where(pd.notna(head), None)

    return head.to_dict(orient="records")

def sanitize_for_json(obj):
    """ Recursively sanitize an object for JSON serialization by replacing NaN/Inf with None. """
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj

    if isinstance(obj, Mapping):
        return {k: sanitize_for_json(v) for k, v in obj.items()}

    if isinstance(obj, Sequence) and not isinstance(obj, (str, bytes, bytearray)):
        return [sanitize_for_json(v) for v in obj]

    return obj

@api_view(["POST"])
@parser_classes([MultiPartParser])
def upload_file(request):
    """ Handle file upload (CSV/Excel). Returns { file_id, filename, is_excel, columns,"""
    f = request.FILES.get("file")
    if not f:
        return JsonResponse({"error": "No file provided"}, status=400)

    obj = UploadedFile.objects.create(file=f, original_name=f.name)
    try:
        df, is_excel = _read_df(obj.file.path)
    except Exception as e:
        print(f"[FILE_READ_FAIL] failed to read uploaded file id={obj.id} name={obj.original_name}: {e}")
        # If reading fails, delete the uploadedFile record
        obj.delete()
        return JsonResponse(
            {
                "error": "Failed to parse uploaded file",
                "detail": str(e),
                "hint": "Try UTF-8 CSV, or re-export without special delimiters/quotes.",
            },
            status=400,
        )

    return JsonResponse({
        "file_id": obj.id,
        "filename": obj.original_name,
        "is_excel": is_excel,
        "columns": df.columns.tolist(),
        "head": _head_records(df),
    })


@api_view(["POST"])
@parser_classes([JSONParser])
def execute(request):
    """
    Unified entry point: user natural language + column name list (loaded by backend) → LLM produces JSON → execute.
    body: { file_id, instruction, download?, chain? }
    """
    file_id = request.data.get("file_id")
    instruction = request.data.get("instruction", "")
    want_download = bool(request.data.get("download", True))
    chain_requested = bool(request.data.get("chain", False))

    if not file_id:
        return JsonResponse({"error": "file_id is required"}, status=400)
    if not instruction.strip():
        return JsonResponse({"error": "instruction is required"}, status=400)

    obj = get_object_or_404(UploadedFile, id=file_id)
    df, _ = _read_df(obj.file.path)

    new_df, payload = nl_execute(
        df=df,
        instruction=instruction,
        want_download=want_download,
        file_tag=f"executed_{obj.id}"
    )

    # Helper: export CSV and return download URL
    def _export_df_to_csv_and_url(df_to_export: pd.DataFrame, base_name: str):
        media_root = Path(getattr(settings, "MEDIA_ROOT", "."))
        media_url = getattr(settings, "MEDIA_URL", "/media/")
        out_dir = media_root / "exports"
        out_dir.mkdir(parents=True, exist_ok=True)

        token = uuid.uuid4().hex[:8]
        fname = f"{base_name}-{token}.csv"
        fpath = out_dir / fname

        # utf-8-sig makes it easier for Excel to open correctly
        df_to_export.to_csv(fpath, index=False, encoding="utf-8-sig")

        rel_path = f"exports/{fname}"
        abs_url = request.build_absolute_uri(f"{media_url.rstrip('/')}/{rel_path}")
        return fpath, abs_url, fname

    # If download or chaining is requested, ensure download_url exists, and create a new UploadedFile when chaining
    if payload.get("mode") == "replace" and (want_download or chain_requested):
        # If nl_execute did not provide download_url, export it here
        if not payload.get("download_url"):
            base = Path(obj.original_name).stem or f"executed_{obj.id}"
            fpath, dl_url, fname = _export_df_to_csv_and_url(new_df, base)
            payload["download_url"] = dl_url
            payload["download_filename"] = fname
            _saved_path = str(fpath)
        else:
            _saved_path = None  # download_url already exists, maybe nl_execute handled saving
            if str(payload["download_url"]).startswith("/"):
                payload["download_url"] = request.build_absolute_uri(payload["download_url"])

        # Chaining: treat the result as a brand-new UploadedFile and return its new file_id/columns/head
        if chain_requested:
            # If we don't have a local file path yet, export again to persist it
            if _saved_path is None:
                base = Path(obj.original_name).stem or f"executed_{obj.id}"
                fpath, dl_url, fname = _export_df_to_csv_and_url(new_df, base)
                payload["download_url"] = dl_url
                payload["download_filename"] = fname
                _saved_path = str(fpath)

            fpath = Path(_saved_path)
            chained_name = next_unique_numeric_name(obj.original_name, ext=".csv")
            with open(fpath, "rb") as fh:
                new_obj = UploadedFile.objects.create(
                    file=File(fh, name=fpath.name),
                    original_name=chained_name,
                )
            payload["chain"] = {
                "file_id": new_obj.id,
                "filename": new_obj.original_name,
                "is_excel": False,
                "columns": new_df.columns.tolist(),
                "head": _head_records(new_df),
            }

    resp = {
        "file_id": obj.id,
        "columns": new_df.columns.tolist(),
        **payload
    }
    return JsonResponse(sanitize_for_json(resp))
