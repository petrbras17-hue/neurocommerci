// Channel Map v4 — публичный API компонентов
export { WorldMap } from './WorldMap';
export type { WorldMapProps, MapBounds } from './WorldMap';

export { ClusterMarker } from './ClusterMarker';
export { ChannelMarker } from './ChannelMarker';

export { useMapData } from './hooks/useMapData';
export type {
  ChannelPoint,
  ClusterPoint,
  MapDataMode,
  MapFilters,
  UseMapDataResult,
} from './hooks/useMapData';

export {
  TILE_URL,
  TILE_ATTRIBUTION,
  MAP_CONFIG,
  ZOOM_LEVELS,
  CATEGORY_COLORS,
  CATEGORY_LABELS,
  SUBSCRIBER_TIERS,
  MARKER_SIZES,
  CLUSTER_SIZES,
  DEFAULT_CATEGORY_COLOR,
  getCategoryColor,
  getCategoryLabel,
  formatSubscribers,
} from './constants';
