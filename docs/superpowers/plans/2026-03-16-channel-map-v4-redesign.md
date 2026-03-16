# Channel Map v4 — Full Redesign Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the broken 3D globe with a production-grade 2D world map with 82K+ clickable channels, clustering, drill-down, and analytics — at TGStat/GramGPT level or better.

**Architecture:** Leaflet.js 2D map with marker clustering (leaflet.markercluster), WebGL overlay for 50K+ points (Leaflet.glify), server-side clustering API, 3-level zoom (countries → cities → individual channels with avatars). Keep existing API endpoints, add new optimized ones.

**Tech Stack:** Leaflet.js + leaflet.markercluster + Leaflet.glify (WebGL points), React wrapper via react-leaflet, existing FastAPI backend, PostgreSQL with PostGIS-like queries.

**Current state:** 82,899 channels in DB, 14 frontend components (3D globe), 19 API endpoints. All categories show as "Другое" due to mapping bug.

**Target:** 2D world map, each channel clickable, hover tooltips, category colors, subscriber-based sizing, avatar bubbles on deep zoom, smooth drill-down, filters that work, real category names.

---

## File Structure

### New files to create:
```
frontend/src/pages/ChannelMapPage.tsx              — New main page (replaces V2)
frontend/src/components/channel-map-v4/
├── WorldMap.tsx                                    — Leaflet map core
├── ChannelMarker.tsx                               — Individual channel marker/bubble
├── ClusterMarker.tsx                               — Cluster circle with count
├── ChannelPopup.tsx                                — Hover/click popup card
├── ChannelSidebar.tsx                              — Right sidebar with full channel info
├── MapFilters.tsx                                  — Top filter bar (category, language, region, subscribers)
├── CategoryLegend.tsx                              — Color legend for categories
├── ChannelList.tsx                                 — Scrollable channel list below map
├── MapStats.tsx                                    — Stats bar (total, filtered, top category)
├── hooks/
│   ├── useMapData.ts                               — Data fetching + filtering
│   ├── useMapClusters.ts                           — Client-side clustering state
│   └── useChannelSearch.ts                         — Search with debounce
└── constants.ts                                    — Category colors, zoom levels, tile URLs
```

### Files to modify:
```
frontend/src/App.tsx                                — Route /app/channel-map → new page
frontend/package.json                               — Add leaflet, react-leaflet, leaflet.markercluster
ops_api.py                                          — Fix category mapping, add optimized endpoints
```

### Files to keep (shared):
```
frontend/src/components/channel-map/SearchOverlay.tsx     — Reuse Cmd+K search
frontend/src/components/channel-map/GlobeErrorBoundary.tsx — Reuse as MapErrorBoundary
```

### Files to deprecate (not delete, just unused):
```
frontend/src/pages/ChannelMapPageV2.tsx             — Old 3D globe page
frontend/src/components/channel-map/Globe.tsx        — Old 3D globe
frontend/src/components/channel-map/ClusterLayer.tsx — Old hex clusters
```

---

## Chunk 1: Foundation — Leaflet Map + Data Layer

### Task 1: Install dependencies and create base map

**Files:**
- Modify: `frontend/package.json`
- Create: `frontend/src/components/channel-map-v4/WorldMap.tsx`
- Create: `frontend/src/components/channel-map-v4/constants.ts`

- [ ] **Step 1: Install Leaflet dependencies**
```bash
cd frontend && npm install leaflet react-leaflet leaflet.markercluster @types/leaflet
```

- [ ] **Step 2: Create constants with category colors and map config**
```typescript
// frontend/src/components/channel-map-v4/constants.ts
export const TILE_URL = 'https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png';
export const TILE_ATTRIBUTION = '&copy; OpenStreetMap &copy; CARTO';

export const MAP_CONFIG = {
  center: [30, 30] as [number, number],  // Center of populated world
  zoom: 3,
  minZoom: 2,
  maxZoom: 18,
  maxBoundsViscosity: 1.0,
};

export const ZOOM_LEVELS = {
  WORLD: 3,      // Show country clusters
  REGION: 6,     // Show city clusters
  CITY: 10,      // Show individual channels
  DETAIL: 14,    // Show channel avatars
};

export const CATEGORY_COLORS: Record<string, string> = {
  tech: '#00ff88',
  crypto: '#f7931a',
  news: '#3b82f6',
  entertainment: '#ec4899',
  education: '#8b5cf6',
  business: '#f59e0b',
  lifestyle: '#06b6d4',
  politics: '#ef4444',
  gaming: '#10b981',
  science: '#6366f1',
  other: '#6b7280',
};

export const SUBSCRIBER_TIERS = [
  { label: 'Все', min: 0 },
  { label: '1K+', min: 1000 },
  { label: '5K+', min: 5000 },
  { label: '10K+', min: 10000 },
  { label: '50K+', min: 50000 },
  { label: '100K+', min: 100000 },
  { label: '1M+', min: 1000000 },
];
```

