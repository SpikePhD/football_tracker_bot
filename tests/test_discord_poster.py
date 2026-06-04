import asyncio
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import discord

os.environ.setdefault("BOT_TOKEN", "test-token")
os.environ.setdefault("API_KEY", "test-api-key")
os.environ.setdefault("CHANNEL_ID", "123456789")


class FakeTextChannel:
    def __init__(self, messages=None):
        self.name = "test-channel"
        self.messages = messages or []
        self.sent = []

    def history(self, limit):
        async def iterator():
            for message in self.messages[:limit]:
                yield message

        return iterator()

    async def send(self, content=None, suppress_embeds=False):
        message = FakeMessage(9000 + len(self.sent))
        message.content = content
        message.suppress_embeds = suppress_embeds
        self.sent.append(message)
        return message


class FakeMessage:
    def __init__(self, message_id, edit_error=None):
        self.id = message_id
        self.edit_error = edit_error
        self.edited_content = None
        self.edit_count = 0

    async def edit(self, content=None, suppress=False):
        self.edit_count += 1
        if self.edit_error:
            raise self.edit_error
        self.edited_content = content
        self.suppress = suppress
        return self


class FakeBot:
    def __init__(self, channel):
        self.channel = channel

    def get_channel(self, channel_id):
        return self.channel


class DiscordPosterTests(unittest.TestCase):

    def test_upsert_live_message_posts_new_when_message_not_in_recent_history(self):
        from modules import discord_poster

        channel = FakeTextChannel(messages=[])

        async def run():
            with patch.object(discord_poster.discord, "TextChannel", FakeTextChannel):
                return await discord_poster.upsert_live_message(
                    bot=FakeBot(channel),
                    channel_id=123,
                    message_id=42,
                    content="live update",
                )

        result = asyncio.run(run())

        self.assertEqual(result.id, 9000)
        self.assertEqual(channel.sent[0].content, "live update")

    def test_upsert_live_message_posts_new_when_edit_raises_not_found(self):
        from modules import discord_poster

        response = SimpleNamespace(status=404, reason="Not Found")
        existing = FakeMessage(42, edit_error=discord.NotFound(response=response, message="deleted"))
        channel = FakeTextChannel(messages=[existing])

        async def run():
            with patch.object(discord_poster.discord, "TextChannel", FakeTextChannel):
                return await discord_poster.upsert_live_message(
                    bot=FakeBot(channel),
                    channel_id=123,
                    message_id=42,
                    content="replacement live update",
                )

        result = asyncio.run(run())

        self.assertEqual(existing.edit_count, 1)
        self.assertEqual(result.id, 9000)
        self.assertEqual(channel.sent[0].content, "replacement live update")

    def test_upsert_live_message_edits_recent_message_without_sending_new(self):
        from modules import discord_poster

        existing = FakeMessage(42)
        channel = FakeTextChannel(messages=[existing])

        async def run():
            with patch.object(discord_poster.discord, "TextChannel", FakeTextChannel):
                return await discord_poster.upsert_live_message(
                    bot=FakeBot(channel),
                    channel_id=123,
                    message_id=42,
                    content="edited live update",
                )

        result = asyncio.run(run())

        self.assertIs(result, existing)
        self.assertEqual(existing.edited_content, "edited live update")
        self.assertEqual(channel.sent, [])


if __name__ == "__main__":
    unittest.main()
