import fnmatch
import hashlib
import os
import re
from collections import OrderedDict
from urllib.parse import urlsplit

import httpx

import apk_verify

OWNER_REPO_RE = re.compile(r"^[A-Za-z0-9._-]+$")
GITHUB_API = "https://api.github.com"
MAX_ASSET_BYTES = apk_verify.MAX_APK_BYTES
# Hosts the asset endpoint may hand us off to. Narrow and fail-closed: the
# error names the refused host, so a GitHub CDN change is a one-line edit here
# rather than an open-ended fetch of whatever a Location header asks for.
ALLOWED_REDIRECT_HOSTS = ("githubusercontent.com", "github.com")


class GithubError(Exception):
    pass


def validate_owner_repo(owner: str, repo: str) -> None:
    if not OWNER_REPO_RE.match(owner) or not OWNER_REPO_RE.match(repo):
        raise GithubError("Owner and repo may only contain letters, digits, '.', '_', '-'")
    if owner in (".", "..") or repo in (".", ".."):
        raise GithubError("Owner and repo may not be '.' or '..'")


def parse_repo_reference(text: str) -> tuple[str, str]:
    """Accepts anything reasonable for "the repo you mean": a GitHub URL (with
    or without scheme, a .git suffix, or a trailing path like /releases), an
    SSH remote (git@github.com:owner/repo.git), or a bare 'owner/repo'.
    Returns a (owner, repo) pair already passed through validate_owner_repo."""
    text = text.strip()
    if not text:
        raise GithubError("Enter a GitHub repo URL or owner/repo")

    ssh_match = re.match(r"^git@github\.com:(.+)$", text, re.IGNORECASE)
    if ssh_match:
        path = ssh_match.group(1)
    else:
        path = re.sub(r"^https?://", "", text, flags=re.IGNORECASE)
        path = re.sub(r"^(www\.)?github\.com/", "", path, flags=re.IGNORECASE)

    path = path.strip("/")
    if path.lower().endswith(".git"):
        path = path[:-4]

    parts = path.split("/")
    if len(parts) < 2 or not parts[0] or not parts[1]:
        raise GithubError("Could not find an owner/repo in that — try pasting the repo's GitHub URL")

    owner, repo = parts[0], parts[1]
    validate_owner_repo(owner, repo)
    return owner, repo


# url -> (etag, parsed JSON). GitHub doesn't count a 304 Not Modified reply
# against the rate limit, so re-polling an unchanged repo is free. One entry
# per watched repo; bounded anyway so it can't grow without limit.
_etag_cache: "OrderedDict[str, tuple[str, object]]" = OrderedDict()
ETAG_CACHE_MAX = 512


async def _get_json(url: str, token: str | None):
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    cached = _etag_cache.get(url)
    if cached:
        headers["If-None-Match"] = cached[0]
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url, headers=headers)
    except httpx.HTTPError as exc:
        raise GithubError(f"Could not reach GitHub: {type(exc).__name__}") from exc
    if resp.status_code == 304 and cached:
        _etag_cache.move_to_end(url)
        return cached[1]
    if resp.status_code == 404:
        raise GithubError("Repo or release not found (private repo needs GITHUB_TOKEN)")
    if resp.status_code == 403:
        raise GithubError("GitHub API rate-limited or forbidden — check GITHUB_TOKEN")
    if resp.status_code != 200:
        raise GithubError(f"GitHub API answered HTTP {resp.status_code}")
    try:
        data = resp.json()
    except ValueError as exc:
        raise GithubError("GitHub API returned something that isn't JSON") from exc
    etag = resp.headers.get("etag")
    if etag:
        _etag_cache[url] = (etag, data)
        _etag_cache.move_to_end(url)
        while len(_etag_cache) > ETAG_CACHE_MAX:
            _etag_cache.popitem(last=False)
    return data


