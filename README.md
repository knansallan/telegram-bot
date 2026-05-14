# Telegram Sticker Store Bot

A free Telegram sticker store bot with two categories.

## Categories
- **ستكرات عبيد** — Obaid's stickers
- **ستكرات مهاوش** — Mahawish's stickers

## How to Add Stickers

Open `bot.py` and find the `STICKERS` dictionary. Add real Telegram sticker `file_id` values:

```python
STICKERS = {
    "obaid": [
        "CAACAgIAAxkBAAI...file_id_1...",
        "CAACAgIAAxkBAAI...file_id_2...",
    ],
    "mahawish": [
        "CAACAgIAAxkBAAI...file_id_1...",
    ],
}
```

### How to Get a Sticker file_id

1. Send the sticker to your bot in Telegram.
2. The bot will log the `file_id` in the console — copy it from there.
   Or forward the sticker to [@userinfobot](https://t.me/userinfobot).

## Setup

1. Set `TELEGRAM_BOT_TOKEN` secret to your bot token (get one from [@BotFather](https://t.me/BotFather)).
2. Run: `pip install -r requirements.txt && python bot.py`
