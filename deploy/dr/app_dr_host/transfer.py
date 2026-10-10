"""The DR secret copy: the credentials and replication CA a standby needs from its primary."""
from app_installer import apps
from app_installer.commands import exists, run
from app_installer.secrets import installed_names, read


def replicated_names(platform):
    """Every raw credential the standby needs, for the platform's complete replication group.

    What the install created, plus each database's replication password.
    """
    return installed_names(platform) + [database.secret("replicator") for database in platform.replicated_databases]


def transfer_names(platform):
    """What the DR secret copy carries: the credentials plus the replication CA.

    The CA is not in replicated_names(), which the promoted host requires:
    a pair set up before replication TLS can still fail over without it.
    """
    return replicated_names(platform) + list(apps.REPLICATION_CA_SECRETS)


def export_replicated(platform):
    """Every credential the standby needs and the replication CA, as {name: value}; sent over stdin only."""
    return {name: read(name) for name in transfer_names(platform)}


def import_replicated(platform, values):
    """Create missing credentials; refuse before any write if one differs. Errors name, never show, values."""
    expected = transfer_names(platform)
    if not isinstance(values, dict) or sorted(values) != sorted(expected):
        raise ValueError("The credential transfer must contain exactly the complete replication group.")
    if not all(isinstance(value, str) and value for value in values.values()):
        raise ValueError("Every transferred credential must be a non-empty string.")
    missing = [name for name in expected if not exists("secret", name)]
    different = [name for name in expected if name not in missing and read(name) != values[name]]
    if different:
        raise RuntimeError(
            "Existing standby secrets differ from primary and were not overwritten: "
            + ", ".join(different) + ". No secret was created; resolve the mismatch explicitly.")
    for name in missing:
        run("podman", "secret", "create", name, "-", input=values[name])
    return bool(missing)
