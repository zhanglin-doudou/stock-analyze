import { useEffect, useState, type FormEvent } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { api } from "../client";
import { saveTokens } from "../store/session";
import { ProblemView } from "../ui/Problem";
import { GuideEntryLink } from "../guide/GuideEntryLink";
import { sanitizeNextPath } from "../utils/nextPath";

export function Login() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const resetSuccess = searchParams.get("reset") === "1";
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [showForgot, setShowForgot] = useState(true);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let active = true;
    api.auth
      .getVerificationPolicy()
      .then((policy) => {
        if (active) setShowForgot(policy.password_reset_enabled);
      })
      .catch(() => {
        // 策略取不到时保留入口；找回页会自行处理“未开放”。
        if (active) setShowForgot(true);
      });
    return () => {
      active = false;
    };
  }, []);

  async function submit(event: FormEvent): Promise<void> {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const result = await api.auth.login({ email, password });
      saveTokens(result);
      // 登录后优先回跳来源页（next 经安全校验，防 open redirect）。
      const next = sanitizeNextPath(searchParams.get("next"));
      navigate(next ?? "/", { replace: true });
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="auth-page">
      <form className="card" onSubmit={(event) => void submit(event)}>
        <h1>登录</h1>
        {resetSuccess ? (
          <p className="notice" role="status">
            密码已重置，请用新密码登录。
          </p>
        ) : null}
        <label>
          邮箱
          <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} required />
        </label>
        <label>
          密码
          <div className="password-input-wrapper">
            <input
              type={showPassword ? "text" : "password"}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
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
        <button className="btn" type="submit" disabled={busy}>
          {busy ? "登录中…" : "登录"}
        </button>
        <ProblemView error={error} />
        <p className="muted">
          没有账号？<Link to="/register">去注册</Link>
        </p>
        {showForgot ? (
          <p className="muted">
            <Link to="/forgot-password">忘记密码？</Link>
          </p>
        ) : null}
        <GuideEntryLink />
      </form>
    </div>
  );
}
