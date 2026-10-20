import { useCallback, useEffect, useState, type FormEvent } from "react";
import type { CreatedPatToken, PatToken } from "@sa/api-client";
import { api } from "../client";
import { ProblemView } from "../ui/Problem";

export function Settings() {
  const [tokens, setTokens] = useState<PatToken[]>([]);
  const [created, setCreated] = useState<CreatedPatToken | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [name, setName] = useState("");
  const [read, setRead] = useState(true);
  const [write, setWrite] = useState(false);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setError(null);
    try {
      const list = await api.tokens.list();
      setTokens(list.items);
    } catch (err) {
      setError(err);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function create(event: FormEvent): Promise<void> {
    event.preventDefault();
    setBusy(true);
    setError(null);
    setCreated(null);
    const scopes: Array<"read" | "write"> = [];
    if (read) scopes.push("read");
    if (write) scopes.push("write");
    try {
      // 明文只返回一次：必须在此处立刻展示并提示
      setCreated(await api.tokens.create({ name, scopes }));
      setName("");
      await load();
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  async function revoke(token: PatToken): Promise<void> {
    if (!window.confirm(`撤销 PAT「${token.name}」？`)) return;
    setError(null);
    try {
      await api.tokens.revoke(token.token_id);
      await load();
    } catch (err) {
      setError(err);
    }
  }

  return (
    <div>
      <h1>设置 · 访问令牌（PAT）</h1>
      <ProblemView error={error} onRetry={() => void load()} />

      <form className="card" onSubmit={(event) => void create(event)}>
        <h2>创建 PAT</h2>
        <label>
          名称
          <input value={name} onChange={(e) => setName(e.target.value)} maxLength={64} required />
        </label>
        <div className="row-actions">
          <label className="check">
            <input type="checkbox" checked={read} onChange={(e) => setRead(e.target.checked)} />
            read（只读）
          </label>
          <label className="check">
            <input type="checkbox" checked={write} onChange={(e) => setWrite(e.target.checked)} />
            write（可写）
          </label>
        </div>
        <button className="btn" type="submit" disabled={busy || (!read && !write) || !name}>
          {busy ? "创建中…" : "创建"}
        </button>
      </form>

      {created ? (
        <div className="card token-once">
          <h2>请立即保存：只显示这一次</h2>
          <code className="token-value">{created.token}</code>
          <p className="muted">离开本页后无法再次查看明文；若遗失请撤销后重新创建。</p>
        </div>
      ) : null}

      <h2>已创建</h2>
      {tokens.length === 0 ? (
        <p className="muted">暂无令牌</p>
      ) : (
        <table className="table">
          <thead>
            <tr>
              <th>名称</th>
              <th>前缀</th>
              <th>权限</th>
              <th>过期</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            {tokens.map((token) => (
              <tr key={token.token_id}>
                <td>{token.name}</td>
                <td>{token.prefix}</td>
                <td>{token.scopes.join(", ")}</td>
                <td>{token.expires_at}</td>
                <td>
                  <button className="btn btn-ghost" type="button" onClick={() => void revoke(token)}>
                    撤销
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