- [ ] **Step 3: Create WorldMap component**
```tsx
// frontend/src/components/channel-map-v4/WorldMap.tsx
// Dark CARTO tiles, full-screen map, zoom controls
// MarkerClusterGroup for channel points
// On zoom change: refetch visible channels from API
// On click cluster: zoom in
// On click channel: open sidebar
```

- [ ] **Step 4: Verify map renders with dark tiles**
```bash
cd frontend && npm run dev
# Open http://localhost:5173/app/channel-map
# Expected: dark world map, zoom controls, no channels yet
```

- [ ] **Step 5: Commit**
```bash
git add frontend/package.json frontend/src/components/channel-map-v4/
git commit -m "feat: Channel Map v4 foundation — Leaflet dark tiles"
```

### Task 2: Fix backend category data

**Files:**
- Modify: `ops_api.py` (category endpoint + list endpoint)

- [ ] **Step 1: Fix /v1/channel-map/categories to return real category names**
The Kaggle import stored categories as our mapped values (tech, crypto, etc.) but CategoryAccordion shows "Другое" for all. Fix the serialization to return the actual category string.

- [ ] **Step 2: Add /v1/channel-map/geo-clusters endpoint**
New endpoint optimized for 2D map clustering:
```python
@app.get("/v1/channel-map/geo-clusters")
async def channel_map_geo_clusters(
    zoom: int = 3,
    bounds_sw_lat: float = -90, bounds_sw_lng: float = -180,
    bounds_ne_lat: float = 90, bounds_ne_lng: float = 180,
    category: Optional[str] = None,
    min_subscribers: int = 0,
    ...
):
    """Server-side clustering for 2D map.

    At low zoom (2-5): GROUP BY country → return country clusters
    At mid zoom (6-9): GROUP BY rounded lat/lng (0.5°) → city clusters
    At high zoom (10+): return individual channels in viewport
    """
```

- [ ] **Step 3: Commit**

### Task 3: Data layer hook + channel markers

**Files:**
- Create: `frontend/src/components/channel-map-v4/hooks/useMapData.ts`
- Create: `frontend/src/components/channel-map-v4/ClusterMarker.tsx`
- Create: `frontend/src/components/channel-map-v4/ChannelMarker.tsx`

- [ ] **Step 1: Create useMapData hook**
```typescript
// Fetches channels/clusters based on current map bounds + zoom
// At zoom < 6: fetch /v1/channel-map/geo-clusters (country level)
// At zoom 6-9: fetch /v1/channel-map/geo-clusters (city level)
// At zoom 10+: fetch /v1/channel-map/viewport (individual channels)
// Debounce 300ms on map move
// AbortController on unmount
```

- [ ] **Step 2: Create ClusterMarker — circle with count + category color**
```tsx
// DivIcon with CSS: circle, background = dominant category color
// Text: channel count (e.g. "1.2K")
// Size: proportional to total subscribers in cluster
// onClick: zoom to cluster bounds
```

- [ ] **Step 3: Create ChannelMarker — individual channel dot/bubble**
```tsx
// At zoom 10-13: small colored dot (8px), tooltip on hover with name + subscribers
// At zoom 14+: avatar bubble (32-48px circle with image), name label below
// onClick: open ChannelSidebar
// Category-colored ring around avatar
```

- [ ] **Step 4: Test with real data from API**
- [ ] **Step 5: Commit**

---

## Chunk 2: Interactivity — Filters, Search, Sidebar

### Task 4: Filter bar

**Files:**
- Create: `frontend/src/components/channel-map-v4/MapFilters.tsx`

- [ ] **Step 1: Create top filter bar**
```tsx
// Horizontal bar above map:
// [Category dropdown] [Language dropdown] [Region dropdown] [Subscriber tier buttons] [Search input]
// Dark terminal theme: #0a0a0b bg, #1a1a1b borders, #00ff88 accent
// Filters update useMapData → refetch
```

- [ ] **Step 2: Wire filters to API calls**
- [ ] **Step 3: Commit**

### Task 5: Channel sidebar (detail panel)

**Files:**
- Create: `frontend/src/components/channel-map-v4/ChannelSidebar.tsx`
- Create: `frontend/src/components/channel-map-v4/ChannelPopup.tsx`

- [ ] **Step 1: Create hover popup (lightweight)**
```tsx
// Leaflet Popup on marker hover:
// Avatar (40px) | Name | @username | 125K subscribers | Category badge
// Compact, disappears on mouseout
```

- [ ] **Step 2: Create click sidebar (full detail)**
```tsx
// Right slide-in panel (380px):
// Header: avatar (64px) + name + @username + t.me link
// Stats grid: subscribers, ER, avg reach, posts/day, comments
// Sparkline: subscriber growth 30 days
// Description: full text
// Tags: topic_tags as badges
// Similar channels: top 5
// Actions: "В ферму", "Blacklist", "Отслеживать", "Экспорт"
// Last post preview: text + date + views
```

- [ ] **Step 3: Fetch channel detail + history + similar on click**
- [ ] **Step 4: Commit**

