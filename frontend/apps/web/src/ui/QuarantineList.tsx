import { useCallback, useEffect, useState } from "react";
import type { QuarantineItem, QuarantinePage } from "@sa/api-client";
import { api } from "../client";
import { Cursor } from "./Cursor";
import { ProblemView } from "./Problem";
import { Table, type Column } from "./Table";

export interface QuarantineListProps {
  batchId: string;
}

const columns: Array<Column<QuarantineItem>> = [
  { key: "line", header: "#", render: (row) => row.line_no ?? "—" },
  { key: "date", header: "日期", render: (row) => row.raw.date ?? "" },
  { key: "code", header: "代码", render: (row) => row.raw.security_code ?? "" },
  { key: "summary", header: "摘要", render: (row) => row.raw.summary ?? "" },
  { key: "reason", header: "原因", render: (row) => row.reason_code },
];

export function QuarantineList({ batchId }: QuarantineListProps) {
  const [page, setPage] = useState<QuarantinePage | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(
    async (cursor?: string) => {
      setLoading(true);
      setError(null);
      try {
        setPage(await api.imports.quarantine(batchId, cursor ? { cursor } : {}));
      } catch (err) {
        setError(err);
      } finally {
        setLoading(false);
      }
    },
    [batchId],
  );

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <div>
      <h3>隔离区（未分类行）</h3>
      {error ? <ProblemView error={error} onRetry={() => void load()} /> : null}
      <Table
        columns={columns}
        rows={page?.items ?? []}
        rowKey={(row) => `${row.line_no ?? "x"}-${row.reason_code}`}
        emptyText={loading ? "加载中…" : "隔离区为空"}
      />
      {page ? (
        <Cursor
          hasMore={Boolean(page.next_cursor)}
          nextCursor={page.next_cursor ?? null}
          disabled={loading}
          onNext={() => void load(page.next_cursor ?? undefined)}
        />
      ) : null}
    </div>
  );
}
