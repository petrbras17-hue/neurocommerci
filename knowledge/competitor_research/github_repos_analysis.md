# GitHub Open-Source Telegram Commenting Projects

Last updated: 2026-03-23

## Summary

The open-source landscape for Telegram auto-commenting is surprisingly active, with projects ranging from simple single-file scripts to full-featured multi-account management systems. Most use Python + Telethon, with one notable C# implementation.

---

## Top Projects

### 1. Masolll/Neurocommenting (C# / .NET 9)

- **URL**: https://github.com/Masolll/Neurocommenting
- **Stars**: 1 | **Language**: C# 100%
- **Created**: Jan 2026 | **Last Updated**: Feb 2026
- **License**: MIT

#### Architecture

```
Src/
  Command/         -- Command pattern implementation (CLI)
  Telegram/        -- Telegram client logic and update handling
  Channels/        -- Channels storage and access logic
  Settings/        -- Config models (app, group, account)
  Infrastructure/  -- File system, logging, paths, proxy, console
  IONet/           -- AI HTTP logic (IONet API for comment generation)
  Program.cs       -- Entry point

Data/Config/
  Settings.json    -- General script settings
  Channels.txt     -- List of public channels
  Prompt.txt       -- AI prompt
```

#### Key Features
- Multi-account Telegram client (user accounts, NOT bot API)
- Account grouping with shared configuration and per-group proxy
- AI comment generation via IONet API (free alternative to ChatGPT)
- Asynchronous processing of Telegram updates
- Configurable delays (before join, before commenting, after comment limit)
- Skip posts with too few words
- Multiple AI model fallback (priority queue)
- Thread-safe handling of shared resources

#### Configuration (Settings.json)

| Parameter | Description |
|-----------|-------------|
| PromptTone | Tone of comment (positive, humorous, etc.) |
| MinWordsInPost | Min words in post; shorter posts are skipped |
| AccountCommentsLimit | Comment limit before mandatory delay |
| AccountChannelsJoinLimit | Max channels per account |
| DelayAfterCommentsLimit | Wait (seconds) after reaching comment limit |
| DelayBeforeJoin | Wait (seconds) before joining channel |
| DelayBeforeCommenting | Wait (seconds) before commenting on new post |
| SkipPostsBeforeCommenting | Number of posts to skip before commenting |
| IONetApiKey | API key for IONet AI |
| ProxyForIONet | SOCKS5 proxy for IONet API |
| AiModels | Prioritized AI model list with fallback |

#### Modes
1. **Start Neurocommenting** -- Main mode: monitor updates and comment
2. **Join Channels** -- Auto-join discussion chats from Channels.txt
3. **Add New Account** -- Add account to a group
4. **Create New Group** -- New group with unique proxy
5. **Runtime Information** -- Show current account status

