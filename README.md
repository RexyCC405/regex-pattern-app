# Data Transformation Platform with NL to Regex

![Rhombus AI](./frontend/assets/icon.png)

A platform built with **Django (DRF)** and **React (Vite + TypeScript)**. It allows users upload CSV/Excel files, describe text patterns in natural language, converts that description to a **regex via an LLM**, applies replacements to selected text columns, and previews/downloads the processed data.

## Features
- Upload **CSV/Excel**.
- Describe a pattern in natural language (e.g., "find email addresses").
- **/api/regex** turns NL → Regex using an LLM provider (Gemini).
- Apply replacements across **all text columns** or specific columns.
- Download the processed file as CSV.

## Tech Stack
- Backend: Django 5, Django REST Framework, pandas (CSV/Excel), django-cors-headers
- Frontend: React 18, Vite, TypeScript

---

## Quickstart

### Prereqs
- Python 3.10+
- Node.js 18+

### 1) Backend
```bash
cd backend
python -m venv .venv && source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# Optional: set your Gemini key for better NL to Regex quality
cp .env.example .env
# Then edit .env and set GEMINI_KEY=<your_key>
# You can also set LLM_PROVIDER=gemini (default falls back to rule_based)

python manage.py makemigrations api
python manage.py migrate
python manage.py runserver 0.0.0.0:8000
```

### 2) Frontend
```bash
cd ../frontend
npm install
npm run dev
```

Open http://localhost:5173 (Vite default). The frontend expects the backend at http://localhost:8000 (configure in `src/config.ts`).

---

## API Summary

* **POST** `/api/upload` - Upload a CSV/Excel file *(multipart/form-data)*

  * **Body (multipart)**

    * `file` *(required, file)* - CSV or Excel
  * **Response (200)**

    * `file_id` - new uploaded file ID
    * `filename` - original filename
    * `is_excel` - whether the file parsed as Excel
    * `columns` - column names detected
    * `head` - preview records
  * **Errors (400)**

    * `{ "error": "No file provided" }`
    * `{ "error": "Failed to parse uploaded file", "detail": <str>, "hint": "Try UTF-8 CSV, or re-export without special delimiters/quotes." }`

---

* **POST** `/api/execute` - Run a natural-language instruction on an uploaded table *(application/json)*

  * **Body (JSON)**
    * `file_id` *(required)* - ID from `/api/upload`
    * `instruction` *(required)* - NL instruction to apply
    * `download` *(optional, default: true)* - export CSV & return link if applicable
    * `chain` *(optional, default: false)* - also save result as a new uploaded file

  * **Response (200)**
    * `file_id` - original file ID
    * `columns` - result DataFrame columns
    * `...payload` - execution payload including:
      * Common: `mode` (`"find"`/`"replace"`), `regex`, `flags`, `columns_applied`, `row_filter`
      * Find-only: `stats`, `examples`, `columns`, `head` *(from original df)*
      * Replace-only: `replacements`, `per_column`, `changed_row_indices`, `head_hit_row_indices`, `head` *(from modified df)*
      * Added by `nl_execute`: `plan_source`, `plan_raw` (when source is `"llm"`), `intent` `{ intent, replacement }`, `regex_source` *(set to `plan_source`)*
      * Conditional (view): if `mode=="replace"` and (`download` or `chain`): `download_url`, `download_filename`; if `chain`: `chain` `{ file_id, filename, is_excel: false, columns, head }`

  * **Special behaviour: Date normalization**
    * If the instruction asks to normalize/standardize dates, the planner may set `intent.replacement` to a reserved sentinel like `__DATE_NORMALIZE__(YYYY-MM-DD; dayfirst=auto)`.  
      The server then normalizes recognized date strings to the requested format.
    * When `pattern` is `^.*$`, whole-cell normalization is attempted; otherwise only date **substrings** in matching cells are normalized.
    * Supported sentinel options:
      * Output format tokens: `YYYY|YY|MM|DD|HH|mm|ss` with separators `- / . _ :`
      * `dayfirst=auto|true|false` (controls D/M/Y ambiguity handling)

  * **Errors (400)**
    * `{ "error": "file_id is required" }`
    * `{ "error": "instruction is required" }`

---

## Notes & Comments
- In dev, media is served by Django (see `MEDIA_URL`).
- For production, use a proper storage service and reverse proxy; lock CORS settings.
- Large files: pandas reads the full file; for huge datasets consider streaming or chunked processing.
- Provide an optimised dataset context for better regex generation by summarizing headers. This can be done either via user instructions, a small user-uploaded sample or basic server-side stats (e.g., unique values, value lengths, etc).
- Use LLM to refine user instructions to make the intent clearer.
- Need more thorough testing with complex combinations of and / or conditions. This is still unstable. One way to make it more robust is to break down complex instructions into simpler steps. Or tell the LLM to only handle one condition at a time (or let LLM to define the condition is an AND or an OR). This is currently optimised by putting the `row_filter` field befroe the `pattern` field in the plan, so that the LLM can focus on row-level filtering first.
- A future improvement could be to allow users to replace more than one value at a time (e.g., mapping "NY" → "New York", "CA" → "California", etc).

## Demo
A video demo is hosted at: [Youtube](https://youtu.be/N2QLOWWpXFw)

---

## License
MIT
