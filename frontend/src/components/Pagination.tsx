import React from "react";
import { fmtNumber } from "../utils/number";

type PaginationProps = {
  page: number;
  pageSize: number;
  total: number;
  onPageChange: (p: number) => void;
  onPageSizeChange?: (s: number) => void;
  disabled?: boolean;
};

export function Pagination({
  page,
  pageSize,
  total,
  onPageChange,
  onPageSizeChange,
  disabled = false,
}: PaginationProps) {
  const pages = Math.max(1, Math.ceil(total / Math.max(1, pageSize)));
  const cur = Math.min(page, pages);
  const canPrev = cur > 1;
  const canNext = cur < pages;

  const goto = (p: number) => onPageChange(Math.min(Math.max(1, p), pages));

  return (
    <div className="row" style={{ justifyContent: "space-between", alignItems: "center", marginTop: 10 }}>
      <span className="muted">
        {fmtNumber(total)} rows · page {cur}/{pages}
      </span>

      <div className="row" style={{ gap: 6 }}>
        {onPageSizeChange && (
          <select
            className="input"
            style={{ width: 110, padding: "6px 8px", height: 34 }}
            value={pageSize}
            onChange={(e) => onPageSizeChange?.(parseInt(e.target.value, 10))}
            disabled={disabled}
            aria-label="Rows per page"
            title="Rows per page"
          >
            {[50, 100, 200, 500].map((n) => (
              <option key={n} value={n}>
                {n} / page
              </option>
            ))}
          </select>
        )}
        <button className="btn ghost" onClick={() => goto(1)} disabled={!canPrev || disabled}>
          « First
        </button>
        <button className="btn ghost" onClick={() => goto(cur - 1)} disabled={!canPrev || disabled}>
          ‹ Prev
        </button>
        <button className="btn ghost" onClick={() => goto(cur + 1)} disabled={!canNext || disabled}>
          Next ›
        </button>
        <button className="btn ghost" onClick={() => goto(pages)} disabled={!canNext || disabled}>
          Last »
        </button>
      </div>
    </div>
  );
}
