import { useEffect, useState, type ReactElement } from "react";
import { api } from "../client";
import { saveTokens } from "../store/session";
import { useGuideAuth } from "./useGuideAuth";

/** 公开 demo 体验账号（明文内置，仅供访客一键体验；该账号内勿存私密数据）。 */
const DEMO_EMAIL = "xingst01@demo.com";
const DEMO_PASSWORD = "xingst666";

/** 合规声明：全站统一口径，页脚与独立合规区块、FAQ 复用同一段文案。 */
const COMPLIANCE =
  "本系统仅为个人交易复盘工具，所展示的数据、指标与分析均基于您自行导入的历史账单计算，" +
  "不构成任何投资建议、投资咨询、收益承诺或证券买卖依据。历史业绩不代表未来表现，投资有风险，入市需谨慎。";

const FLOW = [
  { icon: "⇪", title: "导入账单", desc: "上传券商对账单，认列认类型", core: true },
  { icon: "⚙", title: "解析与对账", desc: "余额链核对，存疑行进隔离区", core: false },
  { icon: "▤", title: "资产与持仓", desc: "每日市值现金，自动对账平账", core: false },
  { icon: "↻", title: "回合", desc: "一只股票从买到卖的完整盈亏", core: false },
  { icon: "📈", title: "净值 · 指标 · 归因", desc: "收益率曲线 + 18 项行为指标", core: false },
];

const PROCESS = [
  { num: "01", title: "认列", desc: "识别每一列是什么：哪列是日期、哪列是金额。", code: "trade_date" },
  { num: "02", title: "认类型", desc: "每行「摘要」属于买入/卖出/分红/划转。", code: "summary→summary_code" },
  { num: "03", title: "对账", desc: "用「资金余额」把流水串成不断裂的链。", code: "上一行余额 ± 本行金额" },
  { num: "04", title: "落账·隔离", desc: "确认的行进账本，存疑的行进隔离区，绝不猜。", code: "ledger.trade" },
];

const MAPPING: Array<[string, string, string]> = [
  ["日期", "trade_date", "归一为日期类型"],
  ["证券代码", "security_code", "保留前导零（如 000651）"],
  ["摘要", "summary / summary_code", "摘要原文 + 归一编码（sys_buy_stock 等）"],
  ["（推断）", "side", "含「证券买入」→ buy，「卖出」→ sell，否则 unknown"],
  ["成交数量", "quantity_units", "×10000 标度整数（200 股 → 20000）"],
  ["成交均价", "price_units", "×1000000 标度（37.05 → 37050000）"],
  ["发生金额", "amount_units", "带符号净额（买入为负，如 -741515）"],
  ["佣金+印花税+其他费", "commission_units / stamp_tax_other_fee_units", "三费归一，印花税与其他费合并"],
  ["资金余额", "cash_balance_units", "对账锚点"],
];

const FEATURES = [
  { icon: "▤", title: "看板", desc: "总资产、收益、回撤一屏总览，数据就绪自动更新。" },
  { icon: "☰", title: "交易流水", desc: "每笔买卖、分红、划转明细可查，支持期间筛选。" },
  { icon: "↻", title: "回合", desc: "一只股票从买到卖的完整盈亏，含执行价差洞察。" },
  { icon: "📈", title: "净值与指标", desc: "收益率曲线 + 18 项行为指标，看清自己的交易画像。" },
  { icon: "◔", title: "归因", desc: "收益从哪来、风险敞口在哪，板块与选股拆解。" },
  { icon: "▦", title: "资产表", desc: "每日持仓市值与现金对账，异常状态一目了然。" },
];

const STEPS = [
  { title: "导出对账单", desc: "从券商 App 或 PC 端导出交割单 / 对账单，支持 xlsx、xls、csv。" },
  { title: "导入并确认", desc: "上传后系统自动认列认类型，核对预览无误，确认落账。" },
  { title: "查看复盘", desc: "资产、回合、净值自动生成，开始你的投资复盘。" },
];

export const FAQ = [
  {
    q: "支持哪些券商？数据安全吗？",
    a: "目前重点适配华泰等主流券商的对账单格式；文件在本地解析后入库，股东账号等敏感信息以 MD5 加密存储，不存明文。",
  },
  {
    q: "导入错误怎么办？隔离区是什么？",
    a: "系统认不出的摘要、对不上的余额行会进入「隔离区」，不会被猜测入库。你可以在隔离区补规则或修订后重跑，不需要重新上传文件。",
  },
  {
    q: "资金余额对不上会怎样？",
    a: "对账链断裂时，从断行起连续隔离到链尾，已核对的前缀保留，绝不带着错误余额继续入账。导入结果页会提示断在第几行、差多少钱。",
  },
  {
    q: "历史数据要按新口径重算怎么办？",
    a: "先撤销原批次再重新上传同一文件，就会按最新口径重新解析、对账、重建资产与回合。直接重传已导入过的账单不会重复写入。",
  },
  {
    q: "和券商 App 自带的统计有什么不同？",
    a: "券商统计通常只看单只盈亏；这里把所有流水串成一条链，统一口径计算回合、净值、收益率与归因，能看到跨标的、跨周期的真实交易画像。",
  },
  {
    q: "这个系统会给我投资建议吗？",
    a: `不会。${COMPLIANCE}`,
  },
];

type Theme = "light" | "dark";
const THEME_KEY = "sa.guide.theme";

function TopNav({
  authed,
  homeHref,
  theme,
  onToggleTheme,
}: {
  authed: boolean;
  homeHref: string;
  theme: Theme;
  onToggleTheme: () => void;
}) {
  return (
    <header className="guide-topbar">
      <div className="guide-container guide-topbar-inner">
        <a className="guide-brand" href="/guide" aria-label="交易复盘使用引导">
          <span className="guide-brand-mark" aria-hidden="true">静</span>
          <span className="guide-brand-text">
            <strong>交易复盘</strong>
            <small>个人复盘工作台</small>
          </span>
        </a>
        <nav className="guide-nav" aria-label="页面导航">
          <a href="#flow">主链路</a>
          <a href="#import">导入账单</a>
          <a href="#features">功能</a>
          <a href="#faq">常见问题</a>
        </nav>
        <div className="guide-topbar-grow" />
        <button
          type="button"
          className="guide-theme-toggle"
          onClick={onToggleTheme}
          aria-label={theme === "dark" ? "切换到浅色主题" : "切换到深色主题"}
          title={theme === "dark" ? "切换到浅色主题" : "切换到深色主题"}
        >
          {theme === "dark" ? "☀" : "◐"}
        </button>
        {authed ? (
          <a className="guide-btn guide-btn-primary" href={homeHref}>返回工作台</a>
        ) : (
          <>
            <a className="guide-btn guide-btn-ghost" href="/login">登录</a>
            <a className="guide-btn guide-btn-primary" href="/register">注册</a>
          </>
        )}
      </div>
    </header>
  );
}

function Hero({ authed }: { authed: boolean }) {
  return (
    <section className="guide-hero" id="hero">
      <div className="guide-container guide-hero-inner">
        <span className="guide-eyebrow">个人交易复盘工作台</span>
        <h1>把券商对账单，<br />变成看得懂的投资复盘</h1>
        <p className="guide-lead">
          导入一份华泰/中信等券商对账单，自动解析、对账、生成持仓回合与净值指标，让每一笔买卖都有迹可循。
        </p>
        <div className="guide-hero-cta">
          <a className="guide-btn guide-btn-primary" href={authed ? "/import" : "/register"}>
            立即导入账单
          </a>
          <a className="guide-btn guide-btn-ghost" href="#import">查看导入示例</a>
        </div>
        <p className="guide-hero-hint">3 分钟完成首次导入 · 支持银证转账 / 买卖 / 分红 / 配股全口径</p>
      </div>
    </section>
  );
}

