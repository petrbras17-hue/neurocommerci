# YouTube Research: Pyrogram Neurocommenting Bot in 50 Lines

> Source: https://youtu.be/XArOcNGdzvg
> Date scraped: 2026-03-23
> Published: 2024-05-01

---

## Video Metadata

- **Title**: Pyrogram нейрокомментинг бот в 50 строк | Уроки Python для новичков
- **Author**: Money Python (@money_python)
- **Channel**: https://www.youtube.com/@money_python
- **Length**: 6:51
- **Views**: ~4,865
- **Likes**: 211
- **Category**: Education
- **Published**: 2024-05-01
- **Keywords**: python, python урок для новичков, пайтон телеграм, pyrogram урок, как зарабатывать на пайтон, заработок программиста, нейрокомментарии, бот нейрокомментер
- **Telegram channel**: https://money-python.com/telegram (redirects to https://t.me/money_python_bot?start=yt_organic)

---

## Full Video Transcript (Russian)

Всем доброго времени суток, дорогие друзья! Сегодня мы с вами сделаем один интересный бот, точнее, заготовку под него, который вы сможете самостоятельно потом развивать. С помощью этого бота вы сможете привлекать бесплатный трафик в свои каналы. С помощью этого бота вы можете также привлекать трафик в чужие каналы. То есть проще говоря, продавать его как услугу. О каком боте я говорю? Я говорю о боте, который нацелен на нейрокометинг. Что это такое? Нейрокометинг это такой подход, при помощи которого многие профессии, в том числе психологи, консультанты, маркетологи и т.д., все, кто в принципе оказывают услуги консалтинга, привлекают к себе клиентов. Как это происходит? Все очень просто. Они находят целевые каналы, подписываются на них. И в том случае, если выходит новый пост, бот его комментирует и человек, когда видит какой-то интересный любой другой комент, с привлекательной аватаркой, описанием например аккаунта, переходит в этот аккаунт и видит ссылочку, ссылочку, которая ведет на целевой канал. Таким образом человек потенциально может получить нового подписчика. Как ни странно, но цены на такие сервисы достаточно высокие. Я же предлагаю сделать вам его буквально за несколько строчек кода, а доработать вы его сможете самостоятельно. В дальнейшем вы можете его продавать как услугу, подсаживать людей на банерную плату, либо раскручивать свои каналы. В общем-то способов монетизации данного бота предостаточно. Прежде чем мы начнем, поставьте лайк, подпишитесь на этот канал. И если вы понимаете, что вы хотите раскручивать свои каналы быстро, системно, прогнозируемо, что самое главное. Понимать вложение и отдачу с ваших каналов, переходите к нам в Telegram-канал. Там есть последние две очень классные статьи, в рамках которых я рассказал о том, как создавать сетку Telegram-каналов с нуля, а также о том, как прогнозировать свой рекламный бюджет. Если же вы захотите сделать это руко об руку, переходите в мастер-группу, единственную в России, с гарантией результата по раскрутке ваших Telegram-сеток. Ну а пока, начинаем. И в том случае, если она у вас не установлена, прописываем PIP, install, Пирограм. Дожидаем все установки и импортируем оставшиеся модули. Это мы импортируем Daytime, из Daytime как DT. Также мы импортируем хендлеры и фильтры, необходимые нам для работы данного бота, потому что мы должны фильтровать входящие сообщения с определенных чатов, а иди которых мы пропишем непосредственно в настройках. У меня настройки, кстати, вынесены в отдельный файл, чтобы их здесь не палить. Поэтому я буду просто импортировать их из того файла. Далее мы указываем канал, который будем комментировать. Для этого нам необходимо получить ID канала. Если вы не знаете, как получить ID канала, воспользуйтесь ботом GetMyIDBot. Также ссылочку на него я как раз оставлял в той же статье по раскрутке сеток своих телеграм-каналов. Переходите, читайте. Прописываем его сюда как int. По-моему, также поддерживается и строковый тип данных. Дальше я просто вывожу здесь принт о том, что сообщение стартовало. Мы не будем здесь заниматься каким-то лагеранием, выводом ошибок, потому что здесь на косящей практически невозможно. Сам бот занимает не более, я не знаю, там 30-50 строк для того, чтобы он уже работал и приносил какую-то пользу. Дальше мы создаем Handler. Handler будет у нас реагировать на определенные сообщения в определенных каналах, которые мы собственно укажем. И задача Handler будет отвечать на новые появившиеся сообщения в этом канале. Сам по себе Handler достаточно простой, он у нас принимает клиент и принимает месседж. Сам клиент нам тоже нужно с вами будет создать для того, чтобы он нас на это реагировал. И после того, как сам Handler уже создан, нам необходимо будет его зарегистрировать в этом клиенте. При регистрации Handler нам необходимо будет указать также фильтры. Следующее, что нам нужно будет с вами сделать, это подробить собственно нейросси. Для этого я создаю отдельный файлик и мы будем работать при помощи одной бесплатной совершенными библиотекой, которая позволит вам работать GPT-3 без всяких проксей, ключей и прочего, прочего, прочего. Еще раз напомню, это просто, скажем так, костяк бота, дорабатывать вы его сможете самостоятельно. То есть используют другие нейросси, эти другие запросы, другие формулировки запросов, другие ответы, обработку ответов, это да, это да. То есть это все я оставляю исключительно на вас. Мое дело просто показать само направление, передать вам идею, которая принесет вам или вашим клиентам деньги. После того, как мы создали эту функцию, мы импортируем ее в наш файлик main, а результат работает в функции, присоиваем переменный, который будем уже отправлять в качестве комментарий. То есть все на самом деле очень просто. Как мы видим, мы суммарно уложились, по-моему, строго в 20 кода. Понятное дело, вы можете дорабатывать функцию, можете работать с разными нейросетями и использовать какие-то свои наработки. Мое дело просто показать, как это делается на базовом уровне. Теперь проверяем, как это работает с нейросетью вместо того, чтобы отдавать готовый коммент. Получается, в принципе, неплохо, совершенно прям для старта. Остальное уже сможете докрутить. И единственное, что бы я вам указал сразу, это то, что не нужно делать первоначально. Это очень глупо. То есть в том плане, чтобы ваш комментарий оказался первым, потому что код будет реагировать мгновенно. И если ваш бот будет всплывать прямо сразу в этих комментариях на первом месте, с высокой долей вероятности его быстренько выпилить из часа. Потому что не рекомендуется, все равно палец. Они выглядят как-то не крути, какой промп не сдавай, как кусок идиотского текста. И он подает только в том случае, если у вас вот вообще нету денег, прям не копейки на продвижение собственных телеграмм каналов, тогда да, вы можете подурачиться с этим. Если же у вас есть деньги, в welcome мастер группу там уже можно с 10 тысяч рублей получать, собственно, отдачу своих телеграмм каналов.

