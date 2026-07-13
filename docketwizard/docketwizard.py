import discord
from redbot.core import commands, Config
from redbot.core.bot import Red
from datetime import datetime


def _build_criminal_embed(case_id, data):
    embed = discord.Embed(
        title=f"\u2696\ufe0f {data.get('docket_title', 'Criminal Docket')}",
        color=discord.Color.red(),
    )
    embed.add_field(name="Case #", value=case_id, inline=True)
    embed.add_field(name="Defendant", value=data.get("defendant", "N/A"), inline=True)
    embed.add_field(name="Attorney", value=data.get("attorney") or "None assigned", inline=True)
    embed.add_field(name="Filed By", value=data.get("filed_by", "N/A"), inline=True)
    embed.add_field(name="Filed At", value=data.get("created_at", "N/A"), inline=True)
    if data.get("hearing_date"):
        embed.add_field(name="Hearing Date", value=data["hearing_date"], inline=False)
    embed.set_footer(text=f"Case #{case_id} \u2022 Criminal")
    return embed


def _build_civil_embed(case_id, data):
    embed = discord.Embed(
        title=f"\U0001f4cb {data.get('docket_title', 'Civil Docket')}",
        color=discord.Color.blue(),
    )
    embed.add_field(name="Case #", value=case_id, inline=True)
    embed.add_field(name="Plaintiff", value=data.get("plaintiff", "N/A"), inline=True)
    embed.add_field(name="Defendant", value=data.get("defendant", "N/A"), inline=True)
    embed.add_field(name="Attorneys", value=data.get("attorneys") or "None assigned", inline=True)
    embed.add_field(name="Filed By", value=data.get("filed_by", "N/A"), inline=True)
    embed.add_field(name="Filed At", value=data.get("created_at", "N/A"), inline=True)
    if data.get("hearing_date"):
        embed.add_field(name="Hearing Date", value=data["hearing_date"], inline=False)
    embed.set_footer(text=f"Case #{case_id} \u2022 Civil")
    return embed


async def _can_edit(interaction, cog, case_id):
    dockets = await cog.config.guild(interaction.guild).dockets()
    data = dockets.get(case_id)
    if not data:
        await interaction.response.send_message("Docket not found.", ephemeral=True)
        return False

    if interaction.user.id == data.get("created_by"):
        return True

    role_id = await cog.config.guild(interaction.guild).docket_creator_role_id()
    if role_id:
        role = interaction.guild.get_role(role_id)
        if role and role in interaction.user.roles:
            return True

    msg = "You don't have permission to edit this docket."
    if role_id:
        msg += f" Only the docket creator or <@&{role_id}> can edit."
    await interaction.response.send_message(msg, ephemeral=True)
    return False


async def _refresh_starter_message(guild, data):
    thread_id = data.get("thread_id")
    starter_id = data.get("starter_message_id")
    case_id = data.get("_case_id")
    if not thread_id or not starter_id or not case_id:
        return

    thread = guild.get_channel(thread_id)
    if not thread:
        return

    try:
        msg = await thread.fetch_message(starter_id)
    except (discord.NotFound, discord.Forbidden):
        return

    if data.get("type") == "criminal":
        new_embed = _build_criminal_embed(case_id, data)
    else:
        new_embed = _build_civil_embed(case_id, data)

    try:
        await msg.edit(embed=new_embed)
    except (discord.NotFound, discord.Forbidden):
        pass


class _EditFieldModal(discord.ui.Modal):
    def __init__(self, cog, case_id, field_name, label, current_value, placeholder="", required=False):
        super().__init__(title=f"Edit {label}")
        self.cog = cog
        self.case_id = case_id
        self.field_name = field_name

        self.input = discord.ui.TextInput(
            label=label,
            default=current_value or "",
            placeholder=placeholder,
            required=required,
            max_length=200,
        )
        self.add_item(self.input)

    async def on_submit(self, interaction: discord.Interaction):
        can_edit = await _can_edit(interaction, self.cog, self.case_id)
        if not can_edit:
            return
        await interaction.response.defer(ephemeral=True)

        async with self.cog.config.guild(interaction.guild).dockets() as dockets:
            if self.case_id not in dockets:
                await interaction.edit_original_response(content="Docket no longer exists.")
                return
            dockets[self.case_id][self.field_name] = self.input.value

        all_dockets = await self.cog.config.guild(interaction.guild).dockets()
        data = all_dockets.get(self.case_id, {})
        data["_case_id"] = self.case_id
        await _refresh_starter_message(interaction.guild, data)

        updated = self.input.value or "(none)"
        await interaction.edit_original_response(
            content=f"**{label}** updated to: {updated}"
        )


