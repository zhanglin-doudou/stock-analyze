import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { Login } from "./Login";
import { api } from "../client";

vi.mock("../client", () => ({
  api: {
    auth: {
      getVerificationPolicy: vi.fn(),
      login: vi.fn(),
    },
  },
}));

const getPolicy = vi.mocked(api.auth.getVerificationPolicy);

beforeEach(() => {
  vi.clearAllMocks();
  getPolicy.mockResolvedValue({
    email_verification_required: true,
    password_reset_enabled: true,
    code_length: 6,
    code_ttl_seconds: 600,
    resend_after_seconds: 60,
    max_attempts: 10,
  });
});

afterEach(cleanup);

describe("Login 找回入口", () => {
  it("策略开启时显示忘记密码", async () => {
    render(
      <MemoryRouter>
        <Login />
      </MemoryRouter>,
    );
    expect(await screen.findByRole("link", { name: "忘记密码？" })).toBeTruthy();
  });

  it("策略关闭时隐藏忘记密码", async () => {
    getPolicy.mockResolvedValue({
      email_verification_required: true,
      password_reset_enabled: false,
      code_length: 6,
      code_ttl_seconds: 600,
      resend_after_seconds: 60,
      max_attempts: 10,
    });
    render(
      <MemoryRouter>
        <Login />
      </MemoryRouter>,
    );
    await waitFor(() => expect(getPolicy).toHaveBeenCalled());
    expect(screen.queryByRole("link", { name: "忘记密码？" })).toBeNull();
  });

  it("reset=1 时显示成功提示", () => {
    render(
      <MemoryRouter initialEntries={["/login?reset=1"]}>
        <Login />
      </MemoryRouter>,
    );
    expect(screen.getByText("密码已重置，请用新密码登录。")).toBeTruthy();
  });
});
