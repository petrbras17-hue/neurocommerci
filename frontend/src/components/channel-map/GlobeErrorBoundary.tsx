import React from 'react';

interface Props {
  children: React.ReactNode;
}

interface State {
  hasError: boolean;
  error?: Error;
}

class GlobeErrorBoundary extends React.Component<Props, State> {
  constructor(props: Props) {
    super(props);
    this.state = { hasError: false };
  }

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, errorInfo: React.ErrorInfo) {
    console.error('Globe WebGL error:', error, errorInfo);
  }

  render() {
    if (this.state.hasError) {
      return (
        <div style={{
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center',
          height: '100%',
          background: '#0a0a0b',
          color: '#888',
          fontFamily: "'Geist Sans', system-ui, sans-serif",
          gap: '16px',
          padding: '40px',
        }}>
          <div style={{ fontSize: '48px' }}>&#x1F310;</div>
          <h2 style={{ color: '#e0e0e0', margin: 0, fontSize: 20, fontWeight: 600 }}>
            WebGL ошибка
          </h2>
          <p style={{
            maxWidth: '400px',
            textAlign: 'center',
            lineHeight: 1.6,
            margin: 0,
            fontSize: 14,
          }}>
            3D-карта каналов не может быть отображена. Это может быть вызвано проблемой с GPU или браузером.
          </p>
          {this.state.error?.message && (
            <code style={{
              background: 'rgba(255,255,255,0.05)',
              border: '1px solid rgba(255,255,255,0.08)',
              borderRadius: 6,
              padding: '6px 12px',
              fontSize: 12,
              color: '#ef4444',
              maxWidth: '400px',
              wordBreak: 'break-all',
              textAlign: 'center',
              fontFamily: "'JetBrains Mono', monospace",
            }}>
              {this.state.error.message}
            </code>
          )}
          <button
            onClick={() => this.setState({ hasError: false, error: undefined })}
            style={{
              padding: '10px 24px',
              background: 'transparent',
              color: '#00ff88',
              border: '1px solid #00ff88',
              borderRadius: '8px',
              cursor: 'pointer',
              fontWeight: 600,
              fontSize: '14px',
              fontFamily: "'Geist Sans', system-ui, sans-serif",
              marginTop: 4,
            }}
          >
            Попробовать снова
          </button>
          <button
            onClick={() => window.location.reload()}
            style={{
              padding: '10px 24px',
              background: '#00ff88',
              color: '#0a0a0b',
              border: 'none',
              borderRadius: '8px',
              cursor: 'pointer',
              fontWeight: 600,
              fontSize: '14px',
              fontFamily: "'Geist Sans', system-ui, sans-serif",
            }}
          >
            Перезагрузить страницу
          </button>
        </div>
      );
    }

    return this.props.children;
  }
}

export default GlobeErrorBoundary;
