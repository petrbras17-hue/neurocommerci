import { useCallback } from 'react';
import { Download } from 'lucide-react';
import type { GeoPoint, ChannelMapStats } from '../../../api';
import FilterControls, { type FilterState } from './FilterControls';
import CategoryAccordion from './CategoryAccordion';
import ViewportChannelList from './ViewportChannelList';
import { DESIGN_TOKENS as T } from '../constants';

interface Props {
  categories: string[];
  stats: ChannelMapStats | null;
  selectedCategory: string | null;
  selectedLanguage: string | null;
  selectedRegion: string | null;
  onCategoryFilter: (cat: string | null) => void;
  onFilterChange: (language: string | null, region: string | null) => void;
  onChannelSelect: (id: number, lat?: number, lng?: number) => void;
  geoPoints: GeoPoint[];
}

export function DiscoveryPanel({
  categories,
  stats,
  selectedCategory,
  selectedLanguage,
  selectedRegion,
  onCategoryFilter,
  onFilterChange,
  onChannelSelect,
  geoPoints,
}: Props) {
  const handleFilterChange = useCallback(
    (filters: FilterState) => {
      onFilterChange(filters.language, filters.region);
    },
    [onFilterChange],
  );

  const handleExport = useCallback(() => {
    const params = new URLSearchParams();
    if (selectedCategory) params.set('category', selectedCategory);
    if (selectedLanguage) params.set('language', selectedLanguage);
    if (selectedRegion) params.set('region', selectedRegion);
    const qs = params.toString();
    window.open(`/v1/channel-map/export${qs ? `?${qs}` : ''}`, '_blank');
  }, [selectedCategory, selectedLanguage, selectedRegion]);

  const languages = stats ? Object.keys(stats.by_language).sort() : [];
  const regions = stats ? Object.keys(stats.by_region).sort() : [];
  const categoryCounts = stats?.by_category ?? {};

  return (
    <div style={{
      display: 'flex',
      flexDirection: 'column',
      height: '100%',
      background: T.SURFACE,
    }}>
      {/* Panel header */}
      <div style={{
        padding: '14px 16px 10px',
        borderBottom: `1px solid ${T.BORDER_SUBTLE}`,
      }}>
        <div style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          marginBottom: 2,
        }}>
          <div style={{
            fontSize: 14,
            fontWeight: 600,
            color: T.TEXT_PRIMARY,
          }}>
            Discovery
          </div>
          {/* Export CSV button */}
          <button
            onClick={handleExport}
            title="Экспортировать каналы в CSV"
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 5,
              padding: '4px 10px',
              borderRadius: 5,
              border: `1px solid ${T.ACCENT}40`,
              background: 'transparent',
              color: T.ACCENT,
              fontSize: 11,
              fontWeight: 600,
              fontFamily: "'JetBrains Mono', monospace",
              cursor: 'pointer',
              transition: 'background 0.15s ease',
              letterSpacing: 0.3,
            }}
            onMouseEnter={(e) => {
              (e.currentTarget as HTMLButtonElement).style.background = T.ACCENT_DIM;
            }}
            onMouseLeave={(e) => {
              (e.currentTarget as HTMLButtonElement).style.background = 'transparent';
            }}
          >
            <Download size={12} />
            CSV
          </button>
        </div>
        <div style={{
          fontSize: 11,
          color: T.TEXT_MUTED,
        }}>
          Поиск и фильтрация каналов
        </div>
      </div>

      {/* Filters */}
      <FilterControls
        onFilterChange={handleFilterChange}
        languages={languages}
        regions={regions}
        initialLanguage={selectedLanguage}
        initialRegion={selectedRegion}
      />

      {/* Categories */}
      <CategoryAccordion
        categories={categories}
        categoryCounts={categoryCounts}
        selectedCategory={selectedCategory}
        onCategorySelect={onCategoryFilter}
      />

      {/* Top channels list */}
      <div style={{ flex: 1, overflowY: 'auto' }}>
        <ViewportChannelList
          geoPoints={geoPoints}
          onChannelSelect={onChannelSelect}
        />
      </div>
    </div>
  );
}
