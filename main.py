import discord
from discord import Embed
from discord.ext import tasks
from discord.ext import commands
from discord import app_commands
from discord.ui import Button
import json
import time
from github import Github
import traceback
import random
from typing import Optional, Callable
from datetime import timedelta
import asyncio

with open("./tokens.json", "r") as f:
    tokens = json.load(f)

MY_GUILD = discord.Object(id=tokens["ids"]["guild_id"])
MODERATOR_ROLE_ID = tokens["ids"]["mod_role_id"]
TICKET_DASHBOARD_CHANNEL = tokens["ids"]["ticket_dashboard_id"]
AUDIT_LOG_CHANNEL = tokens["ids"]["audit_log_id"]
OWNER_ROLE_ID = tokens["ids"]["owner_role_id"]
L = 5
ticketer_version = tokens["bot"]["bot_version"]
g = Github(tokens["external_tokens/keys"]["github"])
strike_path = tokens["external_files"]["strike"]
rule_path = tokens["external_files"]["rules"]

def load_strikes():
    with open(strike_path, "r") as j:
        return json.load(j)
    
def save_strikes(data):
    with open(strike_path, "w") as j:
        json.dump(data, j, indent=4)

def load_rules():
    with open(rule_path, "r") as j:
        return json.load(j)
    
def save_rules(data):
    with open(rule_path, "w") as j:
        json.dump(data, j, indent=4)
    
def is_mod(interaction: discord.Interaction):
    if interaction.user.get_role(MODERATOR_ROLE_ID) is not None:
        return True

def is_owner(interaction: discord.Interaction):
    if interaction.user.get_role(OWNER_ROLE_ID) is not None:
        return True
    
def generate_unix_time_code():
    return int(time.time())    
    
class MyClient(discord.Client):
    def __init__(self, *, intents: discord.Intents):
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
        
    async def setup_hook(self):
        self.tree.copy_global_to(guild=MY_GUILD)
        await self.tree.sync(guild=MY_GUILD)

intents = discord.Intents.default()
intents.members = True
intents.presences = True
intents.message_content = True
intents.voice_states = True
intents.guilds = True 
bot = MyClient(intents=intents)

