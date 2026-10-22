import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { ApiProblemError, type Problem } from "@sa/api-client";
import { ProblemView, toProblem } from "./Problem";

afterEach(cleanup);

function versionConflict(): Problem {
  return {
    type: "https://api.example.com/problems/v1/version-conflict",
    title: "Version conflict",
    status: 409,
    detail: "版本冲突",
    code: "VERSION_CONFLICT",
    user_message: "这条数据刚被改过。",
    what_to_do: "重新获取最新版本后再提交。",
    retryable: false,
    request_id: "req_abc123",
    errors: [{ field: "version", code: "stale", message: "版本已过期" }],
  };
}

describe("ProblemView", () => {
  it("把 7807 错误渲染成 人话 + 怎么办 + request_id", () => {
    render(<ProblemView error={new ApiProblemError(versionConflict())} />);

    expect(screen.getByText("这条数据刚被改过。")).toBeTruthy();
    // what_to_do 与错误码都要出现
    expect(screen.getByText(/重新获取最新版本后再提交。/)).toBeTruthy();
    expect(screen.getByText("VERSION_CONFLICT")).toBeTruthy();
    expect(screen.getByText(/req_abc123/)).toBeTruthy();
    expect(screen.getByText(/版本已过期/)).toBeTruthy();
  });

  it("非 7807 的异常也归一化为人话提示", () => {
    const problem = toProblem(new Error("boom"));
    expect(problem.code).toBe("UNEXPECTED_ERROR");
    expect(problem.what_to_do.length).toBeGreaterThan(0);
  });
});