#### Tech Stack
- .NET 9.0 runtime
- WTelegramClient (Telegram API library for C#)
- IONet API for AI generation (free)
- StarkSoftProxy for SOCKS5

#### What We Can Learn
- **IONet API** as free AI alternative to ChatGPT -- worth investigating
- **Account grouping by proxy** is a clean architecture pattern
- **Comment limit + delay system** is essential for anti-ban
- **Post skipping** (SkipPostsBeforeCommenting) is smart -- avoids commenting on every single post
- **MinWordsInPost** filter prevents commenting on stickers/images/short posts
- Command pattern for CLI is overkill but extensible

---

### 2. Delafault/TelegramAutoCommentor (Python / Telethon)

- **URL**: https://github.com/Delafault/TelegramAutoCommentor
- **Stars**: 6 | **Language**: Python 97.6%, Batchfile 2.4%
- **Created**: Sep 2023 | **Last Updated**: Feb 2024
- **License**: MIT

#### Architecture
- Single file: `main.py` + `settings.ini`
- Uses Telethon for Telegram API
- Uses OpenAI API for comment generation
- Simple configuration via INI file

#### Key Features
- Simplest possible implementation
- Automatically comments on new posts in channels
- ChatGPT-powered comment generation
- Configurable GPT settings (model, temperature, etc.)
- Install & start batch file for easy setup

#### What We Can Learn
- **Simplicity**: Proves the concept can work with a single Python file
- **settings.ini approach**: Easy for non-technical users
- **Batch file installer**: Good UX for Windows users
- **Most starred** purely commenting repo (6 stars)

---

### 3. findwannawhy/tg-commenting-bot-v2 (Python)

- **URL**: https://github.com/findwannawhy/tg-commenting-bot-v2
- **Stars**: 1 | **Language**: Python
- **Created**: Feb 2026 | **Last Updated**: Feb 2026

#### Key Features
- AI-powered commenting bot with GPT
- Ad filtering (can ignore sponsored posts)
- Web dashboard for management
- Version 2 (implies iteration and improvement)

#### What We Can Learn
- **Web dashboard** is a differentiator for open-source projects
- **Ad filtering** is a practical feature -- avoid commenting on ads
- **v2 designation** suggests active development and learning from v1

---

### 4. asintiko/userbot-manager (Python + Telegram Mini App)

- **URL**: https://github.com/asintiko/userbot-manager
- **Stars**: 0 | **Language**: Python
- **Created**: Feb 2026 | **Last Updated**: Feb 2026

#### Key Features
- Telegram auto-commenting panel
- **Telegram Mini App (WebApp)** for management
- Managing automatic comments in channels
- Built with Claude AI collaboration (visible in commit history)

#### What We Can Learn
- **Telegram Mini App approach** is innovative -- management UI inside Telegram itself
- No separate web dashboard needed
- Aligns with Telegram's push for Mini Apps
- Claude-assisted development shows AI-generated code in the wild

---

### 5. artemistrator/telegram-neurocommenting

- **URL**: https://github.com/artemistrator/telegram-neurocommenting
- **Stars**: 0 | **Language**: Unknown
- **Created**: Dec 2025 | **Last Updated**: Dec 2025

#### What We Can Learn
- Direct "neurocommenting" keyword in repo name
- Relatively recent project

---

### 6. SastaDev/Auto-Channel-Comment-Telegram-User-Bot (Python)

- **URL**: https://github.com/SastaDev/Auto-Channel-Comment-Telegram-User-Bot
- **Type**: Telegram userbot for auto-commenting on new channel posts
- **Requirements**: Python 3.6+

#### What We Can Learn
- Basic implementation of channel post monitoring
- Uses Telethon
- Good reference for understanding the minimum viable commenting bot

---

### 7. Other Notable Repos

| Repo | Description | Language | Stars |
|------|-------------|----------|-------|
| coss1333/Telegram-auto-commenter | Telegram "Auto-Commentator" project | Unknown | 0 |
| Gran95/TelegramAutoCommenter | Auto commenter | Unknown | 0 |
| Ag3nt-47/TelegramAutoCommentBot | AI-thinking comment bot with README | Unknown | 0 |
| Mrcocacola21/Telegram_Auto_CommentReaction | Auto comment + reaction on public channels | Python | 0 |
| yltcode/telegram-auto-comment | Auto commenting on posts | Unknown | 0 |
| kristrofimovaa/telegram_comment_bot | Comment bot | Unknown | 0 |
| MityaiGer/telegram-comment-bot | Comment bot | Unknown | 0 |
| sirazh/telegram-comment-bot | Comment bot | Unknown | 0 |
| nehuenchik/telegram-comment-bot | Comment bot (Feb 2026) | Unknown | 0 |

---

## Technology Distribution

| Technology | Count | Examples |
|------------|-------|---------|
| Python + Telethon | ~70% | Most repos |
| Python + Pyrogram | ~10% | Some newer repos |
| C# + WTelegramClient | ~5% | Masolll/Neurocommenting |
| Unknown | ~15% | Various small repos |

## AI API Usage

| AI Provider | Count | Notes |
|-------------|-------|-------|
| OpenAI ChatGPT | ~60% | Most common, paid |
| IONet API | ~5% | Free alternative (Masolll) |
| Not specified | ~35% | Many repos don't specify |

## Key Takeaways for Our Project

1. **Python + Telethon dominates** the open-source space -- our tech stack (Python + Telethon) is well-aligned
2. **No mature, full-featured open-source solution** exists -- opportunity for differentiation
3. **Most repos are MVPs** with 0-6 stars, minimal documentation
4. **Web dashboard** and **Telegram Mini App** approaches for management are emerging
5. **Multi-account support** is table stakes but rarely well-implemented in open source
6. **IONet API** offers free AI generation -- worth evaluating as ChatGPT alternative/fallback
7. **Ad filtering** is a practical feature nobody has well-implemented
8. **Account grouping by proxy** (Masolll's approach) is a clean pattern to adopt
9. **C# implementation exists** but Python ecosystem is much richer for Telegram automation
10. **Claude-assisted development** is already being used to build these tools
