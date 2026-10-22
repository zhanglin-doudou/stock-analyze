import { isApiProblemError, isProblem, type Problem } from "@sa/api-client";

/** 把任意异常归一化为 RFC 7807 形状，供 UI 统一渲染。 */
export function toProblem(error: unknown): Problem {
  if (isApiProblemError(error)) return error.problem;
  if (isProblem(error)) return error;
  if (error instanceof Error) {
    return {
      type: "about:blank",
      title: error.name || "Error",
      status: 0,
      detail: error.message,
      code: "UNEXPECTED_ERROR",
      user_message: "发生了未预期的错误。",
      what_to_do: "请重试；若持续出现，请携带发生时间联系支持。",
      retryable: true,
      request_id: "",
    };
  }
  return {
    type: "about:blank",
    title: "Unknown error",
    status: 0,
    detail: "",
    code: "UNEXPECTED_ERROR",
    user_message: "发生了未预期的错误。",
    what_to_do: "请重试。",
    retryable: true,
    request_id: "",
  };
}

export interface ProblemViewProps {
  error: unknown;
  onRetry?: () => void;
}

/** 把 7807 渲染成“人话 + 怎么办 + request_id”。 */
export function ProblemView({ error, onRetry }: ProblemViewProps) {
  if (error === null || error === undefined) return null;
  const problem = toProblem(error);
  return (
    <div className="problem" role="alert">
      <p className="problem-message">{problem.user_message}</p>
      <p className="problem-action">
        <span className="problem-label">怎么办：</span>
        {problem.what_to_do}
      </p>
      <p className="problem-meta">
        <span className="problem-code">{problem.code}</span>
        {problem.retryable ? <span className="problem-retryable">可重试</span> : <span className="problem-retryable">不可重试</span>}
      </p>
      {problem.errors && problem.errors.length > 0 ? (
        <ul className="problem-fields">
          {problem.errors.map((field, index) => (
            <li key={`${field.field}-${index}`}>
              <code>{field.field}</code>：{field.message}
            </li>
          ))}
        </ul>
      ) : null}
      {problem.request_id ? <p className="problem-request">request_id: {problem.request_id}</p> : null}
      {onRetry && problem.retryable ? (
        <button type="button" className="btn" onClick={onRetry}>
          重试
        </button>
      ) : null}
    </div>
  );
}
