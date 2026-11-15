"""Noul's own settings (contact e-mail, folders, Zotero credentials)."""

from noul import config as _config
from noul import registry as _registry
from noul.schemas import tool_inputs as _inputs


@_registry.tool(
    "configure_noul",
    description=(
        "View (no arguments) or update the persistent Noul configuration: contact email (enables Unpaywall "
        "and the OpenAlex/Crossref polite pool) and default download/research folders. The workspace, the Zotero user "
        "id and whether a Zotero key is configured are shown, but none can be set here: the user sets them in a "
        "terminal with `noul config --workspace W` and `noul config --zotero-user-id ID --zotero-api-key-stdin`, or "
        "with NOUL_WORKSPACE, NOUL_ZOTERO_USER_ID and NOUL_ZOTERO_API_KEY, so never ask for a key in chat. `clear` "
        "resets the rest of the stored configuration."
    ),
    input=_inputs.ConfigureNoulInput,
    writes=True,
    network=False,
    profiles=("research",),
    destructive=True,
    idempotent=True,
    title="Configure Noul",
)
def configure_noul(
    email=None,
    download_dir=None,
    research_dir=None,
    clear=False,
):
    """View or update persistent Noul configuration (email for Unpaywall/polite pool, default folders).
    Called without arguments it only reads the configuration."""
    values = (email, download_dir, research_dir)
    if clear or any(v is not None for v in values):
        return _config.set_config(
            email=email,
            download_dir=download_dir,
            research_dir=research_dir,
            clear=bool(clear),
            keep_protected=True,  # a model that read a hostile paper must not be able to drop them
        )
    return _config.get_config()
