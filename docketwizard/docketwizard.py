import uuid
import discord
from redbot.core import commands, Config
from redbot.core.bot import Red
from datetime import datetime


class DocketWizard(commands.Cog):
    """Create and manage criminal and civil dockets."""

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=4829165730, force_registration=True)

        default_guild = {
            "channel_id": None,
            "setup_message_id": None,
            "dockets": {},
        }
        self.config.register_guild(**default_guild)

        self._persistent_views = []
        bot.loop.create_task(self._initialize())

    async def _initialize(self):
        await self.bot.wait_until_ready()
        all_data = await self.config.all_guilds()
        for guild_id, data in all_data.items():
            msg_id = data.get("setup_message_id")
            channel_id = data.get("channel_id")
            if msg_id and channel_id:
                guild = self.bot.get_guild(guild_id)
                if guild:
                    channel = guild.get_channel(channel_id)
                    if channel:
                        try:
                            msg = await channel.fetch_message(msg_id)
                            view = _DocketWizardView(self)
                            self.bot.add_view(view, message_id=msg.id)
                            self._persistent_views.append(view)
                        except (discord.NotFound, discord.Forbidden):
                            pass

    def cog_unload(self):
        for view in self._persistent_views:
            view.stop()

    @commands.group(name="docketwizard", aliases=["dw"])
    @commands.admin_or_permissions(administrator=True)
    async def dw(self, ctx: commands.Context):
        """DocketWizard commands."""

    @dw.command(name="setup")
    @commands.admin_or_permissions(administrator=True)
    async def setup(self, ctx: commands.Context, channel: discord.TextChannel):
        """Set up the docket wizard in a channel."""
        view = _DocketWizardView(self)
        embed = discord.Embed(
            title="\u2696\ufe0f DocketWizard",
            description="Click a button below to create a new docket entry.",
            color=discord.Color.gold(),
        )
        msg = await channel.send(embed=embed, view=view)

        await self.config.guild(ctx.guild).channel_id.set(channel.id)
        await self.config.guild(ctx.guild).setup_message_id.set(msg.id)

        await ctx.send(f"DocketWizard has been set up in {channel.mention}")

    @dw.command(name="remove")
    @commands.admin_or_permissions(administrator=True)
    async def remove(self, ctx: commands.Context):
        """Remove the docket wizard setup and all stored data for this guild."""
        channel_id = await self.config.guild(ctx.guild).channel_id()
        msg_id = await self.config.guild(ctx.guild).setup_message_id()

        if channel_id and msg_id:
            channel = ctx.guild.get_channel(channel_id)
            if channel:
                try:
                    msg = await channel.fetch_message(msg_id)
                    await msg.delete()
                except (discord.NotFound, discord.Forbidden):
                    pass

        await self.config.guild(ctx.guild).channel_id.set(None)
        await self.config.guild(ctx.guild).setup_message_id.set(None)
        await self.config.guild(ctx.guild).dockets.set({})
        await ctx.send("DocketWizard has been removed from this guild.")

    @dw.command(name="list")
    async def list_dockets(self, ctx: commands.Context, case_type: str = None):
        """List recent dockets. Optionally filter by 'criminal' or 'civil'."""
        dockets = await self.config.guild(ctx.guild).dockets()
        if not dockets:
            await ctx.send("No dockets found.")
            return

        filtered = {}
        for cid, data in dockets.items():
            if case_type and data.get("type") != case_type.lower():
                continue
            filtered[cid] = data

        if not filtered:
            t = f" `{case_type}`" if case_type else ""
            await ctx.send(f"No{t} dockets found.")
            return

        lines = []
        for cid, data in list(filtered.items())[:10]:
            name = data.get("defendant") or data.get("plaintiff", "Unknown")
            lines.append(
                f"**#{cid}** \u2014 {data['type'].title()} \u2014 {name} \u2014 {data['created_at']}"
            )

        embed = discord.Embed(
            title="\U0001f4cb Recent Dockets",
            description="\n".join(lines),
            color=discord.Color.gold(),
        )
        if len(filtered) > 10:
            embed.set_footer(text=f"Showing 10 of {len(filtered)} dockets")
        await ctx.send(embed=embed)

    @dw.command(name="view")
    async def view_docket(self, ctx: commands.Context, case_id: str):
        """View a specific docket by case ID."""
        dockets = await self.config.guild(ctx.guild).dockets()
        data = dockets.get(case_id.upper())
        if not data:
            await ctx.send(f"No docket found with ID `{case_id}`.")
            return

        color = discord.Color.red() if data["type"] == "criminal" else discord.Color.blue()
        embed = discord.Embed(
            title=f"{data['type'].title()} Docket #{case_id.upper()}",
            color=color,
        )
        for key, value in data.items():
            if value and key not in ("type", "created_by"):
                display_name = key.replace("_", " ").title()
                embed.add_field(name=display_name, value=str(value), inline=True)

        await ctx.send(embed=embed)

    @dw.command(name="hearing")
    async def hearing(self, ctx: commands.Context, case_id: str, *, hearing_date: str):
        """Add or update a hearing date for a docket."""
        case_id = case_id.upper()
        async with self.config.guild(ctx.guild).dockets() as dockets:
            if case_id not in dockets:
                await ctx.send(f"No docket found with ID `{case_id}`.")
                return
            dockets[case_id]["hearing_date"] = hearing_date

        await ctx.send(f"Hearing date for case **#{case_id}** set to: {hearing_date}")

    @dw.command(name="notify")
    async def notify(self, ctx: commands.Context, case_id: str, users: commands.Greedy[discord.Member]):
        """Notify users about a docket. Example: [p]dw notify ABC12345 @user1 @user2"""
        dockets = await self.config.guild(ctx.guild).dockets()
        case_id = case_id.upper()
        if case_id not in dockets:
            await ctx.send(f"No docket found with ID `{case_id}`.")
            return

        if not users:
            await ctx.send("Please specify at least one user to notify.")
            return

        mentions = " ".join(u.mention for u in users)
        embed = discord.Embed(
            title=f"\U0001f514 Notification for Case #{case_id}",
            description=f"You have been notified regarding a {dockets[case_id]['type']} docket.",
            color=discord.Color.green(),
        )
        await ctx.send(embed=embed)
        await ctx.send(mentions)


