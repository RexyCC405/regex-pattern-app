import React from "react";

export type FindExampleCell = { count: number; html: string };
export type FindExample = { _index: number } & Record<string, FindExampleCell>;

type ExamplesTableProps = {
  examples: FindExample[];
  columns: string[];
};

// Examples table (preview only)
export function ExamplesTable({ examples, columns }: ExamplesTableProps) {
  if (!examples?.length) return <p className="muted">No examples</p>;
  const cols = columns ?? [];
  return (
    <div className="table-wrap">
      <table className="table">
        <thead>
          <tr>
            <th>Row</th>
            {cols.map((c) => (
              <th key={c}>{c}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {examples.map((ex, i) => (
            <tr key={i}>
              <td>{ex._index}</td>
              {cols.map((c) => {
                const cell = ex[c] as unknown as FindExampleCell | undefined;
                if (cell?.html) {
                  return <td key={c} dangerouslySetInnerHTML={{ __html: cell.html }} />;
                }
                return <td key={c}></td>;
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
