# Best Practices for AI Telegram Commenting

Last updated: 2026-03-23

## How Neuro-Commenting Works (Technical Pipeline)

### Standard Pipeline (All Competitors Follow This)

1. **Channel Subscription**: Account joins target channels (via parser-generated list)
2. **Post Monitoring**: Real-time monitoring of new posts in subscribed channels via Telegram MTProto API
3. **Content Extraction**: New post text is fetched and preprocessed
4. **AI Analysis**: Post content sent to LLM (usually ChatGPT API) with a custom prompt
5. **Comment Generation**: LLM generates context-aware comment matching post topic and tone
6. **Delay Simulation**: Human-like delay before posting (configurable, typically 30s-5min after post)
7. **Comment Posting**: Comment sent via userbot session to the channel's discussion group
8. **Error Handling**: If banned from channel, auto-unsubscribe and move to next

### Key Technical Decisions

| Decision | Common Approach | Best Practice |
|----------|----------------|---------------|
| MTProto Library | Telethon (Python), Pyrogram (Python), WTelegramClient (C#) | Telethon is most mature; Pyrogram is simpler API |
| AI Model | ChatGPT (GPT-3.5/GPT-4), IONet API | GPT-4 for quality, GPT-3.5-turbo for cost |
| Session Type | User accounts (NOT bot API) | StringSession for portability |
| Proxy | SOCKS5, residential | One unique residential proxy per account |
| Comment Target | Discussion group (linked to channel) | Join discussion group, not channel directly |

---

## Anti-Detection Strategies

### 1. Comment Timing & Delays

- **After post publication**: Wait 30 seconds to 5 minutes before first comment
- **Between comments**: Random delays of 2-10 seconds between different channel comments
- **Daily limits**: Cap at 100-200 comments per account per day for safety (vs 3,500 max claimed)
- **Gaussian distribution**: Use random delays following normal distribution, not uniform
- **Time-of-day variation**: Reduce activity at night (local timezone of account's phone number)

### 2. Comment Content Quality

- **Uniqueness**: Every comment must be unique -- never repeat the same text
- **Length variation**: Mix short (5-15 words) and medium (20-50 words) comments
- **Tone matching**: Match the channel's general tone (informal, professional, etc.)
- **No links in comments**: Links trigger spam filters immediately
- **Hidden advertising**: Use account bio/profile for promotion, not comment text
- **Context awareness**: Comment must actually relate to the post content
- **Emoji usage**: Light emoji use looks natural; overuse looks botlike
- **Avoid superlatives**: "Best!", "Amazing!" look like spam; nuanced reactions are better
- **Ask questions**: Questions in comments look more human and generate engagement

### 3. Account Profile Setup

- **Realistic avatar**: Use AI-generated faces (not stock photos, not celebrities)
- **Natural username**: First name + optional last name; avoid @business_name
- **Bio with CTA**: Subtle call-to-action in bio (hidden link if using Scenario B)
- **Account age**: Minimum 2 weeks before starting commenting; 1+ month preferred
- **Activity history**: Regular non-commercial activity before and during commenting
- **Personal channel**: Optional personal channel widget (like @username's channel) for traffic

### 4. Account Rotation & Management

- **Multiple accounts**: Use 3-6 accounts minimum, rotate commenting duties
- **Stagger activity**: Not all accounts commenting on the same channels
- **Different niches**: Spread accounts across different topic areas
- **IP isolation**: One residential proxy per account -- CRITICAL
- **Device fingerprinting**: Unique device_model per account (API ID matching)
- **Session persistence**: Never disconnect/reconnect frequently

### 5. Channel Management

- **Gradual subscription**: Join 2-3 new channels per day maximum
- **Niche relevance**: Only join channels related to your target audience
- **Comment-enabled**: Only join channels with open discussion groups
- **Auto-leave**: Immediately leave channels where commenting is banned
- **Channel cap**: 160-500 channels per account (avoid Telegram's 500 channel limit)
- **Quality over quantity**: Better to comment on 50 relevant channels than 500 random ones

---

## Comment Generation Prompt Engineering

### Base Prompt Structure (Compiled from competitor analysis)

```
Role: You are a regular Telegram user who comments on channel posts.

Context:
- You comment naturally, as a real person would
- Your comments are brief (1-3 sentences)
- You match the tone and topic of the post
- You never advertise anything
- You sometimes ask questions
- You use casual language appropriate to the channel

Post to comment on:
{post_text}

Generate a natural, relevant comment.
```

### Prompt Variations by Niche

- **Crypto/Finance**: More analytical, reference market trends
- **News**: Express opinions, ask follow-up questions
- **Tech**: Share personal experience, technical insights
- **Entertainment**: Humor, reactions, personal stories
- **Business**: Professional tone, agree/disagree thoughtfully

### Key Prompt Rules

1. **No self-promotion** in comments (use profile/bio instead)
2. **Vary response length** (2 words to 2 sentences)
3. **Include occasional typos** or informal speech for naturalness
4. **React to specific details** in the post, not just general topic
5. **Use platform-specific language** (Telegram-style, not formal writing)

---

## Traffic Conversion Strategy

### How Neuro-Commenting Converts to Subscribers

1. User sees AI-generated comment under a relevant channel post
2. Comment is interesting/provocative enough to check who wrote it
3. User clicks on the commenter's profile
4. Profile has:
   - Attractive avatar
   - Bio with value proposition or link
   - Personal channel widget (optional)
5. User visits the promoted channel
6. If channel content is good, user subscribes

### Conversion Optimization

- **Profile > Comment**: The comment's job is to get profile clicks, not sell directly
- **Bio link**: Use Telegram hidden links (`<a href="url">text</a>`) in HTML
- **Personal channel**: More effective than bio links for some niches
- **Comment positioning**: Being first to comment (within 1-2 minutes) gets most visibility
- **Discussion engagement**: Replying to OTHER comments (not just posting) increases visibility

---

## Risk Mitigation

### Common Ban Triggers

1. Too many comments in short time (>50/hour)
2. Identical or very similar comments across channels
3. New accounts commenting immediately after creation
4. Comments containing links or @mentions
5. Multiple accounts from same IP
6. High volume of user reports
7. VoIP phone numbers
8. Sudden activity spikes (0 comments -> 100 comments in one day)

### Recovery Strategy

1. **PeerFloodError**: Stop all activity for 24-48 hours
2. **Account restricted**: Use @SpamBot to check status, wait it out
3. **Phone ban**: Account is lost; don't try to recover
4. **Soft limit**: Reduce volume, increase delays, wait 7 days

### Anti-Ban Checklist

- [ ] Account aged minimum 2 weeks before commenting
- [ ] Profile fully set up (avatar, name, bio, username)
- [ ] Residential proxy (unique per account)
- [ ] Gradual ramp-up: 10 comments day 1, increase 10% daily
- [ ] Maximum 200 comments per account per day
- [ ] Random delays between actions (Gaussian distribution, mean 30s)
- [ ] No links, no @mentions in comments
- [ ] Unique comment text every time
- [ ] Night-time activity reduction
- [ ] Regular non-commenting activity (reading, reacting)

---

## Performance Metrics

### Industry Benchmarks (from competitor claims)

| Metric | Conservative | Optimistic |
|--------|-------------|-----------|
| Comments/account/day | 100-200 | 3,500 |
| Channels/account | 50-160 | 1,000 |
| New subscribers/day (3 accounts) | 10-30 | 60-100 |
| Cost per subscriber | 3-10 RUB | 1-3 RUB |
| Account survival rate | 60-80% | 90%+ |
| Comment ban rate | 5-15% of channels | <5% |

### What Determines Success

1. **Account quality**: Aged, warmed, with proper fingerprints
2. **Comment quality**: AI-generated, context-aware, unique
3. **Niche targeting**: Relevant channels with engaged audience
4. **Profile optimization**: Compelling bio and channel link
5. **Channel content**: What users find when they visit your channel
6. **Consistency**: Daily automated operation, not sporadic bursts
