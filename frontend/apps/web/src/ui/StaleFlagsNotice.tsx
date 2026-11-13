import type { ReactElement } from "react";
import { Link } from "react-router-dom";

interface StaleFlagsNoticeProps {
  /** 看板用：后端给出的「按旧阈值生成」月份（`stale_months`）。 */
  months?: string[];
  /** 回合洞察/详情用：后端给出的 `flag_config.stale` 结论。 */
  stale?: boolean;
}

/**
 * 设计 §7.4 · 旗标过期横幅。
 *
 * 只渲染后端结论（`stale_months` / `flag_config.stale`），前端不推断哪些月份过期、
 * 也不比较指纹；无过期时不渲染。动作入口指向设置页的「立即重算」（API-097）。
 */
export function StaleFlagsNotice({ months = [], stale = false }: StaleFlagsNoticeProps): ReactElement | null {
  if (!stale && months.length === 0) return null;
  return (
    <div className="notice notice-warn" role="status" data-testid="flag-stale-notice">
      <strong>旗标已过期</strong>
      <p>
        {months.length > 0
          ? `以下月份按旧阈值计算：${months.join("、")}。`
          : "账单旗标阈值刚更新，当前旗标待重算。"}
        到设置页点击「立即重算」后按当前阈值更新。
      </p>
      <Link className="btn btn-ghost notice-action" to="/settings" data-testid="flag-stale-action">
        去设置页重算
      </Link>
    </div>
  );
}
