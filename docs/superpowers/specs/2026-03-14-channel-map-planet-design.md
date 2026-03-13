# Channel Map Planet — "Mission Control" Design Spec

**Date:** 2026-03-14
**Status:** Approved
**Author:** Claude Code + User brainstorming session
**Approach:** react-globe.gl (Approach A)

## 1. Overview

Replace the current 2104-line monolithic ChannelMapPage.tsx (custom R3F globe with hardcoded continent coordinates) with a modular "Mission Control" interface built on `react-globe.gl`. The new page features a SpaceX-inspired 3D globe with H3 hex-bin clustering, federated data from multiple channel catalogs (local DB + TGStat + Telemetr.io), drill-down zoom navigation, switchable HUD telemetry modes, and a slide-in channel detail panel.

**Key goals:**
- Visual WOW + full data functionality (user chose "both at maximum")
- Beat GramGPT's 400K channel bubble map with a 3D planet supporting 1M+ channels
- SpaceX Mission Control aesthetic with telemetry panels
- Drill-down zoom + slide-in detail panel interaction model
- Adaptive mobile (globe 40% top, list 60% bottom)
- Three switchable HUD modes: Channels Intel / Farm Ops / Analytics

## 2. Component Architecture

```
ChannelMapPage (container, ~150 lines)
├── MissionControlHUD (top bar, 64px)
│   ├── HudModeSelector ("Channels Intel" | "Farm Ops" | "Analytics")
│   ├── TelemetryCards (4-6 cards per mode, glassmorphism)
│   └── SearchBar (Cmd+K global channel search)
│
├── GlobeViewport (center, 60-70% of screen)
│   ├── ReactGlobe (react-globe.gl wrapper)
│   │   ├── HexBinLayer (H3 clustering at zoom-out)
│   │   ├── PointsLayer (individual channels at zoom-in)
│   │   ├── ArcsLayer (channel relationships)
│   │   └── RingsLayer (pulse on active channels)
│   ├── BreadcrumbNav ("Planet → CIS → Russia → Crypto")
│   └── ZoomControls (+/- and reset)
│
├── ChannelDetailPanel (slide-in right, 380px)
│   ├── ChannelHeader (avatar, name, subscribers)
│   ├── ChannelStats (ER, posts/day, language, category)
│   ├── ChannelTimeline (last 5 posts)
│   ├── SimilarChannels (AI recommendations)
│   └── ActionButtons (add to farm, blacklist, open in TG)
│
└── MobileBottomSheet (replaces slide-in on mobile)
    ├── GlobeCompact (top 40%)
    └── ChannelList (bottom 60%, pull-up)
```

### File structure

```
frontend/src/pages/ChannelMapPage.tsx                (~150 lines, container)
frontend/src/components/channel-map/
  ├── Globe.tsx                                      (~200 lines, react-globe.gl wrapper)
  ├── MissionControlHUD.tsx                          (~180 lines, HUD + telemetry)
  ├── ChannelDetailPanel.tsx                         (~200 lines, slide-in)
  ├── HudModeSelector.tsx                            (~60 lines)
  ├── TelemetryCard.tsx                              (~40 lines)
  ├── SearchOverlay.tsx                              (~100 lines)
  ├── BreadcrumbNav.tsx                              (~50 lines)
  ├── hooks/
  │   ├── useChannelData.ts                          (API + federation)
  │   ├── useGlobeInteraction.ts                     (zoom, drill-down, selection)
  │   └── useHudMode.ts                              (mode switching + telemetry)
  └── constants.ts                                   (categories, colors, regions)
```

Total: ~1000 lines replacing 2104 lines. Modular, testable.

## 3. Globe Engine Configuration

### Visual style (SpaceX Mission Control)

```typescript
// Globe material — dark planet with subtle glow
globeMaterial = new MeshPhongMaterial({
  color: '#1a1a2e',
  emissive: '#0a0a0b',
  shininess: 25,
  transparent: true,
  opacity: 0.95,
})

// Atmosphere — neon green accent
atmosphereColor = '#00ff88'
atmosphereAltitude = 0.18

// Background — deep space
backgroundColor = '#0a0a0b'
```

### Data layers (4 layers, zoom-dependent)

| Zoom level | Layer | Display |
|---|---|---|
| 0-2 (far) | hexBinPointsData | H3 hexagons by region, color = channel density, column height = avg ER |
| 2-4 (medium) | pointsData | Channel dots in groups, size = subscribers, color = category |
| 4+ (close) | pointsData + htmlElementsData | Individual channels with labels (name + category icon) |
| any | arcsData | Green arcs between related channels (cross-posting, same owner) |

### Interaction model

- Hover on hexagon → tooltip: "Russia / Crypto — 1,247 channels, avg ER 3.2%"
- Click on hexagon → camera animate zoom-in → hexagon expands into points
- Click on channel point → slide-in panel with details
- Right-click on channel → context menu: "Add to farm", "Blacklist", "Open in TG"
- Double-click → deeper zoom
- Scroll → zoom in/out
- Drag → rotate planet

### Drill-down flow

