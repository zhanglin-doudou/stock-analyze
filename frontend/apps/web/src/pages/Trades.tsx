import { useCallback, useEffect, useState } from "react";
import { newIdempotencyKey, type Trade, type TradePage } from "@sa/api-client";
import { api } from "../client";
import { Cursor } from "../ui/Cursor";
import { ProblemView } from "../ui/Problem";
import { DataTable, type DataTableColumn } from "../ui/DataTable";

interface EditState {
  tradeId: string;
  quantity: string;
  price: string;
  amount: string;
}

export function Trades() {
  const [page, setPage] = useState<TradePage | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(false);
  const [includeDeleted, setIncludeDeleted] = useState(false);
  const [edit, setEdit] = useState<EditState | null>(null);
  const [notice, setNotice] = useState("");

  const load = useCallback(
    async (cursor?: string) => {
      setLoading(true);
      setError(null);
      try {
        setPage(
          await api.trades.list({
            limit: 50,
            cursor,
            include_deleted: includeDeleted ? true : undefined,
          }),
        );
      } catch (err) {
        setError(err);
      } finally {
        setLoading(false);
      }
    },
    [includeDeleted],
  );

  useEffect(() => {
    void load();
  }, [load]);

  async function saveEdit(trade: Trade): Promise<void> {
    if (!edit) return;
    setError(null);
    setNotice("");
    try {
      await api.trades.patch(
        trade.trade_id,
        { quantity: edit.quantity, price: edit.price, amount: edit.amount },
        { version: trade.version, idempotencyKey: newIdempotencyKey() },
      );
      setEdit(null);
      setNotice("已保存修改。");
      await load();
    } catch (err) {
      // 409 VERSION_CONFLICT / 幂等冲突等都会落到这里，由 ProblemView 渲染人话
      setError(err);
    }
  }

  async function remove(trade: Trade): Promise<void> {
    if (!window.confirm(`确认软删 ${trade.trade_id}？`)) return;
    setError(null);
    try {
      await api.trades.remove(trade.trade_id);
      setNotice("已软删，可在包含已删除时恢复。");
      await load();
    } catch (err) {
      setError(err);
    }
  }

  async function restore(trade: Trade): Promise<void> {
    setError(null);
    try {
      await api.trades.restore(trade.trade_id);
      setNotice("已恢复。");
      await load();
    } catch (err) {
      setError(err);
    }
  }

  const columns: Array<DataTableColumn<Trade>> = [
    { key: "date", header: "业务日", priority: 1, render: (row) => row.trade_date },
    {
      key: "code",
      header: "代码",
      priority: 1,
      render: (row) => `${row.security_code} ${row.security_name ?? ""}`.trim(),
    },
    { key: "side", header: "方向", priority: 1, render: (row) => row.side },
    { key: "quantity", header: "数量", priority: 2, align: "right", render: (row) => row.quantity },
    { key: "price", header: "价格", priority: 2, align: "right", render: (row) => row.price },
    { key: "amount", header: "金额", priority: 2, align: "right", render: (row) => row.amount },
    {
      key: "cash_balance",
      header: "现金余额",
      priority: 3,
      align: "right",
      render: (row) => row.cash_balance,
    },
    { key: "version", header: "版本", priority: 3, align: "right", render: (row) => row.version },
    {
      key: "deleted",
      header: "状态",
      priority: 2,
      render: (row) => (row.deleted_at ? `已删除 ${row.deleted_at}` : "正常"),
    },
    {
      key: "actions",
      header: "操作",
      priority: 1,
      align: "right",
      render: (row) => (
        <div className="row-actions">
          {row.deleted_at ? (
            <button className="btn btn-ghost" type="button" onClick={() => void restore(row)}>
              恢复
            </button>
          ) : (
            <>
              <button
                className="btn btn-ghost"
                type="button"
                onClick={() =>
                  setEdit({
                    tradeId: row.trade_id,
                    quantity: row.quantity,
                    price: row.price,
                    amount: row.amount,
                  })
                }
              >
                编辑
              </button>
              <button className="btn btn-ghost" type="button" onClick={() => void remove(row)}>
                软删
              </button>
            </>
          )}
        </div>
      ),
    },
  ];

  return (
    <div>
      <h1>交易流水</h1>
      <p className="muted">数据截止（data_as_of）：{page?.meta.data_as_of || "—"}</p>
      <label className="check">
        <input type="checkbox" checked={includeDeleted} onChange={(e) => setIncludeDeleted(e.target.checked)} />
        包含已删除
      </label>

      {notice ? <p className="notice">{notice}</p> : null}
      <ProblemView error={error} onRetry={() => void load()} />

      {edit ? (
        <div className="card edit-card">
          <h3>编辑 {edit.tradeId}（提交带 If-Match 版本号）</h3>
          <label>
            数量
            <input value={edit.quantity} onChange={(e) => setEdit({ ...edit, quantity: e.target.value })} />
          </label>
          <label>
            价格
            <input value={edit.price} onChange={(e) => setEdit({ ...edit, price: e.target.value })} />
          </label>
          <label>
            金额
            <input value={edit.amount} onChange={(e) => setEdit({ ...edit, amount: e.target.value })} />
          </label>
          <p className="muted">金额/数量/价格为定点字符串，原样提交，不做数值计算。</p>
          <div className="row-actions">
            <button
              className="btn"
              type="button"
              onClick={() => {
                const target = page?.items.find((item) => item.trade_id === edit.tradeId);
                if (target) void saveEdit(target);
              }}
            >
              保存
            </button>
            <button className="btn btn-ghost" type="button" onClick={() => setEdit(null)}>
              取消
            </button>
          </div>
        </div>
      ) : null}

      <DataTable
        caption="交易流水列表"
        columns={columns}
        rows={page?.items ?? []}
        rowKey={(row) => row.trade_id}
        loading={loading && page == null}
        emptyText="没有流水；先去「导入」上传账单"
      />

      {page ? (
        <Cursor
          hasMore={page.has_more}
          nextCursor={page.next_cursor ?? null}
          disabled={loading}
          onNext={() => void load(page.next_cursor ?? undefined)}
          onReset={() => void load()}
        />
      ) : null}
    </div>
  );
}
