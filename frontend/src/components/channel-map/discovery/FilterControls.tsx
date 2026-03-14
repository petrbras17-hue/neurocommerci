import { useState, useCallback } from 'react';
import { DESIGN_TOKENS as T } from '../constants';

interface Props {
  onFilterChange: (filters: FilterState) => void;
  languages: string[];
  regions: string[];
}

export interface FilterState {
  minMembers: number | null;
  maxMembers: number | null;
  language: string | null;
  region: string | null;
  hasComments: boolean | null;
}

const MEMBER_PRESETS = [
  { label: 'Все', min: null, max: null },
  { label: '1K+', min: 1000, max: null },
  { label: '5K+', min: 5000, max: null },
  { label: '10K+', min: 10000, max: null },
  { label: '50K+', min: 50000, max: null },
  { label: '100K+', min: 100000, max: null },
] as const;

export default function FilterControls({ onFilterChange, languages, regions }: Props) {
  const [activePreset, setActivePreset] = useState(0);
  const [language, setLanguage] = useState<string | null>(null);
  const [region, setRegion] = useState<string | null>(null);
  const [hasComments, setHasComments] = useState<boolean | null>(null);

  const applyFilters = useCallback(
    (preset: number, lang: string | null, reg: string | null, comments: boolean | null) => {
      const p = MEMBER_PRESETS[preset];
      onFilterChange({
        minMembers: p.min,
        maxMembers: p.max,
        language: lang,
        region: reg,
        hasComments: comments,
      });
    },
    [onFilterChange],
  );

  const handlePreset = (i: number) => {
    setActivePreset(i);
    applyFilters(i, language, region, hasComments);
  };

  const handleLanguage = (v: string) => {
    const val = v || null;
    setLanguage(val);
    applyFilters(activePreset, val, region, hasComments);
  };

  const handleRegion = (v: string) => {
    const val = v || null;
    setRegion(val);
    applyFilters(activePreset, language, val, hasComments);
  };

  const handleComments = () => {
    const next = hasComments === null ? true : hasComments ? false : null;
    setHasComments(next);
    applyFilters(activePreset, language, region, next);
  };

  return (
    <div style={{ padding: '12px 16px', borderBottom: `1px solid ${T.BORDER_SUBTLE}` }}>
      {/* Section label */}
      <div style={{
        fontSize: 10,
        fontWeight: 600,
        color: T.TEXT_MUTED,
        letterSpacing: 1.2,
        textTransform: 'uppercase',
        marginBottom: 10,
      }}>
        Фильтры
      </div>

      {/* Subscriber range presets */}
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4, marginBottom: 10 }}>
        {MEMBER_PRESETS.map((p, i) => (
          <button
            key={p.label}
            onClick={() => handlePreset(i)}
            style={{
              padding: '4px 10px',
              borderRadius: 4,
              border: 'none',
              cursor: 'pointer',
              fontSize: 11,
              fontWeight: i === activePreset ? 600 : 400,
              fontFamily: "'JetBrains Mono', monospace",
              background: i === activePreset ? T.ACCENT_DIM : 'transparent',
              color: i === activePreset ? T.ACCENT : T.TEXT_SECONDARY,
              transition: 'all 0.15s ease',
            }}
          >
            {p.label}
          </button>
        ))}
      </div>

      {/* Language + Region selects */}
      <div style={{ display: 'flex', gap: 8, marginBottom: 8 }}>
        <Select
          placeholder="Язык"
          value={language ?? ''}
          options={languages}
          onChange={handleLanguage}
        />
        <Select
          placeholder="Регион"
          value={region ?? ''}
          options={regions}
          onChange={handleRegion}
        />
      </div>

      {/* Comments toggle */}
      <button
        onClick={handleComments}
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 6,
          padding: '5px 10px',
          borderRadius: 4,
          border: `1px solid ${hasComments != null ? T.ACCENT + '40' : T.BORDER_SUBTLE}`,
          background: hasComments === true ? T.ACCENT_DIM : 'transparent',
          color: hasComments != null ? T.ACCENT : T.TEXT_MUTED,
          fontSize: 11,
          cursor: 'pointer',
          fontFamily: 'inherit',
        }}
      >
        <span>{hasComments === true ? '✓' : hasComments === false ? '✗' : '○'}</span>
        Комментарии {hasComments === true ? 'вкл' : hasComments === false ? 'выкл' : ''}
      </button>
    </div>
  );
}

function Select({
  placeholder,
  value,
  options,
  onChange,
}: {
  placeholder: string;
  value: string;
  options: string[];
  onChange: (v: string) => void;
}) {
  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      style={{
        flex: 1,
        padding: '5px 8px',
        borderRadius: 4,
        border: `1px solid ${T.BORDER_SUBTLE}`,
        background: T.SURFACE_ELEVATED,
        color: value ? T.TEXT_PRIMARY : T.TEXT_MUTED,
        fontSize: 11,
        fontFamily: 'inherit',
        cursor: 'pointer',
        appearance: 'none',
      }}
    >
      <option value="">{placeholder}</option>
      {options.map((opt) => (
        <option key={opt} value={opt}>{opt}</option>
      ))}
    </select>
  );
}