### Task 6: Search integration

**Files:**
- Create: `frontend/src/components/channel-map-v4/hooks/useChannelSearch.ts`

- [ ] **Step 1: Search bar in filter strip**
```tsx
// Debounced search input (300ms)
// Calls /v1/channel-map/search
// Results dropdown: top 10 matches with avatars
// Click result: fly to channel on map + open sidebar
```

- [ ] **Step 2: Cmd+K shortcut preserved**
- [ ] **Step 3: Commit**

---

## Chunk 3: Channel List + Stats + Polish

### Task 7: Channel list below map

**Files:**
- Create: `frontend/src/components/channel-map-v4/ChannelList.tsx`

- [ ] **Step 1: Scrollable list panel**
```tsx
// Below map (or left panel on desktop):
// Sorted by subscribers DESC (or filtered)
// Each row: avatar | name | @handle | subscribers | category badge | country flag
// Click: fly to on map + open sidebar
// Virtual scroll for performance (82K items)
// Pagination: load more on scroll
```

- [ ] **Step 2: Sync with map filters (same data source)**
- [ ] **Step 3: Commit**

### Task 8: Stats bar + category legend

**Files:**
- Create: `frontend/src/components/channel-map-v4/MapStats.tsx`
- Create: `frontend/src/components/channel-map-v4/CategoryLegend.tsx`

- [ ] **Step 1: Stats bar**
```tsx
// Top right: "82.9K каналов | 50+ стран | 32 категории | Фильтр: tech (7.1K)"
```

- [ ] **Step 2: Category color legend**
```tsx
// Bottom left: colored dots with category names (collapsible)
// Click category: toggle filter
```

- [ ] **Step 3: Commit**

### Task 9: New page + routing

**Files:**
- Create: `frontend/src/pages/ChannelMapPage.tsx`
- Modify: `frontend/src/App.tsx`

- [ ] **Step 1: Create new page composing all v4 components**
```tsx
// Layout:
// ┌─────────────────────────────────────────────┐
// │ MapFilters                        MapStats  │
// ├─────────────────────────────────────────────┤
// │                                    Sidebar  │
// │           WorldMap (full width)      (380px) │
// │                                             │
// ├─────────────────────────────────────────────┤
// │ CategoryLegend          ChannelList (below) │
// └─────────────────────────────────────────────┘
```

- [ ] **Step 2: Update App.tsx route**
```tsx
// /app/channel-map → lazy(() => import('./pages/ChannelMapPage'))
// Keep V2 at /app/channel-map-globe for comparison
```

- [ ] **Step 3: Build and verify**
```bash
cd frontend && npm run build && npx tsc --noEmit
```

- [ ] **Step 4: Commit**

---

## Chunk 4: Backend Optimization + Deploy

### Task 10: Optimize geo-clusters endpoint

**Files:**
- Modify: `ops_api.py`

- [ ] **Step 1: Implement server-side clustering by zoom level**
```python
# Zoom 2-5: GROUP BY country (from region field)
# Zoom 6-9: GROUP BY ROUND(lat, 1), ROUND(lng, 1)
# Zoom 10+: SELECT individual channels in bounds LIMIT 500
# All with category/language/subscriber filters
```

- [ ] **Step 2: Add caching via CacheService**
- [ ] **Step 3: Test with 82K channels**
- [ ] **Step 4: Commit**

### Task 11: Fix category display

**Files:**
- Modify: `ops_api.py`

- [ ] **Step 1: Fix categories endpoint to return actual names, not "Другое"**
The issue: CategoryAccordion reads from `/v1/channel-map/categories` which returns raw DB values, but the icon/label mapping in constants.ts only knows a few categories. All Kaggle categories (tech, crypto, news, etc.) need proper display names and colors.

- [ ] **Step 2: Update frontend constants with all 32 categories**
- [ ] **Step 3: Commit**

### Task 12: Deploy

- [ ] **Step 1: Commit all changes**
- [ ] **Step 2: Push to main**
- [ ] **Step 3: Rebuild Docker image on VPS**
```bash
ssh deploy@176.124.221.253 "cd /opt/neuro-commenting && git pull origin main && docker compose build ops_api && docker compose up -d ops_api"
```
- [ ] **Step 4: Verify at https://neurocommenting.com/app/channel-map**
- [ ] **Step 5: Take screenshot and compare with before**

---

## Success Criteria

- [ ] 82K+ channels visible on 2D world map
- [ ] Each channel clickable with full detail sidebar
- [ ] Hover shows tooltip with basic info
- [ ] Category colors consistent and correct (not "Другое")
- [ ] Smooth zoom: countries → cities → individual channels → avatars
- [ ] Filters work: category, language, region, subscriber count
- [ ] Search works: find channel by name, fly to on map
- [ ] Channel list synced with map view
- [ ] Performance: <2s initial load, <500ms on filter change
- [ ] Mobile responsive
- [ ] No "Coming in Sprint 2/3" placeholders
