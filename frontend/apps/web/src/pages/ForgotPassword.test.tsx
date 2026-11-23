import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { ForgotPassword } from "./ForgotPassword";
import { api } from "../client";

vi.mock("../client", () => ({
  api: {
    auth: {
      getVerificationPolicy: vi.fn(),
      sendEmailCode: vi.fn(),
      resetPassword: vi.fn(),
    },
  },
}));

const getPolicy = vi.mocked(api.auth.getVerificationPolicy);
const sendCode = vi.mocked(api.auth.sendEmailCode);

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
  sendCode.mockResolvedValue({
    email: "a@example.com",
    purpose: "reset_password",
    expires_in: 600,
    resend_after: 60,
    max_attempts: 10,
    code_length: 6,
    expires_at: "2026-11-22T04:10:00Z",
  });
});

afterEach(cleanup);

function renderPage(): void {
  render(
    <MemoryRouter>
      <ForgotPassword />
    </MemoryRouter>,
  );
}

describe("ForgotPassword", () => {
  it("提供使用引导入口", () => {
    renderPage();
    expect(screen.getByRole("link", { name: /使用引导/ }).getAttribute("href")).toBe("/guide");
  });

  it("发送后显示通用文案与验证码/新密码表单", async () => {
    renderPage();
    await waitFor(() => expect(getPolicy).toHaveBeenCalled());
    fireEvent.change(screen.getAllByRole("textbox")[0], {
      target: { value: "a@example.com" },
    });
    fireEvent.click(screen.getByRole("button", { name: "发送验证码" }));
    await waitFor(() =>
      expect(sendCode).toHaveBeenCalledWith({ email: "a@example.com", purpose: "reset_password" }),
    );
    expect(await screen.findByText(/如果该邮箱已注册/)).toBeTruthy();
    expect(screen.getByPlaceholderText("输入邮件中的 6 位数字")).toBeTruthy();
    expect(screen.getByText("新密码")).toBeTruthy();
  });

  it("策略关闭时显示未开放", async () => {
    getPolicy.mockResolvedValue({
      email_verification_required: true,
      password_reset_enabled: false,
      code_length: 6,
      code_ttl_seconds: 600,
      resend_after_seconds: 60,
      max_attempts: 10,
    });
    renderPage();
    expect(await screen.findByText(/当前部署未开放邮箱找回密码/)).toBeTruthy();
  });
});
