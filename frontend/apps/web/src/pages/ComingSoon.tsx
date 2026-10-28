import { type ReactElement } from "react";
import { Link } from "react-router-dom";

export interface FeaturePlaceholderProps {
  title: string;
  dependency: string;
  description: string;
  backTo?: string;
}

/**
 * 未完成功能的前端入口占位：可发现、可测试、**不渲染任何假数字/假按钮成功**。
 * 依赖任务写进 `data-dependency`，验收脚本可断言入口存在且没有伪造数据。
 */
export function FeaturePlaceholder({
  title,
  dependency,
  description,
  backTo = "/dashboard",
}: FeaturePlaceholderProps): ReactElement {
  return (
    <div
      className="card"
      data-testid="feature-placeholder"
      data-dependency={dependency}
      data-feature={title}
    >
      <h1>{title}</h1>
      <p>{description}</p>
      <p className="muted">
        依赖任务：<code>{dependency}</code>（尚未完成）
      </p>
      <p className="notice notice-warn">
        本页只是入口预留：没有真实数据、没有可用的下载/构建按钮，不会触发任何 job。
      </p>
      <Link to={backTo}>返回看板</Link>
    </div>
  );
}