class _NotifyModal(discord.ui.Modal, title="Notify Users"):
    user_ids = discord.ui.TextInput(
        label="Discord User IDs",
        placeholder="Comma-separated user IDs (right-click user > Copy ID)",
        required=True,
        max_length=200,
    )

    def __init__(self, cog: "DocketWizard", case_id: str):
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
    def __init__(self, cog: "DocketWizard", case_id: str):
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


class _CriminalEditView(discord.ui.View):
    def __init__(self, cog: "DocketWizard", case_id: str, data: dict):
        super().__init__(timeout=86400)
        self.cog = cog
        self.case_id = case_id

        fields = [
            ("case_number", "Case Number", data.get("case_number", ""), True, "e.g., CR-2026-001"),
            ("docket_title", "Docket Title", data.get("docket_title", ""), True, "e.g., State vs John Doe"),
            ("defendant", "Defendant", data.get("defendant", ""), True, "Full name"),
            ("attorney", "Attorney", data.get("attorney", ""), False, "Defense attorney name"),
            ("filed_by", "Filed By", data.get("filed_by", ""), True, "Officer or DA"),
            ("hearing_date", "Hearing Date", data.get("hearing_date", ""), False, "e.g., July 20, 2026"),
        ]

        for i in range(0, len(fields), 3):
            row_fields = fields[i:i + 3]
            for name, label, current, required, placeholder in row_fields:
                btn = discord.ui.Button(
                    label=f"Edit {label}",
                    style=discord.ButtonStyle.secondary,
                    custom_id=f"dw_edit_cr_{case_id}_{name}",
                )
                btn.callback = self._make_callback(name, label, current, placeholder, required)
                self.add_item(btn)

    def _make_callback(self, name, label, current, placeholder, required):
        async def callback(interaction: discord.Interaction):
            can_edit = await _can_edit(interaction, self.cog, self.case_id)
            if not can_edit:
                return
            modal = _EditFieldModal(self.cog, self.case_id, name, label, current, placeholder, required)
            await interaction.response.send_modal(modal)
        return callback


class _CivilEditView(discord.ui.View):
    def __init__(self, cog: "DocketWizard", case_id: str, data: dict):
        super().__init__(timeout=86400)
        self.cog = cog
        self.case_id = case_id

        fields = [
            ("case_number", "Case Number", data.get("case_number", ""), True, "e.g., CV-2026-001"),
            ("docket_title", "Docket Title", data.get("docket_title", ""), True, "e.g., Smith vs Jones"),
            ("plaintiff", "Plaintiff", data.get("plaintiff", ""), True, "Full name"),
            ("defendant", "Defendant", data.get("defendant", ""), True, "Full name"),
            ("attorneys", "Attorneys", data.get("attorneys", ""), False, "Atty names"),
            ("filed_by", "Filed By", data.get("filed_by", ""), True, "Name of filer"),
            ("hearing_date", "Hearing Date", data.get("hearing_date", ""), False, "e.g., July 20, 2026"),
        ]

        for i in range(0, len(fields), 3):
            row_fields = fields[i:i + 3]
            for name, label, current, required, placeholder in row_fields:
                btn = discord.ui.Button(
                    label=f"Edit {label}",
                    style=discord.ButtonStyle.secondary,
                    custom_id=f"dw_edit_cv_{case_id}_{name}",
                )
                btn.callback = self._make_callback(name, label, current, placeholder, required)
                self.add_item(btn)

    def _make_callback(self, name, label, current, placeholder, required):
        async def callback(interaction: discord.Interaction):
            can_edit = await _can_edit(interaction, self.cog, self.case_id)
            if not can_edit:
                return
            modal = _EditFieldModal(self.cog, self.case_id, name, label, current, placeholder, required)
            await interaction.response.send_modal(modal)
        return callback