```
Planet (all channels, hex clusters)
  → click hex → Region view (CIS, Europe, Asia...)
    → click sub-hex → Country/City view (individual channels)
      → click channel → slide-in ChannelDetailPanel
```

Breadcrumb updates: `Planet` → `Planet / CIS` → `Planet / CIS / Crypto`

### Performance

- 100K+ channels: `pointsMerge={true}` merges geometry into single draw call
- Zoom-out: H3 resolution 3 → 100K channels collapse into ~300 hexagons
- Target: smooth 60fps on mid-range hardware

## 4. Mission Control HUD — Three Modes

### Glassmorphism card style

```css
background: rgba(10, 10, 11, 0.75);
backdrop-filter: blur(12px);
border: 1px solid rgba(0, 255, 136, 0.15);
border-radius: 12px;
box-shadow: 0 0 20px rgba(0, 255, 136, 0.05);
```

Typography: numbers in JetBrains Mono, labels in Geist Sans. Green flash animation on data update.

### Mode 1: "Channels Intel" (default)

| Position | Card | Data |
|---|---|---|
| Top-left 1 | Total Channels | count + delta per 24h |
| Top-left 2 | Live Coverage | country count + progress bar |
| Top-right 1 | Avg ER | percentage + 7-day sparkline |
| Top-right 2 | Top Category | name + percentage + mini pie |
| Bottom-left | Parsing Pipeline | running jobs / queue / rate |
| Bottom-right | Trending | top-3 growing categories this week |

### Mode 2: "Farm Ops"

| Position | Card | Data |
|---|---|---|
| Top-left 1 | Active Threads | count/max + utilization bar |
| Top-left 2 | Comments/hr | count + sparkline |
| Top-right 1 | Delivery Rate | percentage + trend arrow |
| Top-right 2 | Account Health | avg score + mini distribution |
| Bottom-left | Live Feed | last 5 comments (channel → text, scroll) |
| Bottom-right | Alerts | FloodWait, mutes, bans in last hour |

Globe overlay: arcs show comments flying in real-time (account → channel), pulse rings on channels being commented now.

### Mode 3: "Analytics"

| Position | Card | Data |
|---|---|---|
| Top-left 1 | ROI Score | multiplier + trend |
| Top-left 2 | Cost per Sub | dollar amount + comparison vs paid ads |
| Top-right 1 | Weekly Growth | subscriber count across managed channels |
| Top-right 2 | Best Performing | top channel by conversion |
| Bottom-left | Heatmap | best hours for commenting (7-day data) |
| Bottom-right | A/B Styles | winning style + conversion rate |

Globe overlay: heatmap — regions colored by ROI (green = high, red = low).

## 5. Federated Data Layer — Backend

### Data source architecture

```
Frontend (useChannelData hook)
  → GET /v1/channel-map?bbox=...&zoom=...&category=...&mode=hex|points
    → Backend aggregator (ops_api.py)
      ├── 1. Local DB (channel_map_entries) — our channels, instant
      ├── 2. TGStat API — 500K+ channels, cached 24h
      ├── 3. Telemetr.io API — alternative catalog, cached 24h
      └── 4. Background Parser — continuously enriches local DB
```

### New API endpoints

```
GET  /v1/channel-map                          — globe data (hex bins or points)
     ?zoom=3&bbox=lat1,lon1,lat2,lon2&category=Crypto&q=term&mode=hex|points&limit=500

GET  /v1/channel-map/{id}                     — channel details + stats + recent posts + similar

GET  /v1/channel-map/telemetry?mode=intel|farm|analytics  — HUD card data

GET  /v1/channel-map/search?q=crypto&sources=local,tgstat,telemetr  — federated search

POST /v1/channel-map/bulk-action              — { channelIds, action: add_to_farm|blacklist|whitelist }

GET  /v1/channel-map/{id}/similar?limit=5     — AI-powered similar channels
```

### Caching strategy

| Source | Cache TTL | Strategy |
|---|---|---|
| Local DB | 0 (realtime) | Direct SQL with RLS |
| TGStat API | 24 hours | Redis hash by bbox+zoom |
| Telemetr.io | 24 hours | Redis hash by bbox+zoom |
| Hex aggregates | 1 hour | Pre-computed H3 bins in Redis |
| Telemetry cards | 5 minutes | Redis key per mode per tenant |

### Deduplication

Channels from different sources merged by `username` or `telegram_id`. Local record always takes priority (has more data: blacklist status, ER history, farm stats).

### Background enrichment pipeline

Every hour:
1. Parser adds new channels → channel_map_entries
2. channel_indexer.py updates metadata (subscribers, language, spam_score)
3. H3 hex bins recomputed in Redis
4. Telemetry counters updated

## 6. Channel Detail Panel

### Layout (380px width, slide-in from right)

