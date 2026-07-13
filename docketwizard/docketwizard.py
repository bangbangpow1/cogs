import discord
from redbot.core import commands, Config
from redbot.core.bot import Red
from datetime import datetime


def _mention(data, key):
    uid = data.get(key)
    if uid:
        return f"<@{uid}>"
    return "None assigned"


def _build_criminal_embed(case_id, data):
    current_id = data.get("case_number", case_id)
    desc = (
        f"**Case # :** {current_id}\n"
        f"**Defendant :** {_mention(data, 'defendant')}\n"
        f"**Attorney :** {_mention(data, 'attorney')}\n"
        f"**Filed By :** {_mention(data, 'filed_by')}\n"
        f"**Filed At :** {data.get('created_at', 'N/A')}\n"
    )
    charges = data.get("charges")
    if charges:
        bulleted = "\n".join(f"\u2022 {c.strip()}" for c in charges.split("\n") if c.strip())
        desc += f"**Charges:**\n{bulleted}\n"
    if data.get("hearing_date"):
        desc += f"**Hearing Date :** {data['hearing_date']}\n"
    embed = discord.Embed(
        title=f"\u2696\ufe0f {data.get('docket_title', 'Criminal Docket')}",
        description=desc.strip(),
        color=discord.Color.red(),
    )
    embed.set_footer(text=f"Case #{current_id} \u2022 Criminal")
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

    thread = await _get_thread(guild, thread_id)
    if not thread:
        return

    try:
        msg = await thread.fetch_message(starter_id)
    except (discord.NotFound, discord.Forbidden):
        return

    new_embed = _build_criminal_embed(case_id, data)

    try:
        await msg.edit(embed=new_embed)
    except (discord.NotFound, discord.Forbidden):
        pass


