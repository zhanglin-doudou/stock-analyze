import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { Register } from "./Register";
import { api } from "../client";

vi.mock("../client", () => ({
  api: {
    auth: {
      getVerificationPolicy: vi.fn(),
      sendEmailCode: vi.fn(),
      register: vi.fn(),
    },
  },
}));

const getPolicy = vi.mocked(api.auth.getVerificationPolicy);
const sendCode = vi.mocked(api.auth.sendEmailCode);
const register = vi.mocked(api.auth.register);

beforeEach(() => {
  vi.clearAllMocks();
  window.localStorage.clear();
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
    purpose: "register",
    expires_in: 600,
    resend_after: 60,
    max_attempts: 10,
    code_length: 6,
    expires_at: "2026-11-22T04:10:00Z",
  });
  register.mockResolvedValue({
    user: {
      id: "00000000-0000-0000-0000-000000000001",
      email: "a@example.com",
      role: "user",
      email_verified: true,
      created_at: "2026-11-22T04:00:00Z",
    },
    access_token: "access",
    refresh_token: "refresh",
    token_type: "Bearer",
    expires_in: 900,
  });
});

afterEach(cleanup);

function renderPage(): void {
  render(
    <MemoryRouter>
      <Register />
    </MemoryRouter>,
  );
}

describe("Register 邮箱验证码", () => {
  it("策略开启时显示获取验证码，发送后出现验证码输入", async () => {
    renderPage();
    await waitFor(() => expect(getPolicy).toHaveBeenCalled());

    const email = screen.getAllByRole("textbox")[0];
    fireEvent.change(email, { target: { value: "a@example.com" } });
    fireEvent.click(screen.getByRole("button", { name: "获取验证码" }));

    await waitFor(() => expect(sendCode).toHaveBeenCalledWith({ email: "a@example.com", purpose: "register" }));
    expect(await screen.findByPlaceholderText("输入邮件中的 6 位数字")).toBeTruthy();
    expect(screen.getByText(/验证码已发送/)).toBeTruthy();

    fireEvent.change(screen.getByPlaceholderText("输入邮件中的 6 位数字"), {
      target: { value: "123456" },
    });
    fireEvent.change(screen.getByPlaceholderText("12~128 位，至少 3 类字符"), {
      target: { value: "Str0ng-Passw0rd!" },
    });
    fireEvent.change(screen.getByPlaceholderText("请再次输入密码"), {
      target: { value: "Str0ng-Passw0rd!" },
    });
    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.click(screen.getByRole("button", { name: "注册" }));
    await waitFor(() =>
      expect(register).toHaveBeenCalledWith(
        expect.objectContaining({ email: "a@example.com", email_code: "123456" }),
      ),
    );
  });

  it("策略关闭时不显示验证码按钮，提交不带 email_code", async () => {
    getPolicy.mockResolvedValue({
      email_verification_required: false,
      password_reset_enabled: false,
      code_length: 6,
      code_ttl_seconds: 600,
      resend_after_seconds: 60,
      max_attempts: 10,
    });
    renderPage();
    await waitFor(() => expect(getPolicy).toHaveBeenCalled());
    expect(screen.queryByRole("button", { name: "获取验证码" })).toBeNull();
  });
});
