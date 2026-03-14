# Channel Map v3 — Implementation Sprint Plan

Date: 2026-03-14
Design spec: `docs/superpowers/specs/2026-03-14-channel-map-v3-design.md`
Branch: `main` (feature branch: `feature/channel-map-v3`)
Baseline: `71759e1`

## Overview

Transform Channel Map from demo globe into professional 3-mode marketing intelligence tool.
5 micro-sprints, each deployable independently. Total: ~3-4 sessions.

---

## Micro-Sprint 1: Foundation — Layout + Discovery Mode (core)

**Goal:** Replace current ChannelMapPageV2 with new 3-panel layout. Discovery mode fully functional.

### Tasks

#### 1.1 Backend: New endpoints + migrations
- [ ] Migration: `CREATE INDEX ix_channel_map_entries_lat_lng ON channel_map_entries (lat, lng)`
- [ ] `GET /v1/channel-map/viewport` — channels in bounding box (sw_lat, sw_lng, ne_lat, ne_lng, limit)
- [ ] `GET /v1/channel-map/clusters` — server-side cluster aggregation (zoom, category, language)
  - Grid-based clustering: divide lat/lng into cells based on zoom level
  - Return: `[{lat, lng, count, dominant_category, avg_members}]`
  - Max 100 clusters at zoom 0-2, max 300 at zoom 3-4
- [ ] Update telemetry endpoint mode regex: `^(intel|farm|analytics)$` → `^(discovery|farm|intelligence)$`

#### 1.2 Frontend: Layout shell
- [ ] Create `ModeTabBar.tsx` (replaces HudModeSelector) — 3 tabs: Discovery / Farm Control / Intelligence
- [ ] Refactor `ChannelMapPageV2.tsx` — 3-panel layout: left (320px) + globe (flex) + right (380px, hidden)
- [ ] Right panel slide-in/out animation (framer-motion)
- [ ] Mode state management: `useMapMode.ts` (replaces useHudMode)
- [ ] Update `constants.ts` — add new design tokens, mode enum

#### 1.3 Frontend: Discovery left panel
- [ ] `DiscoveryPanel.tsx` — container for discovery mode left panel
- [ ] `FilterControls.tsx` — subscriber range slider, language/region dropdowns, comments checkbox
- [ ] `CategoryAccordion.tsx` — expandable category list with subcategories
  - Color dots from `getCategoryMeta()`
  - Channel count per category
  - Multi-select support (Ctrl+click)
  - Globe filter on category select
- [ ] `ViewportChannelList.tsx` — top 10 channels in current viewport (updates on globe rotate)

#### 1.4 Frontend: Globe cluster upgrade
- [ ] `ClusterLayer.tsx` — manages cluster data fetching + rendering
- [ ] `NumericCluster.tsx` — circle with number, rendered via `htmlElementsData`
- [ ] `useClusters.ts` — fetch clusters from server, debounce on zoom/rotate
- [ ] Cluster click → zoom in + split animation
- [ ] At zoom 5+: switch from clusters to individual `pointsData`

#### 1.5 Frontend: Hover mini-card + detail panel
- [ ] `HoverMiniCard.tsx` — floating card on 300ms hover (title, subscribers, ER, category badge)
- [ ] Update `ChannelDetailPanel.tsx` — badges row, 2x2 stats grid, description, similar channels, action buttons
- [ ] Click channel → fly-to + open right panel
- [ ] Close button → hide right panel

#### 1.6 Tests + deploy
- [ ] Test: cluster endpoint returns correct aggregation
- [ ] Test: viewport endpoint filters by bounding box
- [ ] Test: mode switch doesn't crash
- [ ] `tsc --noEmit` clean
- [ ] Deploy to VPS, smoke-test

**Definition of done:** Discovery mode fully works — filters, categories, clusters, hover cards, detail panel. Other modes show placeholder "Coming soon".

---

## Micro-Sprint 2: Farm Control Mode

**Goal:** Farm Control mode with live farm status on globe + arc lines.

### Tasks

#### 2.1 Backend: Farm overlay endpoints
- [ ] Migration: add `lat`/`lng` Float columns to `proxies` table
  - Populate from proxy country/city using lookup table (28 Russian cities + major world cities)
- [ ] `GET /v1/farm/map-overlay` — farm status for globe
  - Returns: `[{channel_id, farm_id, farm_name, status, last_comment_at, account_name}]`
  - Joins: farm_threads → channel_map_entries → farms → accounts
  - Tenant-scoped
- [ ] `GET /v1/farm/{id}/arcs` — account→channel connections
  - Returns: `[{proxy_lat, proxy_lng, channel_lat, channel_lng, intensity, status, account_name}]`
  - Intensity = comments in last 24h for this account→channel pair

