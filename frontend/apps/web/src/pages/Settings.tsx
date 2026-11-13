import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import type {
  CreatedPatToken,
  PatToken,
  SectorCatalogItem,
  SettingsOut,
  SettingsPatchIn,
} from "@sa/api-client";
import { api } from "../client";
import { fetchSettings, saveSettings } from "../data/settings";
import { isAuthenticated } from "../store/session";
import { DataTable, type DataTableColumn } from "../ui/DataTable";
import { ProblemView, toProblem } from "../ui/Problem";
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
  { value: "system", label: "跟随系统", hint: "跟随操作系统浅色/深色偏好" },
];

const TREND_OPTIONS: Array<{ value: TrendColor; label: string; hint: string }> = [
  { value: "red-up", label: "红涨绿跌", hint: "A 股惯例（默认）" },
  { value: "green-up", label: "绿涨红跌", hint: "国际惯例 / 个人习惯" },
];

/** 账单旗标阈值：表单值全部是后端给的字符串，前端不解析成数字。 */
const BILL_FIELDS = [
  {
    form: "near",
    api: "near_equal_tolerance_pct",
    label: "近似等价容差",
    hint: "状态桶边界：价差在此范围内视为「近似等价」，决定加仓/减仓是否判为等价。",
  },
  {
    form: "chase",
    api: "chase_margin_pct",
    label: "追高阈值",
    hint: "买入价高于取窗内最低价超过该幅度时判为「追高」。",
  },
  {
    form: "stop",
    api: "stop_loss_margin_pct",
    label: "止损偏离",
    hint: "卖出价低于持仓均价且破位超过该幅度判为「止损」；破位但未低于均价归「回撤卖出」。",
  },
  {
    form: "short",
    api: "short_term_margin_pct",
    label: "短期阈值",
    hint: "即时/短期抄底、被套、逃顶、卖飞共用的幅度门槛。",
  },
] as const;

/** 后端只接受 `x.xx%`；仅做形状提示，范围仍由后端校验。 */
const BILL_PATTERN = /^\d{1,2}\.\d{2}%$/;

const WINDOW_OPTIONS = ["1", "2", "3", "5", "10", "20"];

const CONCEPT_MODES: Array<{ value: "off" | "hide" | "exclude"; label: string; hint: string }> = [
  { value: "off", label: "关闭", hint: "概念板块按全量参与统计（默认）。" },
  { value: "hide", label: "仅隐藏", hint: "从概念页与图表隐藏被排除的概念，汇总口径不变。" },
  { value: "exclude", label: "不参与统计", hint: "被排除概念不计入覆盖与集中度，并保留未分类与提示文案。" },
];

interface BillForm {
  near: string;
  chase: string;
  stop: string;
  short: string;
  window: string;
}

interface SessionRow {
  open: string;
  close: string;
}

interface SessionsForm {
  rows: SessionRow[];
  dataReadyAfter: string;
  noticeEnabled: boolean;
}

interface ConceptEntry {
  code: string;
  name: string;
  resolved: boolean;
}

interface ConceptForm {
  mode: "off" | "hide" | "exclude";
  excluded: ConceptEntry[];
}

function billFormOf(settings: SettingsOut): BillForm {
  return {
    near: settings.bill_flags.near_equal_tolerance_pct,
    chase: settings.bill_flags.chase_margin_pct,
    stop: settings.bill_flags.stop_loss_margin_pct,
    short: settings.bill_flags.short_term_margin_pct,
    window: settings.bill_flags.window_trading_days,
  };
}

function sessionsFormOf(settings: SettingsOut): SessionsForm {
  return {
    rows: settings.market_sessions.sessions.map((item) => ({ open: item.open, close: item.close })),
    dataReadyAfter: settings.market_sessions.data_ready_after,
    noticeEnabled: settings.market_sessions.session_notice_enabled,
  };
}

function conceptFormOf(settings: SettingsOut): ConceptForm {
  return {
    mode: settings.concept_filters.mode,
    excluded: (settings.concept_filters.excluded ?? []).map((item) => ({
      code: item.code,
      name: item.name,
      resolved: item.resolved,
    })),
  };
}

