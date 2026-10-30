import { useCallback, useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import type { BatchList, ImportBatch, ImportBatchStatus, RevokeResult } from "@sa/api-client";
import { api } from "../client";
import { Cursor } from "../ui/Cursor";
import { ProblemView } from "../ui/Problem";
import { QuarantineList } from "../ui/QuarantineList";
import { RevokeBatchButton } from "../ui/RevokeBatch";
import { DataTable, type DataTableColumn } from "../ui/DataTable";

const STATUS_LABELS: Record<ImportBatchStatus, string> = {
  pending: "等待中",
  running: "处理中",
  succeeded: "已完成",
  failed: "失败",
  revoked: "已撤销",
};

/** 状态中文标签；未知状态原样展示（后端新增状态时不至于空白）。 */
export function batchStatusLabel(status: string): string {
  return STATUS_LABELS[status as ImportBatchStatus] ?? status;
}

function formatAssumption(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  try {
    return JSON.stringify(value) ?? "—";
  } catch {
    return "—";
  }
}

function countSummary(batch: ImportBatch): string {
  const { parsed, inserted, skipped, quarantined } = batch.counts;
  const filled = batch.filled_count ?? 0;
  return `解析 ${parsed} · 写入 ${inserted} · 跳过 ${skipped} · 隔离 ${quarantined}${filled > 0 ? ` · 系统补差 ${filled}` : ""}`;
}

/** 导入记录（/imports）：批次列表 + 详情（计数/警告/假设与范围）+ 隔离区 + 撤销。 */
export function Imports() {
  const [searchParams] = useSearchParams();
  const deepLinkBatch = searchParams.get("batch");

  const [page, setPage] = useState<BatchList | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<unknown>(null);

  const [selectedId, setSelectedId] = useState<string | null>(deepLinkBatch);
  const [detail, setDetail] = useState<ImportBatch | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<unknown>(null);

  const [notice, setNotice] = useState("");

  const load = useCallback(async (cursor?: string) => {
    setLoading(true);
    setError(null);
    try {
      setPage(await api.imports.listBatches({ limit: 20, cursor }));
    } catch (err) {
      setError(err);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const loadDetail = useCallback(async (batchId: string) => {
    setDetailLoading(true);
    setDetailError(null);
    try {
      setDetail(await api.imports.getBatch(batchId));
    } catch (err) {
      setDetailError(err);
      setDetail(null);
    } finally {
      setDetailLoading(false);
    }
  }, []);

  useEffect(() => {
    if (selectedId) void loadDetail(selectedId);
  }, [selectedId, loadDetail]);

  const onRevoked = useCallback(
    (result: RevokeResult) => {
      setNotice(
        `已撤销批次 ${result.batch_id}：回退流水 ${result.reverted.inserted ?? 0} 行（撤销于 ${result.revoked_at}）。流水列表已回退，可在「交易流水」核对。`,
      );
      void load();
      if (selectedId) void loadDetail(selectedId);
    },
    [load, loadDetail, selectedId],
  );

  const columns: Array<DataTableColumn<ImportBatch>> = [
    { key: "batch", header: "批次号", priority: 1, render: (row) => <code>{row.batch_id}</code> },
    { key: "file", header: "文件", priority: 1, render: (row) => row.file_name || "—" },
    {
      key: "status",
      header: "状态",
      priority: 1,
      render: (row) => (
        <span className={`badge badge-${row.status}`}>{batchStatusLabel(row.status)}</span>
      ),
    },
    { key: "mode", header: "模式", priority: 3, render: (row) => row.mode },
    {
      key: "counts",
      header: "解析 / 写入 / 跳过 / 隔离",
      priority: 2,
      align: "right",
      render: (row) => countSummary(row),
    },
    { key: "created", header: "创建时间", priority: 2, render: (row) => row.created_at },
    {
      key: "actions",
      header: "操作",
      priority: 1,
      align: "right",
      render: (row) => (
        <div className="row-actions">
          <button className="btn btn-ghost" type="button" onClick={() => setSelectedId(row.batch_id)}>
            详情
          </button>
          {row.can_revoke ? <RevokeBatchButton batchId={row.batch_id} onRevoked={onRevoked} /> : null}
        </div>
      ),
    },
  ];

  return (
    <div>
      <h1>导入记录</h1>
      <p className="muted">每次导入生成一个批次；撤销会软删该批次写入的流水，撤销后流水行数回退。</p>

      {notice ? <p className="notice">{notice}</p> : null}
      <ProblemView error={error} onRetry={() => void load()} />

      <DataTable
        caption="导入批次列表"
        columns={columns}
        rows={page?.items ?? []}
        rowKey={(row) => row.batch_id}
        loading={loading && page == null}
        emptyText="还没有导入批次；先去「导入」上传账单"
      />
      {page ? (
        <Cursor
          hasMore={Boolean(page.next_cursor)}
          nextCursor={page.next_cursor ?? null}
          disabled={loading}
          onNext={() => void load(page.next_cursor ?? undefined)}
          onReset={() => void load()}
        />
      ) : null}

      {selectedId ? (
        <div className="card">
          <div className="row-actions">
            <h2>批次详情</h2>
            <button className="btn btn-ghost" type="button" onClick={() => setSelectedId(null)}>
              收起
            </button>
          </div>

          {detailLoading ? <p className="muted">加载中…</p> : null}
          <ProblemView error={detailError} onRetry={() => void loadDetail(selectedId)} />

          {detail ? (
            <>
              <p className="muted">
                批次 <code>{detail.batch_id}</code> · 文件 {detail.file_name} · 券商 {detail.broker} · 模式 {detail.mode} · 状态{" "}
                {batchStatusLabel(detail.status)}
              </p>
              <p>{countSummary(detail)}</p>
              <p className="muted">
                创建 {detail.created_at}
                {detail.finished_at ? ` · 完成 ${detail.finished_at}` : ""}
                {detail.revoked_at ? ` · 撤销 ${detail.revoked_at}` : ""}
              </p>

              {(detail.filled_count ?? 0) > 0 ? (
                <div className="notice-warn" role="alert">
                  <h3>账单存在缺失：系统已自动补差 {detail.filled_count} 笔</h3>
                  <table className="table">
                    <thead>
                      <tr>
                        <th>日期</th>
                        <th>行号</th>
                        <th>补差金额</th>
                        <th>补差后余额</th>
                        <th>摘要</th>
                      </tr>
                    </thead>
                    <tbody>
                      {(detail.fills ?? []).map((fill) => (
                        <tr key={fill.trade_id}>
                          <td>{fill.trade_date}</td>
                          <td>{fill.line_no ?? "—"}</td>
                          <td>{fill.amount}</td>
                          <td>{fill.cash_balance ?? "—"}</td>
                          <td>{fill.summary ?? "对账自动补差"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  <p className="muted">补差行 side=missing、source=system；撤销本批次会一并删除这些补差流水。</p>
                </div>
              ) : null}

              <h3>警告</h3>
              {(detail.warnings ?? []).length > 0 ? (
                <ul>
                  {(detail.warnings ?? []).map((warning, index) => (
                    <li key={`${warning}-${index}`}>
                      <code>{warning}</code>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="muted">无警告</p>
              )}

              <h3>假设与范围</h3>
              {detail.assumptions && Object.keys(detail.assumptions).length > 0 ? (
                <table className="table">
                  <thead>
                    <tr>
                      <th>键</th>
                      <th>值</th>
                    </tr>
                  </thead>
                  <tbody>
                    {Object.entries(detail.assumptions).map(([key, value]) => (
                      <tr key={key}>
                        <td>{key}</td>
                        <td>{formatAssumption(value)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : (
                <p className="muted">无额外假设</p>
              )}

              {detail.can_revoke ? (
                <RevokeBatchButton batchId={detail.batch_id} label="撤销本批次" onRevoked={onRevoked} />
              ) : (
                <p className="muted">该批次当前不可撤销（已撤销或未成功完成）。</p>
              )}

              <QuarantineList batchId={detail.batch_id} />
            </>
          ) : null}
        </div>
      ) : (
        <p className="muted">点某行的「详情」查看计数、警告、假设与隔离区。</p>
      )}
    </div>
  );
}
