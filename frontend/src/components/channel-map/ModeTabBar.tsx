import { MAP_MODES, DESIGN_TOKENS, type MapMode } from './constants';

interface Props {
  mode: MapMode;
  onSwitch: (m: MapMode) => void;
}

export default function ModeTabBar({ mode, onSwitch }: Props) {
  return (
    <div style={{ display: 'flex', gap: 4 }}>
      {MAP_MODES.map((m) => {
        const active = m.key === mode;
        return (
          <button
            key={m.key}
            onClick={() => onSwitch(m.key)}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 6,
              padding: '8px 16px',
              borderRadius: 6,
              border: 'none',
              cursor: 'pointer',
              fontSize: 12,
              fontWeight: active ? 600 : 500,
              fontFamily: 'Geist Sans, Inter, sans-serif',
              background: active ? DESIGN_TOKENS.ACCENT_DIM : 'transparent',
              color: active ? DESIGN_TOKENS.ACCENT : DESIGN_TOKENS.TEXT_MUTED,
              transition: 'all 0.2s ease',
            }}
          >
            <span>{m.icon}</span>
            <span>{m.label}</span>
          </button>
        );
      })}
    </div>
  );
}