async def _get_thread(guild, thread_id):
    thread = guild.get_channel(thread_id)
    if thread is None:
        try:
            thread = guild.get_thread(thread_id)
        except AttributeError:
            pass
    if thread is None:
        try:
            thread = guild.get_channel_or_thread(thread_id)
        except AttributeError:
            pass
    if thread is None:
        try:
            thread = await guild.fetch_channel(thread_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass
    return thread


async def _cleanup_stale_case(guild, cog, case_id):
    async with cog.config.guild(guild).dockets() as dockets:
        if case_id not in dockets:
            return False
        data = dockets[case_id]
        thread_id = data.get("thread_id")
        if thread_id:
            thread = await _get_thread(guild, thread_id)
            if thread:
                return False
        del dockets[case_id]
        return True


class _EditFieldModal(discord.ui.Modal):
    def __init__(self, cog, case_id, field_name, label, current_value, placeholder="", required=False, multiline=False):
        super().__init__(title=f"Edit {label}")
        self.cog = cog
        self.case_id = case_id
        self.field_name = field_name
        self.label = label

        self.input = discord.ui.TextInput(
            label=label,
            default=current_value or "",
            placeholder=placeholder,
            required=required,
            style=discord.TextStyle.paragraph if multiline else discord.TextStyle.short,
            max_length=1000,
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

        if self.field_name == "docket_title":
            thread_id = data.get("thread_id")
            if thread_id:
                thread = await _get_thread(interaction.guild, thread_id)
                if thread:
                    try:
                        await thread.edit(name=self.input.value[:100])
                    except discord.Forbidden:
                        await interaction.followup.send(
                            "I don't have permission to rename this thread (need Manage Threads).",
                            ephemeral=True,
                        )
                    except discord.HTTPException as e:
                        await interaction.followup.send(
                            f"Couldn't rename the thread: {e}",
                            ephemeral=True,
                        )
                else:
                    await interaction.followup.send(
                        "Couldn't find the thread to rename it.", ephemeral=True
                    )

        updated = self.input.value or "(none)"
        await interaction.edit_original_response(
            content=f"**{self.label}** updated to: {updated}"
        )




PERSON_FIELDS_CRIMINAL = {"defendant", "attorney", "filed_by"}


class _EditPersonSelect(discord.ui.UserSelect):
    def __init__(self, max_values, custom_id, handle_cb):
        super().__init__(
            placeholder="Select a Discord member",
            min_values=1,
            max_values=max_values,
            custom_id=custom_id,
        )
        self._handle_cb = handle_cb

    async def callback(self, interaction: discord.Interaction):
        await self._handle_cb(interaction, list(self.values))


class _EditPersonView(discord.ui.View):
    def __init__(self, cog, case_id, field_name, label):
        super().__init__(timeout=300)
        self.cog = cog
        self.case_id = case_id
        self.field_name = field_name
        self.label = label

        max_vals = 5 if field_name == "attorneys" else 1
        select = _EditPersonSelect(
            max_values=max_vals,
            custom_id=f"dw_edit_person_{case_id}_{field_name}",
            handle_cb=self._handle_select,
        )
        self.add_item(select)

    async def _handle_select(self, interaction, selected):
        can_edit = await _can_edit(interaction, self.cog, self.case_id)
        if not can_edit:
            return
        await interaction.response.defer(ephemeral=True)

        if self.field_name == "attorneys":
            value = ",".join(str(m.id) for m in selected)
            mentions = " ".join(m.mention for m in selected)
        else:
            value = str(selected[0].id)
            mentions = selected[0].mention

        async with self.cog.config.guild(interaction.guild).dockets() as dockets:
            if self.case_id not in dockets:
                await interaction.edit_original_response(content="Docket no longer exists.")
                return
            dockets[self.case_id][self.field_name] = value

            if self.field_name in ("plaintiff", "defendant"):
                p_id = dockets[self.case_id].get("plaintiff")
                d_id = dockets[self.case_id].get("defendant")
                if p_id and d_id:
                    p = interaction.guild.get_member(int(p_id))
                    d = interaction.guild.get_member(int(d_id))
                    if p and d:
                        new_title = f"{p.display_name} vs {d.display_name}"
                        dockets[self.case_id]["docket_title"] = new_title

        all_dockets = await self.cog.config.guild(interaction.guild).dockets()
        data = all_dockets.get(self.case_id, {})
        data["_case_id"] = self.case_id
        await _refresh_starter_message(interaction.guild, data)

        thread_id = data.get("thread_id")
        if thread_id:
            thread = await _get_thread(interaction.guild, thread_id)
            if thread:
                new_name = data.get("docket_title", "")
                if new_name:
                    try:
                        await thread.edit(name=new_name[:100])
                    except discord.Forbidden:
                        await interaction.followup.send(
                            "I don't have permission to rename this thread (need Manage Threads).",
                            ephemeral=True,
                        )
                    except discord.HTTPException as e:
                        await interaction.followup.send(
                            f"Couldn't rename the thread: {e}",
                            ephemeral=True,
                        )
            else:
                await interaction.followup.send(
                    "Couldn't find the thread to rename it.", ephemeral=True
                )

        await interaction.edit_original_response(content=f"**{self.label}** updated to: {mentions}")


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
            ("charges", "Charges", data.get("charges", ""), False, "One charge per line", True),
            ("hearing_date", "Hearing Date", data.get("hearing_date", ""), False, "e.g., July 20, 2026"),
        ]

        for i in range(0, len(fields), 3):
            row_fields = fields[i:i + 3]
            for entry in row_fields:
                name, label, current, required, placeholder = entry[:5]
                multiline = entry[5] if len(entry) > 5 else False
                btn = discord.ui.Button(
                    label=f"Edit {label}",
                    style=discord.ButtonStyle.secondary,
                    custom_id=f"dw_edit_cr_{case_id}_{name}",
                )
                btn.callback = self._make_callback(name, label, current, placeholder, required, multiline)
                self.add_item(btn)

    def _make_callback(self, name, label, current, placeholder, required, multiline=False):
        async def callback(interaction: discord.Interaction):
            can_edit = await _can_edit(interaction, self.cog, self.case_id)
            if not can_edit:
                return
            if name in PERSON_FIELDS_CRIMINAL:
                await interaction.response.send_message(
                    view=_EditPersonView(self.cog, self.case_id, name, label),
                    ephemeral=True,
                )
            else:
                modal = _EditFieldModal(self.cog, self.case_id, name, label, current, placeholder, required, multiline)
                await interaction.response.send_modal(modal)
        return callback





class _DocketFieldModal(discord.ui.Modal):
    def __init__(self, label, placeholder, required, callback_fn, multiline=False):
        super().__init__(title=label)
        self.field = discord.ui.TextInput(
            label=label,
            placeholder=placeholder,
            required=required,
            style=discord.TextStyle.paragraph if multiline else discord.TextStyle.short,
            max_length=1000,
        )
        self.add_item(self.field)
        self._callback = callback_fn

    async def on_submit(self, interaction: discord.Interaction):
        await self._callback(interaction, self.field.value)


class _DocketCreateView(discord.ui.View):
    def __init__(self, cog, user):
        super().__init__(timeout=600)
        self.cog = cog
        self.user = user
        self.selected = {}
        self.text = {}

        self._add_select("defendant", "Select Defendant", 1, 1)
        self._add_select("attorney", "Select Attorney (optional)", 0, 1)
        self._add_select("filed_by", "Select Officer or DA", 1, 1)

    def _add_select(self, field, placeholder, min_vals, max_vals):
        sel = _DocketUserSelect(field, placeholder, min_vals, max_vals, self._on_select)
        self.add_item(sel)

    async def _on_select(self, interaction, field, members):
        if interaction.user != self.user:
            await interaction.response.send_message("This is not your docket form.", ephemeral=True)
            return
        self.selected[field] = members
        embed = self._build_status_embed()
        await interaction.response.edit_message(embed=embed, view=self)

    def _build_status_embed(self):
        lines = []
        for f in ("case_number", "docket_title", "defendant", "attorney", "filed_by", "charges", "hearing_date"):
            if f in ("case_number", "docket_title", "charges", "hearing_date"):
                val = self.text.get(f) or "Not set"
            else:
                members = self.selected.get(f, [])
                val = members[0].mention if members else "Not set"
            lines.append(f"**{f.replace('_', ' ').title()}:** {val}")

        embed = discord.Embed(
            title="\u2696\ufe0f New Criminal Docket",
            description="\n".join(lines),
            color=discord.Color.red(),
        )
        embed.set_footer(text="Fill in all fields, then click Create Docket")
        return embed

    def _set_text(self, field, value):
        self.text[field] = value

    async def _make_field_callback(self, field, label, placeholder, required):
        async def callback(interaction, value):
            if interaction.user != self.user:
                await interaction.response.send_message("This is not your docket form.", ephemeral=True)
                return
            self._set_text(field, value.strip())
            embed = self._build_status_embed()
            await interaction.response.edit_message(embed=embed, view=self)
        return callback

    def _add_text_button(self, field, label, placeholder, required, row, multiline=False):
        async def btn_callback(interaction):
            if interaction.user != self.user:
                await interaction.response.send_message("This is not your docket form.", ephemeral=True)
                return
            current = self.text.get(field, "")
            cb = await self._make_field_callback(field, label, placeholder, required)
            modal = _DocketFieldModal(label, placeholder, required, cb, multiline)
            await interaction.response.send_modal(modal)

        btn = discord.ui.Button(
            label=label,
            style=discord.ButtonStyle.secondary,
            row=row,
        )
        btn.callback = btn_callback
        self.add_item(btn)

    async def _create(self, interaction: discord.Interaction):
        if interaction.user != self.user:
            await interaction.response.send_message("This is not your docket form.", ephemeral=True)
            return

        guild = interaction.guild
        case_id = self.text.get("case_number", "").strip().upper()
        if not case_id:
            await interaction.response.send_message("Please set the Case Number first.", ephemeral=True)
            return

        if case_id in await self.cog.config.guild(guild).dockets():
            if not await _cleanup_stale_case(guild, self.cog, case_id):
                await interaction.response.send_message(f"Case number `{case_id}` already exists.", ephemeral=True)
                return

        missing = [f for f in ("defendant", "filed_by") if f not in self.selected or not self.selected[f]]
        if missing:
            names = [f.replace("_", " ").title() for f in missing]
            await interaction.response.send_message(f"Please select: {', '.join(names)}", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        forum_id = await self.cog.config.guild(guild).forum_channel_id()
        if not forum_id:
            await interaction.edit_original_response(content="No forum channel set. Use `dw setforum`.")
            return
        forum = guild.get_channel(forum_id)
        if not forum or not isinstance(forum, discord.ForumChannel):
            await interaction.edit_original_response(content="Forum channel no longer available.")
            return

        ts_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        defendants = self.selected.get("defendant", [])
        attorneys = self.selected.get("attorney", [])
        filed_by = self.selected.get("filed_by", [])
        case_data = {
            "type": "criminal",
            "case_number": case_id,
            "docket_title": self.text.get("docket_title", "Criminal Docket"),
            "defendant": str(defendants[0].id) if defendants else None,
            "attorney": str(attorneys[0].id) if attorneys else None,
            "filed_by": str(filed_by[0].id) if filed_by else None,
            "charges": self.text.get("charges") or None,
            "hearing_date": self.text.get("hearing_date") or None,
            "created_at": ts_str,
            "created_by": interaction.user.id,
        }
        embed = _build_criminal_embed(case_id, case_data)
        thread_name = case_data["docket_title"][:100]
        edit_view = _CriminalEditView(self.cog, case_id, case_data)

        thread = await forum.create_thread(
            name=thread_name,
            embed=embed,
            content=f"**Case #{case_id}** \u2014 Criminal Docket filed by {interaction.user.mention}",
        )

        actual_thread = thread.thread if hasattr(thread, 'thread') else thread
        try:
            sm = actual_thread.starter_message
            if sm is None:
                sm = await actual_thread.fetch_message(actual_thread.id)
        except (discord.NotFound, discord.Forbidden):
            sm = None

        case_data["thread_id"] = actual_thread.id
        case_data["starter_message_id"] = sm.id if sm else None

        async with self.cog.config.guild(guild).dockets() as dockets:
            dockets[case_id] = case_data

        await actual_thread.send(
            content=f"**Docket Actions** \u2014 Use the buttons below to edit this docket. Only {interaction.user.mention} or authorized roles can edit.",
            view=edit_view,
        )

        try:
            await interaction.edit_original_response(content=f"Docket created: {actual_thread.mention}", embed=None, view=None)
        except (discord.NotFound, discord.HTTPException):
            pass


class _DocketUserSelect(discord.ui.UserSelect):
    def __init__(self, field_name, placeholder, min_values, max_values, handle_cb):
        super().__init__(
            placeholder=placeholder,
            min_values=min_values,
            max_values=max_values,
        )
        self._field_name = field_name
        self._handle_cb = handle_cb

    async def callback(self, interaction: discord.Interaction):
        await self._handle_cb(interaction, self._field_name, list(self.values))


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
        view = _DocketCreateView(self.cog, interaction.user)
        view._add_text_button("case_number", "Case Number", "e.g., CR-2026-001", True, 3)
        view._add_text_button("docket_title", "Docket Title", "e.g., State vs John Doe", True, 3)
        view._add_text_button("charges", "Charges", "One charge per line", False, 4, multiline=True)
        view._add_text_button("hearing_date", "Hearing Date", "e.g., July 20, 2026", False, 4)

        btn = discord.ui.Button(label="Create Docket", style=discord.ButtonStyle.success, row=4)
        btn.callback = view._create
        view.add_item(btn)

        await interaction.response.send_message(
            embed=view._build_status_embed(),
            view=view,
            ephemeral=True,
        )


class _SetupSelect(discord.ui.ChannelSelect):
    def __init__(self, store_attr, channel_types, placeholder, min_vals, max_vals):
        super().__init__(
            channel_types=channel_types,
            placeholder=placeholder,
            min_values=min_vals,
            max_values=max_vals,
        )
        self._store_attr = store_attr

    async def callback(self, interaction: discord.Interaction):
        if interaction.user != self.view.author:
            await interaction.response.send_message("Only the command author can configure setup.", ephemeral=True)
            return
        if self.values:
            setattr(self.view, self._store_attr, self.values[0])
        await self.view.refresh(interaction)


class _SetupRoleSelect(discord.ui.RoleSelect):
    def __init__(self, store_attr, placeholder, min_vals, max_vals):
        super().__init__(
            placeholder=placeholder,
            min_values=min_vals,
            max_values=max_vals,
        )
        self._store_attr = store_attr

    async def callback(self, interaction: discord.Interaction):
        if interaction.user != self.view.author:
            await interaction.response.send_message("Only the command author can configure setup.", ephemeral=True)
            return
        setattr(self.view, self._store_attr, self.values[0] if self.values else None)
        await self.view.refresh(interaction)


class _SetupView(discord.ui.View):
    def __init__(self, cog, author):
        super().__init__(timeout=300)
        self.cog = cog
        self.author = author
        self.text_channel = None
        self.forum_channel = None
        self.role = None

        self.add_item(_SetupSelect(
            "text_channel", [discord.ChannelType.text],
            "Select the text channel for buttons", 1, 1,
        ))
        self.add_item(_SetupSelect(
            "forum_channel", [discord.ChannelType.forum],
            "Select the forum channel for docket threads", 1, 1,
        ))
        self.add_item(_SetupRoleSelect(
            "role", "Select edit role (optional)", 0, 1,
        ))

        done = discord.ui.Button(label="Complete Setup", style=discord.ButtonStyle.success, row=3)
        done.callback = self._complete
        self.add_item(done)

    def _status_embed(self):
        lines = [
            f"**Text Channel:** {self.text_channel.mention if self.text_channel else 'Not set'}",
            f"**Forum Channel:** {self.forum_channel.mention if self.forum_channel else 'Not set'}",
            f"**Edit Role:** {self.role.mention if self.role else 'None (optional)'}",
        ]
        return discord.Embed(
            title="\u2696\ufe0f DocketWizard Setup",
            description="\n".join(lines),
            color=discord.Color.gold(),
        )

    async def refresh(self, interaction: discord.Interaction):
        await interaction.response.edit_message(embed=self._status_embed(), view=self)

    async def _complete(self, interaction: discord.Interaction):
        if interaction.user != self.author:
            await interaction.response.send_message("Only the command author can configure setup.", ephemeral=True)
            return
        if not self.text_channel or not self.forum_channel:
            await interaction.response.send_message("Please select both a text channel and a forum channel.", ephemeral=True)
            return

        guild = interaction.guild
        channel = guild.get_channel(self.text_channel.id) or self.text_channel
        forum = guild.get_channel(self.forum_channel.id) or self.forum_channel

        view = _DocketWizardView(self.cog)
        embed = discord.Embed(
            title="\u2696\ufe0f DocketWizard",
            description="Click a button below to create a new docket entry.",
            color=discord.Color.gold(),
        )
        msg = await channel.send(embed=embed, view=view)
        await self.cog.config.guild(guild).channel_id.set(channel.id)
        await self.cog.config.guild(guild).setup_message_id.set(msg.id)
        await self.cog.config.guild(guild).forum_channel_id.set(forum.id)
        if self.role:
            await self.cog.config.guild(guild).docket_creator_role_id.set(self.role.id)

        await interaction.response.edit_message(
            content=f"Setup complete! Buttons sent to {channel.mention}.",
            embed=None, view=None,
        )


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
    async def setup(self, ctx: commands.Context):
        """Set up the docket wizard interactively."""
        view = _SetupView(self, ctx.author)
        try:
            await ctx.send(embed=view._status_embed(), view=view, ephemeral=True)
        except TypeError:
            await ctx.send(embed=view._status_embed(), view=view)



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

    @dw.command(name="cleanup")
    @commands.admin_or_permissions(administrator=True)
    async def cleanup(self, ctx: commands.Context):
        """Remove stale dockets whose forum threads no longer exist."""
        cleaned = 0
        async with self.config.guild(ctx.guild).dockets() as dockets:
            for case_id in list(dockets.keys()):
                data = dockets[case_id]
                thread_id = data.get("thread_id")
                if thread_id:
                    thread = await _get_thread(ctx.guild, thread_id)
                    if thread:
                        continue
                del dockets[case_id]
                cleaned += 1
        await ctx.send(f"Cleaned up {cleaned} stale docket(s).")

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


