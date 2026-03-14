# Channel Map v3 — Design Spec

Date: 2026-03-14
Status: Approved
Author: Claude + Founder

## 1. Overview

Redesign the Channel Map from a novelty 3D globe demo into a professional marketing intelligence tool with three operational modes. The globe remains as the visual core, but is surrounded by a full-featured UI: filter panels, category navigation, channel detail cards, farm overlays, and analytics layers.

Reference: "like Yandex Maps, but for Telegram channels on a 3D globe."

## 2. Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Globe vs flat map | Keep 3D globe, add UI around it | Differentiator, already built |
| Rendering library | react-globe.gl (keep) + custom React layers on top | Minimize rewrite, extend with overlays |
| Modes | 3 modes: Discovery, Farm Control, Intelligence | Full operator workflow coverage |
| Channel click behavior | Hover = mini-card on globe, click = full right panel | Standard map UX pattern |
| Category interaction | Accordion in left panel + globe filter | Stay in context, no page navigation |
| Cluster visualization | Numeric circles (Google Maps style) | Most intuitive, universal pattern |
| Farm visualization | Status-colored dots + arc lines on farm select | Shows both coverage and activity |
| Intelligence visualization | Toggleable overlay layers (heatmap, growth, ROI) | Operator chooses what to see |

## 3. Layout Architecture

```
┌──────────────────────────────────────────────────────────────┐
│ HEADER (56px)                                                │
│ [Logo]  [Discovery] [Farm Control] [Intelligence]  [Cmd+K]  │
├──────────┬───────────────────────────────────┬───────────────┤
│ LEFT     │ GLOBE ZONE                        │ RIGHT PANEL   │
│ PANEL    │ (flex, takes remaining space)      │ (380px)       │
│ (320px)  │                                   │ hidden by     │
│          │  ┌─────────────────────┐          │ default,      │
│ Context  │  │   3D Globe          │          │ slides in     │
│ changes  │  │   + overlays        │          │ on select     │
│ per mode │  │   + clusters        │          │               │
│          │  │   + hover cards     │          │               │
│          │  └─────────────────────┘          │               │
│          │                                   │               │
│          │  [Stats overlay]    [Zoom +-]     │               │
├──────────┴───────────────────────────────────┴───────────────┤
│ (Mobile: bottom sheet replaces right panel)                  │
└──────────────────────────────────────────────────────────────┘
```

### Dimensions
- Total: 1440x900 (desktop reference)
- Header: 100% x 56px, bg `#111113`
- Left panel: 320px, bg `#111113`, scrollable
- Globe zone: flex (remaining), bg `#0a0a0b`
- Right panel: 380px, bg `#111113`, hidden by default, animated slide-in
- Mobile breakpoint: 768px — left panel becomes drawer, right panel becomes bottom sheet

### Design Tokens (Dark Terminal Theme)
- Background: `#0a0a0b`
- Surface: `#111113`
- Surface elevated: `#1a1a1d`
- Accent: `#00ff88`
- Text primary: `#ffffff`
- Text secondary: `#cccccc`
- Text muted: `#555555`
- Text disabled: `#333333`
- Border: `#1a1a1d`
- Font: Geist Sans (UI), JetBrains Mono (numbers/stats)

### Category Colors
Category colors are defined in `frontend/src/components/channel-map/constants.ts` CATEGORIES registry (21 categories). The constants file is the single source of truth. Examples (illustrative):
- Crypto: amber tones
- Marketing: blue tones
- E-commerce: green tones
All new components must use `getCategoryMeta()` from constants.ts, never hardcode colors.

## 4. Mode 1: Discovery

### Left Panel Content
1. **Search box** — local filter + Cmd+K shortcut hint
2. **Filters section**
   - Subscribers: range slider [5K — 500K]
   - Language: dropdown (All, RU, EN, UK, KZ...)
   - Region: dropdown (All, Russia, CIS, EU, Global)
   - Comments: checkbox "Включены"
3. **Categories section** — accordion list
   - Each row: color dot + name + channel count
   - Click to expand: subcategories with counts
   - Click subcategory: filter globe to that subcategory
   - Multiple selection supported (Ctrl+click)
   - Selected category: filled background with 20% category color
4. **Top channels in viewport** — dynamic list
   - Updates as globe rotates/zooms
   - Shows top 10 by member_count in current viewport
   - Click → fly-to + open detail panel