#### 2.2 Frontend: Farm left panel
- [ ] `FarmPanel.tsx` — left panel container for farm mode
- [ ] `FarmCard.tsx` — accordion per farm (status badge, threads, comments/hr, bans, channels, accounts)
  - Click farm → highlight channels on globe
- [ ] `AccountStatusList.tsx` — account rows with status dots (active/cooldown/quarantine/dead)
- [ ] Live stats section (total comments today, success rate, avg response time)
- [ ] `useFarmOverlay.ts` — poll farm status every 10s

#### 2.3 Frontend: Globe farm overlay
- [ ] Channel dots recolored by farm status (#00ff88 active, #ffaa00 pause, #ff4444 ban, #333 not in farm)
- [ ] Pulsing animation on active channels (CSS animation on htmlElementsData)
- [ ] `ArcLayer.tsx` — curved lines from proxy to channels via `arcsData` prop
  - Thickness = intensity
  - Color = status
  - Show on farm select, hide on deselect

#### 2.4 Frontend: Farm detail panel
- [ ] `FarmDetailPanel.tsx` — right panel for farm click (24h sparklines, thread list, action buttons)
- [ ] `CommentActivityFeed.tsx` — last 5 comments when clicking a channel in farm mode
- [ ] Action buttons: Start / Stop / Pause / Redistribute

#### 2.5 Tests + deploy
- [ ] Test: farm overlay endpoint returns correct status
- [ ] Test: arc endpoint joins correctly
- [ ] Test: mode switch between Discovery ↔ Farm preserves globe camera
- [ ] Deploy to VPS

**Definition of done:** Farm Control mode shows live farm status on globe, arc lines connect accounts to channels, farm detail panel works.

---

## Micro-Sprint 3: Intelligence Mode

**Goal:** Intelligence mode with toggleable analytical overlays.

### Tasks

#### 3.1 Backend: Intelligence endpoints + snapshot infrastructure
- [ ] Migration: create `channel_map_snapshots` table
  - `id`, `channel_id` (FK), `member_count` (int), `recorded_at` (datetime)
  - Index on `(channel_id, recorded_at)`
  - FORCE RLS policy
- [ ] Background job: daily snapshot of all channel member_counts
  - Register as `enqueue_app_job("channel_snapshot")`
  - Runs once/day, iterates channel_map_entries, inserts snapshots
- [ ] `GET /v1/intelligence/growth` — subscriber growth by region/category
  - Params: `period` (7d/30d/90d)
  - Compares latest snapshot vs snapshot at period start
- [ ] `GET /v1/intelligence/heatmap` — density/activity/competitive heatmap
  - Params: `layer` (density | activity | competitive)
  - density: aggregate lat/lng into grid cells, return intensity
  - activity: aggregate analytics_events by channel location
  - competitive: aggregate spam_score by region
- [ ] Register `intelligence_insight` task in `core/ai_router.py` → worker tier
  - Input: top growing niches + uncovered channels
  - Output: `{insights: [{text, action, channel_ids}]}`
- [ ] `GET /v1/intelligence/recommendations` — calls route_ai_task

#### 3.2 Frontend: Intelligence left panel
- [ ] `IntelPanel.tsx` — container for intelligence mode
- [ ] Period selector (7d / 30d / 90d dropdown)
- [ ] `OverlayToggle.tsx` — checkbox list for 5 overlay layers
- [ ] `GrowthRanking.tsx` — top growing niches + top channels by growth
- [ ] `AiRecommendation.tsx` — AI insight card with action button
- [ ] `useIntelData.ts` — fetch growth, heatmap, recommendations

#### 3.3 Frontend: Globe intelligence overlays
- [ ] `HeatmapLayer.tsx` — canvas-based heatmap overlay on globe container
  - Positioned absolute over globe
  - Renders intensity grid as colored semi-transparent circles
  - Toggleable per overlay checkbox
- [ ] Region growth labels — floating lat/lng labels with growth % via htmlElementsData
- [ ] Activity heatmap — blue-green overlay of farm comment locations

#### 3.4 Frontend: Region analytics panel
- [ ] `RegionAnalyticsPanel.tsx` — right panel for region click
  - Region name + flag
  - Channel count, avg ER
  - Top-3 categories mini bar chart (CSS-only, no charting lib)
  - Uncovered channels list
- [ ] Channel detail in intel mode — add subscriber trend sparkline (SVG, last 30 data points)

#### 3.5 Tests + deploy
- [ ] Test: snapshot job creates correct records
- [ ] Test: growth endpoint calculates delta correctly
- [ ] Test: heatmap returns grid data
- [ ] Test: AI recommendation endpoint returns valid JSON
- [ ] Deploy to VPS

**Definition of done:** Intelligence mode works with all 5 overlay toggles, growth rankings update on period change, AI recommendations render.

---

## Micro-Sprint 4: Polish + Mobile + Performance

**Goal:** Mobile UX, animations, edge cases, performance optimization.

### Tasks

#### 4.1 Mobile responsive
- [ ] Left panel → hamburger drawer (768px breakpoint)
- [ ] Right panel → bottom sheet (keep existing MobileBottomSheet, extend for all modes)
- [ ] Farm: disable arcs on mobile, simplified farm cards in drawer
- [ ] Intelligence: lower resolution heatmap, overlay toggles in drawer
- [ ] Min 44px tap targets on clusters

#### 4.2 Animations
- [ ] Fly-to animation: 1s ease-out on channel/region select
- [ ] Cluster split: 300ms zoom-in animation
- [ ] Mode transition: 500ms crossfade on dot recoloring
- [ ] Auto-rotate: 5 RPM when idle, stop on interaction
- [ ] Right panel: 300ms slide-in ease-out

#### 4.3 Performance
- [ ] Debounce viewport query: 500ms after globe stops rotating
- [ ] Cluster cache: memoize by zoom level + active filters
- [ ] Lazy load Farm/Intelligence panels (React.lazy + Suspense)
- [ ] AbortController on all fetch hooks (cancel on mode switch)
- [ ] Profile with React DevTools: target <16ms render per frame

#### 4.4 Edge cases
- [ ] Empty state: no channels in viewport
- [ ] Error state: API failures with retry button
- [ ] Loading states: skeleton loaders for panels
- [ ] WebGL fallback: message for unsupported browsers
- [ ] No farms: Farm Control shows "Создайте первую ферму" CTA
- [ ] No snapshots yet: Intelligence shows "Данные собираются..." for growth

#### 4.5 Tests + deploy
- [ ] E2E: Playwright test — load map, click channel, verify detail panel
- [ ] E2E: switch modes, verify left panel content changes
- [ ] Performance: lighthouse audit on /app/channel-map
- [ ] Deploy to VPS

**Definition of done:** Mobile works for all 3 modes, animations are smooth, edge cases handled.

---

## Micro-Sprint 5: Channel Expansion + Final QA

**Goal:** Expand channel database from 188 to 2000+, final QA pass.

### Tasks

#### 5.1 Channel import
- [ ] Run Telegram Bot API verification on `data/tgstat_usernames.txt` (2233 usernames)
- [ ] Run verification on `data/russian_telegram_channels.txt` (854 usernames)
- [ ] Import verified channels (5K+ subscribers) into PostgreSQL on VPS
- [ ] Run daily snapshot job to start building history
- [ ] Verify clusters look good at scale (2000+ channels)

#### 5.2 QA audit
- [ ] Full 3-mode walkthrough: Discovery → Farm → Intelligence
- [ ] Verify all 16 existing + 7 new endpoints work
- [ ] RLS audit: ensure all new endpoints are tenant-safe
- [ ] Security review: no SQL injection in viewport/cluster queries
- [ ] Cross-browser: Chrome, Firefox, Safari
- [ ] Mobile: iOS Safari, Android Chrome

#### 5.3 Documentation
- [ ] Update README.md with Channel Map v3 section
- [ ] Update change register
- [ ] Update CLAUDE.md if needed

**Definition of done:** 2000+ real channels on globe, all 3 modes QA'd, deployed to VPS, change register updated.

---

## Sprint Dependencies

```
Sprint 1 (Foundation + Discovery) ← no dependencies
Sprint 2 (Farm Control) ← needs Sprint 1 layout
Sprint 3 (Intelligence) ← needs Sprint 1 layout + snapshot migration
Sprint 4 (Polish + Mobile) ← needs Sprints 1-3
Sprint 5 (Expansion + QA) ← needs all above
```

Sprints 2 and 3 can run in parallel after Sprint 1.

## Risk Register

| Risk | Mitigation |
|------|------------|
| react-globe.gl htmlElementsData perf with 100+ clusters | Server pre-aggregation limits to max 100; debounce updates |
| Heatmap overlay alignment with globe rotation | Use CSS transform synced to globe camera state |
| Daily snapshot job at scale (10K+ channels) | Batch inserts, run during off-peak hours |
| Arc layer with many connections | Limit to top 50 arcs per farm, aggregate rest |
| Intelligence AI recommendations latency | Cache results for 1h, show loading state |