class ticket_controls(discord.ui.View): # Buttons to A=archive or delete the ticket
    def __init__(self, channel: discord.TextChannel):
        super().__init__(timeout=None)
        self.channel = channel
    
    @discord.ui.button(label="Archive", style=discord.ButtonStyle.success, custom_id="archive_ticket")
    async def archive(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        # Check if the user has the required role
        role = discord.utils.get(interaction.guild.roles, name='all mods')
        if role not in interaction.user.roles:
            await interaction.response.send_message("❌ You can't archive this ticket.", ephemeral=True)
            return
        else:
    
            default_role = discord.utils.get(interaction.guild.roles, name='👥Members👥')
            category = discord.utils.get(guild.categories, name="archive")
            overwrites = self.channel.overwrites_for(role)
            default_overwrites = self.channel.overwrites_for(default_role)
            overwrites.read_messages = True  # Allow the role to read messages
            overwrites.send_messages = False  # Deny the role to send messages
            overwrites.manage_messages = False  # Deny the role to manage messages
            default_overwrites.view_channel = False
            await self.channel.set_permissions(interaction.user, overwrite=None)
            await self.channel.set_permissions(interaction.guild.default_role, read_messages=False)
            await self.channel.set_permissions(role, overwrite=overwrites)
            await self.channel.edit(category=category)
            await interaction.response.defer()
            
    @discord.ui.button(label="Delete", style=discord.ButtonStyle.success, custom_id="delete_ticket")
    async def delete(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Check if the user has the required role
        role = discord.utils.get(interaction.guild.roles, name='all mods')
        if role not in interaction.user.roles:
            await interaction.response.send_message("❌ You can't delete this ticket.", ephemeral=True)
            return
        else:
            await interaction.response.send_message("Channel will be deleted in 5 seconds...", ephemeral=True)
        
            # Wait for 5 seconds
            await asyncio.sleep(5)
            
            # Delete the channel
            await self.channel.delete()

class create_ticket_button(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        
    @discord.ui.button(label="Create a ticket", style=discord.ButtonStyle.success, custom_id="create_ticket")
    async def create_ticket(self, interaction: discord.Interaction, button: discord.ui.Button, ):
        guild = interaction.guild
        category = discord.utils.get(guild.categories, name="tickets")
        role = discord.utils.get(guild.roles, name='all mods')
        CHANNEL_NAME = f"ticket-{random.randint(1,1000)}"
        
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),  # Deny @everyone
            role: discord.PermissionOverwrite(read_messages=True),                 # Allow the role
            interaction.user: discord.PermissionOverwrite(read_messages=True)      # Allow the creator
        }
        
        ticket_channel = await guild.create_text_channel(name=CHANNEL_NAME, category=category, overwrites=overwrites)
        await interaction.channel.send(f"✅ Created <#{ticket_channel.id}>", delete_after=5)
        
        embed = Embed(
            title=CHANNEL_NAME,
            description=f"""Hello {interaction.user.mention}!\n Please wait for mods to assist you. The best thing you could do is describing your issue in this text channel and being patient! Thankyou! \n\n ***This message was sent automatically, please ask the mods if there is anything wrong with this bot or with this dialouge.***""",
            colour= discord.colour.Color.blue()
            )
        
        await ticket_channel.send(embed=embed, view=ticket_controls(ticket_channel))
        
        await interaction.response.defer()
        
class Pagination(discord.ui.View):
    def __init__(self, interaction: discord.Interaction, get_page: Callable):
        self.interaction = interaction
        self.get_page = get_page
        self.total_pages: Optional[int] = None
        self.index = 1
        super().__init__(timeout=100)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user == self.interaction.user:
            return True
        else:
            emb = discord.Embed(
                description=f"Only the author of the command can perform this action.",
                color=discord.colour.Color.red()
            )
            await interaction.response.send_message(embed=emb, ephemeral=True)
            return False

    async def navigate(self):
        emb, self.total_pages = await self.get_page(self.index)
        if self.total_pages == 1:
            await self.interaction.response.send_message(embed=emb)
        elif self.total_pages > 1:
            self.update_buttons()
            await self.interaction.response.send_message(embed=emb, view=self)

    async def edit_page(self, interaction: discord.Interaction):
        emb, self.total_pages = await self.get_page(self.index)
        self.update_buttons()
        await interaction.response.edit_message(embed=emb, view=self)

    def update_buttons(self):
        self.children[0].disabled = self.index == 1
        self.children[1].disabled = self.index == 1
        self.children[2].disabled = self.index == self.total_pages
        self.children[3].disabled = self.index == self.total_pages

    @discord.ui.button(emoji="⏮️", style=discord.ButtonStyle.blurple)
    async def first_page(self, interaction: discord.Interaction, button: discord.Button):
        self.index = 1
        await self.edit_page(interaction)

    @discord.ui.button(emoji="◀️", style=discord.ButtonStyle.blurple)
    async def previous(self, interaction: discord.Interaction, button: discord.Button):
        self.index -= 1
        await self.edit_page(interaction)

    @discord.ui.button(emoji="▶️", style=discord.ButtonStyle.blurple)
    async def next(self, interaction: discord.Interaction, button: discord.Button):
        self.index += 1
        await self.edit_page(interaction)

    @discord.ui.button(emoji="⏭️", style=discord.ButtonStyle.blurple)
    async def last_page(self, interaction: discord.Interaction, button: discord.Button):
        self.index = self.total_pages
        await self.edit_page(interaction)

    async def on_timeout(self):
        # Remove buttons on timeout
        message = await self.interaction.original_response()
        await message.delete()
        
    @staticmethod
    def compute_total_pages(total_results: int, results_per_page: int) -> int:
        return ((total_results - 1) // results_per_page) + 1

class bug_report(discord.ui.Modal, title='Bug Report'):
    # name = discord.ui.TextInput(label='Name', placeholder='Your name here...')
    about_bug = discord.ui.TextInput(
        label='Whats the bug with the bot?',
        style=discord.TextStyle.long,
        placeholder='Type your bug issue here...',
        required=True,
        max_length=300,
    )
    bug_recreation = discord.ui.TextInput(
        label='How did you get this bug?',
        style=discord.TextStyle.long,
        placeholder='We need to know this so that we can replicate the same bug on our side and resolve the issue!',
        required=True,
        max_length=300,
    )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.send_message(f'Thanks for your contribution!, {interaction.user.mention}!', ephemeral=True)
        log_channel = bot.get_guild(1263832488493584486).get_channel(1329839312216391863)

        embed = discord.Embed(title=f'Bug Report from {interaction.user.name} (id:{interaction.user.id})!', color=discord.Colour.yellow())
        embed.set_thumbnail(url=interaction.user.avatar)
        embed.description = f"Probelm:\n```{self.about_bug.value}```\nCause:```{self.bug_recreation.value}```"
        await log_channel.send(embed=embed)

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        await interaction.response.send_message('Oops! Something went wrong.', ephemeral=True)
        traceback.print_exception(type(error), error, error.__traceback__)

class about_view(discord.ui.View):
    def __init__(self):
        super().__init__()

@bot.event
async def on_ready():
    print(f"Ready as {bot.user} ID: {bot.user.id}")
    print('------')
    bot.add_view(create_ticket_button())

    embed = Embed(title="Make a permanent strike aappeal here!", color=discord.colour.Color.blue())
    
    ticket_dashboard = bot.get_channel(TICKET_DASHBOARD_CHANNEL)
    await ticket_dashboard.purge(limit=None, check=lambda m: m.author == bot.user)
    await ticket_dashboard.send(embed=embed, view=create_ticket_button())

# ---Automod Functions---

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return  # Ignore bot messages

    # Load the moderation rules
    data = load_rules()

    # Check for channel exceptions
    if message.channel.id in data["channel_exceptions"]:
        return

    # Check for role exceptions
    if any(role.id in data["role_exceptions"] for role in message.author.roles):
        return

    # Check for user exceptions
    if message.author.id in data["user_exceptions"]:
        return

    # Convert message content to lowercase for case-insensitive detection
    words_in_message = message.content.lower().split()

    # Check if any prohibited word is in the message
    violated_word = next((word for word in data["words"] if word.lower() in words_in_message), None)

    if violated_word:
        # Handle the violation (e.g., delete the message and notify the user)
        await message.delete()

        if data["consequences"] == "strike":
                data = load_strikes()
                # Find the user or create a new entry
                try:
                    user_entry = next((user for user in data["users"] if user["user_id"] == message.author.id), None)
                except KeyError:
                    user_entry = None
                    
                if user_entry is None:
                    user_entry = {
                        "name": message.author.name,
                        "user_id": message.author.id,
                        "warnings": []
                    }
                    data["users"].append(user_entry)

                current_time = generate_unix_time_code()
                warning_entry = {
                    "message": f"Automod Violation - {violated_word}",
                    "time": current_time
                }
                
                user_entry["warnings"].append(warning_entry)
                save_strikes(data)
                
                warn_embed = Embed(description=f"You been striked by Ticketer's built-in Automod for reason :- ```Broke Word Rule: {violated_word}```", color=discord.Color.red())
                await message.author.send(embed=warn_embed)
                await message.channel.send(embed=Embed(title="Watch your words!", color=discord.colour.Color.red()), delete_after=5)

        elif data["consequences"] == "timeout":
            timeout_duration = timedelta(minutes=5)
            await message.author.timeout(timeout_duration, reason=f"Automod Violation - {violated_word}")
            await message.channel.send(embed=Embed(title="Watch your words!", color=discord.colour.Color.red()), delete_after=5)
            timemout_embed = Embed(description=f"You been timed out by Ticketer's built-in Automod for reason :- ```Broke Word Rule: {violated_word}```", color=discord.Color.red())
            await message.author.send(embed=timemout_embed)

        elif data["consequences"] == "kick":
            await message.author.kick(reason=f"Automod Violation - {violated_word}")
            kick_embed = Embed(description=f"You been kicked out by Ticketer's built-in Automod for reason :- ```Broke Word Rule: {violated_word}```", color=discord.Color.red())
            await message.author.send(embed=kick_embed)

        elif data["consequences"] == "ban":
            await message.author.ban(reason=f"Automod Violation - {violated_word}")
            ban_embed = Embed(description=f"You been kicked out by Ticketer's built-in Automod for reason :- ```Broke Word Rule: {violated_word}```", color=discord.Color.red())
            await message.author.send(embed=ban_embed)

# ---Audit Log Functions---
@bot.event
async def on_message_delete(message:discord.Message):
    if message.author == bot.user:
        return
    audit_log_channel = bot.get_channel(AUDIT_LOG_CHANNEL)
    embed = Embed(
                title=f"Message deleted from {message.author.name} ({message.author.nick})",
                colour=discord.colour.Colour.blue(),
                description=f"```{message.content or '[Was a Embed]'}```"
                )

    await audit_log_channel.send(embed=embed)
    if message.attachments:
        attachment_urls = "\n".join(attachment.url for attachment in message.attachments)
        await audit_log_channel.send(attachment_urls)
            
    
@bot.event
async def on_message_edit(before: discord.Message, after: discord.Message):
    audit_log_channel = bot.get_channel(AUDIT_LOG_CHANNEL)

    if before.content == after.content and before.attachments == after.attachments:
        return

    embed = discord.Embed(
        title=f"✏️ Message edited by {before.author.name} ({before.author.display_name})",
        colour=discord.Colour.blue(),
        description=f"**Before:**\n```{before.content or '[No Text]'}```\n"
                    f"**After:**\n```{after.content or '[No Text]'}```"
    )

    await audit_log_channel.send(embed=embed)
    
@bot.event
async def on_audit_log_entry_create(entry):
    audit_log_channel = bot.get_channel(AUDIT_LOG_CHANNEL)

    actions = {
        discord.AuditLogAction.kick: ("👢", "User Kicked", discord.Color.orange()),
        discord.AuditLogAction.ban: ("🔨", "User Banned", discord.Color.red()),
        discord.AuditLogAction.unban: ("⚖️", "User Unbanned", discord.Color.green()),
        discord.AuditLogAction.member_move: ("🎧", "User Moved", discord.Color.blue()),
        discord.AuditLogAction.member_update: ("⏳", "User Timed Out", discord.Colour.purple()),
    }

    if entry.action in actions:
        emoji, title, color = actions[entry.action]
        target = entry.target
        actor = entry.user
        reason = entry.reason or "No reason provided."

        embed = discord.Embed(
            title=f"{emoji} {title}",
            color=color
        )
        embed.add_field(name="User", value=f"{target}", inline=True)
        embed.add_field(name="By", value=f"{actor.mention}", inline=True)
        if entry.action in [discord.AuditLogAction.kick, discord.AuditLogAction.ban]:
            embed.add_field(name="Reason", value=reason, inline=False)
        embed.set_footer(text=f"User ID: {target.id} | See Audit Log for more details")
        embed.timestamp = entry.created_at

        await audit_log_channel.send(embed=embed)
            
# ---Moderation Commands---

@bot.tree.command(name="rule_add", description="Set a rule for automod")
@app_commands.describe(
    word="If left blank, automod will not register any word. You can add more words by separating them by a comma.",
    channel_exceptions="If left blank, automod will ignore messages from the channels",
    role_exceptions="If left blank, automod will ignore messages from the roles",
    user_exceptions="If left blank, automod will ignore messages from the users",
    consequences="If left blank, automod will presume the previous consequence. The action to be taken if the rule is violated, Chose from 'strike', 'timeout', 'kick' or 'ban'. These consiquences apply in all the rules."
    )
@app_commands.check(is_owner)
async def rule_add(interaction: discord.Interaction, word: Optional[str], channel_exceptions: Optional[discord.TextChannel], role_exceptions: Optional[discord.Role], user_exceptions: Optional[discord.User], consequences: Optional[str]):
    """Add a rule for Automod. Ownly the owner can add rules"""
    data = load_rules()

    if word in data["words"]:
        await interaction.channel.send("Rule already exists!", delete_after=5)
    elif word != None:
        words = [w.strip() for w in word.split(',')]
        for word in words:
            data["words"].append(word)
        await interaction.channel.send("Rule Added.", delete_after=5)

    if channel_exceptions != None:
        data["channel_exceptions"].append(channel_exceptions.id)
        await interaction.channel.send("Channel Exception Added.", delete_after=5)
    elif channel_exceptions in data["channel_exceptions"]:
        await interaction.channel.send("Channel Exception already exists!", delete_after=5)
    
    if role_exceptions != None:
        data["role_exceptions"].append(role_exceptions.id)
        await interaction.channel.send("Role Exception Added.", delete_after=5)
    elif role_exceptions in data["role_exceptions"]:
        await interaction.channel.send("Role Exception already exists!", delete_after=5)

    if user_exceptions != None:
        data["user_exceptions"].append(user_exceptions.id)
        await interaction.channel.send("User Exception Added.", delete_after=5)
    elif user_exceptions in data["user_exceptions"]:
        await interaction.channel.send("User Exception already exists!", delete_after=5)

    if consequences != None:
        data["consequences"] = consequences.lower()
        await interaction.channel.send("Consequence Changed.", delete_after=5)


    save_rules(data)
    await interaction.response.send_message("Rules Added Successfully!", ephemeral=True)

@bot.tree.command(name="rule_remove", description="Set a rule for automod")
@app_commands.describe(
    word="If left blank, automod will not remove any word. You can add more words to remove by separating them by a comma.",
    channel_exceptions="If left blank, automod will not ignore messages from the channels",
    role_exceptions="If left blank, automod will not ignore messages from the roles",
    user_exceptions="If left blank, automod will not ignore messages from the users",
    )
@app_commands.check(is_owner)
async def rule_remove(interaction: discord.Interaction, word: Optional[str], channel_exceptions: Optional[discord.TextChannel], role_exceptions: Optional[discord.Role], user_exceptions: Optional[discord.User]): #, consequences: Optional[str]):
    """Add a rule for Automod. Only the owner can remove rules"""
    data = load_rules()

    # Remove word(s)
    if word:
        words = [w.strip().lower() for w in word.split(",")]
        removed_words = []
        for w in words:
            if w in data["words"]:
                data["words"].remove(w)
                removed_words.append(w)
        if removed_words:
            await interaction.channel.send(f"Removed words: {', '.join(removed_words)}", delete_after=5)
        else:
            await interaction.channel.send(f"No matching words found to remove!", delete_after=5)

    # Remove channel exception
    if channel_exceptions:
        try:
            data["channel_exceptions"].remove(channel_exceptions.id)
            await interaction.channel.send(f"Removed channel exception: {channel_exceptions.mention}", delete_after=5)
        except ValueError:
            await interaction.channel.send("Channel exception does not exist!", delete_after=5)

    # Remove role exception
    if role_exceptions:
        try:
            data["role_exceptions"].remove(role_exceptions.id)
            await interaction.channel.send(f"Removed role exception: {role_exceptions.name}", delete_after=5)
        except ValueError:
            await interaction.channel.send("Role exception does not exist!", delete_after=5)

    # Remove user exception
    if user_exceptions:
        try:
            data["user_exceptions"].remove(user_exceptions.id)
            await interaction.channel.send(f"Removed user exception: {user_exceptions.mention}", delete_after=5)
        except ValueError:
            await interaction.channel.send("User exception does not exist!", delete_after=5)

    # Save updated rules
    save_rules(data)
    await interaction.response.send_message("Changes saved successfully!", ephemeral=True)

@rule_add.error
async def rule_error(interaction: discord.Interaction, error):
    await interaction.response.send_message(embed=Embed(title=
        "Apparently, you don't have permission to add any rules :(",
        colour=discord.Color.red()
        ),
        ephemeral=True)
    
@rule_remove.error 
async def rule_error(interaction: discord.Interaction, error):
    await interaction.response.send_message(embed=Embed(title=
        "Apparently, you don't have permission to remove any rules :(",
        colour=discord.Color.red()
        ),
        ephemeral=True)


@bot.tree.command(name="strike", description="Strike a user")
@app_commands.describe(member="The member to strike", reason="The reason for the strike")
@app_commands.check(is_mod)
async def strike(interaction: discord.Interaction, member: discord.Member, reason: str):
    """Strikes the user"""
    await apply_strike(interaction, member, reason)

async def apply_strike(interaction: discord.Interaction, member: discord.Member, reason: str):
    data = load_strikes()

    # Find the user or create a new entry
    try:
        user_entry = next((user for user in data["users"] if user["user_id"] == member.id), None)
    except KeyError:
        user_entry = None
        
    if user_entry is None:
        user_entry = {
            "name": member.name,
            "user_id": member.id,
            "warnings": []
        }
        data["users"].append(user_entry)

    current_time = generate_unix_time_code()
    warning_entry = {
        "message": reason,
        "time": current_time
    }
    
    user_entry["warnings"].append(warning_entry)
    save_strikes(data)
    
    warn_embed = Embed(description=f"***{member.mention} has been striked by {interaction.user.mention} for reason :- ***{reason}", color=discord.Color.red())
    await interaction.response.send_message(embed=warn_embed)

@strike.error
async def role_error(interaction: discord.Interaction, error):
    await interaction.response.send_message(embed=Embed(title=
        "Apparently, you don't have permission to strike anyone :(",
        colour=discord.Color.red()
        ),
        ephemeral=True)

@bot.tree.command(name="view_strkies", description="View a member's strike history")
@app_commands.describe(member="The member you want to view their strike log")
@app_commands.check(is_mod)
async def view_strikes(interaction: discord.Interaction, member: discord.Member):
    """View a member's strike history"""
    async def get_page(page: int):
        data = load_strikes()

        # Find the user entry
        user_entry = next((user for user in data["users"] if user["user_id"] == member.id), None)

        if user_entry is None or "warnings" not in user_entry or not user_entry["warnings"]:
            await interaction.response.send_message(f"{member.mention} has no strikes.", ephemeral=True)
            return

        warnings = user_entry["warnings"]
        total_warnings = len(warnings)
        start_index = (page - 1) * L
        end_index = min(start_index + L, total_warnings)
        current_warnings = warnings[start_index:end_index]

        emb = discord.Embed(title="User Strikes", description="")
        for index, warning in enumerate(current_warnings, start=start_index + 1):
            emb.add_field(name=f"Strike {index}", value=f"Time: <t:{warning['time']}:f>\nMessage: {warning['message']}", inline=False)
        emb.set_author(name=f"Requested by {interaction.user}")
        n = Pagination.compute_total_pages(total_warnings, L)
        emb.set_footer(text=f"Page {page} of {n}")
        return emb, n

    await Pagination(interaction, get_page).navigate()

@view_strikes.error
async def role_error(interaction: discord.Interaction, error):
    await interaction.response.send_message(embed=Embed(title=
        "Apparently, you don't have permission get anyone's strike history:(",
        colour=discord.Color.red()
        ),
        ephemeral=True)

@bot.tree.command()
@app_commands.describe(member="The member whose strikes you want to clear")
@app_commands.check(is_mod)
async def clear_strikes(interaction: discord.Interaction, member: discord.Member):
    """Clears all strikes of a user"""
    user_data = load_strikes()

    # Find the user entry by user_id
    user_entry = next((user for user in user_data["users"] if str(user.get("user_id")) == str(member.id)), None)
    
    if user_entry:
        user_entry["warnings"] = []
        save_strikes(user_data)  # Save the entire updated data
        await interaction.response.send_message(f"All strike for {member.mention} have been cleared.")
    else:
        await interaction.response.send_message(f"No strike found for {member.mention}.")
    
@clear_strikes.error
async def role_error(interaction: discord.Interaction, error):
    await interaction.response.send_message(embed=Embed(title=
        "Apparently, you don't have permission to clear anyones strike history :(",
        colour=discord.Color.red()
        ),
        ephemeral=True)

@bot.tree.command()
@app_commands.rename(timeouttime="time")
@app_commands.describe(member="The member you want to timeout", timeouttime="The amount of time to timeout a user (Minutes)")
@app_commands.check(is_mod)
async def timeout(interaction: discord.Interaction, member: discord.Member, timeouttime: Optional[int] = None, reason: Optional[str] = None):
    """Timeouts the member"""
    await apply_timeout(interaction, member, timeouttime, reason)

async def apply_timeout(interaction: discord.Interaction, member: discord.Member, timeouttime: int, reason: str):
    timeout_duration = timedelta(minutes=timeouttime)
    await member.timeout(timeout_duration, reason=reason)
    await interaction.response.send_message(embed=Embed(
        title=f"{member.name}({member.nick}/{member.id}) has been timed out for {timeouttime} minutes at <t:{generate_unix_time_code()}:F>",
        color=discord.Color.red(),
        description=f"```{reason or 'No Reason Given'}```"
        ),
    ephemeral=True)

@timeout.error
async def role_error(interaction: discord.Interaction, error):
    await interaction.response.send_message(embed=Embed(title=
        "Apparently, you don't have permission to timeout anyone :(",
        colour=discord.Color.red()
        ),
        ephemeral=True)
    
@bot.tree.command()
@app_commands.describe(member='The member you want to kick')
@app_commands.check(is_mod)
async def kick(interaction: discord.Interaction, member: discord.Member, reason: Optional[str] = None):
    """Kicks a member"""
    await apply_kick(interaction, member, reason)

async def apply_kick(interaction: discord.Interaction, member: discord.Member, reason: str):
    await member.kick(reason=reason)
    await interaction.response.send_message(embed=Embed(
        title=f"{member.name}({member.nick}/{member.id}) has been kicked at <t:{generate_unix_time_code()}:F>",
        color=discord.Color.red(),
        description=f"```{reason or 'No Reason Given'}```"
        ),
    ephemeral=True)

@kick.error
async def role_error(interaction: discord.Interaction, error):
    await interaction.response.send_message(embed=Embed(title=
        "Apparently, you don't have permission to kick anyone :(",
        colour=discord.Color.red()
        ),
        ephemeral=True)
    
@bot.tree.command()
@app_commands.describe(member='The member you want to ban')
@app_commands.check(is_mod)
async def ban(interaction: discord.Interaction, member: discord.Member, reason: Optional[str] = None):
    """Bans the member"""
    await apply_ban(interaction, member, reason)

async def apply_ban(interaction: discord.Interaction, member: discord.Member, reason: str):
    await member.ban(reason=reason)
    await interaction.response.send_message(embed=Embed(
        title=f"{member.name}({member.nick}/{member.id}) has been banned at <t:{generate_unix_time_code()}:F>",
        color=discord.Color.red(),
        description=f"```{reason or 'No Reason Given'}```"
        ),
    ephemeral=True)

@ban.error
async def role_error(interaction: discord.Interaction, error):
   await interaction.response.send_message(embed=Embed(title=
        "Apparently, you don't have permission to ban anyone :(",
        colour=discord.Color.red()
        ),
        ephemeral=True)

@bot.tree.command(name="lock_channel", description="Locks the channel where the command is run")
@app_commands.check(is_mod)
async def lock_channel(interaction: discord.Interaction, reason: Optional[str]):
    """Locks the channel where the command is run"""
    channel = interaction.channel
    overwrites = channel.overwrites_for(interaction.guild.default_role)
    overwrites.send_messages = False
    overwrites.add_reactions = False
    await channel.set_permissions(interaction.guild.default_role, overwrite=overwrites)
    await interaction.channel.send("🔒 Channel has been locked.", ephemeral=True)
    await interaction.response.send_message(embed=Embed(
        title=f"🔒 Channel has been locked🔒",
        color=discord.Color.purple(),
        description=f"```{reason or 'No Reason Given'}```"
    ))


@lock_channel.error
async def lock_channel_error(interaction: discord.Interaction, error):
    await interaction.response.send_message(embed=Embed(
        title="You don't have permission to lock this channel.",
        color=discord.Color.red()
    ), ephemeral=True)

@bot.tree.command(name="unlock_channel", description="Unlocks the channel where the command is run")
@app_commands.check(is_mod)
async def unlock_channel(interaction: discord.Interaction, reason: Optional[str]):
    """Unlocks the channel where the command is run"""
    channel = interaction.channel
    overwrites = channel.overwrites_for(interaction.guild.default_role)
    overwrites.send_messages = None
    overwrites.add_reactions = None
    await channel.set_permissions(interaction.guild.default_role, overwrite=overwrites)
    await interaction.channel.send("🔓 Channel has been unlocked.", ephemeral=True)
    await channel.purge(limit=1)

@unlock_channel.error   
async def unlock_channel_error(interaction: discord.Interaction, error):
    await interaction.response.send_message(embed=Embed(
        title="You don't have permission to unlock this channel.",
        color=discord.Color.red()
    ), ephemeral=True)

@bot.tree.command()
@app_commands.describe(member='The member you want to create a modlog for', action_taken='The action taken', rule_violation='The rule violated', reason='The reason for the action', notes='Any additional notes')
@app_commands.check(is_mod) 
async def create_modlog(interaction:discord.Interaction, member:discord.Member, action_taken:str, rule_violation:str, reason:str, notes:str):
    """Creates a modlog for a member"""
    await make_modlog(interaction, member, action_taken, rule_violation, reason, notes)

async def make_modlog(interaction: discord.Interaction, member: discord.Member, action_taken: str, rule_violation: str, reason: str, notes: str):
    mod_channel = bot.get_channel(AUDIT_LOG_CHANNEL)
    mod_role = discord.utils.get(interaction.guild.roles, id=MODERATOR_ROLE_ID)

    embed = discord.Embed(
        title=f"Modlog for {member.name} ({member.nick})",
        color=discord.Color.red(),
        description=f"{mod_role.mention}"
    )
    embed.add_field(name="Time", value=f"<t:{generate_unix_time_code()}:F>", inline=True)
    embed.add_field(name="Moderator", value=interaction.user.mention, inline=True)
    embed.add_field(name="Member", value=member.mention, inline=True)
    embed.add_field(name="Action Taken", value=action_taken, inline=True)
    embed.add_field(name="Rule Violation", value=rule_violation, inline=True)
    embed.add_field(name="Reason", value=reason, inline=True)
    embed.add_field(name="Notes", value=notes, inline=True)
    embed.set_thumbnail(url=member.avatar.url)
    await mod_channel.send(embed=embed)
    await mod_channel.send(f"{mod_role.mention}")
    await interaction.response.send_message("Modlog created successfully!", ephemeral=True)

@bot.tree.command()
async def report_bug(interaction: discord.Interaction):
    """Report a Bug"""
    await interaction.response.send_modal(bug_report())

@bot.tree.command()
async def ping(interaction: discord.Interaction):
    """Pong!"""
    await interaction.response.send_message(Embed(title=f"Pong! ms: {bot.latency * 1000}", color=discord.Color.green()))

@bot.tree.command()
async def about(interaction: discord.Interaction):
    """About the Bot"""

    repo_url = "https://github.com/overtue/ticketer-moderation-bot"  # Replace with the desired repository URL
    owner, repo_name = repo_url.split("/")[-2:]

    # Fetch the repository
    repo = g.get_repo(f"{owner}/{repo_name}")

    embed = Embed(
        title=f"Ticketer {ticketer_version}", 
        color=discord.Color.yellow()
        )
    embed.add_field(name="Author", value="overtue")
    embed.add_field(name="Version", value=ticketer_version)
    embed.add_field(name="Update Log", value="```Added Automod Function and Channel Locking and Un-Locking```")
    embed.add_field(name="Stars", value=repo.stargazers_count)
    embed.add_field(name="Forks", value=repo.forks_count)  
    embed.add_field(name="Watchers", value=repo.watchers_count)
    embed.add_field(name="Commits", value=repo.get_commits().totalCount)

    # embed.set_thumbnail(url=bot.get_user(1130883869625815232).avatar.url)

    buttons = discord.ui.Button(style=discord.ButtonStyle.link, label="GitHub", url=repo_url)
    view = about_view()
    view.add_item(buttons)

    await interaction.response.send_message(embed=embed, view=view)
    
    
bot.run(tokens["bot"]["bot_token"])