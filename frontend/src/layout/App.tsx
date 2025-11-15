import React, { useMemo, useRef, useState } from "react";
import { API_BASE } from "../config";
import icon from "../../assets/icon.png";
import { Icon } from "../components/Icon";
import { DataTable } from "../components/DataTable";
import { Pagination } from "../components/Pagination";
import { HighlightPreviewTable } from "../components/HighlightPreviewTable";
import type { FindExample } from "../components/ExamplesTable";
import { fmtNumber } from "../utils/number";
import { makeRegex, isMatchAllRegex } from "../utils/regex";
import { getBackendOriginFromApiBase } from "../utils/url";
import type { UploadResp, FindStats, IntentInfo } from "../types/api";


export default function App() {
  const [upload, setUpload] = useState<UploadResp | null>(null);
  const [error, setError] = useState<string>("");
  const [toast, setToast] = useState<string>("");

  const [nl, setNl] = useState("");

  const [processed, setProcessed] = useState<any[] | null>(null);
  const [downloadUrl, setDownloadUrl] = useState<string | null>(null);
  const [loadingRun, setLoadingRun] = useState(false);

  const [findStats, setFindStats] = useState<FindStats | null>(null);
  const [findExamples, setFindExamples] = useState<FindExample[] | null>(null);
  const [findColumns, setFindColumns] = useState<string[]>([]);
  const [intentInfo, setIntentInfo] = useState<IntentInfo | null>(null);
  const [smartMode, setSmartMode] = useState<"find" | "replace" | null>(null);

  const [regex, setRegex] = useState<string>("");
  const [regexFlags, setRegexFlags] = useState<string>("");
  const [regexSource, setRegexSource] = useState<string>("");
  const [displayRegex, setDisplayRegex] = useState<string>("");
  const [displayRegexSource, setDisplayRegexSource] = useState<string>("");

  // Row filter shown in UI (prefer normalized from backend)
  const [rowFilter, setRowFilter] = useState<string>("");

  // UI toggles
  const [showRegex, setShowRegex] = useState<boolean>(false);
  const [autoChain, setAutoChain] = useState<boolean>(true);
  const [showHitsOnly, setShowHitsOnly] = useState<boolean>(false);

  // Replace mode head hits (indices within preview head)
  const [replaceHeadHits, setReplaceHeadHits] = useState<number[]>([]);

  // head-level mask indices from backend for precise preview highlighting
  const [maskHeadIndices, setMaskHeadIndices] = useState<number[] | null>(null);

  const [resultRowsDescription, setResultRowsDescription] = useState<string | null>(null);
  const [resultRowsCount, setResultRowsCount] = useState<number | null>(null);

  // Pagination
  const [page, setPage] = useState<number>(1);
  const [pageSize, setPageSize] = useState<number>(100);

  const inputRef = useRef<HTMLInputElement | null>(null);

  const resolvedDownloadUrl = useMemo(() => {
    if (!downloadUrl) return null;
    if (/^https?:\/\//i.test(downloadUrl)) return downloadUrl;
    const origin = getBackendOriginFromApiBase(API_BASE);
    const path = downloadUrl.startsWith("/") ? downloadUrl : `/${downloadUrl}`;
    return origin ? origin + path : downloadUrl;
  }, [downloadUrl]);

  const effectiveRegexForDisplay = displayRegex || regex;

  const hasRowFilter = rowFilter.trim().length > 0;
  const hasRealPattern = !!effectiveRegexForDisplay && !isMatchAllRegex(effectiveRegexForDisplay);
  const shouldShowResultRows =
    hasRowFilter &&
    hasRealPattern &&
    typeof resultRowsCount === "number" &&
    resultRowsCount >= 0;

  // Actions
  function handleToggleAutoChain() {
    if (!autoChain) {
      const ok = window.confirm(
        [
          "Auto-chain will promote each REPLACE run to be the new working base.",
          "",
          "Important:",
          "• Only runs performed while Auto-chain is ON become the new base.",
          "• Any earlier REPLACE done with Auto-chain OFF will NOT be used as the base for future runs.",
          "• If you need that previous result as the base, re-upload its exported CSV or re-run the REPLACE with Auto-chain ON.",
          "",
          "Turn on Auto-chain now?"
        ].join("\n")
      );
      if (!ok) return;
    }
    setAutoChain((v) => !v);
    setToast(`Auto-chain ${!autoChain ? "ON" : "OFF"}`);
  }

  async function handleFile(file: File) {
    setError("");
    setToast("");
    resetResults();

    const form = new FormData();
    form.append("file", file);

    const res = await fetch(`${API_BASE}/upload`, { method: "POST", body: form });
    if (!res.ok) {
      setError("Upload failed. Please check if the backend is running and migrations are applied.");
      return;
    }
    const json: UploadResp = await res.json();
    setUpload(json);
    setToast(`Uploaded: ${json.filename}`);
  }

  function resetResults() {
    setProcessed(null);
    setDownloadUrl(null);
    setFindStats(null);
    setFindExamples(null);
    setFindColumns([]);
    setIntentInfo(null);
    setSmartMode(null);
    setRegex("");
    setRegexFlags("");
    setRegexSource("");
    setRowFilter("");
    setShowRegex(false);
    setShowHitsOnly(false);
    setReplaceHeadHits([]);
    setMaskHeadIndices(null);
    setPage(1);
    setResultRowsDescription(null);
    setResultRowsCount(null);
    setDisplayRegex("");
    setDisplayRegexSource("");
  }

  async function onFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    const f = e.target.files?.[0];
    if (f) await handleFile(f);
    if (inputRef.current) inputRef.current.value = "";
  }

  function onDrop(e: React.DragEvent<HTMLDivElement>) {
    e.preventDefault();
    if (loadingRun) return;
    const f = e.dataTransfer.files?.[0];
    if (f) handleFile(f);
  }

  function removeFile() {
    setUpload(null);
    resetResults();
    if (inputRef.current) inputRef.current.value = "";
    setToast("File removed");
  }

  // /execute: run find/replace from NL
  async function runExecute() {
    if (!upload) return;
    setError("");
    setLoadingRun(true);

    // clear old
    setProcessed(null);
    setDownloadUrl(null);
    setFindStats(null);
    setFindExamples(null);
    setFindColumns([]);
    setIntentInfo(null);
    setSmartMode(null);
    setShowHitsOnly(false);
    setReplaceHeadHits([]);
    setRegex("");
    setRegexFlags("");
    setRegexSource("");
    setRowFilter("");
    setMaskHeadIndices(null);
    setPage(1);
    setResultRowsDescription(null);
    setResultRowsCount(null);
    setDisplayRegex("");
    setDisplayRegexSource("");

    try {
      const res = await fetch(`${API_BASE}/execute`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          file_id: upload.file_id,
          instruction: nl,
          download: true,
          chain: autoChain
        })
      });
      if (!res.ok) throw new Error("Failed");
      const json = await res.json();

      setRegex(json.regex || "");
      setRegexFlags(json.flags || "");
      setRegexSource(json.regex_source || "");

      // Display-only regex (e.g. row_filter-derived for REPLACE)
      const dr = typeof json.display_regex === "string" ? json.display_regex : "";
      const drSource =
        typeof json.display_regex_source === "string" ? json.display_regex_source : "";

      setDisplayRegex(dr);
      setDisplayRegexSource(drSource);

      if (json.intent) setIntentInfo(json.intent as IntentInfo);

      // Prefer normalized row filter if present, otherwise original
      const rf = (json.row_filter_normalized ?? json.row_filter ?? json.stats?.row_filter ?? "") as string;
      setRowFilter(typeof rf === "string" ? rf : "");

      // NEW: result rows description & count for UI display
      setResultRowsDescription(
        typeof json.result_rows_description === "string"
          ? json.result_rows_description
          : null
      );

      const resultCountFromServer =
        typeof json.result_rows_count === "number"
          ? json.result_rows_count
          : (json.stats?.rows_with_hits as number | undefined);

      setResultRowsCount(
        typeof resultCountFromServer === "number" ? resultCountFromServer : null
      );

      // New head-level mask for preview highlighting (unified for find/replace)
      const headMaskFromServer: number[] | null =
        Array.isArray(json.mask_row_indices_head) ? json.mask_row_indices_head : null;
      setMaskHeadIndices(headMaskFromServer);

      if (json.mode === "find") {
        setSmartMode("find");
        setFindStats(json.stats || null);
        setFindExamples(json.examples || null);
        setFindColumns(json.columns || []);
        const total = json.stats?.total_matches || 0;
        setToast(`Detected FIND · ${fmtNumber(total)} matches`);
      } else {
        setSmartMode("replace");
        setProcessed(json.head || null);
        setDownloadUrl(json.download_url || null);

        // Prefer mask_row_indices_head; fall back to legacy
        const headHits =
          headMaskFromServer ??
          json.head_hit_row_indices ??
          json.stats?.head_hit_row_indices ??
          [];
        setReplaceHeadHits(headHits);

        const count = json.replacements || 0;
        setToast(`Detected REPLACE · ${fmtNumber(count)} changes`);

        if (autoChain) {
          if (json.chain?.file_id) {
            setUpload((prev) => prev ? {
              ...prev,
              file_id: json.chain.file_id,
              is_excel: !!json.chain.is_excel,
              columns: json.chain.columns || [],
              head: json.chain.head || []
            } : prev);
            setToast("Result set as new base (server-chained)");
          } else {
            setToast("Auto-chain ON, but backend returned no chained file.");
          }
        }
      }
    } catch (e) {
      console.log("API_BASE =", API_BASE);
      console.error(e);
      setError("Processing failed. Please check backend logs.");
    } finally {
      setLoadingRun(false);
    }
  }

  const isBusy = loadingRun;

  // —— Find: determine head-hit set for preview (prefer server head mask) —— //
  const headHitsFind = useMemo(
    () => new Set<number>(
      (maskHeadIndices ?? findStats?.head_hit_row_indices ?? []) as number[]
    ),
    [maskHeadIndices, findStats]
  );

  // —— Find preview rows (head slice) —— //
  const previewRowsForFind = useMemo(() => {
    if (!upload?.head?.length) return [];
    const rows = upload.head;

    if (!showHitsOnly) return rows;

    if (headHitsFind.size) {
      // Keep only head rows whose 0-based head index is in the server-provided set
      return rows.filter((_, i) => headHitsFind.has(i));
    }

    // Fallback to client regex if server mask is unavailable
    const cols = upload.columns || Object.keys(rows[0] ?? {});
    const applied = (findColumns?.length ? findColumns : cols);
    const rx = makeRegex(regex, regexFlags);
    if (!rx) return rows;

    const rxCheck = new RegExp(rx.source, rx.flags.replace("g", "") || "i");
    const rowHits = (row: any) =>
      applied.some((c) => {
        const v = row?.[c];
        return v != null && rxCheck.test(String(v));
      });

    return rows.filter(rowHits);
  }, [upload, findStats, headHitsFind, findColumns, showHitsOnly, regex, regexFlags]);

  // —— Replace: page rows (use processed or original head), then filter when showHitsOnly —— //
  const previewRowsForReplace = useMemo(() => {
    if (!processed?.length) return processed ?? [];
    if (!showHitsOnly) return processed;
    const hit = new Set<number>(replaceHeadHits || []);
    return processed.filter((_, i) => hit.has(i));
  }, [processed, showHitsOnly, replaceHeadHits]);

  // —— Full rows to display (mode-aware) —— //
  const fullRowsForDisplay = useMemo(() => {
    if (!upload?.head?.length) return [] as any[];
    if (!smartMode) return upload.head;
    if (smartMode === "find") return previewRowsForFind;
    return previewRowsForReplace ?? processed ?? upload.head;
  }, [upload, smartMode, previewRowsForFind, previewRowsForReplace, processed]);

  // —— Pagination slice —— //
  const pagedRows = useMemo(() => {
    const start = (page - 1) * pageSize;
    return fullRowsForDisplay.slice(start, start + pageSize);
  }, [fullRowsForDisplay, page, pageSize]);

  // —— Find: page row mask using head indices —— //
  const pageRowMask = useMemo<Set<number> | undefined>(() => {
    if (smartMode !== "find") return undefined;
    if (showHitsOnly) return undefined;
    if (!headHitsFind.size) return undefined;

    const start = (page - 1) * pageSize;
    const end = start + pageSize;

    const mask = new Set<number>();
    for (let i = start; i < end; i++) {
      if (headHitsFind.has(i)) mask.add(i - start);
    }
    return mask;
  }, [smartMode, showHitsOnly, headHitsFind, page, pageSize]);

  // —— Replace: page row mask using head indices —— //
  const pageRowMaskReplace = useMemo<Set<number> | undefined>(() => {
    if (smartMode !== "replace") return undefined;
    if (!replaceHeadHits?.length) return undefined;

    const start = (page - 1) * pageSize;
    const end = start + pageSize;

    const mask = new Set<number>();
    for (let i = start; i < end; i++) {
      if (replaceHeadHits.includes(i)) mask.add(i - start);
    }
    return mask;
  }, [smartMode, replaceHeadHits, page, pageSize]);

  return (
    <div className="app" aria-busy={isBusy}>
      <style>{css}</style>

      <header className="hero">
        <div className="hero-inner">
          <div>
            <h1 style={{ display: "flex", alignItems: "center", gap: 10 }}>
              <img src={icon} alt="App Icon" width={32} style={{ height: "auto" }} />
              <span className="brand">Data Transformation Platform</span>
            </h1>
            <p className="sub">Upload → describe in natural language → auto Find/Replace → preview & export</p>
          </div>
          {resolvedDownloadUrl && (
            <a
              className="btn ghost"
              href={isBusy ? undefined : resolvedDownloadUrl ?? undefined}
              target="_blank"
              rel="noopener noreferrer"
              aria-disabled={isBusy}
              style={isBusy ? { pointerEvents: "none", opacity: 0.6 } : undefined}
              title={isBusy ? "Processing…" : "Download result"}
            >
              <Icon.Download style={{ marginRight: 8 }} /> Download result
            </a>
          )}
        </div>
        <div className="bg-glow" aria-hidden />
      </header>

      <main className="grid">
        {/* —— Search / Instruction —— */}
        <section className="card" style={{ gridColumn: "1 / -1" }}>
          <div className="card-head">
            <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
              <h3 style={{ margin: 0 }}>Search / Instruction</h3>
            </div>
            <div className="row" style={{ gap: 8 }}>
              <button className="btn" onClick={runExecute} disabled={!upload || isBusy}>
                {isBusy ? <Icon.Loader /> : <Icon.Sparkles style={{ marginRight: 8 }} />} Start processing
              </button>
              <button
                className="btn ghost"
                onClick={() => setShowRegex((v) => !v)}
                disabled={isBusy || (!regex && !intentInfo && !rowFilter)}
                title={showRegex ? "Hide regex & filters" : "Show regex & filters"}
              >
                <Icon.Regex />
                {showRegex ? "Hide Regex/Filter" : "Show Regex/Filter"}
              </button>
            </div>
          </div>

          <div className="row">
            <label className="label">Query</label>
            <input
              className="input"
              value={nl}
              onChange={(e) => setNl(e.target.value)}
              placeholder="e.g., Find email addresses · Replace URLs with [LINK] · Mask phone numbers"
              disabled={isBusy}
            />
          </div>

          <div className="pillbar" aria-disabled={isBusy}>
            {[
              "Find email addresses",
              "Find Charlie's orders",
              "Replace URLs with [LINK]",
              "Mask phone numbers",
              "Normalize dates to YYYY-MM-DD"
            ].map((t) => (
              <button key={t} className="pill" onClick={() => setNl(t)} disabled={isBusy}>
                <Icon.Sparkles style={{ marginRight: 6 }} /> {t}
              </button>
            ))}
          </div>

          {showRegex && (effectiveRegexForDisplay || intentInfo || rowFilter) && (
            <div className="actions">
              {effectiveRegexForDisplay && (
                <div className="regex-chip" title={effectiveRegexForDisplay}>
                  <code>{effectiveRegexForDisplay}</code>
                  <span className="source">
                    {displayRegex
                      ? displayRegexSource || "row_filter-derived"
                      : regexSource || "regex"}
                  </span>
                </div>
              )}
              {rowFilter && (
                <div className="regex-chip" title={rowFilter}>
                  <code>{rowFilter}</code>
                  <span className="source">row filter</span>
                </div>
              )}
              {intentInfo && (
                <div
                  className="regex-chip"
                  title={`confidence ${Math.round((intentInfo.confidence || 0) * 100)}%`}
                >
                  <code>
                    {intentInfo.intent === "replace"
                      ? `replace → ${intentInfo.replacement || "REDACTED"}`
                      : "find only"}
                  </code>
                  <span className="source">intent</span>
                </div>
              )}
            </div>
          )}
        </section>

        {/* —— Data & Results —— */}
        <section className="card" style={{ gridColumn: "1 / -1" }}>
          <div className="card-head">
            <h3>Data & Results</h3>
            <div className="row" style={{ gap: 8 }}>
              {smartMode === "replace" && resolvedDownloadUrl && (
                <a
                  className="btn ghost"
                  href={isBusy ? undefined : resolvedDownloadUrl ?? undefined}
                  target="_blank"
                  rel="noopener noreferrer"
                  aria-disabled={isBusy}
                  style={{ pointerEvents: isBusy ? "none" : undefined, opacity: isBusy ? 0.6 : undefined }}
                  title={isBusy ? "Processing…" : "Download CSV"}
                >
                  <Icon.Download style={{ marginRight: 8 }} /> Download CSV
                </a>
              )}
              <button
                className={autoChain ? "btn" : "btn ghost"}
                style={{ padding: "6px 10px" }}
                onClick={handleToggleAutoChain}
                aria-pressed={autoChain}
                title="Auto-chain on replace"
                disabled={isBusy}
              >
                ↻ Auto-chain {autoChain ? "ON" : "OFF"}
              </button>
            </div>
          </div>

          {/* hidden input for new upload trigger */}
          <input
            ref={inputRef}
            type="file"
            accept=".csv,.xlsx,.xls"
            onChange={onFileChange}
            hidden
          />

          {/* Upload area */}
          {!upload ? (
            <div
              className="dropzone"
              onDragOver={(e) => e.preventDefault()}
              onDrop={onDrop}
              onClick={() => { if (!isBusy) inputRef.current?.click(); }}
              style={isBusy ? { pointerEvents: "none", opacity: 0.7 } : undefined}
              title={isBusy ? "Processing…" : undefined}
            >
              <p className="dz-title">
                Drag & drop file here, or <span className="link">click to choose</span>
              </p>
              <p className="muted">Supports .csv / .xlsx / .xls</p>
            </div>
          ) : (
            <div className="upload-meta" style={{ marginTop: 10, display: "flex", alignItems: "center", gap: 10 }}>
              <span className="badge">{upload.filename}</span>
              <span className="muted">
                {upload.is_excel ? "Excel" : "CSV"} · {upload.columns.length} columns · preview {upload.head.length} rows
              </span>
              <div style={{ display: "flex", gap: 8, marginLeft: "auto" }}>
                <button
                  className="btn ghost"
                  style={{ padding: "6px 10px" }}
                  onClick={() => inputRef.current?.click()}
                  disabled={isBusy}
                  title="Upload new file"
                >
                  <Icon.Upload /> Upload new file
                </button>
                <button
                  className="btn ghost"
                  style={{ padding: "6px 10px" }}
                  onClick={removeFile}
                  disabled={isBusy}
                  title="Remove current file"
                >
                  ✕ Remove file
                </button>
              </div>
            </div>
          )}

          {/* Content */}
          <div className="preview" style={{ marginTop: 10 }}>
            {!upload && <p className="muted">Upload a file to view preview</p>}

            {/* No mode yet: show original preview */}
            {!smartMode && upload && (
              <>
                <DataTable rows={pagedRows} />
                <Pagination
                  page={page}
                  pageSize={pageSize}
                  total={fullRowsForDisplay.length}
                  onPageChange={setPage}
                  onPageSizeChange={(s) => { setPageSize(s); setPage(1); }}
                  disabled={isBusy}
                />
              </>
            )}

            {/* FIND mode */}
            {smartMode === "find" && upload && (
              <div>
                {intentInfo && (
                  <p className="muted">
                    Intent: <strong>{intentInfo.intent}</strong> · Replacement{" "}
                    {intentInfo.replacement || "(none)"}
                  </p>
                )}

                {shouldShowResultRows && (
                  <p className="muted">
                    Result rows:{" "}
                    <strong>{fmtNumber(resultRowsCount ?? 0)}</strong>{" "}
                    <span style={{ opacity: 0.85 }}>
                      {resultRowsDescription || (
                        <>
                          Result rows = rows where<br />
                          1. <strong>row_filter</strong> is true<br />
                          2. at least one of the selected columns matches the pattern
                        </>
                      )}
                    </span>
                  </p>
                )}

                <div className="row" style={{ gap: 8, margin: "6px 0" }}>
                  <button
                    className={showHitsOnly ? "btn" : "btn ghost"}
                    onClick={() => { setShowHitsOnly((v) => !v); setPage(1); }}
                    disabled={isBusy}
                    title="Display only rows with matches"
                  >
                    Display only rows with matches
                  </button>
                </div>

                <HighlightPreviewTable
                  rows={pagedRows}
                  columns={upload.columns}
                  rx={makeRegex(regex, regexFlags)}
                  appliedColumns={findColumns?.length ? findColumns : upload.columns}
                  rowMask={
                    showHitsOnly
                      ? new Set(Array.from({ length: pagedRows.length }, (_, i) => i)) // when showing only hits, highlight all visible rows
                      : pageRowMask
                  }
                  fullRowHighlight={true}
                />

                <Pagination
                  page={page}
                  pageSize={pageSize}
                  total={fullRowsForDisplay.length}
                  onPageChange={setPage}
                  onPageSizeChange={(s) => { setPageSize(s); setPage(1); }}
                  disabled={isBusy}
                />
              </div>
            )}

            {/* REPLACE mode */}
            {smartMode === "replace" && upload && (
              <div>
                {intentInfo && (
                  <p className="muted">
                    Intent: <strong>{intentInfo.intent || "replace"}</strong> · Replacement:{" "}
                    {intentInfo.replacement || "REDACTED"}
                  </p>
                )}

                {typeof resultRowsCount === "number" && (
                  <p className="muted">
                    Result rows:{" "}
                    <strong>{fmtNumber(resultRowsCount)}</strong>{" "}
                    <span style={{ opacity: 0.85 }}>
                      {resultRowsDescription || (
                        <>
                          Result rows = rows where<br />
                          1️⃣ <strong>row_filter</strong> is true<br />
                          2️⃣ at least one of the selected columns matches the pattern
                        </>
                      )}
                    </span>
                  </p>
                )}

                <div className="row" style={{ gap: 8, margin: "6px 0" }}>
                  <button
                    className={showHitsOnly ? "btn" : "btn ghost"}
                    onClick={() => { setShowHitsOnly((v) => !v); setPage(1); }}
                    disabled={isBusy}
                    title="Display only rows with changes"
                  >
                    Display only rows with changes
                  </button>
                </div>

                <HighlightPreviewTable
                  rows={pagedRows}
                  columns={upload.columns}
                  rx={null}
                  appliedColumns={upload.columns}
                  rowMask={
                    showHitsOnly
                      ? new Set(Array.from({ length: pagedRows.length }, (_, i) => i))
                      : pageRowMaskReplace
                  }
                  fullRowHighlight={true}
                />

                <Pagination
                  page={page}
                  pageSize={pageSize}
                  total={fullRowsForDisplay.length}
                  onPageChange={setPage}
                  onPageSizeChange={(s) => { setPageSize(s); setPage(1); }}
                  disabled={isBusy}
                />
              </div>
            )}
          </div>
        </section>
      </main>

      {/* Toast / Error */}
      {toast && (
        <div className="toast" role="status" onAnimationEnd={() => setToast("")}>{toast}</div>
      )}
      {error && (
        <div className="toast error" role="alert" onClick={() => setError("")}>{error}</div>
      )}

      <footer className="foot">
        <span>Made with ❤</span>
      </footer>
    </div>
  );
}