async def get_latest_release(owner: str, repo: str, token: str | None, include_prereleases: bool = False) -> dict:
    """The newest release. /releases/latest skips pre-releases by design, so
    when they're wanted, take the newest non-draft entry of /releases."""
    validate_owner_repo(owner, repo)
    if not include_prereleases:
        return await _get_json(f"{GITHUB_API}/repos/{owner}/{repo}/releases/latest", token)
    releases = await _get_json(f"{GITHUB_API}/repos/{owner}/{repo}/releases?per_page=10", token)
    for release in releases:
        if not release.get("draft"):
            return release
    raise GithubError("Repo has no published releases")


def _validated_redirect(url: str) -> str:
    parts = urlsplit(url)
    if parts.scheme != "https":
        raise GithubError("Refusing to follow a non-HTTPS redirect for a release asset")
    host = (parts.hostname or "").lower()
    if not any(host == h or host.endswith(f".{h}") for h in ALLOWED_REDIRECT_HOSTS):
        raise GithubError(f"Refusing to follow the asset redirect to unexpected host '{host}'")
    return url


def find_matching_assets(release: dict, glob_pattern: str) -> list[dict]:
    return [a for a in release.get("assets", []) if fnmatch.fnmatch(a["name"], glob_pattern)]


async def download_asset(asset: dict, dest_path: str, token: str | None) -> tuple[str, int]:
    """Downloads `asset` to `dest_path`. Validates it's a real APK zip and
    returns (sha256_hex, size_bytes). The GitHub token is sent only to
    api.github.com — GitHub's asset endpoint 302s to a signed, time-limited S3
    URL, and re-sending a long-lived PAT to that third-party host would be an
    unnecessary credential leak, so redirects are followed manually with the
    Authorization header dropped."""
    url = asset.get("url") or ""  # api.github.com asset API URL, not the CDN browser_download_url
    # The token is only ever sent to the API host itself, whatever the release
    # JSON says the asset URL is.
    if not url.startswith(f"{GITHUB_API}/"):
        raise GithubError("Release asset URL is not on api.github.com — refusing to download it")
    headers = {"Accept": "application/octet-stream"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    tmp_path = dest_path + ".part"
    hasher = hashlib.sha256()
    total = 0

    async def _stream_to_file(response: httpx.Response) -> None:
        nonlocal total
        with open(tmp_path, "wb") as f:
            async for chunk in response.aiter_bytes(65536):
                total += len(chunk)
                if total > MAX_ASSET_BYTES:
                    raise GithubError(f"Asset exceeds the {MAX_ASSET_BYTES // (1024*1024)}MB size cap")
                hasher.update(chunk)
                f.write(chunk)

    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=False) as client:
            async with client.stream("GET", url, headers=headers) as resp:
                if resp.status_code in (301, 302, 303, 307, 308):
                    redirect_url = resp.headers.get("location")
                    if not redirect_url:
                        raise GithubError("GitHub returned a redirect with no Location header")
                    redirect_url = _validated_redirect(redirect_url)
                else:
                    resp.raise_for_status()
                    await _stream_to_file(resp)
                    redirect_url = None

            if redirect_url:
                async with client.stream("GET", redirect_url) as resp2:
                    resp2.raise_for_status()
                    await _stream_to_file(resp2)

        try:
            apk_verify.assert_apk_container(tmp_path)
        except apk_verify.ApkVerifyError as exc:
            raise GithubError(f"Downloaded {exc}") from exc

        digest = hasher.hexdigest()
        expected = asset.get("digest")  # e.g. "sha256:<hex>", not always present
        if expected and expected.startswith("sha256:") and expected.split(":", 1)[1] != digest:
            raise GithubError("SHA-256 mismatch against GitHub-reported asset digest")

        os.replace(tmp_path, dest_path)
        return digest, total
    except httpx.HTTPError as exc:
        # Network failures and non-2xx answers become GithubError, so the
        # poller treats them as a retryable download failure and cleans up.
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise GithubError(f"Download failed: {exc}") from exc
    except Exception:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise
