import { type CSSProperties, type ReactElement } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { keepPreviousData, useQuery } from '@tanstack/react-query';
import type {
  Attribution,
  AttributionFactor,
  AttributionMissing,
} from '@sa/api-client';
import { api } from '../client';
import { latestCompletedMonth } from '../lib/date';
import { ProblemView } from '../ui/Problem';
import { displayValue } from '../ui/format';
import { SAMPLE_STATUS_LABELS, dataStatusLabel } from '../ui/status';
import './dashboard.css';
import './attribution.css';
import { PeriodInput } from '../ui/PeriodInput';

function StatusBadge({ status }: { status: string }): ReactElement {
  return (
    <span className={`dash-badge is-${status}`} data-testid="attr-status-badge">
      {dataStatusLabel(status)}
    </span>
  );
}

/** 因子行（零口径）：宽度直接用后端 `presentation.bar_pct`，不解析不换算。 */
function FactorRow({ factor }: { factor: AttributionFactor }): ReactElement {
  const barPct = (factor.presentation.bar_pct ?? '0%').replace(/^[+−-]/, '');
  return (
    <li className="attr-factor" data-testid={`attr-factor-${factor.code}`}>
      <span className="attr-rank" aria-label={`排名 ${factor.rank}`}>
        {factor.rank}
      </span>
      <div className="attr-factor-main">
        <div className="attr-factor-head">
          <strong>{factor.name}</strong>
          <span className="attr-factor-amount num">
            {displayValue(factor.amount)}
          </span>
          <span className="muted num">{displayValue(factor.ratio)}</span>
          <span className="muted num">占构成 {displayValue(factor.share)}</span>
        </div>
        <div className="attr-bar" aria-hidden="true">
          <i
            data-tone={factor.presentation.tone}
            style={{ '--w': barPct } as CSSProperties}
          />
        </div>
        <p className="attr-factor-conclusion">
          {displayValue(factor.conclusion)}
        </p>
      </div>
    </li>
  );
}

function MissingList({
  missing,
}: {
  missing: AttributionMissing[];
}): ReactElement {
  return (
    <ul className="attr-missing" data-testid="attr-missing">
      {missing.map((item, index) => (
        <li
          key={`${item.code}-${item.field}-${index}`}
          data-testid={`attr-missing-${item.code}`}
        >
          <strong>{item.reason}</strong>
          <span className="muted">{item.field}</span>
          <span className="attr-what-to-do">{item.what_to_do}</span>
        </li>
      ))}
    </ul>
  );
}

/** 摘要卡：summary 与五因子固定顺序（后端 rank 决定展示顺序，前端不重排）。 */
function SummaryCard({ data }: { data: Attribution }): ReactElement {
  return (
    <section className="dash-panel attr-panel" data-testid="attr-summary">
      <div className="attr-panel-head">
        <h2>本期结论</h2>
        <span className="muted num">
          总损益 {displayValue(data.total_pnl)} · 基准 {data.ratio_basis}
        </span>
      </div>
      {/* 结论整句只渲染后端 summary（含样本不足/空账户固定句，随 Accept-Language） */}
      {data.summary != null ? (
        <p className="attr-summary" data-testid="attr-summary-text">
          {data.summary}
        </p>
      ) : (
        <div className="attr-summary-na" data-testid="attr-summary-na">
          <p className="attr-summary">本期暂不生成结论</p>
          <p className="muted">
            {data.summary_na_reason ??
              '样本或数据覆盖不足，原因见下方「数据质量」。'}
          </p>
        </div>
      )}
      <ul className="attr-factors">
        {(data.factors ?? []).map((factor) => (
          <FactorRow key={factor.code} factor={factor} />
        ))}
      </ul>
    </section>
  );
}

function ResidualCard({ data }: { data: Attribution }): ReactElement {
  const buckets = Object.entries(data.residual_breakdown ?? {});
  const unattributed = (data.coverage?.unattributed ?? []).slice(0, 5);
  return (
    <section className="dash-panel attr-panel" data-testid="attr-residual">
      <div className="attr-panel-head">
        <h2>残差</h2>
        <span className="muted num">
          残差 {displayValue(data.residual)} · 残差比{' '}
          {displayValue(data.residual_ratio)}
        </span>
      </div>
      <div className="dash-kpi-grid attr-kpis">
        <div className="dash-kpi">
          <span className="dash-kpi-label">资金覆盖率</span>
          <strong className="num" data-testid="attr-capital-ratio">
            {displayValue(data.coverage?.capital_ratio)}
          </strong>
          <span className="dash-kpi-hint">
            {data.coverage
              ? `已归因 ${data.coverage.segments_attributed}/${data.coverage.segments_total} 段`
              : '—'}
          </span>
        </div>
        <div className="dash-kpi">
          <span className="dash-kpi-label">样本</span>
          <strong className="num">{data.sample.n_attributed_segments}</strong>
          <span className="dash-kpi-hint">
            门槛 {data.sample.min_n} 段 ·{' '}
            {SAMPLE_STATUS_LABELS[data.sample.sample_status]}
          </span>
        </div>
      </div>
      <ul className="attr-buckets" data-testid="attr-residual-buckets">
        {buckets.map(([key, value]) => (
          <li key={key}>
            <span className="muted">{key}</span>
            <span className="num">{displayValue(value)}</span>
          </li>
        ))}
      </ul>
      {unattributed.length > 0 ? (
        <ul className="attr-unattributed" data-testid="attr-unattributed">
          {unattributed.map((item) => (
            <li key={item.security_code}>
              <strong>{item.security_code}</strong>
              <span className="muted">{item.symbol_class}</span>
              <span className="muted">{item.reason_code ?? ''}</span>
              <span className="num">{displayValue(item.amount)}</span>
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}

function QualityCard({ data }: { data: Attribution }): ReactElement {
  return (
    <section className="dash-panel attr-panel" data-testid="attr-quality">
      <div className="attr-panel-head">
        <h2>数据质量</h2>
        <span className="muted">
          数据截至 {data.data_as_of ?? '—'} · 口径 {data.definition_version}
        </span>
      </div>
      {(data.missing ?? []).length > 0 ? (
        <MissingList missing={data.missing ?? []} />
      ) : (
        <p className="muted">本期无降级项。</p>
      )}
      <nav className="attr-links" aria-label="相关入口">
        <Link to={data.links.rounds}>回合</Link>
        <Link to={data.links.sectors}>板块</Link>
        <Link to={data.links.dashboard}>看板</Link>
        <Link to={data.links.reports}>月报</Link>
      </nav>
    </section>
  );
}

export function AttributionPage(): ReactElement {
  const [params, setParams] = useSearchParams();
  const period = params.get('period') ?? latestCompletedMonth();

  const query = useQuery({
    queryKey: ['attribution', period],
    queryFn: () => api.attribution.get({ period }),
    placeholderData: keepPreviousData,
  });

  const updatePeriod = (next: string): void => {
    const draft = new URLSearchParams(params);
    if (next) draft.set('period', next);
    else draft.delete('period');
    setParams(draft, { replace: true });
  };

  if (query.error != null) {
    return (
      <div className="attr-page" data-testid="attribution-page">
        <h1 className="dash-title">收益归因</h1>
        <ProblemView error={query.error} onRetry={() => void query.refetch()} />
      </div>
    );
  }

  const data = query.data;
  const blocked = data?.status === 'blocked';

  return (
    <div className="attr-page" data-testid="attribution-page">
      <header className="dash-head">
        <div className="dash-eyebrow">归因口径 · 五因子</div>
        <h1 className="dash-title">收益归因</h1>
        <p className="dash-sub">
          {data
            ? `${data.period_start} ~ ${data.period_end} · 模型 ${data.model}`
            : '选择期间查看五因子收益拆解；所有数值均为后端口径，本页只做呈现。'}
        </p>
        <div className="dash-head-meta">
          <PeriodInput
            value={period}
            disabled={query.isLoading}
            onChange={updatePeriod}
          />
          <span>
            页面状态 <StatusBadge status={data?.status ?? 'missing'} />
          </span>
          <span>数据截至 {data?.data_as_of ?? '—'}</span>
          {data ? <span>总损益口径 {data.total_pnl_basis}</span> : null}
        </div>
      </header>

      {data == null ? (
        <section className="dash-panel attr-panel">
          <p className="muted">加载中…</p>
        </section>
      ) : blocked ? (
        // blocked：不渲染任何金额（§8.1-④），只渲染数据质量卡。
        <QualityCard data={data} />
      ) : (
        <>
          <SummaryCard data={data} />
          <ResidualCard data={data} />
          <QualityCard data={data} />
        </>
      )}
    </div>
  );
}
