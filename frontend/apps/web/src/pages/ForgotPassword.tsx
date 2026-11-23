import { useEffect, useRef, useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { isApiProblemError } from "@sa/api-client";
import { api } from "../client";
import { ProblemView } from "../ui/Problem";
import { GuideEntryLink } from "../guide/GuideEntryLink";

export function ForgotPassword() {
  const navigate = useNavigate();
  const [available, setAvailable] = useState(true);
  const [email, setEmail] = useState("");
  const [code, setCode] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [sent, setSent] = useState(false);
  const [cooldown, setCooldown] = useState(0);
  const [sending, setSending] = useState(false);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  const codeInputRef = useRef<HTMLInputElement>(null);

  const passwordsMismatch = confirmPassword.length > 0 && password !== confirmPassword;
  const isCoolingDown = cooldown > 0;

  useEffect(() => {
    let active = true;
    api.auth
      .getVerificationPolicy()
      .then((policy) => {
        if (active) setAvailable(policy.password_reset_enabled);
      })
      .catch(() => {
        // 保守放行：后端仍会按开关返回 VALIDATION_ERROR。
        if (active) setAvailable(true);
      });
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    if (!isCoolingDown) return;
    const timer = window.setInterval(() => {
      setCooldown((seconds) => (seconds > 0 ? seconds - 1 : 0));
    }, 1000);
    return () => window.clearInterval(timer);
  }, [isCoolingDown]);

  function onEmailChange(value: string): void {
    setEmail(value);
    if (sent) {
      setSent(false);
      setCode("");
      setNotice("邮箱已修改，请重新获取验证码。");
    }
  }

  async function sendCode(): Promise<void> {
    if (!email || sending || cooldown > 0) return;
    setSending(true);
    setError(null);
    try {
      const result = await api.auth.sendEmailCode({ email, purpose: "reset_password" });
      setSent(true);
      setCooldown(result.resend_after);
      setNotice(
        `如果该邮箱已注册，我们会发送 6 位验证码。${Math.floor(result.expires_in / 60)} 分钟内有效，请检查收件箱和垃圾箱。`,
      );
    } catch (err) {
      setError(err);
      if (isApiProblemError(err) && err.code === "RATE_LIMITED") setCooldown(60);
    } finally {
      setSending(false);
    }
  }

  async function submit(event: FormEvent): Promise<void> {
    event.preventDefault();
    if (!sent || code.length !== 6) return;
    if (password !== confirmPassword) {
      setError({
        type: "about:blank",
        title: "Password mismatch",
        status: 400,
        detail: "两次输入的密码不一致",
        code: "PASSWORD_MISMATCH",
        user_message: "两次输入的密码不一致",
        what_to_do: "请重新输入两次相同的密码。",
        retryable: false,
        request_id: "",
      });
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await api.auth.resetPassword({ email, email_code: code, new_password: password });
      navigate("/login?reset=1", { replace: true });
    } catch (err) {
      setError(err);
      if (isApiProblemError(err) && err.code === "EMAIL_CODE_EXPIRED") {
        setCode("");
        setSent(false);
        setCooldown(0);
        setNotice("验证码已过期，请重新获取。");
      } else if (isApiProblemError(err) && err.code === "EMAIL_CODE_INVALID") {
        codeInputRef.current?.focus();
      }
    } finally {
      setBusy(false);
    }
  }

  if (!available) {
    return (
      <div className="auth-page">
        <div className="card">
          <h1>忘记密码</h1>
          <p className="muted">当前部署未开放邮箱找回密码，请联系管理员。</p>
          <p className="muted">
            <Link to="/login">返回登录</Link>
          </p>
          <GuideEntryLink />
        </div>
      </div>
    );
  }

  return (
    <div className="auth-page">
      <form className="card" onSubmit={(event) => void submit(event)}>
        <h1>忘记密码</h1>
        <p className="muted">通过注册邮箱验证身份后设置新密码。</p>
        <label>
          注册邮箱
          <div className="auth-code-row">
            <input
              type="email"
              value={email}
              onChange={(e) => onEmailChange(e.target.value)}
              required
              autoComplete="email"
            />
            <button
              type="button"
              className="btn auth-code-button"
              onClick={() => void sendCode()}
              disabled={!email || sending || cooldown > 0 || busy}
            >
              {sending ? "发送中…" : cooldown > 0 ? `重新发送（${cooldown}s）` : "发送验证码"}
            </button>
          </div>
        </label>
        {notice ? (
          <p className="auth-code-hint" role="status">
            {notice}
          </p>
        ) : null}
        {sent ? (
          <>
            <label>
              邮箱验证码
              <input
                ref={codeInputRef}
                value={code}
                onChange={(e) => setCode(e.target.value.replace(/\D/g, "").slice(0, 6))}
                inputMode="numeric"
                autoComplete="one-time-code"
                pattern="[0-9]{6}"
                maxLength={6}
                placeholder="输入邮件中的 6 位数字"
                required
              />
              <span className="auth-code-hint">10 分钟内有效，仅可使用一次。</span>
            </label>
            <label>
              新密码
              <div className="password-input-wrapper">
                <input
                  type={showPassword ? "text" : "password"}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder="12~128 位，至少 3 类字符"
                  minLength={12}
                  autoComplete="new-password"
                  required
                />
                <button
                  type="button"
                  className="password-toggle"
                  onClick={() => setShowPassword((v) => !v)}
                  aria-label={showPassword ? "隐藏密码" : "显示密码"}
                >
                  {showPassword ? "🙈" : "👁"}
                </button>
              </div>
            </label>
            <label>
              确认新密码
              <input
                type="password"
                value={confirmPassword}
                onChange={(e) => setConfirmPassword(e.target.value)}
                placeholder="请再次输入新密码"
                minLength={12}
                autoComplete="new-password"
                required
              />
              {passwordsMismatch && <span className="field-error">两次输入的密码不一致</span>}
            </label>
            <button
              className="btn"
              type="submit"
              disabled={busy || passwordsMismatch || code.length !== 6}
            >
              {busy ? "重置中…" : "重置密码"}
            </button>
          </>
        ) : null}
        <ProblemView error={error} />
        <p className="muted">
          想起密码了？<Link to="/login">返回登录</Link>
        </p>
        <GuideEntryLink />
      </form>
    </div>
  );
}