function FlowDiagram() {
  return (
    <section id="flow" className="guide-section">
      <div className="guide-container">
        <div className="guide-section-head">
          <div className="guide-section-eyebrow">POST-LOGIN FLOW</div>
          <h2>登录之后，一条链路走完全部复盘</h2>
          <p>从导入一份账单开始，系统自动把原始流水变成资产、回合、净值与指标，你只需要专注复盘决策。</p>
        </div>
        <div className="guide-flow">
          {FLOW.map((node, i) => (
            <div className="guide-flow-item" key={node.title}>
              <div className={`guide-flow-node${node.core ? " is-core" : ""}`}>
                {node.core ? <span className="guide-flow-badge">核心入口</span> : null}
                <div className="guide-flow-ico" aria-hidden="true">{node.icon}</div>
                <h3>{node.title}</h3>
                <p>{node.desc}</p>
              </div>
              {i < FLOW.length - 1 ? (
                <div className={`guide-flow-arrow${node.core ? " is-main" : ""}`} aria-hidden="true">→</div>
              ) : null}
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

function ImportComparison() {
  return (
    <section id="import" className="guide-section">
      <div className="guide-container">
        <div className="guide-section-head">
          <div className="guide-section-eyebrow">CORE ENTRY</div>
          <h2>导入账单：一份对账单的解析之旅</h2>
          <p>不是把 Excel 原样塞进数据库，而是认列 → 认类型 → 对账 → 落账/隔离，每一步都可核对。</p>
        </div>
        <div className="guide-import-block">
          <div className="guide-compare">
            <div className="guide-compare-col">
              <div className="guide-compare-figure">
                <img
                  src="/guide/bill-raw.png"
                  alt="华泰证券对账单原始表格，含日期、币种、证券代码、摘要、成交数量、成交均价、佣金、印花税、其他费、发生金额、资金余额等列"
                  width={1338}
                  height={1106}
                  loading="lazy"
                  decoding="async"
                />
              </div>
              <div className="guide-compare-caption"><strong>你导出的</strong>券商对账单（华泰示例）</div>
            </div>
            <div className="guide-process" aria-hidden="true">
              {["认列", "认类型", "对账", "落账·隔离"].map((label, i) => (
                <div className="guide-process-step" key={label}>
                  <div className="guide-step-pill">{label}</div>
                  {i < 3 ? <div className="guide-step-arrow">↓</div> : null}
                </div>
              ))}
            </div>
            <div className="guide-compare-col">
              <div className="guide-compare-figure">
                <img
                  src="/guide/bill-parsed.png"
                  alt="解析后的结构化账本表格，含 trade_date、security_code、security_name、summary、summary_code、side、quantity_units、price_units、amount_units、cash_balance_units 等字段"
                  width={2350}
                  height={934}
                  loading="lazy"
                  decoding="async"
                />
              </div>
              <div className="guide-compare-caption"><strong>系统得到的</strong>结构化账本（字段归一 / 金额标度 / 方向识别）</div>
            </div>
          </div>

          <div className="guide-process-grid">
            {PROCESS.map((p) => (
              <div className="guide-pcard" key={p.num}>
                <div className="guide-pcard-num">{p.num}</div>
                <h4>{p.title}</h4>
                <p>{p.desc}</p>
                <code>{p.code}</code>
              </div>
            ))}
          </div>

          <div className="guide-mapping">
            <h4>解析结果 · 字段映射一览</h4>
            <table className="guide-mtable">
              <thead>
                <tr><th>原始对账单列</th><th>解析后字段</th><th>说明</th></tr>
              </thead>
              <tbody>
                {MAPPING.map(([col, field, desc]) => (
                  <tr key={field}>
                    <td>{col}</td>
                    <td><code>{field}</code></td>
                    <td>{desc}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </section>
  );
}

function FeatureCards() {
  return (
    <section id="features" className="guide-section">
      <div className="guide-container">
        <div className="guide-section-head">
          <div className="guide-section-eyebrow">FEATURES</div>
          <h2>一套覆盖全周期的复盘工具</h2>
          <p>从流水到净值，从回合格局到收益归因，所有页面读取同一套账本口径，数值处处一致。</p>
        </div>
        <div className="guide-features">
          {FEATURES.map((f) => (
            <div className="guide-fcard" key={f.title}>
              <div className="guide-fcard-shot">
                <span><span className="guide-ph-ico" aria-hidden="true">{f.icon}</span>截图待补 · {f.title}</span>
              </div>
              <div className="guide-fcard-body">
                <h4>{f.title}</h4>
                <p>{f.desc}</p>
              </div>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

function Steps() {
  return (
    <section id="start" className="guide-section">
      <div className="guide-container">
        <div className="guide-section-head">
          <div className="guide-section-eyebrow">GET STARTED</div>
          <h2>三步开始你的第一次复盘</h2>
        </div>
        <div className="guide-steps">
          {STEPS.map((s, i) => (
            <div className="guide-step" key={s.title}>
              <span className="guide-step-index" aria-hidden="true">{i + 1}</span>
              <h4>{s.title}</h4>
              <p>{s.desc}</p>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

function Faq() {
  return (
    <section id="faq" className="guide-section">
      <div className="guide-container">
        <div className="guide-section-head">
          <div className="guide-section-eyebrow">FAQ</div>
          <h2>常见问题</h2>
        </div>
        <div className="guide-faq">
          {FAQ.map((item) => (
            <details key={item.q}>
              <summary>{item.q}</summary>
              <p>{item.a}</p>
            </details>
          ))}
        </div>
      </div>
    </section>
  );
}

function ComplianceNote() {
  return (
    <section id="compliance" className="guide-section" aria-label="合规与风险提示">
      <div className="guide-container">
        <div className="guide-compliance" role="note">
          <h2>合规与风险提示</h2>
          <p>{COMPLIANCE}</p>
          <p className="guide-compliance-sub">
            您在系统中看到的任何收益率、回合盈亏、行为指标与归因结果，均为对历史数据的统计与呈现，
            不代表对未来收益的任何预测或承诺。请基于自身独立判断做出投资决策，并自行承担投资风险。
          </p>
        </div>
      </div>
    </section>
  );
}

function CtaFooter({ authed, homeHref }: { authed: boolean; homeHref: string }) {
  const [demoPending, setDemoPending] = useState(false);
  const [demoError, setDemoError] = useState(false);

  /** 一键 demo 登录：立即发起登录请求，成功后带着令牌进入工作台。 */
  async function demoLogin(): Promise<void> {
    setDemoPending(true);
    setDemoError(false);
    try {
      const result = await api.auth.login({ email: DEMO_EMAIL, password: DEMO_PASSWORD });
      saveTokens(result);
      window.location.assign(homeHref);
    } catch {
      setDemoError(true);
    } finally {
      setDemoPending(false);
    }
  }

  return (
    <>
      <section className="guide-section">
        <div className="guide-container">
          <div className="guide-cta">
            <h2>现在，导入你的第一份对账单</h2>
            <p>免费注册，3 分钟完成首次导入，让每一笔买卖都有迹可循。</p>
            <div className="guide-cta-row">
              {authed ? (
                <a className="guide-btn guide-btn-primary" href={homeHref}>进入工作台</a>
              ) : (
                <>
                  <a className="guide-btn guide-btn-primary" href="/register">免费注册开始</a>
                  <a className="guide-btn guide-btn-ghost" href="/login">已有账号，去登录</a>
                </>
              )}
            </div>
            {authed ? null : (
              <div className="guide-cta-demo">
                <button
                  type="button"
                  className="guide-demo-link"
                  onClick={() => void demoLogin()}
                  disabled={demoPending}
                >
                  {demoPending ? "正在登录…" : "用demo账号体验"}
                </button>
                {demoError ? (
                  <p className="guide-demo-error" role="alert">
                    登录失败，请稍后重试，或注册自己的账号。
                  </p>
                ) : null}
              </div>
            )}
          </div>
        </div>
      </section>
      <footer className="guide-footer">
        <div className="guide-container guide-footer-inner">
          <span>© 2026 交易复盘 · 个人复盘工作台</span>
          <span className="guide-footer-links">
            <a href="#flow">主链路</a> · <a href="#import">导入说明</a> · <a href="#features">功能</a> · <a href="#faq">常见问题</a>
          </span>
        </div>
        <div className="guide-container">
          <p className="guide-footer-compliance">本系统不构成任何投资建议。投资有风险，入市需谨慎。</p>
        </div>
      </footer>
    </>
  );
}

export function GuideApp(): ReactElement {
  const { authed, homeHref } = useGuideAuth();
  const [theme, setTheme] = useState<Theme>("light");

  useEffect(() => {
    let initial: Theme = "light";
    try {
      initial = window.localStorage.getItem(THEME_KEY) === "dark" ? "dark" : "light";
    } catch {
      initial = "light";
    }
    setTheme(initial);
    document.documentElement.setAttribute("data-theme", initial);
  }, []);

  function toggleTheme(): void {
    const next: Theme = theme === "dark" ? "light" : "dark";
    setTheme(next);
    document.documentElement.setAttribute("data-theme", next);
    try {
      window.localStorage.setItem(THEME_KEY, next);
    } catch {
      /* 隐私模式忽略写入失败 */
    }
  }

  return (
    <div className="guide-page">
      <a className="guide-skip" href="#guide-main">跳到主要内容</a>
      <TopNav authed={authed} homeHref={homeHref} theme={theme} onToggleTheme={toggleTheme} />
      <main id="guide-main">
        <Hero authed={authed} />
        <FlowDiagram />
        <ImportComparison />
        <FeatureCards />
        <Steps />
        <Faq />
        <ComplianceNote />
        <CtaFooter authed={authed} homeHref={homeHref} />
      </main>
    </div>
  );
}