### Globe Overlays
- **Clusters (zoom 0-2):** Numeric circles, 40-60px diameter
  - Number = channel count in cluster
  - Border color = dominant category color
  - Fill = category color at 20% opacity
  - Click → zoom in, cluster splits
- **Medium clusters (zoom 3-4):** Smaller circles, 24-36px
- **Individual points (zoom 5+):** 12px dots with category color
  - Tiny category icon overlay at zoom 6+
- **Hover mini-card:** Floating card near cursor
  - Channel name, subscriber count, ER, category badge
  - 200px wide, appears after 300ms hover delay
  - Disappears on mouse leave
- **Stats overlay (top-left):** Semi-transparent card
  - Total channels (filtered count)
  - Total subscriber reach
- **Zoom controls (bottom-left):** +/- buttons

### Right Panel — Channel Detail
1. **Header:** Avatar (48px circle) + title + @username + close button
2. **Badges row:** Category (colored), language (flag), verified (checkmark)
3. **Stats grid (2x2):**
   - Subscribers | ER%
   - Avg Views | Posts/day
4. **Description:** 2-3 lines, expandable
5. **Similar channels:** Up to 5, clickable (fly-to + swap detail)
6. **Action buttons:**
   - "В ферму" — primary green button
   - "В базу" — secondary
   - "Следить" — outline
   - "Blacklist" — ghost/destructive

## 5. Mode 2: Farm Control

### Left Panel Content
1. **My Farms list** — accordion per farm
   - Status badge: RUN (green pulse), PAUSE (yellow), STOP (grey)
   - Expanded: threads count, comments/hr, bans, channels, accounts
   - Click farm → globe highlights its channels
2. **Accounts section**
   - Each account: status dot + name + current state
   - States: active (green), cooldown (yellow), quarantine (red), dead (grey)
3. **Live Stats**
   - Total comments today
   - Success rate %
   - Avg response time

### Globe Overlays
- **Channel dots colored by farm status:**
  - `#00ff88` — actively commenting
  - `#ffaa00` — paused / cooldown
  - `#ff4444` — banned / quarantine
  - `#333333` — not in any farm
- **Pulsing animation** on actively commenting channels
- **Arc lines (on farm select):**
  - Curved lines from account location to channel locations
  - Line thickness = commenting intensity
  - Line color = status (green/yellow/red)
  - Animated flow along arcs (particles moving from account to channel)
- **Stats overlay:** Active threads, comments/hr, success rate

### Right Panel — Farm/Channel Detail
- **On channel click:** Discovery info + "Farm Activity" section
  - Last 5 comments in this channel
  - Which account commented
  - Timestamp + status (delivered / deleted / reported)
- **On farm click:** Farm summary
  - 24h sparkline charts: comments, bans, success rate
  - Thread list with individual status
  - Action buttons: Start / Stop / Pause / Redistribute

## 6. Mode 3: Intelligence

### Left Panel Content
1. **Period selector:** 7d / 30d / 90d dropdown
2. **Overlay layers** — checkboxes to toggle:
   - Subscriber growth (gradient on regions)
   - Channel density (heatmap)
   - Competitive activity (orange zones from spam_score)
   - ROI by region (numeric labels)
   - Our comments (heatmap of our farm activity)
3. **Top growing niches** — ranked list with % change and trend arrow
4. **Top channels by growth** — ranked by subscriber delta in period
5. **AI Recommendations** — route_ai_task("intelligence_insight")
   - 2-3 sentence insight about opportunities
   - Action button: "Добавить в ферму"

### Globe Overlays (toggleable)
- **Subscriber growth:** Color gradient on regions
  - Green = positive growth, Red = decline
  - Intensity = magnitude of change
- **Channel density:** Heatmap layer
  - Brightness = concentration of channels
- **Competitive activity:** Orange semi-transparent zones
  - Based on aggregated spam_score data
- **ROI by region:** Floating labels on regions with ROI%
- **Our comments:** Blue-green heatmap of farm activity coverage

### Right Panel — Region/Niche Analytics
- **On region click:**
  - Region name + flag
  - Channel count in region
  - Average ER
  - Top-3 categories (mini pie chart)
  - Growth line chart for selected period
  - "Uncovered channels" — channels not in farms
- **On channel click:** Discovery detail + subscriber trend sparkline

## 7. Interactions & Animations

### Globe
- Smooth fly-to animation on channel/region select (1s ease-out)
- Cluster split animation on zoom-in (300ms)
- Mode transition: globe dots re-color with 500ms crossfade
- Auto-rotate when idle (5 RPM), stops on interaction

