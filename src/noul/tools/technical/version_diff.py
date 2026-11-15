"""arXiv version diff: what changed between two versions of a preprint (`diff_arxiv_versions`)."""


def diff_arxiv_versions(arxiv_id: str, from_version: str = "v1", to_version: str = "v2") -> dict:
    """Compare two versions of an arXiv paper (metadata, metrics, claim attenuations, new baselines/datasets)."""
    from noul.providers import arxiv_client

    base_id, v_old, v_new = arxiv_client.parse_arxiv_version_args(
        arxiv_id, from_version=from_version, to_version=to_version
    )
    snap_old = arxiv_client.fetch_version_snapshot(base_id, v_old)
    snap_new = arxiv_client.fetch_version_snapshot(base_id, v_new)
    report = arxiv_client.diff_snapshots(base_id, v_old, snap_old, v_new, snap_new)
    return report.model_dump(mode="json")
