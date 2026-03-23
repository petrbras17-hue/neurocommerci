# GitHub: TelegramAutoCommentor (Open Source)

**URL:** https://github.com/Delafault/TelegramAutoCommentor
**Type:** Open Source Project
**Stars:** 6 | **Language:** Python (97.6%) | **License:** MIT
**Last Updated:** Feb 2024
**Scraped:** 2026-03-23

## Summary

The simplest open-source script for automatically commenting on newly created posts in Telegram channels. Built on Telethon with OpenAI/GPT integration for comment generation.

## Technical Stack

- **Python 3.6+**
- **Telethon** -- Telegram MTProto client library
- **OpenAI API** -- for generating comments
- **Configuration:** settings.ini file

## Setup Process

1. Download repository + install Python
2. Run `pip install -r requirements` or `install&start.bat`
3. Enter api_id and api_hash (from my.telegram.org)
4. Configure GPT settings
5. Done -- bot starts commenting

## Architecture

- Single-file design (main.py)
- Configuration via settings.ini
- Supports customizable GPT settings
- Simple event-driven: listen for new posts -> generate comment -> post

## Key Takeaways

1. **Extremely simple architecture** -- validates that basic commenting is trivial to implement
2. **Only 6 stars** -- the market wants production-ready solutions, not scripts
3. **No anti-ban features** -- no delays, no warming, no health monitoring
4. **No multi-account support** -- runs a single account
5. **No proxy support** visible in the basic setup
6. **MIT licensed** -- can study the approach
7. **Last updated Feb 2024** -- not actively maintained

## Also Found: SastaDev/Auto-Channel-Comment-Telegram-User-Bot

Another open-source userbot for auto-commenting, similar simplicity.

## Competitive Analysis

- These basic scripts show the floor of the market
- Our product is orders of magnitude more sophisticated
- Basic scripts don't survive in production (bans, crashes, no monitoring)
- The gap between "script" and "service" is where our value proposition lives
- Users who try DIY scripts quickly learn they need a managed service