### Panels
- Right panel: slide-in from right, 300ms ease-out
- Left panel accordion: 200ms expand/collapse
- Filter changes: globe updates with 200ms transition

### Hover
- Mini-card appears after 300ms hover delay
- Fades in over 150ms
- Follows cursor with 8px offset
- Max 200px wide

### Mobile (< 768px)
- Left panel → hamburger drawer (all modes)
- Right panel → bottom sheet (3 snap: peek 30vh, half 60vh, full 90vh)
- Clusters slightly larger for touch (min 44px tap target)
- No hover behavior, tap only

**Farm Control mobile:**
- Farm list in hamburger drawer, simplified (no expanded stats by default)
- Arc lines disabled on mobile (performance)
- Tap farm → channels highlight on globe, bottom sheet shows farm summary
- Live stats condensed to 1-line bar at top of drawer

**Intelligence mobile:**
- Overlay toggles in hamburger drawer
- Heatmap rendered at lower resolution (2x larger grid cells)
- AI recommendations shown as a card at top of bottom sheet
- Growth ranking list scrollable in drawer
- Region analytics in bottom sheet on tap

## 8. Data Requirements

### Existing Endpoints (minor changes noted)
- `GET /v1/channel-map/geo` — compact geo data for globe
- `GET /v1/channel-map/categories` — category list
- `GET /v1/channel-map/stats` — global stats
- `GET /v1/channel-map/{id}` — channel detail
- `GET /v1/channel-map/{id}/similar` — similar channels
- `POST /v1/channel-map/search` — full-text search
- `POST /v1/channel-map/bulk-action` — farm/blacklist/track actions
- `GET /v1/channel-map/telemetry` — HUD telemetry cards
  - **CHANGE NEEDED:** update mode regex from `^(intel|farm|analytics)$` to `^(discovery|farm|intelligence)$`
  - Mode name mapping: `intel` → `discovery`, `analytics` → `intelligence`, `farm` stays `farm`

### New Endpoints Needed
- `GET /v1/channel-map/viewport` — channels in bounding box (for "top in viewport")
  - Params: `sw_lat, sw_lng, ne_lat, ne_lng, limit`
- `GET /v1/channel-map/clusters` — pre-computed clusters at zoom level
  - Params: `zoom, category, language`
  - Returns: `[{lat, lng, count, dominant_category, avg_members}]`
  - Max 100 clusters at zoom 0-2, max 300 at zoom 3-4
  - Clustering approach: server-side pre-aggregation; rendered via react-globe.gl `htmlElementsData` as numeric circle overlays (max 100 DOM elements at any time)
  - At zoom 5+: switch from clusters to individual `pointsData` (WebGL, no DOM)
- `GET /v1/farm/map-overlay` — farm status for globe overlay
  - Returns: `{channel_id, farm_id, status, last_comment_at, account_id}`
- `GET /v1/farm/{id}/arcs` — account→channel connections for arc lines
  - Returns: `{proxy_lat, proxy_lng, channel_lat, channel_lng, intensity, status, account_name}`
  - Note: arc origin = proxy geo-location (new lat/lng on proxies table)
- `GET /v1/intelligence/growth` — subscriber growth by region/category
  - Params: `period` (7d/30d/90d)
  - Returns: `[{region, category, growth_pct, channel_count}]`
  - Depends on `channel_map_snapshots` table (daily member_count history)
  - Growth = (latest_count - count_at_period_start) / count_at_period_start * 100
- `GET /v1/intelligence/heatmap` — density/activity heatmap data
  - Params: `layer` (density/activity/competitive)
  - Returns: `[{lat, lng, intensity}]`
- `GET /v1/intelligence/recommendations` — AI-powered insights
  - Returns: `{insights: [{text, action, channels}]}`

### New DB Columns/Tables

**New migration required:**

1. **`channel_map_snapshots`** — historical subscriber counts for growth tracking
   - `id` (PK), `channel_id` (FK → channel_map_entries), `member_count` (int), `recorded_at` (datetime)
   - Index on `(channel_id, recorded_at)`
   - Populated by a scheduled job (daily cron or background task)
   - Required for Intelligence mode growth endpoints

2. **`lat`/`lng` columns on `proxies` table** — geo-coordinates for account arc lines
   - Derived from proxy country/city using a server-side lookup table
   - Account arcs originate from the proxy's geo-location
   - If proxy has no geo → fallback to region centroid (e.g., Moscow for RU)