function sameCodes(left: ConceptEntry[], right: ConceptEntry[]): boolean {
  if (left.length !== right.length) return false;
  const a = left.map((item) => item.code).sort();
  const b = right.map((item) => item.code).sort();
  return a.every((code, index) => code === b[index]);
}

/** 时段的前端预校验（后端仍会再校验一次）。 */
function validateSessions(form: SessionsForm): string | null {
  if (form.rows.length < 1 || form.rows.length > 2) return "交易时段需为 1~2 段";
  let previousClose = "";
  for (const row of form.rows) {
    if (!row.open || !row.close) return "请填写完整的开盘/收盘时间";
    if (row.open >= row.close) return "开盘时间必须早于收盘时间";
    if (previousClose && row.open < previousClose) return "时段需按时间升序且不能重叠";
    previousClose = row.close;
  }
  if (previousClose && form.dataReadyAfter < previousClose) {
    return "盘后就绪时间不能早于收盘时间";
  }
  return null;
}

export function Settings() {
  const [tokens, setTokens] = useState<PatToken[]>([]);
  const [created, setCreated] = useState<CreatedPatToken | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [name, setName] = useState("");
  const [read, setRead] = useState(true);
  const [write, setWrite] = useState(false);
  const [busy, setBusy] = useState(false);
  const [preferences, setPreferences] = useState<Preferences>(() => loadPreferences());

  const [settings, setSettings] = useState<SettingsOut | null>(null);
  const [settingsLoading, setSettingsLoading] = useState<boolean>(() => isAuthenticated());
  const [settingsError, setSettingsError] = useState<unknown>(null);
  const [saveError, setSaveError] = useState<unknown>(null);
  const [saving, setSaving] = useState<string | null>(null);

  const [bill, setBill] = useState<BillForm | null>(null);
  const [sessions, setSessions] = useState<SessionsForm | null>(null);
  const [concept, setConcept] = useState<ConceptForm | null>(null);

  const [catalog, setCatalog] = useState<SectorCatalogItem[]>([]);
  const [catalogCursor, setCatalogCursor] = useState<string | null>(null);
  const [catalogLoading, setCatalogLoading] = useState(false);
  const [catalogError, setCatalogError] = useState<unknown>(null);
  const [conceptQuery, setConceptQuery] = useState("");

  const load = useCallback(async () => {
    setError(null);
    try {
      const list = await api.tokens.list();
      setTokens(list.items);
    } catch (err) {
      setError(err);
    }
  }, []);

  const hydrate = useCallback((next: SettingsOut) => {
    setSettings(next);
    setBill(billFormOf(next));
    setSessions(sessionsFormOf(next));
    setConcept(conceptFormOf(next));
  }, []);

  const loadSettings = useCallback(async () => {
    if (!isAuthenticated()) {
      setSettingsLoading(false);
      return;
    }
    setSettingsLoading(true);
    setSettingsError(null);
    try {
      const next = await fetchSettings(true);
      if (!next) return;
      hydrate(next);
      // 服务端已保存过外观 → 覆盖本地（无行时由 AppShell 用本地值播种）。
      if (next.updated_at !== null) {
        setPreferences(updatePreferences(next.appearance));
      }
    } catch (err) {
      setSettingsError(err);
    } finally {
      setSettingsLoading(false);
    }
  }, [hydrate]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    void loadSettings();
  }, [loadSettings]);

  const loadCatalog = useCallback(async (cursor?: string) => {
    setCatalogLoading(true);
    try {
      const page = await api.sectors.list({
        taxonomy: "concept",
        active_only: true,
        limit: 100,
        cursor,
      });
      setCatalog((prev) => (cursor ? [...prev, ...page.items] : page.items));
      setCatalogCursor(page.has_more ? (page.next_cursor ?? null) : null);
      setCatalogError(null);
    } catch (err) {
      setCatalogError(err);
    } finally {
      setCatalogLoading(false);
    }
  }, []);

  useEffect(() => {
    if (settings && catalog.length === 0 && !catalogLoading && !catalogError) {
      void loadCatalog();
    }
  }, [settings, catalog.length, catalogLoading, catalogError, loadCatalog]);

  function patchLocalPreferences(patch: Partial<Preferences>): void {
    const next = updatePreferences(patch);
    setPreferences(next);
    if (!settings) return;
    const appearance: NonNullable<SettingsPatchIn["appearance"]> = {};
    if (patch.theme !== undefined) appearance.theme = patch.theme;
    if (patch.density !== undefined) appearance.density = patch.density;
    if (patch.trend !== undefined) appearance.trend = patch.trend;
    if (Object.keys(appearance).length === 0) return;
    // 本地已生效；同步失败不回滚，也不阻断当前操作。
    void saveSettings({ appearance })
      .then((saved) => {
        if (saved) setSettings(saved);
      })
      .catch(() => undefined);
  }

  async function submitGroup<T extends SettingsPatchIn>(
    group: string,
    patch: T | null,
  ): Promise<SettingsOut | null> {
    if (!patch) return null;
    setSaving(group);
    setSaveError(null);
    try {
      const next = await saveSettings(patch);
      if (next) {
        setSettings(next);
        if (group === "bill") setBill(billFormOf(next));
        if (group === "sessions") setSessions(sessionsFormOf(next));
        if (group === "concept") setConcept(conceptFormOf(next));
      }
      return next;
    } catch (err) {
      setSaveError(err);
      return null;
    } finally {
      setSaving(null);
    }
  }

  function billPatchOf(form: BillForm, base: BillForm): NonNullable<SettingsPatchIn["bill_flags"]> {
    const patch: NonNullable<SettingsPatchIn["bill_flags"]> = {};
    for (const field of BILL_FIELDS) {
      if (form[field.form] !== base[field.form]) {
        patch[field.api] = form[field.form];
      }
    }
    if (form.window !== base.window) patch.window_trading_days = form.window;
    return patch;
  }

  async function saveBill(): Promise<void> {
    if (!settings || !bill) return;
    const patch = billPatchOf(bill, billFormOf(settings));
    if (Object.keys(patch).length === 0) return;
    await submitGroup("bill", { bill_flags: patch });
  }

  async function resetBill(): Promise<void> {
    // 用 `null` 让后端恢复默认值，前端不重复维护默认口径。
    await submitGroup("bill", {
      bill_flags: {
        near_equal_tolerance_pct: null,
        chase_margin_pct: null,
        stop_loss_margin_pct: null,
        short_term_margin_pct: null,
        window_trading_days: null,
      },
    });
  }

  async function saveSessions(): Promise<void> {
    if (!settings || !sessions) return;
    const base = sessionsFormOf(settings);
    const patch: NonNullable<SettingsPatchIn["market_sessions"]> = {};
    const rowsChanged =
      sessions.rows.length !== base.rows.length ||
      sessions.rows.some(
        (row, index) => row.open !== base.rows[index]?.open || row.close !== base.rows[index]?.close,
      );
    if (rowsChanged) patch.sessions = sessions.rows;
    if (sessions.dataReadyAfter !== base.dataReadyAfter) {
      patch.data_ready_after = sessions.dataReadyAfter;
    }
    if (sessions.noticeEnabled !== base.noticeEnabled) {
      patch.session_notice_enabled = sessions.noticeEnabled;
    }
    if (Object.keys(patch).length === 0) return;
    await submitGroup("sessions", { market_sessions: patch });
  }

  async function saveConcept(): Promise<void> {
    if (!settings || !concept) return;
    const base = conceptFormOf(settings);
    const patch: NonNullable<SettingsPatchIn["concept_filters"]> = {};
    if (concept.mode !== base.mode) patch.mode = concept.mode;
    if (!sameCodes(concept.excluded, base.excluded)) {
      // 数组字段发全量（不做增量 diff）。
      patch.excluded = concept.excluded.map((item) => ({ code: item.code, name: item.name }));
    }
    if (Object.keys(patch).length === 0) return;
    await submitGroup("concept", { concept_filters: patch });
  }

  function toggleConcept(item: SectorCatalogItem): void {
    if (!concept) return;
    const exists = concept.excluded.some((entry) => entry.code === item.sector_code);
    setConcept({
      ...concept,
      excluded: exists
        ? concept.excluded.filter((entry) => entry.code !== item.sector_code)
        : [...concept.excluded, { code: item.sector_code, name: item.sector_name, resolved: true }],
    });
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
        <button type="button" className="settings-btn settings-btn-ghost" onClick={() => void revoke(row)}>
          撤销
        </button>
      ),
    },
  ];

  const billInvalid = bill
    ? BILL_FIELDS.some((field) => !BILL_PATTERN.test(bill[field.form]))
    : false;
  const billDirty = Boolean(bill && settings && Object.keys(billPatchOf(bill, billFormOf(settings))).length > 0);
  const windowOptions = bill && !WINDOW_OPTIONS.includes(bill.window)
    ? [bill.window, ...WINDOW_OPTIONS]
    : WINDOW_OPTIONS;
  const sessionsValidation = sessions ? validateSessions(sessions) : null;
  const sessionsDirty = Boolean(
    sessions &&
      settings &&
      (sessionsValidation === null
        ? JSON.stringify(sessions) !== JSON.stringify(sessionsFormOf(settings))
        : false),
  );
  const conceptDirty = Boolean(
    concept &&
      settings &&
      (concept.mode !== conceptFormOf(settings).mode ||
        !sameCodes(concept.excluded, conceptFormOf(settings).excluded)),
  );

  const catalogOptions = useMemo(() => {
    const query = conceptQuery.trim().toLowerCase();
    if (!query) return catalog;
    return catalog.filter(
      (item) =>
        item.sector_name.toLowerCase().includes(query) ||
        item.sector_code.toLowerCase().includes(query),
    );
  }, [catalog, conceptQuery]);

  const selectedCodes = useMemo(
    () => new Set((concept?.excluded ?? []).map((item) => item.code)),
    [concept],
  );

  const saveErrorMessage = saveError ? toProblem(saveError).user_message : null;

  return (
    <div className="settings">
      <header className="settings-head">
        <div className="settings-eyebrow">Preferences</div>
        <h1 className="settings-title">设置</h1>
        <p className="settings-sub">外观、账单旗标阈值、行情时段与概念过滤；偏好同步到当前账号。</p>
      </header>

      <ProblemView error={error} onRetry={() => void load()} />

      <section className="settings-card">
        <div className="settings-card-head">
          <h2>外观与显示</h2>
          <span className="settings-meta">
            {isAuthenticated() ? "立即生效 · 已同步到云端" : "立即生效 · 存于浏览器"}
          </span>
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
                onClick={() => patchLocalPreferences({ density: option.value })}
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
                onClick={() => patchLocalPreferences({ trend: option.value })}
              >
                {option.label}
              </button>
            ))}
          </div>
        </div>

        <div className="settings-row">
          <div>
            <div className="settings-label">主题</div>
            <div className="settings-hint">深色主题使用同名 token 覆盖；跟随系统时由系统偏好解析，切换后立即生效。</div>
          </div>
          <div className="settings-seg" role="group" aria-label="主题">
            {THEME_OPTIONS.map((option) => (
              <button
                key={option.value}
                type="button"
                className={preferences.theme === option.value ? "is-active" : ""}
                aria-pressed={preferences.theme === option.value}
                title={option.hint}
                onClick={() => patchLocalPreferences({ theme: option.value })}
              >
                {option.label}
              </button>
            ))}
          </div>
        </div>
      </section>

      {settingsError ? <ProblemView error={settingsError} onRetry={() => void loadSettings()} /> : null}
      {settingsLoading && !settings ? (
        <section className="settings-card">
          <p className="settings-hint">正在加载云端配置…</p>
        </section>
      ) : null}

      {saveErrorMessage ? (
        <p className="settings-error" role="alert">
          保存失败：{saveErrorMessage}
        </p>
      ) : null}

      {settings && bill ? (
        <section className="settings-card">
          <div className="settings-card-head">
            <h2>账单旗标阈值</h2>
            <span className="settings-meta">
              {settings.bill_flags.is_default
                ? "当前为默认阈值"
                : `配置指纹 ${settings.bill_flags.flags_hash}`}
            </span>
          </div>

          {!settings.bill_flags.is_default ? (
            <div className="settings-notice" role="status">
              <strong>需重建账单旗标</strong>
              <p className="settings-hint">
                阈值或窗口与默认值不同：看板与回合洞察中已有旗标仍按旧配置生成，重建账单旗标后才会按新配置更新。
              </p>
            </div>
          ) : null}

          <div className="settings-form-grid">
            {BILL_FIELDS.map((field) => (
              <div className="settings-field-group" key={field.form}>
                <label className="settings-field">
                  {field.label}
                  <input
                    value={bill[field.form]}
                    inputMode="decimal"
                    aria-invalid={!BILL_PATTERN.test(bill[field.form])}
                    onChange={(event) => setBill({ ...bill, [field.form]: event.target.value })}
                  />
                </label>
                <span className="settings-hint">{field.hint}</span>
              </div>
            ))}
            <div className="settings-field-group">
              <label className="settings-field">
                取窗交易日数
                <select
                  value={bill.window}
                  onChange={(event) => setBill({ ...bill, window: event.target.value })}
                >
                  {windowOptions.map((option) => (
                    <option key={option} value={option}>
                      {option}
                    </option>
                  ))}
                </select>
              </label>
              <span className="settings-hint">价格类旗标取前后各 N 个行情日（不含当日）。</span>
            </div>
          </div>

          {billInvalid ? (
            <p className="settings-hint">阈值格式应为「1.00%」（两位小数）；具体范围由后端校验。</p>
          ) : null}
          <div className="settings-actions">
            <button
              type="button"
              className="settings-btn"
              disabled={!billDirty || billInvalid || saving !== null}
              onClick={() => void saveBill()}
            >
              {saving === "bill" ? "保存中…" : "保存阈值"}
            </button>
            <button
              type="button"
              className="settings-btn settings-btn-ghost"
              disabled={saving !== null}
              onClick={() => void resetBill()}
            >
              恢复默认
            </button>
          </div>
        </section>
      ) : null}

      {settings && sessions ? (
        <section className="settings-card">
          <div className="settings-card-head">
            <h2>行情时段</h2>
            <span className="settings-meta">{settings.market_sessions.timezone}</span>
          </div>
          <p className="settings-hint">
            仅影响盘前/盘后提示文案，不改变系统收盘口径与资产估值边界。
          </p>

          <div className="settings-stack">
            {sessions.rows.map((row, index) => (
              <div className="settings-session-row" key={index}>
                <label className="settings-field">
                  第 {index + 1} 段开盘
                  <input
                    type="time"
                    value={row.open}
                    onChange={(event) =>
                      setSessions({
                        ...sessions,
                        rows: sessions.rows.map((item, i) =>
                          i === index ? { ...item, open: event.target.value } : item,
                        ),
                      })
                    }
                  />
                </label>
                <label className="settings-field">
                  收盘
                  <input
                    type="time"
                    value={row.close}
                    onChange={(event) =>
                      setSessions({
                        ...sessions,
                        rows: sessions.rows.map((item, i) =>
                          i === index ? { ...item, close: event.target.value } : item,
                        ),
                      })
                    }
                  />
                </label>
                <button
                  type="button"
                  className="settings-btn settings-btn-ghost"
                  disabled={sessions.rows.length <= 1}
                  onClick={() =>
                    setSessions({
                      ...sessions,
                      rows: sessions.rows.filter((_, i) => i !== index),
                    })
                  }
                >
                  删除
                </button>
              </div>
            ))}
          </div>

          <div className="settings-actions">
            <button
              type="button"
              className="settings-btn settings-btn-ghost"
              disabled={sessions.rows.length >= 2}
              onClick={() =>
                setSessions({ ...sessions, rows: [...sessions.rows, { open: "", close: "" }] })
              }
            >
              增加时段
            </button>
          </div>

          <div className="settings-field-group">
            <label className="settings-field">
              盘后就绪时间
              <input
                type="time"
                value={sessions.dataReadyAfter}
                onChange={(event) =>
                  setSessions({ ...sessions, dataReadyAfter: event.target.value })
                }
              />
            </label>
            <span className="settings-hint">不早于最后一段收盘时间。</span>
          </div>

          <label className="check settings-check">
            <input
              type="checkbox"
              checked={sessions.noticeEnabled}
              onChange={(event) =>
                setSessions({ ...sessions, noticeEnabled: event.target.checked })
              }
            />
            显示行情时段提示
          </label>

          {sessionsValidation ? <p className="settings-hint">{sessionsValidation}</p> : null}
          <div className="settings-actions">
            <button
              type="button"
              className="settings-btn"
              disabled={!sessionsDirty || saving !== null}
              onClick={() => void saveSessions()}
            >
              {saving === "sessions" ? "保存中…" : "保存时段"}
            </button>
            <button
              type="button"
              className="settings-btn settings-btn-ghost"
              disabled={saving !== null}
              onClick={() => void submitGroup("sessions", { market_sessions: null })}
            >
              恢复默认
            </button>
          </div>
        </section>
      ) : null}

      {settings && concept ? (
        <section className="settings-card">
          <div className="settings-card-head">
            <h2>概念板块过滤</h2>
            <span className="settings-meta">仅影响概念轴，行业轴不变</span>
          </div>

          <div className="settings-field-group">
            <label className="settings-field">
              模式
              <select
                value={concept.mode}
                onChange={(event) =>
                  setConcept({ ...concept, mode: event.target.value as ConceptForm["mode"] })
                }
              >
                {CONCEPT_MODES.map((option) => (
                  <option key={option.value} value={option.value} title={option.hint}>
                    {option.label}
                  </option>
                ))}
              </select>
            </label>
            <span className="settings-hint">
              {CONCEPT_MODES.find((option) => option.value === concept.mode)?.hint}
            </span>
          </div>

          <div className="settings-label">已排除概念（{concept.excluded.length}）</div>
          {concept.excluded.length === 0 ? (
            <p className="settings-hint">尚未排除任何概念。</p>
          ) : (
            <ul className="settings-chips">
              {concept.excluded.map((entry) => (
                <li key={entry.code} className={entry.resolved ? "" : "is-unresolved"}>
                  <span>{entry.name}</span>
                  <code>{entry.code}</code>
                  {entry.resolved ? null : <em>目录中未找到</em>}
                  <button
                    type="button"
                    aria-label={`移除 ${entry.name}`}
                    onClick={() =>
                      setConcept({
                        ...concept,
                        excluded: concept.excluded.filter((item) => item.code !== entry.code),
                      })
                    }
                  >
                    ×
                  </button>
                </li>
              ))}
            </ul>
          )}

          <div className="settings-row settings-catalog">
            <div>
              <div className="settings-label">概念目录</div>
              <div className="settings-hint">
                默认只列启用中的概念（API-088）。目录未同步时为空，可稍后在概念页同步。
              </div>
            </div>
            <input
              className="settings-search"
              type="search"
              placeholder="按名称或代码筛选"
              value={conceptQuery}
              onChange={(event) => setConceptQuery(event.target.value)}
            />
          </div>

          <ProblemView error={catalogError} onRetry={() => void loadCatalog()} />

          {catalogOptions.length === 0 && !catalogLoading ? (
            <p className="settings-hint">没有可添加的概念。</p>
          ) : (
            <div className="settings-catalog-list">
              {catalogOptions.map((item) => (
                <label className="check" key={item.sector_code}>
                  <input
                    type="checkbox"
                    checked={selectedCodes.has(item.sector_code)}
                    onChange={() => toggleConcept(item)}
                  />
                  {item.sector_name}
                  <code>{item.sector_code}</code>
                </label>
              ))}
            </div>
          )}

          {catalogCursor ? (
            <div className="settings-actions">
              <button
                type="button"
                className="settings-btn settings-btn-ghost"
                disabled={catalogLoading}
                onClick={() => void loadCatalog(catalogCursor)}
              >
                {catalogLoading ? "加载中…" : "加载更多"}
              </button>
            </div>
          ) : null}

          <div className="settings-actions">
            <button
              type="button"
              className="settings-btn"
              disabled={!conceptDirty || saving !== null}
              onClick={() => void saveConcept()}
            >
              {saving === "concept" ? "保存中…" : "保存过滤"}
            </button>
            <button
              type="button"
              className="settings-btn settings-btn-ghost"
              disabled={saving !== null}
              onClick={() => void submitGroup("concept", { concept_filters: null })}
            >
              恢复默认
            </button>
          </div>
        </section>
      ) : null}

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
