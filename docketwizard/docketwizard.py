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
    desc = (
        f"**Case # :** {case_id}\n"
        f"**Defendant :** {_mention(data, 'defendant')}\n"
        f"**Attorney :** {_mention(data, 'attorney')}\n"
        f"**Filed By :** {_mention(data, 'filed_by')}\n"
        f"**Filed At :** {data.get('created_at', 'N/A')}\n"
    )
    if data.get("hearing_date"):
        desc += f"**Hearing Date :** {data['hearing_date']}\n"
    embed = discord.Embed(
        title=f"\u2696\ufe0f {data.get('docket_title', 'Criminal Docket')}",
        description=desc.strip(),
        color=discord.Color.red(),
    )
    embed.set_footer(text=f"Case #{case_id} \u2022 Criminal")
    return embed


def _build_civil_embed(case_id, data):
    desc = (
        f"**Case # :** {case_id}\n"
        f"**Plaintiff :** {_mention(data, 'plaintiff')}\n"
        f"**Defendant :** {_mention(data, 'defendant')}\n"
    )
    attorneys = data.get("attorneys")
    if attorneys:
        desc += f"**Attorneys :** {' '.join(f'<@{uid}>' for uid in attorneys.split(','))}\n"
    else:
        desc += "**Attorneys :** None assigned\n"
    desc += (
        f"**Filed By :** {_mention(data, 'filed_by')}\n"
        f"**Filed At :** {data.get('created_at', 'N/A')}\n"
    )
    if data.get("hearing_date"):
        desc += f"**Hearing Date :** {data['hearing_date']}\n"
    embed = discord.Embed(
        title=f"\U0001f4cb {data.get('docket_title', 'Civil Docket')}",
        description=desc.strip(),
        color=discord.Color.blue(),
    )
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

    thread = _get_thread(guild, thread_id)
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


def _get_thread(guild, thread_id):
    thread = _get_thread(guild, thread_id)
    if thread is None:
        try:
            thread = guild.get_thread(thread_id)
        except AttributeError:
            pass
    return thread


async def _cleanup_stale_case(guild, cog, case_id):
    async with cog.config.guild(guild).dockets() as dockets:
        if case_id not in dockets:
            return False
        data = dockets[case_id]
        thread_id = data.get("thread_id")
        if thread_id:
            thread = _get_thread(guild, thread_id)
            if thread:
                return False
        del dockets[case_id]
        return True


class _EditFieldModal(discord.ui.Modal):
    def __init__(self, cog, case_id, field_name, label, current_value, placeholder="", required=False):
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

        if self.field_name == "docket_title":
            thread_id = data.get("thread_id")
            if thread_id:
                thread = _get_thread(interaction.guild, thread_id)
                if thread:
                    try:
                        await thread.edit(name=self.input.value[:100])
                    except (discord.Forbidden, discord.HTTPException):
                        pass

        updated = self.input.value or "(none)"
        await interaction.edit_original_response(
            content=f"**{self.label}** updated to: {updated}"
        )



class _PeopleUserSelect(discord.ui.UserSelect):
    def __init__(self, view_ref, field_name, **kwargs):
        super().__init__(**kwargs)
        self.view_ref = view_ref
        self.field_name = field_name

    async def callback(self, interaction: discord.Interaction):
        self.view_ref.selected[self.field_name] = [m for m in self.values]
        await interaction.response.defer()