3. **Spatial index on `channel_map_entries`:**
   - `CREATE INDEX ix_channel_map_entries_lat_lng ON channel_map_entries (lat, lng)`
   - Required for viewport bounding-box queries at scale (10K+ channels)

4. **AI router task registration:**
   - Add `intelligence_insight` → worker tier in `core/ai_router.py` routing table
   - Input: `{region, category, period, top_channels_json}`
   - Output: `{insights: [{text: str, action: str, channel_ids: list[int]}]}`

**Existing tables used (no changes):**
- `channel_map_entries` (channels)
- `farms` + `farm_threads` (farm status)
- `accounts` (account status)
- `analytics_events` (activity data)

**Potential optimization:** materialized view for cluster aggregation at zoom levels 0-2

## 9. Component Architecture

### New Components
```
frontend/src/components/channel-map/
  ├── Globe.tsx                    (MODIFY — add cluster layer, arc layer)
  ├── ChannelDetailPanel.tsx       (MODIFY — add farm activity section)
  ├── SearchOverlay.tsx            (KEEP)
  ├── MobileBottomSheet.tsx        (KEEP)
  ├── BreadcrumbNav.tsx            (KEEP)
  ├── FallbackStates.tsx           (KEEP)
  ├── constants.ts                 (MODIFY — add farm/intel colors)
  ├── HudModeSelector.tsx          (MODIFY → ModeTabBar.tsx)
  ├── TelemetryCard.tsx            (KEEP)
  │
  ├── clusters/
  │   ├── NumericCluster.tsx       (NEW — circle with number)
  │   └── ClusterLayer.tsx         (NEW — manages cluster rendering)
  │
  ├── discovery/
  │   ├── DiscoveryPanel.tsx       (NEW — left panel for discovery mode)
  │   ├── CategoryAccordion.tsx    (NEW — expandable category list)
  │   ├── FilterControls.tsx       (NEW — range sliders, dropdowns)
  │   ├── ViewportChannelList.tsx  (NEW — top channels in current view)
  │   └── HoverMiniCard.tsx        (NEW — floating card on hover)
  │
  ├── farm/
  │   ├── FarmPanel.tsx            (NEW — left panel for farm mode)
  │   ├── FarmCard.tsx             (NEW — single farm with stats)
  │   ├── AccountStatusList.tsx    (NEW — account status rows)
  │   ├── ArcLayer.tsx             (NEW — account→channel connections)
  │   ├── FarmDetailPanel.tsx      (NEW — right panel for farm view)
  │   └── CommentActivityFeed.tsx  (NEW — recent comments list)
  │
  ├── intelligence/
  │   ├── IntelPanel.tsx           (NEW — left panel for intel mode)
  │   ├── OverlayToggle.tsx        (NEW — checkbox list for layers)
  │   ├── GrowthRanking.tsx        (NEW — top growing niches/channels)
  │   ├── AiRecommendation.tsx     (NEW — AI insight card)
  │   ├── HeatmapLayer.tsx         (NEW — CSS/canvas heatmap overlay)
  │   └── RegionAnalyticsPanel.tsx (NEW — right panel for region view)
  │
  └── hooks/
      ├── useChannelData.ts        (MODIFY — add viewport query)
      ├── useGlobeInteraction.ts   (MODIFY — cluster click/zoom logic)
      ├── useHudMode.ts            (MODIFY → useMapMode.ts)
      ├── useFarmOverlay.ts        (NEW — farm status polling)
      ├── useIntelData.ts          (NEW — intelligence data fetching)
      └── useClusters.ts           (NEW — cluster computation/caching)
```

### Pages
- `ChannelMapPageV2.tsx` — MODIFY to orchestrate 3 modes

## 10. Out of Scope

- Real-time WebSocket updates (polling is fine for v3)
- Offline/cached globe tiles
- Custom map tiles (satellite/terrain)
- Channel comparison tool
- Export to PDF/report
- Telegram bot integration for map
- 2D flat map toggle (future consideration)

## 11. Success Criteria

1. All 3 modes functional with distinct left panel, globe overlay, and right panel
2. Cluster zoom animation smooth at 60fps
3. Hover mini-card appears within 300ms
4. Category filter updates globe within 200ms
5. Farm arc lines render correctly with status colors
6. Intelligence heatmap toggles without lag
7. Mobile bottom sheet works for all 3 modes
8. All existing 16 endpoints still work
9. New endpoints return data within 200ms
10. No regression in existing channel-map functionality
