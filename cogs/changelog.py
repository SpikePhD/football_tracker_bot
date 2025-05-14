# cogs/changelog.py
import logging
import pathlib # For path manipulation to find the changelog file
from discord.ext import commands
# MODIFIED: Import from the new discord_poster module
from modules.discord_poster import post_new_message_to_context

logger = logging.getLogger(__name__)

class Changelog(commands.Cog):
    """Read & post your CHANGELOG.md file on command."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Assumes CHANGELOG.md is in the root directory of the bot project
        # (parent of the 'cogs' directory)
        self.changelog_path = pathlib.Path(__file__).parent.parent / "CHANGELOG.md"

    @commands.command(
        name="changelog",
        help="Show the contents of CHANGELOG.md"
    )
    async def changelog(self, ctx: commands.Context):
        if not self.changelog_path.exists():
            error_message = "❌ CHANGELOG.md not found at the expected location."
            logger.warning(f"Changelog command: {error_message} Expected at: {self.changelog_path}")
            # MODIFIED: Use discord_poster
            await post_new_message_to_context(ctx, content=error_message)
            return
        
        try:
            text = self.changelog_path.read_text(encoding="utf-8").strip()
            if not text:
                # MODIFIED: Use discord_poster
                await post_new_message_to_context(ctx, content="ℹ️ CHANGELOG.md is empty.")
                return

            # Discord has a 2000‐char limit per message, so we split into chunks.
            # Each chunk will be sent as a separate message.
            current_chunk = "```md\n" # Start with markdown code block
            for line in text.splitlines():
                # Check if adding the next line (plus newline and closing ```) exceeds limit
                if len(current_chunk) + len(line) + len("\n```") + 1 > 1990: # 1990 to be safe
                    current_chunk += "```" # Close current markdown block
                    # MODIFIED: Use discord_poster
                    await post_new_message_to_context(ctx, content=current_chunk)
                    current_chunk = "```md\n" # Start new markdown block
                current_chunk += line + "\n"
            
            # Send any remaining part of the last chunk
            if current_chunk != "```md\n": # Ensure there's content beyond the initial md tag
                current_chunk += "```" # Close the final markdown block
                # MODIFIED: Use discord_poster
                await post_new_message_to_context(ctx, content=current_chunk)

        except Exception as e:
            logger.error(f"Error reading or processing changelog: {e}", exc_info=True)
            # MODIFIED: Use discord_poster
            await post_new_message_to_context(ctx, content="❌ An error occurred while trying to display the changelog.")


async def setup(bot: commands.Bot):
    await bot.add_cog(Changelog(bot))
    logger.info("✔ cogs.changelog loaded")
