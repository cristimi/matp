"""Run LOCALLY, once, interactively, to mint a StringSession for TG_SESSION.

    cd social-listener && python -m app.generate_session

Use a DEDICATED throwaway Telegram account, never your main account.
"""
from telethon import TelegramClient
from telethon.sessions import StringSession

api_id = int(input("TG_API_ID: ").strip())
api_hash = input("TG_API_HASH: ").strip()

with TelegramClient(StringSession(), api_id, api_hash) as client:
    print("\nTG_SESSION=" + client.session.save())
