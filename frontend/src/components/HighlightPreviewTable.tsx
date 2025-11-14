import React from "react";

type HighlightPreviewTableProps = {
  rows: any[];
  columns: string[];
  rx: RegExp | null;
  appliedColumns: string[];
  rowMask?: Set<number>;
  fullRowHighlight?: boolean;
};

// HTML escape (XSS-safe)
const htmlEscape = (s: string) =>
  s.replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch] as string));

export function HighlightPreviewTable({
  rows,
  columns,
  rx,
  appliedColumns,
  rowMask,
  fullRowHighlight = false,
}: HighlightPreviewTableProps) {
  if (!rows?.length) return <p className="muted">No data</p>;
  const applied = new Set(appliedColumns);

  const asGlobal = (r: RegExp) =>
    new RegExp(r.source, r.flags.includes("g") ? r.flags : r.flags + "g");

  const rowsAsHtml = rows.map((r, rowIdx) => {
    const out: Record<string, string> = {};
    const highlightThisRow = rowMask ? rowMask.has(rowIdx) : false;
    for (const c of columns) {
      const text = r?.[c] == null ? "" : String(r[c]);
      const shouldMark = !!(rx && !fullRowHighlight && highlightThisRow && applied.has(c) && text);

      if (shouldMark) {
        let last = 0;
        const parts: string[] = [];
        const local = asGlobal(rx!);
        let m: RegExpExecArray | null;
        while ((m = local.exec(text))) {
          parts.push(htmlEscape(text.slice(last, m.index)));
          parts.push("<mark>", htmlEscape(m[0]), "</mark>");
          last = m.index + m[0].length;
          if (m[0].length === 0) local.lastIndex++; // guard zero-length
        }
        parts.push(htmlEscape(text.slice(last)));
        out[c] = parts.join("");
      } else {
        out[c] = htmlEscape(text);
      }
    }
    return { html: out, highlight: highlightThisRow };
  });

  return (
    <div className="table-wrap">
      <table className="table">
        <thead>
          <tr>{columns.map((c) => <th key={c}>{c}</th>)}</tr>
        </thead>
        <tbody>
          {rowsAsHtml.map((row, i) => (
            <tr key={i} className={row.highlight && fullRowHighlight ? "row-hit" : undefined}>
              {columns.map((c) => (
                <td key={c} dangerouslySetInnerHTML={{ __html: row.html[c] }} />
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