---

## Key Technical Details (Extracted from Video)

### Library Stack

| Library | Purpose |
|---------|---------|
| **Pyrogram** | Telegram MTProto API client (userbot mode) |
| **datetime** | Timestamp handling |
| **Free GPT-3 library** (likely **g4f** / gpt4free) | AI comment generation without API keys, proxies, or registration |

### Installation
```bash
pip install pyrogram
# + free GPT library (likely: pip install g4f)
```

### File Structure
```
project/
  main.py          # Main bot logic (~20-50 lines)
  config.py        # Settings (api_id, api_hash, channel IDs) - separate file to hide credentials
  neuro.py         # AI/neural network comment generation function
```

### Architecture (Reconstructed from Transcript)

```python
# main.py (reconstructed skeleton based on video description)
from pyrogram import Client, filters
from pyrogram.handlers import MessageHandler
from datetime import datetime as DT
from config import API_ID, API_HASH, PHONE  # credentials in separate file
from neuro import generate_comment  # AI generation in separate file

# Target channel ID (get via @GetMyIDBot)
CHANNEL_ID = -100XXXXXXXXXX  # int type, also supports string

print("Bot started!")

# Create Pyrogram client (userbot, not bot API)
app = Client("my_account", api_id=API_ID, api_hash=API_HASH)

# Handler: react to new messages in specific channels
async def comment_handler(client, message):
    # Get the post text
    post_text = message.text or message.caption or ""

    # Generate AI comment using neural network
    comment = generate_comment(post_text)

    # Get discussion message to reply to (comment on channel post)
    discussion_msg = await client.get_discussion_message(
        chat_id=message.chat.id,
        message_id=message.id
    )

    # Send the comment as a reply
    await discussion_msg.reply(comment)

# Register handler with filters for specific channel(s)
app.add_handler(MessageHandler(
    comment_handler,
    filters.chat(CHANNEL_ID) & filters.channel
))

app.run()
```

```python
# neuro.py (reconstructed - free GPT-3 without keys)
# Uses a free library for GPT-3 access without API keys or proxies
# Most likely g4f (gpt4free) or similar

from g4f.client import Client as G4FClient

def generate_comment(post_text: str) -> str:
    client = G4FClient()
    response = client.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=[{
            "role": "user",
            "content": f"Прочитай пост и напиши короткий комментарий к нему: {post_text}"
        }]
    )
    return response.choices[0].message.content
```