class _CriminalDocketModal(discord.ui.Modal, title="New Criminal Docket"):
    case_number = discord.ui.TextInput(
        label="Case Number",
        placeholder="e.g., CR-2026-001",
        required=True,
        max_length=50,
    )
    docket_title = discord.ui.TextInput(
        label="Docket Title",
        placeholder="e.g., State vs John Doe",
        required=True,
        max_length=100,
    )
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

    def __init__(self, cog: "DocketWizard"):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        case_id = self.case_number.value.strip().upper()
        timestamp = datetime.now()
        ts_str = timestamp.strftime("%Y-%m-%d %H:%M:%S")
        guild = interaction.guild

        await interaction.response.defer(ephemeral=True)

        existing = await self.cog.config.guild(guild).dockets()
        if case_id in existing:
            await interaction.edit_original_response(
                content=f"Case number `{case_id}` already exists. Please use a unique case number."
            )
            return

        forum_id = await self.cog.config.guild(guild).forum_channel_id()
        if not forum_id:
            await interaction.edit_original_response(
                content="No forum channel has been set. Use `dw setforum <forum>` first."
            )
            return

        forum = guild.get_channel(forum_id)
        if not forum or not isinstance(forum, discord.ForumChannel):
            await interaction.edit_original_response(
                content="The configured forum channel is no longer available."
            )
            return

        case_data = {
            "type": "criminal",
            "case_number": case_id,
            "docket_title": self.docket_title.value.strip(),
            "defendant": self.defendant.value.strip(),
            "attorney": self.attorney.value.strip() or None,
            "filed_by": self.filed_by.value.strip(),
            "hearing_date": None,
            "created_at": ts_str,
            "created_by": interaction.user.id,
        }

        embed = _build_criminal_embed(case_id, case_data)
        thread_name = self.docket_title.value.strip()[:100]

        thread = await forum.create_thread(
            name=thread_name,
            embed=embed,
            content=f"**Case #{case_id}** \u2014 Criminal Docket filed by {interaction.user.mention}",
        )

        try:
            starter_msg = thread.starter_message
            if starter_msg is None:
                starter_msg = await thread.fetch_message(thread.id)
        except (discord.NotFound, discord.Forbidden):
            starter_msg = None

        case_data["thread_id"] = thread.id
        case_data["starter_message_id"] = starter_msg.id if starter_msg else None

        async with self.cog.config.guild(guild).dockets() as dockets:
            dockets[case_id] = case_data

        edit_view = _CriminalEditView(self.cog, case_id, case_data)
        await thread.send(
            content=f"**Docket Actions** \u2014 Use the buttons below to edit this docket. Only {interaction.user.mention} or authorized roles can edit.",
            view=edit_view,
        )

        await interaction.edit_original_response(
            content=f"Docket thread created: {thread.mention}"
        )
        await interaction.followup.send(
            view=_NotifyView(self.cog, case_id), ephemeral=True
        )


class _CivilDocketModal(discord.ui.Modal, title="New Civil Docket"):
    case_number = discord.ui.TextInput(
        label="Case Number",
        placeholder="e.g., CV-2026-001",
        required=True,
        max_length=50,
    )
    docket_title = discord.ui.TextInput(
        label="Docket Title",
        placeholder="e.g., Smith vs Jones",
        required=True,
        max_length=100,
    )
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
    filed_by = discord.ui.TextInput(
        label="Filed By",
        placeholder="Name of the filer",
        required=True,
        max_length=100,
    )

    def __init__(self, cog: "DocketWizard"):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        case_id = self.case_number.value.strip().upper()
        timestamp = datetime.now()
        ts_str = timestamp.strftime("%Y-%m-%d %H:%M:%S")
        guild = interaction.guild

        await interaction.response.defer(ephemeral=True)

        existing = await self.cog.config.guild(guild).dockets()
        if case_id in existing:
            await interaction.edit_original_response(
                content=f"Case number `{case_id}` already exists. Please use a unique case number."
            )
            return

        forum_id = await self.cog.config.guild(guild).forum_channel_id()
        if not forum_id:
            await interaction.edit_original_response(
                content="No forum channel has been set. Use `dw setforum <forum>` first."
            )
            return

        forum = guild.get_channel(forum_id)
        if not forum or not isinstance(forum, discord.ForumChannel):
            await interaction.edit_original_response(
                content="The configured forum channel is no longer available."
            )
            return

        case_data = {
            "type": "civil",
            "case_number": case_id,
            "docket_title": self.docket_title.value.strip(),
            "plaintiff": self.plaintiff.value.strip(),
            "defendant": self.defendant.value.strip(),
            "attorneys": None,
            "filed_by": self.filed_by.value.strip(),
            "hearing_date": None,
            "created_at": ts_str,
            "created_by": interaction.user.id,
        }

        embed = _build_civil_embed(case_id, case_data)
        thread_name = self.docket_title.value.strip()[:100]

        thread = await forum.create_thread(
            name=thread_name,
            embed=embed,
            content=f"**Case #{case_id}** \u2014 Civil Docket filed by {interaction.user.mention}",
        )

        try:
            starter_msg = thread.starter_message
            if starter_msg is None:
                starter_msg = await thread.fetch_message(thread.id)
        except (discord.NotFound, discord.Forbidden):
            starter_msg = None

        case_data["thread_id"] = thread.id
        case_data["starter_message_id"] = starter_msg.id if starter_msg else None

        async with self.cog.config.guild(guild).dockets() as dockets:
            dockets[case_id] = case_data

        edit_view = _CivilEditView(self.cog, case_id, case_data)
        await thread.send(
            content=f"**Docket Actions** \u2014 Use the buttons below to edit this docket. Only {interaction.user.mention} or authorized roles can edit.",
            view=edit_view,
        )

        await interaction.edit_original_response(
            content=f"Docket thread created: {thread.mention}"
        )
        await interaction.followup.send(
            view=_NotifyView(self.cog, case_id), ephemeral=True
        )


