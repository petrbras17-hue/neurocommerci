import { FormEvent, useState } from "react";
import { Navigate } from "react-router-dom";
import { useAuth } from "../auth";

export function ProfileCompletionPage() {
  const auth = useAuth();
  const [email, setEmail] = useState("");
  const [company, setCompany] = useState("");
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  if (auth.status === "authenticated") {
    return <Navigate to="/dashboard" replace />;
  }
  if (auth.status !== "profile_incomplete") {
    return <Navigate to="/login" replace />;
  }

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault();
    setError("");
    setSubmitting(true);
    try {
      await auth.completeProfile(email, company);
    } catch (err) {
      setError(err instanceof Error ? err.message : "profile_completion_failed");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="auth-screen">
      <form className="auth-card" onSubmit={handleSubmit}>
        <div className="eyebrow">Завершение входа</div>
        <h1>Заполните профиль</h1>
        <p className="auth-copy">
          Email и компания нужны для Sprint 4 billing, workspace identity и SaaS onboarding.
        </p>
        <label className="field">
          <span>Email</span>
          <input value={email} onChange={(event) => setEmail(event.target.value)} type="email" required />
        </label>
        <label className="field">
          <span>Компания</span>
          <input value={company} onChange={(event) => setCompany(event.target.value)} required />
        </label>
        {error ? <div className="form-error">{error}</div> : null}
        <button className="primary-button" type="submit" disabled={submitting}>
          {submitting ? "Сохраняем…" : "Продолжить"}
        </button>
      </form>
    </div>
  );
}