```python
# config.py
API_ID = 12345678
API_HASH = "your_api_hash_here"
PHONE = "+7XXXXXXXXXX"
```

### Key Pyrogram Methods Used

1. **`Client()`** - Create userbot client (NOT bot API)
2. **`MessageHandler()`** - Register handler for incoming messages
3. **`filters.chat(CHANNEL_ID)`** - Filter messages from specific channel
4. **`filters.channel`** - Filter channel posts only
5. **`get_discussion_message()`** - Get discussion thread message for a channel post
6. **`.reply()`** - Reply to discussion message (= leave a comment)

### Pyrogram get_discussion_message() API

```python
# Official Pyrogram API
m = await app.get_discussion_message(channel_id, message_id)
await m.reply("comment")  # This leaves a comment on the channel post
```

- **Parameters**: `chat_id` (int|str), `message_id` (int)
- **Returns**: Message object (the first discussion message)
- **Compatible with**: Users and Bots

---

## Commenting Strategy

### What the Author Recommends
1. Create an account with an attractive avatar and profile description
2. Put a link to your target channel/bot in the account bio
3. Subscribe to channels where your target audience hangs out
4. Bot automatically comments on new posts
5. People see interesting comment -> check profile -> follow bio link -> become subscriber

### What the Author Explicitly Warns Against
- **DO NOT be the first commenter** - the bot reacts instantly, and if your comment always appears first, channel admins will quickly ban you
- **Neural network comments still look robotic** - "no matter what prompt you give, they look like a chunk of idiotic text"
- **Only use this if you have ZERO budget** for promotion
- If you have money (even 10K RUB), use proper promotion methods instead

### Timing
- The bot reacts **instantly** to new posts (no delay implemented in the basic version)
- Author warns this is a problem - need to add delays to avoid detection
- No specific delay values given in video (left for viewer to implement)

---

## Anti-Detection Measures

### What the Video Covers (Minimal)
- Settings in a separate `config.py` file (hide credentials)
- Author acknowledges detection is a real risk
- Warning about being first commenter = instant ban

### What the Video Does NOT Cover (Gaps)
- No random delays between comments
- No multi-account support
- No proxy rotation
- No fingerprint management
- No session health monitoring
- No human-like behavior simulation
- No FloodWait error handling
- No rate limiting

---

## Account Management

### From the Video
- Single account approach (one userbot session)
- Pyrogram session file persists auth across restarts
- Get channel IDs via @GetMyIDBot
- No multi-account orchestration

### What's Missing vs Our System
- No account rotation
- No session backup
- No health monitoring
- No device fingerprint management
- No warmup schedule

---

## Monetization Model (Author's Perspective)

1. **Self-use**: Grow your own Telegram channels for free
2. **Sell as service**: Offer neurocommenting to clients
3. **Subscription model**: Charge recurring fees
4. **Channel networks**: Build networks of Telegram channels

### Market Pricing Context
- Commercial neurocommenting services: ~20,000-26,500 RUB for 2 months (per ppc.world research)
- One bot can handle up to 3,500 comments/day across 1,000 channels (per industry sources)

---

## Comparison: This Video vs Our System (NEURO COMMENTING)

| Feature | Money Python (Video) | Our System |
|---------|---------------------|------------|
| **Library** | Pyrogram | Telethon (+ aiogram for admin bot) |
| **AI Engine** | Free GPT-3 (g4f/gpt4free) | Configurable (OpenAI, local models) |
| **Accounts** | Single account | Multi-account with rotation |
| **Anti-detection** | None (warning only) | Session health, fingerprints, warmup, delays |
| **Architecture** | Single file ~50 lines | Full SaaS with DB, admin panel, watchdog |
| **Session management** | Basic Pyrogram session | Encrypted backups, health monitoring, StringSession |
| **Proxy support** | None | Per-account proxy assignment |
| **Delays** | None (instant, warned against) | Configurable timing with Gaussian distribution |
| **Error handling** | None ("impossible to crash") | FloodWait handling, AuthKey detection, auto-restart |
| **Scale** | 1 channel | Multi-channel per user, multi-tenant SaaS |
| **Admin interface** | None (CLI only) | Telegram bot admin panel |
| **Account creation** | Manual | Account factory (SMS providers, emulator farm) |
| **Fingerprinting** | None | 176 device fingerprints (Android + Desktop) |
| **Complexity** | ~50 lines | 10,000+ lines across modules |

