from secondshift.config.constants import GITHUB_BASE_URL


def blob_url(repository: str, revision: str, path: str) -> str:
    return f"{GITHUB_BASE_URL}/{repository}/blob/{revision}/{path}"
