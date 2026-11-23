import { useEffect, useRef, useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { isApiProblemError } from "@sa/api-client";
import { api } from "../client";
import { saveTokens } from "../store/session";
import { ProblemView } from "../ui/Problem";
import { GuideEntryLink } from "../guide/GuideEntryLink";

export function Register() {
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [acceptTos, setAcceptTos] = useState(false);
  const [emailCode, setEmailCode] = useState("");
  const [verificationRequired, setVerificationRequired] = useState(true);
  const [codeSent, setCodeSent] = useState(false);
  const [cooldown, setCooldown] = useState(0);
  const [sendingCode, setSendingCode] = useState(false);
  const [codeNotice, setCodeNotice] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const codeInputRef = useRef<HTMLInputElement>(null);

  const passwordsMismatch = confirmPassword.length > 0 && password !== confirmPassword;
  const isCoolingDown = cooldown > 0;

  useEffect(() => {
    let active = true;
    api.auth
      .getVerificationPolicy()
      .then((policy) => {
        if (active) setVerificationRequired(policy.email_verification_required);
      })
      .catch(() => {
        // 策略取不到时 fail-closed：仍显示验证码，服务端最终兜底。
        if (active) setVerificationRequired(true);
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

  function resetCodeState(message: string | null = null): void {
    setCodeSent(false);
    setEmailCode("");
    setCodeNotice(message);
  }

  function onEmailChange(value: string): void {
    setEmail(value);
    if (codeSent) resetCodeState("邮箱已修改，请重新获取验证码。");
  }

  async function sendCode(): Promise<void> {
    if (!email || sendingCode || cooldown > 0) return;
    setSendingCode(true);
    setError(null);
    try {
      const result = await api.auth.sendEmailCode({ email, purpose: "register" });
      setCodeSent(true);
      setCooldown(result.resend_after);
      setCodeNotice(`验证码已发送，${Math.floor(result.expires_in / 60)} 分钟内有效。请查看收件箱和垃圾箱。`);
    } catch (err) {
      setError(err);
      if (isApiProblemError(err) && err.code === "RATE_LIMITED") setCooldown(60);
    } finally {
      setSendingCode(false);
    }
  }

  async function submit(event: FormEvent): Promise<void> {
    event.preventDefault();
    if (verificationRequired && emailCode.length !== 6) return;
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
      const result = await api.auth.register({
        email,
        password,
        display_name: displayName || undefined,
        accept_tos: acceptTos,
        email_code: verificationRequired ? emailCode : undefined,
      });
      saveTokens(result);
      navigate("/", { replace: true });
    } catch (err) {
      setError(err);
      if (isApiProblemError(err) && err.code === "EMAIL_CODE_EXPIRED") {
        setEmailCode("");
        setCodeSent(false);
        setCooldown(0);
        setCodeNotice("验证码已过期，请重新获取。");
      } else if (isApiProblemError(err) && err.code === "EMAIL_CODE_INVALID") {
        codeInputRef.current?.focus();
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="auth-page">
      <form className="card" onSubmit={(event) => void submit(event)}>
        <h1>注册</h1>
        <label>
          邮箱
          <div className="auth-code-row">
            <input
              type="email"
              value={email}
              onChange={(e) => onEmailChange(e.target.value)}
              required
              autoComplete="email"
            />
            {verificationRequired ? (
              <button
                type="button"
                className="btn auth-code-button"
                onClick={() => void sendCode()}
                disabled={!email || sendingCode || cooldown > 0 || busy}
              >
                {sendingCode ? "发送中…" : cooldown > 0 ? `重新发送（${cooldown}s）` : "获取验证码"}
              </button>
            ) : null}
          </div>
        </label>
        {verificationRequired && codeNotice ? (
          <p className="auth-code-hint" role="status">
            {codeNotice}
          </p>
        ) : null}
        {verificationRequired && codeSent ? (
          <label>
            邮箱验证码
            <input
              ref={codeInputRef}
              value={emailCode}
              onChange={(e) => setEmailCode(e.target.value.replace(/\D/g, "").slice(0, 6))}
              inputMode="numeric"
              autoComplete="one-time-code"
              pattern="[0-9]{6}"
              maxLength={6}
              placeholder="输入邮件中的 6 位数字"
              required
            />
            <span className="auth-code-hint">10 分钟内有效，仅可使用一次。</span>
          </label>
        ) : null}
        <label>
          密码
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="12~128 位，至少 3 类字符"
            minLength={12}
            required
          />
        </label>
        <label>
          确认密码
          <input
            type="password"
            value={confirmPassword}
            onChange={(e) => setConfirmPassword(e.target.value)}
            placeholder="请再次输入密码"
            minLength={12}
            required
          />
          {passwordsMismatch && <span className="field-error">两次输入的密码不一致</span>}
        </label>
        <label>
          昵称（可选）
          <input value={displayName} onChange={(e) => setDisplayName(e.target.value)} maxLength={40} />
        </label>
        <label className="check">
          <input type="checkbox" checked={acceptTos} onChange={(e) => setAcceptTos(e.target.checked)} />
          我已阅读并同意服务条款
        </label>
        <button
          className="btn"
          type="submit"
          disabled={
            busy ||
            !acceptTos ||
            passwordsMismatch ||
            (verificationRequired && emailCode.length !== 6)
          }
        >
          {busy ? "注册中…" : "注册"}
        </button>
        <ProblemView error={error} />
        <p className="muted">
          已有账号？<Link to="/login">去登录</Link>
        </p>
        <GuideEntryLink />
      </form>
    </div>
  );
}
