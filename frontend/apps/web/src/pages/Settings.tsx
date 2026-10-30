import { useCallback, useEffect, useState, type FormEvent } from "react";
import type { CreatedPatToken, PatToken } from "@sa/api-client";
import { api } from "../client";
import { DataTable, type DataTableColumn } from "../ui/DataTable";
import { ProblemView } from "../ui/Problem";
import {
  loadPreferences,
  updatePreferences,
  type Density,
  type Preferences,
  type Theme,
  type TrendColor,
} from "../theme/theme";
import "./settings.css";

const DENSITY_OPTIONS: Array<{ value: Density; label: string; hint: string }> = [
  { value: "cozy", label: "舒适", hint: "表头/行高更大，适合长时间阅读" },
  { value: "compact", label: "紧凑", hint: "一屏更多行，适合密集列表" },
];

const THEME_OPTIONS: Array<{ value: Theme; label: string; hint: string }> = [
  { value: "light", label: "浅色", hint: "纸感浅色（默认）" },
  { value: "dark", label: "深色", hint: "暗色高对比，适合夜间复盘" },
];

const TREND_OPTIONS: Array<{ value: TrendColor; label: string; hint: string }> = [
  { value: "red-up", label: "红涨绿跌", hint: "A 股惯例（默认）" },
  { value: "green-up", label: "绿涨红跌", hint: "国际惯例 / 个人习惯" },
];

export function Settings() {
  const [tokens, setTokens] = useState<PatToken[]>([]);
  const [created, setCreated] = useState<CreatedPatToken | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [name, setName] = useState("");
  const [read, setRead] = useState(true);
  const [write, setWrite] = useState(false);
  const [busy, setBusy] = useState(false);
  const [preferences, setPreferences] = useState<Preferences>(() => loadPreferences());

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

  function patchPreferences(patch: Partial<Preferences>): void {
    setPreferences(updatePreferences(patch));
  }

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

  const tokenColumns: Array<DataTableColumn<PatToken>> = [
    { key: "name", header: "名称", priority: 1, render: (row) => row.name },
    { key: "prefix", header: "前缀", priority: 2, render: (row) => <code>{row.prefix}</code> },
    { key: "scopes", header: "权限", priority: 1, render: (row) => row.scopes.join(", ") },
    {
      key: "expires",
      header: "过期",
      priority: 3,
      render: (row) => (row.expires_at ? row.expires_at.slice(0, 10) : "—"),
    },
    {
      key: "actions",
      header: "操作",
      priority: 1,
      align: "right",
      render: (row) => (
        <button className="settings-btn settings-btn-ghost" type="button" onClick={() => void revoke(row)}>
          撤销
        </button>
      ),
    },
  ];

  return (
    <div className="settings">
      <header className="settings-head">
        <div className="settings-eyebrow">偏好 · 令牌 · 本地实例</div>
        <h1 className="settings-title">设置</h1>
        <p className="settings-sub">外观与显示偏好只存本地；PAT 令牌由后端签发，明文只显示一次。</p>
      </header>

      <ProblemView error={error} onRetry={() => void load()} />

      <section className="settings-card">
        <div className="settings-card-head">
          <h2>外观与显示</h2>
          <span className="settings-meta">立即生效 · 存于浏览器</span>
        </div>

        <div className="settings-row">
          <div>
            <div className="settings-label">表格密度</div>
            <div className="settings-hint">影响 DataTable 行高与内边距，不影响字号层级。</div>
          </div>
          <div className="settings-seg" role="group" aria-label="表格密度">
            {DENSITY_OPTIONS.map((option) => (
              <button
                key={option.value}
                type="button"
                className={preferences.density === option.value ? "is-active" : ""}
                aria-pressed={preferences.density === option.value}
                title={option.hint}
                onClick={() => patchPreferences({ density: option.value })}
              >
                {option.label}
              </button>
            ))}
          </div>
        </div>

        <div className="settings-row">
          <div>
            <div className="settings-label">涨跌色</div>
            <div className="settings-hint">
              颜色只作辅助，正负号与箭头仍由后端字符串与文案表达（WCAG 2.2 1.4.1）。
            </div>
          </div>
          <div className="settings-seg" role="group" aria-label="涨跌色">
            {TREND_OPTIONS.map((option) => (
              <button
                key={option.value}
                type="button"
                className={preferences.trend === option.value ? "is-active" : ""}
                aria-pressed={preferences.trend === option.value}
                title={option.hint}
                onClick={() => patchPreferences({ trend: option.value })}
              >
                {option.label}
              </button>
            ))}
          </div>
        </div>

        <div className="settings-row">
          <div>
            <div className="settings-label">主题</div>
            <div className="settings-hint">深色主题使用同名 token 覆盖，切换后立即生效。</div>
          </div>
          <div className="settings-seg" role="group" aria-label="主题">
            {THEME_OPTIONS.map((option) => (
              <button
                key={option.value}
                type="button"
                className={preferences.theme === option.value ? "is-active" : ""}
                aria-pressed={preferences.theme === option.value}
                title={option.hint}
                onClick={() => patchPreferences({ theme: option.value })}
              >
                {option.label}
              </button>
            ))}
          </div>
        </div>
      </section>

      <section className="settings-card">
        <div className="settings-card-head">
          <h2>创建 PAT</h2>
          <span className="settings-meta">read / write 至少选一项</span>
        </div>
        <form onSubmit={(event) => void create(event)}>
          <label className="settings-field">
            名称
            <input value={name} onChange={(e) => setName(e.target.value)} maxLength={64} required />
          </label>
          <div className="settings-checks">
            <label className="check">
              <input type="checkbox" checked={read} onChange={(e) => setRead(e.target.checked)} />
              read（只读）
            </label>
            <label className="check">
              <input type="checkbox" checked={write} onChange={(e) => setWrite(e.target.checked)} />
              write（可写）
            </label>
          </div>
          <button className="settings-btn" type="submit" disabled={busy || (!read && !write) || !name}>
            {busy ? "创建中…" : "创建"}
          </button>
        </form>
      </section>

      {created ? (
        <section className="settings-card settings-token-once">
          <div className="settings-card-head">
            <h2>请立即保存：只显示这一次</h2>
          </div>
          <code className="settings-token-value">{created.token}</code>
          <p className="settings-hint">离开本页后无法再次查看明文；若遗失请撤销后重新创建。</p>
        </section>
      ) : null}

      <section className="settings-card">
        <div className="settings-card-head">
          <h2>已创建</h2>
          <span className="settings-meta">{tokens.length} 个</span>
        </div>
        <DataTable
          caption="PAT 令牌列表"
          columns={tokenColumns}
          rows={tokens}
          rowKey={(row) => row.token_id}
          emptyText="暂无令牌"
        />
      </section>
    </div>
  );
}