// —— Styles: DO NOT MODIFY ——
const css = `
:root{
  /* Theme tuned to the provided images (turquoise/cyan brand on deep slate background) */
  --bg:#0b1217;
  --panel:#121a27;
  --border:#1f2c3a;
  --text:#e6e9ef;
  --muted:#9aa3b2;

  /* Brand colors from the screenshots */
  --brand:#6ec5d5;    /* cyan title color */
  --brand-2:#2a6f89;  /* darker teal/blue from the logo */
  --ring:#6ec5d5;     /* focus ring = brand */

  /* Status colors (unchanged semantics) */
  --ok:#38d39f; --warn:#ffce57; --err:#ff7a7a;
}
*{box-sizing:border-box}
html,body,#root{height:100%}
body {
  margin: 0;
  background: var(--bg);
  color: var(--text);
  font:14px/1.45 ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial;
}

.app{min-height:100%; display:flex; flex-direction:column}
.hero{position:relative; padding:40px 24px}
.hero-inner{max-width:1100px; margin:0 auto; display:flex; align-items:flex-end; justify-content:space-between; gap:16px}
.hero h1{margin:0; font-size:28px}
.brand{background:linear-gradient(90deg, var(--brand), var(--brand-2)); -webkit-background-clip:text; background-clip:text; color:transparent}
.sub{margin:6px 0 0; color:var(--muted)}
.bg-glow{position:absolute; inset:0; pointer-events:none;}

.grid{max-width:1100px; margin:8px auto 40px; padding:0 24px; display:grid; grid-template-columns:1fr 1fr; gap:16px}
@media (max-width: 980px){ .grid{grid-template-columns:1fr} }

.card{background:linear-gradient(180deg, rgba(255,255,255,.02), rgba(255,255,255,.01)); border:1px solid var(--border); border-radius:16px; padding:16px; position:relative}
.card-head{display:flex; align-items:center; justify-content:space-between; gap:10px; margin-bottom:10px}
.card-head h3{margin:0; font-size:16px}

.dropzone{border:1px dashed var(--border); border-radius:14px; padding:24px; text-align:center; cursor:pointer; transition: all .2s ease}
.dropzone:hover{border-color:var(--ring); box-shadow:0 0 0 4px rgba(110,197,213,.12)}
.dz-title{margin:0 0 4px}
.link{color:var(--brand)}
.upload-meta{display:flex; align-items:center; gap:10px; margin-top:10px}

.pillbar{display:flex; flex-wrap:wrap; gap:8px; margin-top:12px}
.pill{display:inline-flex; align-items:center; gap:4px; padding:8px 10px; border:1px solid var(--border); background:rgba(255,255,255,.02); border-radius:999px; color:var(--text); cursor:pointer}
.pill:hover{border-color:var(--ring)}

.row{display:flex; gap:10px}
.label{min-width:64px; display:flex; align-items:center; color:var(--muted)}
.input{flex:1; border:1px solid var(--border); background:rgba(12,16,40,.75); color:var(--text); border-radius:12px; padding:10px 12px; outline:none}
.input:focus{border-color:var(--ring); box-shadow:0 0 0 4px rgba(110,197,213,.12)}

.actions{display:flex; flex-direction:column; gap:8px; margin-top:12px;}
.regex-chip {display:flex; align-items:center; gap:8px; padding:8px 10px; border:1px solid var(--border); border-radius:10px; max-width:100%; overflow:hidden; font-size:9px}
.regex-chip code{white-space:nowrap; overflow:hidden; text-overflow:ellipsis}
.source{font-size:12px; color:var(--muted); border:1px solid var(--border); padding:2px 6px; border-radius:999px}

.cols{display:flex; gap:8px; flex-wrap:wrap; max-height:196px; overflow:auto; padding:2px}
.col-pill{display:inline-flex; align-items:center; gap:8px; border:1px solid var(--border); padding:8px 10px; border-radius:10px; cursor:pointer; user-select:none}
.col-pill input{accent-color:var(--ring)}
.col-pill.is-selected{background:rgba(110,197,213,.08); border-color:var(--ring)}

.btn{
  display:inline-flex; align-items:center; justify-content:center; gap:6px; padding:10px 14px; border-radius:12px;
  background:linear-gradient(180deg, var(--brand), var(--brand-2));
  color:#fff; border:1px solid var(--brand-2); cursor:pointer; font-weight:600
}
.btn:disabled{opacity:.6; cursor:not-allowed}
.btn.ghost{background:transparent; color:var(--text); border:1px solid var(--border)}
.btn.ghost:hover{border-color:var(--ring)}

.table-wrap{max-height:420px; overflow:auto; border:1px solid var(--border); border-radius:12px}
.table{width:100%; border-collapse:collapse; font-size:13px}
.table th, .table td{padding:10px 12px; border-bottom:1px solid var(--border)}
.table thead th{position:sticky; top:0; background:rgba(9,13,30,.9); backdrop-filter: blur(6px); z-index:1}
.table tr.row-hit td{
  background: rgba(110,197,213,.14);
}
.table tr.row-hit td:first-child{
  box-shadow: inset 3px 0 0 var(--ring);
}

.preview{margin-top:6px}
.muted{color:var(--muted)}
.badge{display:inline-flex; align-items:center; padding:6px 10px; border:1px solid var(--ring); border-radius:999px; color:#fff; background:rgba(110,197,213,.08)}

.toast{position:fixed; left:50%; transform:translateX(-50%); bottom:18px; background:#121936; border:1px solid var(--ring); color:var(--text); padding:10px 14px; border-radius:12px; box-shadow:0 10px 40px rgba(0,0,0,.3); animation:pop .25s ease, fadeOut 3s 1s forwards}
.toast.error{border-color:var(--err); color:#ffd6d6}
@keyframes pop{from{transform:translateX(-50%) scale(.95); opacity:.6} to{transform:translateX(-50%) scale(1); opacity:1}}
@keyframes fadeOut{to{opacity:0}}

.foot{opacity:.7; text-align:center; padding:30px 0; background:var(--bg)}


svg.spin{animation:spin 1s linear infinite}
@keyframes spin { to { transform: rotate(360deg) } }
`;