class _NotifyModal(discord.ui.Modal, title="Notify Users"):
    user_ids = discord.ui.TextInput(
        label="Discord User IDs",
        placeholder="Comma-separated user IDs (right-click user > Copy ID)",
        required=True,
        max_length=200,
    )

    def __init__(self, cog: DocketWizard, case_id: str):
        super().__init__()
        self.cog = cog
        self.case_id = case_id

    async def on_submit(self, interaction: discord.Interaction):
        ids = [
            id.strip()
            for id in self.user_ids.value.replace(",", " ").split()
            if id.strip().isdigit()
        ]
        if not ids:
            await interaction.response.send_message("No valid user IDs provided.", ephemeral=True)
            return

        mentions = " ".join(f"<@{uid}>" for uid in ids)
        embed = discord.Embed(
            title=f"\U0001f514 Notification for Case #{self.case_id}",
            color=discord.Color.green(),
        )
        await interaction.response.send_message(embed=embed)
        await interaction.followup.send(
            f"{mentions} \u2014 You have been notified regarding case **#{self.case_id}**."
        )


class _NotifyView(discord.ui.View):
    def __init__(self, cog: DocketWizard, case_id: str):
        super().__init__(timeout=300)
        self.cog = cog
        self.case_id = case_id

        button = discord.ui.Button(
            label="Notify Users",
            style=discord.ButtonStyle.secondary,
            custom_id=f"docketwizard_notify_{case_id}",
            emoji="\U0001f514",
        )
        button.callback = self._notify_callback
        self.add_item(button)

    async def _notify_callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(_NotifyModal(self.cog, self.case_id))