### Key Takeaways for Us

1. **Pyrogram vs Telethon**: The video uses Pyrogram. Key difference is `get_discussion_message()` + `.reply()` pattern for commenting, which is equivalent to our Telethon approach but with cleaner API.

2. **g4f library**: Worth investigating as a fallback AI provider -- free GPT without API keys. Could be useful for cost-sensitive users in our SaaS model.

3. **The "first comment" problem**: Author explicitly warns against instant commenting. Confirms our delay-based approach is correct.

4. **Market validation**: Even a 50-line bot with no anti-detection is being taught as a monetizable tool. Our full-featured system has massive competitive advantage.

5. **Target audience**: Psychologists, consultants, marketers, anyone selling consulting services. Good market segmentation insight.

6. **Simplicity sells**: The "50 lines" marketing angle is effective for education. Our system could offer a "lite" mode for onboarding.

---

## Related Resources Found

### Pyrogram Documentation
- `get_discussion_message()`: https://docs.pyrogram.org/api/methods/get_discussion_message
- `send_message()`: https://docs.pyrogram.org/api/methods/send_message
- Full API methods: https://docs.pyrogram.org/api/methods/

### Similar Implementations (from Research)
- **Ithy article** (full architecture with sberbank-ai/rugpt3large): https://ithy.com/article/telegram-pyrogram-ai-bot-2fbbtlne
- **Habr article** (Pyrogram userbot basics): https://habr.com/ru/companies/amvera/articles/838204/
- **Forbes article** (neurocommenting phenomenon): https://www.forbes.ru/tekhnologii/537955-cto-za-razgovor-kak-ustroen-nejrokommenting-v-telegram-kanalah
- **ppc.world** (how bots work + pricing): https://ppc.world/articles/neyrokommenting-chto-eto-takoe-i-kak-rabotayut-boty-v-telegram/
- **gpt4free** (free GPT library): https://github.com/xtekky/gpt4free

### Alternative AI Approaches Found in Research
1. **g4f (gpt4free)** - Free GPT-3.5/4 without API keys (likely what video uses)
2. **sberbank-ai/rugpt3large_based_on_gpt2** - Local Russian GPT model via HuggingFace transformers
3. **OpenAI API** - Paid but highest quality
4. **Any LLM via API** - Configurable approach (our method)

### Advanced Implementation Pattern (from Ithy Research)

```python
# More sophisticated config pattern (from ithy.com article)
COMMENT_DELAY_FROM_POST = 300      # 5 min delay before commenting
MIN_COMMENT_INTERVAL = 600         # 10 min between comments globally
REACTION_PROBABILITY = 0.7         # 70% chance of adding emoji reaction
REACTIONS = ["thumbs_up", "heart", "laughing", "party", "surprised"]

# Local AI model (no API key needed)
model_name = 'sberbank-ai/rugpt3large_based_on_gpt2'
tokenizer = AutoTokenizer.from_pretrained(model_name)
model = AutoModelForCausalLM.from_pretrained(model_name)

# Comment generation with no_repeat_ngram
def generate_reply(prompt: str, max_length=128) -> str:
    inputs = tokenizer.encode(prompt, return_tensors="pt").to(device)
    outputs = model.generate(
        inputs,
        max_length=max_length,
        num_return_sequences=1,
        no_repeat_ngram_size=3,
        early_stopping=True
    )
    return tokenizer.decode(outputs[0], skip_special_tokens=True)

# Channel monitoring loop
async def monitor_channels(self):
    while True:
        for channel_id in self.subscribed_channels:
            # Fetch latest message, compare against last_seen_id
            # Dispatch processing task if new
            pass
        await asyncio.sleep(20)  # Check every 20 seconds
```

---

## Summary

The Money Python video presents a **minimal viable neurocommenting bot** in ~50 lines of Python using Pyrogram + a free GPT library. It is a beginner tutorial showing the basic concept:

1. Subscribe to target channels
2. Listen for new posts via Pyrogram handler + filters
3. Generate AI comment via free GPT-3 library
4. Post comment using `get_discussion_message().reply()`

The implementation has **zero anti-detection**, **no delays**, **single account only**, and the author himself admits the quality is poor. Our system is orders of magnitude more sophisticated with multi-tenant SaaS architecture, multi-account management, session health monitoring, device fingerprinting, configurable AI engines, and comprehensive anti-fraud measures.

**Key competitive insight**: The market charges 20-26K RUB for basic neurocommenting services. Our system's capabilities far exceed what's available, positioning us for premium pricing or high-volume SaaS model.
