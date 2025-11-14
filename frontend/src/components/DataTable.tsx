import React from "react";

type DataTableProps = {
  rows: any[];
};

export function DataTable({ rows }: DataTableProps) {
  if (!rows?.length) return <p className="muted">No data</p>;
  const cols = Object.keys(rows[0] ?? {});
  return (
    <div className="table-wrap">
      <table className="table">
        <thead>
          <tr>
            {cols.map((c) => (
              <th key={c}>{c}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i}>
              {cols.map((c) => (
                <td key={c}>{String(r?.[c] ?? "")}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
