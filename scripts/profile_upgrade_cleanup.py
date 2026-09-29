"""One-time assertion-guarded maintenance for the approved profile upgrade."""

from pathlib import Path


def replace(path, old, new):
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    if new in text and old not in text:
        return
    assert text.count(old) == 1, (path, old[:80])
    p.write_text(text.replace(old, new), encoding="utf-8")


replace(
    "src/config.py", "enable_long_term_memory: bool = True", "enable_long_term_memory: bool = False"
)
replace(
    "src/config.py",
    '_bool(env, "ENABLE_LONG_TERM_MEMORY", default=True)',
    '_bool(env, "ENABLE_LONG_TERM_MEMORY", default=False)',
)
replace(
    "tests/test_aclient.py",
    '    assert "enhanced creative capabilities" in messages[0]["content"]',
    '    from src.personas import PERSONAS\n    assert PERSONAS["creative"] in messages[0]["content"]',
)
replace(
    "src/personas.py",
    '''    """Legacy compatibility; authorization is enforced by DiscordClient/config."""
    return False''',
    '''    """Read configured admin identities without changing available personalities."""
    import os
    if user_id is None:
        return False
    configured = os.getenv("BOT_ADMIN_IDS")
    if configured is None:
        configured = os.getenv("ADMIN_USER_IDS", "")
    return str(user_id).strip() in {v.strip() for v in configured.split(",") if v.strip()}''',
)
replace(
    "src/member_settings.py",
    "from pathlib import Path",
    "from pathlib import Path\nfrom typing import Any",
)
replace(
    "src/member_settings.py",
    "        out = dict(timezone=zone, visibility=_visibility(data))",
    "        out: dict[str, Any] = dict(timezone=zone, visibility=_visibility(data))",
)
replace("src/profile_client.py", "import os\n", "import os\nfrom typing import TYPE_CHECKING\n")
replace(
    "src/profile_client.py",
    "class ProfileClientMixin:",
    """if TYPE_CHECKING:
    from src.aclient import DiscordClient as _ProfileBase
else:
    _ProfileBase = object


class ProfileClientMixin(_ProfileBase):
    profile_store: ProfileStore | None = None
    _profile_active: dict[int, int]
    _profile_erasing: set[int]""",
)
replace(
    "src/birthday_client.py",
    "from datetime import datetime, timezone",
    "from datetime import datetime, timezone\nfrom typing import TYPE_CHECKING, Any",
)
replace(
    "src/birthday_client.py",
    "_MEMORY_SCOPE = ContextVar('birthday_memory_scope', default=None)",
    "_MEMORY_SCOPE: ContextVar[tuple[Any, Any, Any] | None] = ContextVar('birthday_memory_scope', default=None)",
)
replace(
    "src/birthday_client.py",
    "class BirthdayMemoryMixin:",
    """if TYPE_CHECKING:
    from src.aclient import DiscordClient as _BirthdayBase
else:
    _BirthdayBase = object


class BirthdayMemoryMixin(_BirthdayBase):""",
)
replace(
    "src/birthday_memory.py",
    "from pathlib import Path",
    "from pathlib import Path\nfrom typing import Any",
)
replace(
    "src/birthday_memory.py",
    "    else:\n        mentions = ",
    "    else:\n        assert greeting is not None\n        mentions = ",
)
replace(
    "src/birthday_memory.py",
    "        groups = {}",
    "        groups: dict[tuple[int, int, int], dict[str, Any]] = {}",
)
replace(
    "src/profile_ui.py",
    "        header = ui.TextDisplay(",
    "        header: ui.TextDisplay[Any] = ui.TextDisplay(",
)
replace(
    "src/profile_ui.py",
    "    async def on_error(self, interaction, error):",
    "    async def on_error(self, interaction, error, item=None):",
)
replace(
    "src/profile_client.py",
    "        busy = getattr(self, '_profile_erasing', set())",
    "        busy: set[int] = getattr(self, '_profile_erasing', set())",
)
replace(
    "src/profile_client.py",
    """            for key in list(self.settings):
                if key[0] == guild_id and key[2] == user_id:
                    self.settings.pop(key, None)""",
    """            for settings_key in list(self.settings):
                if settings_key[0] == guild_id and settings_key[2] == user_id:
                    self.settings.pop(settings_key, None)""",
)
replace(
    "src/profile_client.py",
    "                    member = interaction.guild.get_member(uid)",
    "                    member = interaction.guild.get_member(uid) if interaction.guild is not None else None",
)
replace(
    "src/profile_client.py",
    """        for command in (birthdays, birthday_scan_status, birthday_forget):
            self.tree.add_command(command)""",
    """        self.tree.add_command(birthdays)
        self.tree.add_command(birthday_scan_status)
        self.tree.add_command(birthday_forget)""",
)
replace(
    "src/birthday_client.py",
    "    kwargs = dict(limit=100, oldest_first=False)",
    "    kwargs: dict[str, Any] = dict(limit=100, oldest_first=False)",
)
replace(
    "src/birthday_client.py",
    "        self._birthday_archived_channels = {}",
    "        self._birthday_archived_channels: dict[int, dict[int, Any]] = {}",
)
replace(
    "src/birthday_client.py",
    "        failures, pages = {}, 0",
    "        failures: dict[Any, int] = {}\n        pages = 0",
)
replace(
    "src/birthday_client.py",
    "            message = await channel.fetch_message(payload.message_id)",
    """            fetch_message = getattr(channel, 'fetch_message', None)
            if not callable(fetch_message):
                return
            message = await fetch_message(payload.message_id)""",
)
replace(
    "tests/test_runtime_storage.py",
    "def test_symlinked_mount_refused_before_ownership_change(tmp_path):\n    m=api()",
    "def test_symlinked_mount_refused_before_ownership_change(tmp_path):\n    api()",
)
