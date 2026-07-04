import discord
import logging
from typing import Dict
from redbot.core import commands, Config
from redbot.core.bot import Red
from datetime import datetime, timezone

log = logging.getLogger("red.invitetracking")


class InviteTracking(commands.Cog):
    """Track invite links and who joins using them."""

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=739182465, force_registration=True)

        default_guild = {
            "invites": {},
            "invite_cache": {},
        }
        self.config.register_guild(**default_guild)

        bot.loop.create_task(self._initialize())

    async def _initialize(self):
        await self.bot.wait_until_ready()

        for guild in self.bot.guilds:
            await self._refresh_invite_cache(guild)

        log.info("InviteTracking cog initialized")

    async def _refresh_invite_cache(self, guild: discord.Guild):
        try:
            invites = await guild.invites()
        except (discord.Forbidden, discord.HTTPException):
            return

        cache = {}
        for inv in invites:
            cache[inv.code] = inv.uses
        await self.config.guild(guild).invite_cache.set(cache)

    async def _get_invite_cache(self, guild: discord.Guild) -> Dict[str, int]:
        return await self.config.guild(guild).invite_cache() or {}

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if member.bot:
            return

        guild = member.guild

        try:
            current_invites = await guild.invites()
        except (discord.Forbidden, discord.HTTPException):
            return

        old_cache = await self._get_invite_cache(guild)
        new_cache = {}
        used_invite = None

        for inv in current_invites:
            new_cache[inv.code] = inv.uses
            old_uses = old_cache.get(inv.code, 0)
            if inv.uses > old_uses:
                used_invite = inv

        await self.config.guild(guild).invite_cache.set(new_cache)

        if not used_invite:
            return

        stored = await self.config.guild(guild).invites()
        if used_invite.code not in stored:
            stored[used_invite.code] = {
                "creator_id": used_invite.inviter.id if used_invite.inviter else None,
                "creator_name": str(used_invite.inviter) if used_invite.inviter else "Unknown",
                "channel_id": used_invite.channel.id if used_invite.channel else None,
                "max_age": used_invite.max_age,
                "max_uses": used_invite.max_uses,
                "uses": used_invite.uses,
                "used_by": [],
                "created_at": datetime.now(timezone.utc).isoformat(),
            }

        stored[used_invite.code]["uses"] = new_cache[used_invite.code]
        if member.id not in stored[used_invite.code]["used_by"]:
            stored[used_invite.code]["used_by"].append(member.id)
        await self.config.guild(guild).invites.set(stored)

        log.info(
            "%s joined %s using invite %s (created by %s)",
            member, guild.name, used_invite.code,
            stored[used_invite.code].get("creator_name", "unknown")
        )

    @commands.Cog.listener()
    async def on_invite_create(self, invite: discord.Invite):
        guild = invite.guild
        if guild is None:
            return

        stored = await self.config.guild(guild).invites()
        if invite.code in stored:
            return

        stored[invite.code] = {
            "creator_id": invite.inviter.id if invite.inviter else None,
            "creator_name": str(invite.inviter) if invite.inviter else "Unknown",
            "channel_id": invite.channel.id if invite.channel else None,
            "max_age": invite.max_age,
            "max_uses": invite.max_uses,
            "uses": invite.uses,
            "used_by": [],
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        await self.config.guild(guild).invites.set(stored)

        cache = await self._get_invite_cache(guild)
        cache[invite.code] = invite.uses
        await self.config.guild(guild).invite_cache.set(cache)

        log.info(
            "Tracking invite %s created by %s in %s",
            invite.code, stored[invite.code]["creator_name"], guild.name
        )

    @commands.Cog.listener()
    async def on_invite_delete(self, invite: discord.Invite):
        guild = invite.guild
        if guild is None:
            return

        stored = await self.config.guild(guild).invites()
        stored.pop(invite.code, None)
        await self.config.guild(guild).invites.set(stored)

        cache = await self._get_invite_cache(guild)
        cache.pop(invite.code, None)
        await self.config.guild(guild).invite_cache.set(cache)

    @commands.group(name="invites", aliases=["invitetracking"])
    @commands.guild_only()
    async def invites(self, ctx: commands.Context):
        """Manage invite tracking."""
        if ctx.invoked_subcommand is None:
            await ctx.send_help()

    @invites.command(name="list")
    async def invites_list(self, ctx: commands.Context):
        """List all tracked invites and their usage."""
        stored = await self.config.guild(ctx.guild).invites()
        if not stored:
            await ctx.send("No invites have been created yet.")
            return

        embed = discord.Embed(
            title="Invite Tracking",
            color=discord.Color.blue()
        )

        for code, data in list(stored.items())[:10]:
            creator = ctx.guild.get_member(data["creator_id"])
            creator_name = creator.mention if creator else data.get("creator_name", "Unknown")
            used_by = data.get("used_by", [])
            members = []
            for uid in used_by[:5]:
                m = ctx.guild.get_member(uid)
                members.append(m.mention if m else f"<@{uid}>")
            member_str = ", ".join(members) if members else "None yet"
            if len(used_by) > 5:
                member_str += f" (+{len(used_by) - 5} more)"

            embed.add_field(
                name=f"`{code}` — {len(used_by)} use(s)",
                value=f"Creator: {creator_name}\nUsers: {member_str}",
                inline=False
            )

        total = sum(len(d.get("used_by", [])) for d in stored.values())
        embed.set_footer(text=f"{len(stored)} invite(s) · {total} total join(s)")
        await ctx.send(embed=embed)

    @invites.command(name="user", aliases=["member"])
    async def invites_user(self, ctx: commands.Context, member: discord.Member = None):
        """Show invites created by or used by a member."""
        member = member or ctx.author
        stored = await self.config.guild(ctx.guild).invites()

        created = []
        used = []
        for code, data in stored.items():
            if data["creator_id"] == member.id:
                created.append(code)
            if member.id in data.get("used_by", []):
                used.append(code)

        embed = discord.Embed(
            title=f"Invite Stats — {member.display_name}",
            color=discord.Color.blue()
        )
        embed.add_field(name="Invites Created", value=str(len(created)), inline=True)
        embed.add_field(name="Invites Used", value=str(len(used)), inline=True)

        if created:
            lines = []
            for code in created[:5]:
                u = len(stored[code].get("used_by", []))
                lines.append(f"`{code}` — {u} join(s)")
            embed.add_field(
                name="Recent Created",
                value="\n".join(lines) or "None",
                inline=False
            )

        if used:
            lines = []
            for code in used[:5]:
                u = len(stored[code].get("used_by", []))
                lines.append(f"`{code}` — {u} join(s)")
            embed.add_field(
                name="Recent Used",
                value="\n".join(lines) or "None",
                inline=False
            )

        await ctx.send(embed=embed)

    @invites.command(name="clear")
    @commands.admin_or_permissions(manage_guild=True)
    async def invites_clear(self, ctx: commands.Context):
        """Clear all stored invite data for this server."""
        await self.config.guild(ctx.guild).invites.set({})
        await self.config.guild(ctx.guild).invite_cache.set({})
        await ctx.send("All invite data cleared.")