```
┌─────────────────────────────────┐
│ [X]                    [@open_tg]│
│  ┌──────┐  CryptoDaily           │
│  │avatar│  @cryptodaily_ru       │
│  └──────┘  ₿ Crypto  🇷🇺 RU     │
│  247K subs  ER 3.2%  14 posts/d  │
├──────────────────────────────────┤
│  Stats                           │
│  [subscribers sparkline 30d]     │
│  Avg views: 45K   Avg reacts: 2K│
│  Spam score: 12%  Quality: A     │
├──────────────────────────────────┤
│  Recent Posts (3)                │
│  "Биткоин пробил 95K..."        │
│  2h ago · 👁 34K · 💬 127       │
├──────────────────────────────────┤
│  Similar Channels (AI)          │
│  CryptoNews  198K  ER 2.8%  →   │
│  BitBoss     92K   ER 4.1%  →   │
├──────────────────────────────────┤
│  Actions                         │
│  [Add to farm] [Blacklist]       │
│  [Add to DB]   [Copy link]       │
│  [Track]       [Tag]             │
└──────────────────────────────────┘
```

### Similar channels logic

SQL-based scoring: same category + language + subscriber range overlap + topic_tags JSONB cosine similarity. No ML model required.

### Action integrations

- "Add to farm" → POST /v1/farm/{active_farm_id}/channels
- "Blacklist" → POST /v1/channel-db/{db_id}/channels/{id}/blacklist
- "Add to DB" → POST /v1/channel-db/{db_id}/import (single channel)
- "Track" → subscribe to changes with Telegram digest notification
- "Tag" → PATCH /v1/channel-map/{id} with custom tags

## 7. Mobile Adaptive

### Layout (< 768px)

- Globe: top 40vh, reduced particles, no arcs, no htmlElements, pointResolution=8
- Touch: pinch zoom, swipe rotate, tap = select
- Bottom sheet: 3 positions via Framer Motion useDragControls — peek (30%), half (60%), full (90%)
- Tap channel in list → bottom sheet switches to ChannelDetail view
- HUD: compact 1-row with mode tabs + 2 key metrics only
- Landscape: globe left 50%, list right 50%

### Search overlay

- Cmd+K on desktop, search icon tap on mobile
- Debounced input (300ms)
- Results grouped: channels → categories → regions
- Select channel → globe flies to it + opens detail panel
- Select category → globe filters (others dim)
- Select region → globe rotates + zooms
- ESC closes

## 8. Error Handling

| Situation | Behavior |
|---|---|
| WebGL unavailable | Fallback: 2D CSS grid by category + banner |
| Federated API timeout | Show local DB only + subtle badge |
| Empty database (0 channels) | Spinning empty globe + CTA "Run parser to populate" |
| Mobile low-end (< 4GB RAM) | Auto-detect → COBE dot globe (5KB) |
| Network loss | Offline badge + cached last state in localStorage |

## 9. Dependencies

### New npm packages
- `react-globe.gl` — globe engine
- `h3-js` — H3 hexagonal indexing (peer dep of react-globe.gl)

### Existing packages (already in project)
- `three` — Three.js (already used by current R3F implementation)
- `framer-motion` — animations
- `lucide-react` — icons

### Packages to remove (replaced by react-globe.gl)
- `@react-three/fiber` — no longer needed for globe (may keep if used elsewhere)
- `@react-three/drei` — same as above

## 10. Design Tokens

```
Background:       #0a0a0b
Surface:           rgba(10, 10, 11, 0.75)
Border:            rgba(0, 255, 136, 0.15)
Accent primary:    #00ff88
Accent secondary:  #00d4ff
Text primary:      #ffffff
Text secondary:    #70777b
Globe body:        #1a1a2e
Globe emissive:    #0a0a0b
Atmosphere:        #00ff88
Font mono:         JetBrains Mono
Font sans:         Geist Sans
Border radius:     12px
Blur:              12px
Shadow:            0 0 20px rgba(0, 255, 136, 0.05)
```

## 11. Category Colors (from existing implementation)

```
Crypto:        #f59e0b
Marketing:     #3b82f6
E-commerce:    #10b981
EdTech:        #6366f1
News:          #8b5cf6
Entertainment: #ec4899
Tech:          #14b8a6
Finance:       #0ea5e9
Lifestyle:     #f97316
Health:        #22c55e
Gaming:        #e11d48
18+:           #ef4444
Politics:      #dc2626
Sports:        #eab308
Travel:        #06b6d4
Business:      #84cc16
Science:       #a855f7
Music:         #f472b6
Food:          #34d399
AI/ML:         #d946ef
Cybersecurity: #22d3ee
```

## 12. Competitive Advantage vs GramGPT

| Dimension | GramGPT Channel Map | Our Channel Map Planet |
|---|---|---|
| Visualization | 2D bubble map | 3D interactive globe |
| Channels | 400K | 1M+ (federated) |
| Categories | 103 + 646 micro | 21 + expandable via AI |
| Interaction | Click → list | Drill-down zoom + slide-in panel |
| Data sources | Single DB | Federated (local + TGStat + Telemetr.io) |
| Telemetry | None | 3-mode Mission Control HUD |
| Search | Basic filter | Cmd+K federated search |
| Actions | View only | Add to farm, blacklist, track, tag |
| Mobile | "Use desktop only" | Adaptive 40/60 split |
| Real-time | Static | Live arcs, pulse rings, feed |
| AI | None | Similar channels, trending, predictions |
