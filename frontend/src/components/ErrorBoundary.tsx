import { Component, ReactNode } from "react";

interface Props { children: ReactNode; }
interface State { hasError: boolean; error: Error | null; }

export class ErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false, error: null };

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  render() {
    if (this.state.hasError) {
      return (
        <div style={{
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          minHeight: "60vh",
          gap: 16,
          color: "var(--text)",
        }}>
          <div style={{ fontSize: 48 }}>⚠</div>
          <h2 style={{ fontSize: 20, fontWeight: 600 }}>Что-то пошло не так</h2>
          <p style={{ color: "var(--muted)", maxWidth: 400, textAlign: "center" }}>
            {this.state.error?.message || "Произошла непредвиденная ошибка"}
          </p>
          <button
            className="secondary-button"
            onClick={() => { this.setState({ hasError: false, error: null }); window.location.reload(); }}
          >
            Перезагрузить
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}
