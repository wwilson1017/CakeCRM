"""Admin-operated database backups, independent of the assistant."""


class BackupError(Exception):
    """A backup failure safe to show on the settings card."""