class _CriminalDocketModal(discord.ui.Modal, title="New Criminal Docket"):
    defendant = discord.ui.TextInput(
        label="Defendant",
        placeholder="Full name of the defendant",
        required=True,
        max_length=100,
    )
    attorney = discord.ui.TextInput(
        label="Attorney",
        placeholder="Name of defense attorney",
        required=False,
        max_length=100,
    )
    filed_by = discord.ui.TextInput(
        label="Filed By",
        placeholder="Officer name or District Attorney",
        required=True,
        max_length=100,
    )
    hearing_date = discord.ui.TextInput(
        label="Hearing Date",
        placeholder="e.g., July 20, 2026 at 9:00 AM",
        required=False,
        max_length=100,
    )

    def __init__(self, cog: DocketWizard):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        case_id = str(uuid.uuid4())[:8].upper()
        timestamp = datetime.now()
        ts_str = timestamp.strftime("%Y-%m-%d %H:%M:%S")

        embed = discord.Embed(
            title=f"\u2696\ufe0f Criminal Docket #{case_id}",
            color=discord.Color.red(),
            timestamp=timestamp,
        )
        embed.add_field(name="Defendant", value=self.defendant.value, inline=True)
        embed.add_field(name="Attorney", value=self.attorney.value or "None assigned", inline=True)
        embed.add_field(name="Filed By", value=self.filed_by.value, inline=True)
        embed.add_field(name="Filed At", value=ts_str, inline=True)
        if self.hearing_date.value:
            embed.add_field(name="Hearing Date", value=self.hearing_date.value, inline=False)

        embed.set_footer(text=f"Case #{case_id} \u2022 Criminal")

        await interaction.response.send_message(embed=embed)
        await interaction.followup.send(
            view=_NotifyView(self.cog, case_id), ephemeral=True
        )

        case_data = {
            "type": "criminal",
            "defendant": self.defendant.value,
            "attorney": self.attorney.value or None,
            "filed_by": self.filed_by.value,
            "hearing_date": self.hearing_date.value or None,
            "created_at": ts_str,
            "created_by": interaction.user.id,
        }
        async with self.cog.config.guild(interaction.guild).dockets() as dockets:
            dockets[case_id] = case_data


class _CivilDocketModal(discord.ui.Modal, title="New Civil Docket"):
    plaintiff = discord.ui.TextInput(
        label="Plaintiff",
        placeholder="Full name of the plaintiff",
        required=True,
        max_length=100,
    )
    defendant = discord.ui.TextInput(
        label="Defendant",
        placeholder="Full name of the defendant",
        required=True,
        max_length=100,
    )
    attorneys = discord.ui.TextInput(
        label="Attorneys",
        placeholder="Plaintiff's Atty: Name, Defendant's Atty: Name",
        required=False,
        max_length=200,
    )
    filed_by = discord.ui.TextInput(
        label="Filed By",
        placeholder="Name of the filer",
        required=True,
        max_length=100,
    )
    hearing_date = discord.ui.TextInput(
        label="Hearing Date",
        placeholder="e.g., July 20, 2026 at 9:00 AM",
        required=False,
        max_length=100,
    )

    def __init__(self, cog: DocketWizard):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        case_id = str(uuid.uuid4())[:8].upper()
        timestamp = datetime.now()
        ts_str = timestamp.strftime("%Y-%m-%d %H:%M:%S")

        embed = discord.Embed(
            title=f"\U0001f4cb Civil Docket #{case_id}",
            color=discord.Color.blue(),
            timestamp=timestamp,
        )
        embed.add_field(name="Plaintiff", value=self.plaintiff.value, inline=True)
        embed.add_field(name="Defendant", value=self.defendant.value, inline=True)
        embed.add_field(name="Attorneys", value=self.attorneys.value or "None assigned", inline=True)
        embed.add_field(name="Filed By", value=self.filed_by.value, inline=True)
        embed.add_field(name="Filed At", value=ts_str, inline=True)
        if self.hearing_date.value:
            embed.add_field(name="Hearing Date", value=self.hearing_date.value, inline=False)

        embed.set_footer(text=f"Case #{case_id} \u2022 Civil")

        await interaction.response.send_message(embed=embed)
        await interaction.followup.send(
            view=_NotifyView(self.cog, case_id), ephemeral=True
        )

        case_data = {
            "type": "civil",
            "plaintiff": self.plaintiff.value,
            "defendant": self.defendant.value,
            "attorneys": self.attorneys.value or None,
            "filed_by": self.filed_by.value,
            "hearing_date": self.hearing_date.value or None,
            "created_at": ts_str,
            "created_by": interaction.user.id,
        }
        async with self.cog.config.guild(interaction.guild).dockets() as dockets:
            dockets[case_id] = case_data


class _DocketWizardView(discord.ui.View):
    def __init__(self, cog: DocketWizard):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(
        label="Criminal Docket",
        style=discord.ButtonStyle.danger,
        custom_id="docketwizard_criminal",
        emoji="\u2696\ufe0f",
    )
    async def criminal_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(_CriminalDocketModal(self.cog))

    @discord.ui.button(
        label="Civil Docket",
        style=discord.ButtonStyle.primary,
        custom_id="docketwizard_civil",
        emoji="\U0001f4cb",
    )
    async def civil_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(_CivilDocketModal(self.cog))
