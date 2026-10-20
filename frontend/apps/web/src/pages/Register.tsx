import { useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../client";
import { saveTokens } from "../store/session";
import { ProblemView } from "../ui/Problem";

export function Register() {
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [acceptTos, setAcceptTos] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent): Promise<void> {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const result = await api.auth.register({
        email,
        password,
        display_name: displayName || undefined,
        accept_tos: acceptTos,
      });
      saveTokens(result);
      navigate("/trades", { replace: true });
    } catch (err) {
      setError(err);
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
          <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} required />
        </label>
        <label>
          密码
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="12~128 位，至少 3 类字符"
            required
          />
        </label>
        <label>
          昵称（可选）
          <input value={displayName} onChange={(e) => setDisplayName(e.target.value)} maxLength={40} />
        </label>
        <label className="check">
          <input type="checkbox" checked={acceptTos} onChange={(e) => setAcceptTos(e.target.checked)} />
          我已阅读并同意服务条款
        </label>
        <button className="btn" type="submit" disabled={busy || !acceptTos}>
          {busy ? "注册中…" : "注册"}
        </button>
        <ProblemView error={error} />
        <p className="muted">
          已有账号？<Link to="/login">去登录</Link>
        </p>
      </form>
    </div>
  );
}