class _CriminalPeopleView(discord.ui.View):
    def __init__(self, cog, partial_data):
        super().__init__(timeout=300)
        self.cog = cog
        self.partial = partial_data
        self.selected = {}
        cid = partial_data["case_number"]

        self.add_item(_PeopleUserSelect(
            self, "defendant",
            placeholder="Select the Defendant",
            min_values=1, max_values=1,
            custom_id=f"dw_cr_people_def_{cid}",
        ))
        self.add_item(_PeopleUserSelect(
            self, "attorney",
            placeholder="Select the Attorney (optional)",
            min_values=0, max_values=1,
            custom_id=f"dw_cr_people_atty_{cid}",
        ))
        self.add_item(_PeopleUserSelect(
            self, "filed_by",
            placeholder="Select the Officer or DA",
            min_values=1, max_values=1,
            custom_id=f"dw_cr_people_filed_{cid}",
        ))

        btn = discord.ui.Button(label="Create Docket", style=discord.ButtonStyle.success, custom_id=f"dw_cr_create_{cid}")
        btn.callback = self._create
        self.add_item(btn)

    async def _create(self, interaction: discord.Interaction):
        if "defendant" not in self.selected or not self.selected["defendant"]:
            await interaction.response.send_message("Please select a Defendant first.", ephemeral=True)
            return
        if "filed_by" not in self.selected or not self.selected["filed_by"]:
            await interaction.response.send_message("Please select who filed the docket first.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        p = self.partial
        sel = self.selected

        case_id = p["case_number"]
        ts_str = p["created_at"]

        forum_id = await self.cog.config.guild(guild).forum_channel_id()
        if not forum_id:
            await interaction.edit_original_response(content="No forum channel set. Use `dw setforum`.")
            return
        forum = guild.get_channel(forum_id)
        if not forum or not isinstance(forum, discord.ForumChannel):
            await interaction.edit_original_response(content="Forum channel no longer available.")
            return

        def uid(member_list):
            return str(member_list[0].id) if member_list else None

        case_data = {
            "type": "criminal",
            "case_number": case_id,
            "docket_title": p["docket_title"],
            "defendant": uid(sel.get("defendant")),
            "attorney": uid(sel.get("attorney")),
            "filed_by": uid(sel.get("filed_by")),
            "hearing_date": p.get("hearing_date"),
            "created_at": ts_str,
            "created_by": interaction.user.id,
        }

        embed = _build_criminal_embed(case_id, case_data)
        thread_name = p["docket_title"][:100]

        thread = await forum.create_thread(
            name=thread_name,
            embed=embed,
            content=f"**Case #{case_id}** \u2014 Criminal Docket filed by {interaction.user.mention}",
        )

        actual_thread = thread.thread if hasattr(thread, 'thread') else thread
        try:
            starter_msg = actual_thread.starter_message
            if starter_msg is None:
                starter_msg = await actual_thread.fetch_message(actual_thread.id)
        except (discord.NotFound, discord.Forbidden):
            starter_msg = None

        case_data["thread_id"] = actual_thread.id
        case_data["starter_message_id"] = starter_msg.id if starter_msg else None

        async with self.cog.config.guild(guild).dockets() as dockets:
            dockets[case_id] = case_data

        edit_view = _CriminalEditView(self.cog, case_id, case_data)
        await actual_thread.send(
            content=f"**Docket Actions** \u2014 Use the buttons below to edit this docket. Only {interaction.user.mention} or authorized roles can edit.",
            view=edit_view,
        )

        await interaction.edit_original_response(content=f"Docket thread created: {actual_thread.mention}")


class _CivilPeopleView(discord.ui.View):
    def __init__(self, cog, partial_data):
        super().__init__(timeout=300)
        self.cog = cog
        self.partial = partial_data
        self.selected = {}
        cid = partial_data["case_number"]

        self.add_item(_PeopleUserSelect(
            self, "plaintiff",
            placeholder="Select the Plaintiff",
            min_values=1, max_values=1,
            custom_id=f"dw_cv_people_pl_{cid}",
        ))
        self.add_item(_PeopleUserSelect(
            self, "defendant",
            placeholder="Select the Defendant",
            min_values=1, max_values=1,
            custom_id=f"dw_cv_people_def_{cid}",
        ))
        self.add_item(_PeopleUserSelect(
            self, "attorneys",
            placeholder="Select Attorney(s) (optional)",
            min_values=0, max_values=5,
            custom_id=f"dw_cv_people_atty_{cid}",
        ))
        self.add_item(_PeopleUserSelect(
            self, "filed_by",
            placeholder="Select who filed this case",
            min_values=1, max_values=1,
            custom_id=f"dw_cv_people_filed_{cid}",
        ))

        btn = discord.ui.Button(label="Create Docket", style=discord.ButtonStyle.success, custom_id=f"dw_cv_create_{cid}")
        btn.callback = self._create
        self.add_item(btn)

    async def _create(self, interaction: discord.Interaction):
        if "plaintiff" not in self.selected or not self.selected["plaintiff"]:
            await interaction.response.send_message("Please select a Plaintiff first.", ephemeral=True)
            return
        if "defendant" not in self.selected or not self.selected["defendant"]:
            await interaction.response.send_message("Please select a Defendant first.", ephemeral=True)
            return
        if "filed_by" not in self.selected or not self.selected["filed_by"]:
            await interaction.response.send_message("Please select who filed the docket first.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        p = self.partial
        sel = self.selected

        case_id = p["case_number"]
        ts_str = p["created_at"]

        forum_id = await self.cog.config.guild(guild).forum_channel_id()
        if not forum_id:
            await interaction.edit_original_response(content="No forum channel set. Use `dw setforum`.")
            return
        forum = guild.get_channel(forum_id)
        if not forum or not isinstance(forum, discord.ForumChannel):
            await interaction.edit_original_response(content="Forum channel no longer available.")
            return

        plaintiff_id = str(sel["plaintiff"][0].id)
        defendant_id = str(sel["defendant"][0].id)
        filed_by_id = str(sel["filed_by"][0].id)
        atty_ids = ",".join(str(m.id) for m in sel.get("attorneys", []))

        auto_title = f"{sel['plaintiff'][0].display_name} vs {sel['defendant'][0].display_name}"

        case_data = {
            "type": "civil",
            "case_number": case_id,
            "docket_title": p.get("docket_title") or auto_title,
            "plaintiff": plaintiff_id,
            "defendant": defendant_id,
            "attorneys": atty_ids or None,
            "filed_by": filed_by_id,
            "hearing_date": p.get("hearing_date"),
            "created_at": ts_str,
            "created_by": interaction.user.id,
        }

        embed = _build_civil_embed(case_id, case_data)
        thread_name = (p.get("docket_title") or auto_title)[:100]

        thread = await forum.create_thread(
            name=thread_name,
            embed=embed,
            content=f"**Case #{case_id}** \u2014 Civil Docket filed by {interaction.user.mention}",
        )

        actual_thread = thread.thread if hasattr(thread, 'thread') else thread
        try:
            starter_msg = actual_thread.starter_message
            if starter_msg is None:
                starter_msg = await actual_thread.fetch_message(actual_thread.id)
        except (discord.NotFound, discord.Forbidden):
            starter_msg = None

        case_data["thread_id"] = actual_thread.id
        case_data["starter_message_id"] = starter_msg.id if starter_msg else None

        async with self.cog.config.guild(guild).dockets() as dockets:
            dockets[case_id] = case_data

        edit_view = _CivilEditView(self.cog, case_id, case_data)
        await actual_thread.send(
            content=f"**Docket Actions** \u2014 Use the buttons below to edit this docket. Only {interaction.user.mention} or authorized roles can edit.",
            view=edit_view,
        )

        await interaction.edit_original_response(content=f"Docket thread created: {actual_thread.mention}")


PERSON_FIELDS_CRIMINAL = {"defendant", "attorney", "filed_by"}
PERSON_FIELDS_CIVIL = {"plaintiff", "defendant", "attorneys", "filed_by"}


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
            thread = _get_thread(interaction.guild, thread_id)
            if thread:
                new_name = data.get("docket_title", "")
                if new_name:
                    try:
                        await thread.edit(name=new_name[:100])
                    except (discord.Forbidden, discord.HTTPException):
                        pass

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
            if name in PERSON_FIELDS_CRIMINAL:
                await interaction.response.send_message(
                    view=_EditPersonView(self.cog, self.case_id, name, label),
                    ephemeral=True,
                )
            else:
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
            if name in PERSON_FIELDS_CIVIL:
                await interaction.response.send_message(
                    view=_EditPersonView(self.cog, self.case_id, name, label),
                    ephemeral=True,
                )
            else:
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
    hearing_date = discord.ui.TextInput(
        label="Hearing Date",
        placeholder="e.g., July 20, 2026 at 9:00 AM",
        required=False,
        max_length=100,
    )

    def __init__(self, cog: "DocketWizard"):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        case_id = self.case_number.value.strip().upper()
        ts_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        guild = interaction.guild

        if case_id in await self.cog.config.guild(guild).dockets():
            if not await _cleanup_stale_case(guild, self.cog, case_id):
                await interaction.response.send_message(
                    f"Case number `{case_id}` already exists. Please use a unique case number.",
                    ephemeral=True,
                )
                return

        partial = {
            "case_number": case_id,
            "docket_title": self.docket_title.value.strip(),
            "hearing_date": self.hearing_date.value.strip() or None,
            "created_at": ts_str,
        }

        await interaction.response.send_message(
            "**Step 2:** Select the people involved in this docket from the dropdowns below, then click **Create Docket**.",
            view=_CriminalPeopleView(self.cog, partial),
            ephemeral=True,
        )


class _CivilDocketModal(discord.ui.Modal, title="New Civil Docket"):
    case_number = discord.ui.TextInput(
        label="Case Number",
        placeholder="e.g., CV-2026-001",
        required=True,
        max_length=50,
    )
    hearing_date = discord.ui.TextInput(
        label="Hearing Date",
        placeholder="e.g., July 20, 2026 at 9:00 AM",
        required=False,
        max_length=100,
    )

    def __init__(self, cog: "DocketWizard"):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        case_id = self.case_number.value.strip().upper()
        ts_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        guild = interaction.guild

        existing = await self.cog.config.guild(guild).dockets()
        if case_id in existing:
            await interaction.response.send_message(
                f"Case number `{case_id}` already exists. Please use a unique case number.",
                ephemeral=True,
            )
            return

        partial = {
            "case_number": case_id,
            "docket_title": None,
            "hearing_date": self.hearing_date.value.strip() or None,
            "created_at": ts_str,
        }

        await interaction.response.send_message(
            "**Step 2:** Select the people involved in this docket from the dropdowns below, then click **Create Docket**.",
            view=_CivilPeopleView(self.cog, partial),
            ephemeral=True,
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
                    thread = _get_thread(ctx.guild, thread_id)
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


