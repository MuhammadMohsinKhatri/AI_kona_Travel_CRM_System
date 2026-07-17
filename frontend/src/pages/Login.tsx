import { FormEvent, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";

export default function Login() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const [email, setEmail] = useState("admin@konaice.com");
  const [password, setPassword] = useState("changeme");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      await login(email, password);
      navigate("/");
    } catch (err: any) {
      setError(err.message || "Login failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="login-wrap">
      <form className="card login-card" onSubmit={onSubmit}>
        <div className="brand" style={{ marginBottom: 16 }}>
          <span className="dot" /> Conbyt AI Automation Financial System
        </div>
        <h1>Sign in</h1>
        <p className="sub">Event → invoice automation console</p>
        {error && <div className="error-msg">{error}</div>}
        <div className="field">
          <label>Email</label>
          <input
            className="input"
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            autoFocus
          />
        </div>
        <div className="field">
          <label>Password</label>
          <input
            className="input"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </div>
        <button className="btn primary" style={{ width: "100%" }} disabled={busy}>
          {busy ? "Signing in…" : "Sign in"}
        </button>
        <p className="hint">Default seed: admin@konaice.com / changeme</p>
      </form>
    </div>
  );
}