class _DocketWizardView(discord.ui.View):
    def __init__(self, cog: "DocketWizard"):
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


class DocketWizard(commands.Cog):
    """Create and manage criminal and civil dockets."""

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=4829165730, force_registration=True)

        default_guild = {
            "channel_id": None,
            "setup_message_id": None,
            "forum_channel_id": None,
            "docket_creator_role_id": None,
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
    async def setup(
        self,
        ctx: commands.Context,
        channel: discord.TextChannel,
        forum_channel: discord.ForumChannel,
        role: discord.Role = None,
    ):
        """Set up the docket wizard.

        Parameters:
        -----------
        channel : discord.TextChannel
            The channel where the docket buttons will appear.
        forum_channel : discord.ForumChannel
            The forum channel where docket threads will be created.
        role : discord.Role
            The role that can edit dockets (optional, can be set later with `setrole`).
        """
        view = _DocketWizardView(self)
        embed = discord.Embed(
            title="\u2696\ufe0f DocketWizard",
            description="Click a button below to create a new docket entry.",
            color=discord.Color.gold(),
        )
        msg = await channel.send(embed=embed, view=view)

        await self.config.guild(ctx.guild).channel_id.set(channel.id)
        await self.config.guild(ctx.guild).setup_message_id.set(msg.id)
        await self.config.guild(ctx.guild).forum_channel_id.set(forum_channel.id)
        if role:
            await self.config.guild(ctx.guild).docket_creator_role_id.set(role.id)

        parts = [f"Buttons in {channel.mention}", f"docket threads in {forum_channel.mention}"]
        if role:
            parts.append(f"edit role: {role.mention}")
        await ctx.send("DocketWizard set up! " + ", ".join(parts) + ".")

    @dw.command(name="setforum")
    @commands.admin_or_permissions(administrator=True)
    async def setforum(self, ctx: commands.Context, forum_channel: discord.ForumChannel):
        """Set the forum channel where docket threads will be created."""
        await self.config.guild(ctx.guild).forum_channel_id.set(forum_channel.id)
        await ctx.send(f"Docket threads will now be created in {forum_channel.mention}.")

    @dw.command(name="setrole")
    @commands.admin_or_permissions(administrator=True)
    async def setrole(self, ctx: commands.Context, role: discord.Role):
        """Set the role that can edit dockets (alongside the docket creator)."""
        await self.config.guild(ctx.guild).docket_creator_role_id.set(role.id)
        await ctx.send(f"Users with the {role.mention} role can now edit dockets.")

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
        await self.config.guild(ctx.guild).forum_channel_id.set(None)
        await self.config.guild(ctx.guild).docket_creator_role_id.set(None)
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
            title = data.get("docket_title") or data.get("defendant") or data.get("plaintiff", "Unknown")
            lines.append(
                f"**{cid}** \u2014 {data['type'].title()} \u2014 {title} \u2014 {data['created_at']}"
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
            title=f"{data['type'].title()} Docket {case_id.upper()}",
            color=color,
        )
        for key, value in data.items():
            if value and key not in ("type", "created_by", "thread_id", "starter_message_id", "_case_id"):
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

            data = dict(dockets[case_id])
            data["_case_id"] = case_id

        await _refresh_starter_message(ctx.guild, data)
        await ctx.send(f"Hearing date for case **{case_id}** set to: {hearing_date}")

    @dw.command(name="notify")
    async def notify(self, ctx: commands.Context, case_id: str, users: commands.Greedy[discord.Member]):
        """Notify users about a docket. Example: [p]dw notify CR-2026-001 @user1 @user2"""
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
            title=f"\U0001f514 Notification for Case {case_id}",
            description=f"You have been notified regarding a {dockets[case_id]['type']} docket.",
            color=discord.Color.green(),
        )
        await ctx.send(embed=embed)
        await ctx.send(mentions)
