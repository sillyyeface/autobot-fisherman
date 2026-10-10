# optional integrations that react to bot events; each one is enabled in settings.py
# a new integration is a module with a register() function that subscribes to events

import settings
from integrations import discord_webhook


def register_integrations():
    if settings.DISCORD_WEBHOOK_ENABLED:
        discord_webhook.register()
