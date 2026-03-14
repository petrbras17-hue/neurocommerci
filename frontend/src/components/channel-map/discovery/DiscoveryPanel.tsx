import { useCallback } from 'react';
import type { GeoPoint, ChannelMapStats } from '../../../api';
import FilterControls, { type FilterState } from './FilterControls';
import CategoryAccordion from './CategoryAccordion';
import ViewportChannelList from './ViewportChannelList';
import { DESIGN_TOKENS as T } from '../constants';

interface Props {
  categories: string[];
  stats: ChannelMapStats | null;
  selectedCategory: string | null;
  onCategoryFilter: (cat: string | null) => void;
  onChannelSelect: (id: number, lat?: number, lng?: number) => void;
  geoPoints: GeoPoint[];
}

export function DiscoveryPanel({
  categories,
  stats,
  selectedCategory,
  onCategoryFilter,
  onChannelSelect,
  geoPoints,
}: Props) {
  const handleFilterChange = useCallback(
    (_filters: FilterState) => {
      // TODO: wire up filter state to data layer in Sprint 1.4
      // For now, filters visually render but only category filtering is active
    },
    [],
  );

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
          fontSize: 14,
          fontWeight: 600,
          color: T.TEXT_PRIMARY,
          marginBottom: 2,
        }}>
          Discovery
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
