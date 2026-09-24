import fnmatch
import hashlib
import os
import re
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timezone
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
# Workflow artifacts are served from Azure blob storage instead.
ARTIFACT_REDIRECT_HOSTS = (*ALLOWED_REDIRECT_HOSTS, "blob.core.windows.net")


class GithubError(Exception):
    pass


class NoRelease(GithubError):
    """The repo exists but has no published release (yet)."""


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


def forget_cached(owner: str, repo: str) -> int:
    """Drops every cached answer about one repo, so the next requests are
    full fetches rather than conditional ones. Returns how many."""
    validate_owner_repo(owner, repo)
    prefix = f"{GITHUB_API}/repos/{owner}/{repo}/"
    stale = [u for u in _etag_cache if u.startswith(prefix) or u == prefix.rstrip("/")]
    for url in stale:
        del _etag_cache[url]
    return len(stale)


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
    if resp.status_code in (301, 302, 307, 308):
        # A renamed or transferred repo. Not followed: the new location is a
        # different name than the one that was reviewed and pinned.
        raise GithubError("Repo has moved or been renamed on GitHub — remove it and add it under its current name")
    if resp.status_code == 404:
        raise GithubError("Repo or release not found (a private repo needs a GitHub token)")
    if resp.status_code == 403:
        raise GithubError("GitHub API rate-limited or forbidden — check the GitHub token")
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


@dataclass(frozen=True)
class RepoInfo:
    """What GitHub says a repo is. `id` and `owner_id` are GitHub's numeric
    IDs, which are never reused: a name can be re-registered by someone else
    after a rename or deletion, an ID can't."""

    id: int
    owner: str
    repo: str
    owner_id: int
    owner_type: str  # "User" or "Organization"
    created_at: str
    fork: bool
    archived: bool
    stars: int
    description: str


def _parse_repo_info(data) -> RepoInfo:
    try:
        owner = data["owner"]
        full_owner, full_repo = data["full_name"].split("/", 1)
        info = RepoInfo(
            id=int(data["id"]), owner=full_owner, repo=full_repo,
            owner_id=int(owner["id"]), owner_type=str(owner["type"]),
            created_at=str(data.get("created_at") or ""), fork=bool(data.get("fork")),
            archived=bool(data.get("archived")), stars=int(data.get("stargazers_count") or 0),
            description=str(data.get("description") or "")[:300],
        )
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise GithubError("GitHub's description of that repo is missing expected fields") from exc
    validate_owner_repo(info.owner, info.repo)
    return info


async def get_repo_info(owner: str, repo: str, token: str | None) -> RepoInfo:
    validate_owner_repo(owner, repo)
    return _parse_repo_info(await _get_json(f"{GITHUB_API}/repos/{owner}/{repo}", token))


ACTIONS_BOT = "github-actions[bot]"


def uploader_allowed(asset: dict, owner: str, owner_type: str) -> bool:
    """Who may have uploaded a release asset. A workflow's GITHUB_TOKEN
    uploads as github-actions[bot], which only the repo's own workflows can
    act as. A user-owned repo's owner may also upload by hand; for an
    organization, whose members' access can't be checked from here, assets
    must come from a workflow. A missing uploader fails closed."""
    uploader = asset.get("uploader") or {}
    login = uploader.get("login") if isinstance(uploader, dict) else None
    if not isinstance(login, str):
        return False
    if login == ACTIONS_BOT:
        return True
    return owner_type == "User" and login.lower() == owner.lower()


async def get_latest_release(owner: str, repo: str, token: str | None, include_prereleases: bool = False) -> dict:
    """The newest release. /releases/latest skips pre-releases by design, so
    when they're wanted, take the newest non-draft entry of /releases."""
    validate_owner_repo(owner, repo)
    if not include_prereleases:
        try:
            return await _get_json(f"{GITHUB_API}/repos/{owner}/{repo}/releases/latest", token)
        except GithubError as exc:
            # A 404 here means no release (the repo itself was just looked up
            # by the poller, which stops first if the repo is gone).
            if "not found" in str(exc):
                raise NoRelease("No published release yet") from exc
            raise
    releases = await _get_json(f"{GITHUB_API}/repos/{owner}/{repo}/releases?per_page=10", token)
    for release in releases:
        if not release.get("draft"):
            return release
    raise NoRelease("No published release yet")


async def get_release(owner: str, repo: str, release_id: int, token: str | None) -> dict:
    validate_owner_repo(owner, repo)
    release = await _get_json(f"{GITHUB_API}/repos/{owner}/{repo}/releases/{int(release_id)}", token)
    if not isinstance(release, dict):
        raise GithubError("GitHub's description of that release is missing expected fields")
    return release


async def list_releases(owner: str, repo: str, token: str | None, per_page: int = 20) -> list[dict]:
    """Published releases, newest first (drafts left out)."""
    validate_owner_repo(owner, repo)
    releases = await _get_json(f"{GITHUB_API}/repos/{owner}/{repo}/releases?per_page={int(per_page)}", token)
    if not isinstance(releases, list):
        raise GithubError("GitHub's release list is missing expected fields")
    return [r for r in releases if isinstance(r, dict) and not r.get("draft") and r.get("tag_name")]


async def release_commits(owner: str, repo: str, tags: set[str], token: str | None) -> set[str]:
    """Commit SHAs the given tags point at (one page of the repo's tags)."""
    validate_owner_repo(owner, repo)
    if not tags:
        return set()
    listed = await _get_json(f"{GITHUB_API}/repos/{owner}/{repo}/tags?per_page=100", token)
    if not isinstance(listed, list):
        return set()
    return {str((t.get("commit") or {}).get("sha") or "") for t in listed
            if isinstance(t, dict) and t.get("name") in tags} - {""}


def _validated_redirect(url: str, allowed_hosts: tuple[str, ...] = ALLOWED_REDIRECT_HOSTS) -> str:
    parts = urlsplit(url)
    if parts.scheme != "https":
        raise GithubError("Refusing to follow a non-HTTPS redirect for a release asset")
    host = (parts.hostname or "").lower()
    if not any(host == h or host.endswith(f".{h}") for h in allowed_hosts):
        raise GithubError(f"Refusing to follow the asset redirect to unexpected host '{host}'")
    return url


def find_matching_assets(release: dict, glob_pattern: str) -> list[dict]:
    return [a for a in release.get("assets", []) if fnmatch.fnmatch(a["name"], glob_pattern)]


async def download_asset(asset: dict, dest_path: str, token: str | None) -> tuple[str, int]:
    """Downloads a release asset to `dest_path`. Validates it's a real APK
    zip and returns (sha256_hex, size_bytes)."""
    url = asset.get("url") or ""  # api.github.com asset API URL, not the CDN browser_download_url
    tmp_path = dest_path + ".part"
    try:
        digest, total = await _download(url, tmp_path, token, "application/octet-stream", ALLOWED_REDIRECT_HOSTS)
        try:
            apk_verify.assert_apk_container(tmp_path)
        except apk_verify.ApkVerifyError as exc:
            raise GithubError(f"Downloaded {exc}") from exc
        expected = asset.get("digest")  # e.g. "sha256:<hex>", not always present
        if expected and expected.startswith("sha256:") and expected.split(":", 1)[1] != digest:
            raise GithubError("SHA-256 mismatch against GitHub-reported asset digest")
        os.replace(tmp_path, dest_path)
        return digest, total
    except Exception:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise


async def _download(
    url: str, dest_path: str, token: str | None, accept: str, allowed_hosts: tuple[str, ...],
) -> tuple[str, int]:
    """Streams `url` to `dest_path` under MAX_ASSET_BYTES and returns
    (sha256_hex, size_bytes). The GitHub token is sent only to api.github.com —
    GitHub 302s downloads to a signed, time-limited storage URL, and re-sending
    a long-lived PAT to that host would be an unnecessary credential leak, so
    redirects are followed manually with the Authorization header dropped.
    On failure dest_path may hold a partial file; the caller removes it."""
    # The token is only ever sent to the API host itself, whatever the JSON
    # that supplied the URL says.
    if not url.startswith(f"{GITHUB_API}/"):
        raise GithubError("Download URL is not on api.github.com — refusing to download it")
    headers = {"Accept": accept}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    hasher = hashlib.sha256()
    total = 0

    async def _stream_to_file(response: httpx.Response) -> None:
        nonlocal total
        with open(dest_path, "wb") as f:
            async for chunk in response.aiter_bytes(65536):
                total += len(chunk)
                if total > MAX_ASSET_BYTES:
                    raise GithubError(f"Download exceeds the {MAX_ASSET_BYTES // (1024*1024)}MB size cap")
                hasher.update(chunk)
                f.write(chunk)

    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=False) as client:
            async with client.stream("GET", url, headers=headers) as resp:
                if resp.status_code in (301, 302, 303, 307, 308):
                    redirect_url = resp.headers.get("location")
                    if not redirect_url:
                        raise GithubError("GitHub returned a redirect with no Location header")
                    redirect_url = _validated_redirect(redirect_url, allowed_hosts)
                else:
                    resp.raise_for_status()
                    await _stream_to_file(resp)
                    redirect_url = None

            if redirect_url:
                async with client.stream("GET", redirect_url) as resp2:
                    resp2.raise_for_status()
                    await _stream_to_file(resp2)
    except httpx.HTTPError as exc:
        # Network failures and non-2xx answers become GithubError, so callers
        # treat them as a retryable download failure and clean up.
        raise GithubError(f"Download failed: {exc}") from exc
    return hasher.hexdigest(), total


# ---- the token itself ----

# Classic (ghp_, gho_, ghu_, ghs_, ghr_) and fine-grained (github_pat_) tokens.
TOKEN_RE = re.compile(r"^(gh[pousr]_[A-Za-z0-9]{20,255}|github_pat_[A-Za-z0-9_]{20,255})$")


@dataclass(frozen=True)
class TokenStatus:
    login: str
    rate_limit: int
    expires_at: str | None  # ISO 8601 UTC, from GitHub's header; None = no expiry reported


def _parse_expiry(header: str | None) -> str | None:
    """GitHub sends e.g. "2026-12-23 10:00:00 UTC" for a token with an expiry."""
    if not header:
        return None
    try:
        return datetime.strptime(header.replace(" UTC", "").strip(), "%Y-%m-%d %H:%M:%S") \
            .replace(tzinfo=timezone.utc).isoformat()
    except ValueError:
        return None


async def check_token(token: str) -> TokenStatus:
    """Asks GitHub who a token belongs to. Not ETag-cached: the answer is
    about the token, and the cache is keyed by URL alone."""
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
               "Authorization": f"Bearer {token}"}
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(f"{GITHUB_API}/user", headers=headers)
    except httpx.HTTPError as exc:
        raise GithubError(f"Could not reach GitHub: {type(exc).__name__}") from exc
    if resp.status_code == 401:
        raise GithubError("GitHub rejected that token: it's wrong, expired or revoked")
    if resp.status_code != 200:
        raise GithubError(f"GitHub API answered HTTP {resp.status_code}")
    try:
        login = str(resp.json()["login"])
        limit = int(resp.headers.get("x-ratelimit-limit") or 0)
    except (ValueError, KeyError, TypeError) as exc:
        raise GithubError("GitHub's answer about that token is missing expected fields") from exc
    return TokenStatus(login=login[:100], rate_limit=limit,
                       expires_at=_parse_expiry(resp.headers.get("github-authentication-token-expiration")))


async def can_download_artifact(owner: str, repo: str, artifact_id: int, token: str) -> bool:
    """Whether the token may download an artifact, without downloading it:
    GitHub answers the download URL with a redirect only when it may."""
    validate_owner_repo(owner, repo)
    url = f"{GITHUB_API}/repos/{owner}/{repo}/actions/artifacts/{int(artifact_id)}/zip"
    headers = {"Accept": "application/vnd.github+json", "Authorization": f"Bearer {token}"}
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=False) as client:
            resp = await client.get(url, headers=headers)
    except httpx.HTTPError as exc:
        raise GithubError(f"Could not reach GitHub: {type(exc).__name__}") from exc
    return resp.status_code in (301, 302, 303, 307, 308)


# ---- workflow artifacts ----

@dataclass(frozen=True)
class Artifact:
    id: int
    name: str
    size: int
    created_at: str
    branch: str
    head_sha: str
    run_id: int


def _parse_artifact(data, github_id: int) -> Artifact | None:
    """None for an artifact that isn't usable, or isn't this repo's own: an
    expired one, or one from a pull request run out of a fork. A fork's PR
    runs execute here and their artifacts are listed under this repo, but
    anyone on GitHub can open such a PR — so the run's head repository must
    be this repo too, not just the repository the run happened in."""
    try:
        run = data.get("workflow_run") or {}
        if data.get("expired") or int(run.get("repository_id") or 0) != github_id \
                or int(run.get("head_repository_id") or 0) != github_id:
            return None
        return Artifact(
            id=int(data["id"]), name=str(data["name"])[:200], size=int(data.get("size_in_bytes") or 0),
            created_at=str(data.get("created_at") or ""), branch=str(run.get("head_branch") or "")[:200],
            head_sha=str(run.get("head_sha") or "")[:40], run_id=int(run.get("id") or 0),
        )
    except (KeyError, TypeError, ValueError, AttributeError):
        return None


async def list_artifacts(owner: str, repo: str, github_id: int, token: str | None) -> list[Artifact]:
    """The repo's newest workflow artifacts, keeping only the usable ones
    (see _parse_artifact)."""
    validate_owner_repo(owner, repo)
    data = await _get_json(f"{GITHUB_API}/repos/{owner}/{repo}/actions/artifacts?per_page=30", token)
    items = data.get("artifacts") if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise GithubError("GitHub's artifact list is missing expected fields")
    return [a for a in (_parse_artifact(d, github_id) for d in items) if a is not None]


async def get_artifact(owner: str, repo: str, artifact_id: int, github_id: int, token: str | None) -> Artifact:
    validate_owner_repo(owner, repo)
    data = await _get_json(f"{GITHUB_API}/repos/{owner}/{repo}/actions/artifacts/{int(artifact_id)}", token)
    artifact = _parse_artifact(data, github_id) if isinstance(data, dict) else None
    if artifact is None:
        raise GithubError("That artifact has expired, or wasn't built by this repo's own workflows")
    if artifact.size > MAX_ASSET_BYTES:
        raise GithubError(f"That artifact exceeds the {MAX_ASSET_BYTES // (1024*1024)}MB size cap")
    return artifact


MAX_NOTES_CHARS = 20_000


async def list_runs(owner: str, repo: str, token: str | None, per_page: int = 50) -> dict[int, dict]:
    """Recent workflow runs by ID: title, trigger, number and commit message,
    so a list of artifacts can say what each build is without a call per run."""
    validate_owner_repo(owner, repo)
    data = await _get_json(f"{GITHUB_API}/repos/{owner}/{repo}/actions/runs?per_page={int(per_page)}", token)
    runs = data.get("workflow_runs") if isinstance(data, dict) else None
    out: dict[int, dict] = {}
    for run in runs if isinstance(runs, list) else []:
        if not isinstance(run, dict) or not isinstance(run.get("id"), int):
            continue
        message = str((run.get("head_commit") or {}).get("message") or "").strip()[:MAX_NOTES_CHARS]
        out[run["id"]] = {"title": str(run.get("display_title") or run.get("name") or "")[:200],
                          "event": str(run.get("event") or ""), "number": run.get("run_number"),
                          "message": message, "subject": message.split("\n", 1)[0][:200]}
    return out


async def get_build_notes(owner: str, repo: str, artifact: "Artifact", token: str | None) -> str:
    """The closest thing an artifact has to release notes: what its workflow
    run built (title, trigger, branch, commit, link), the pull request it was
    for if any (title and description), and the full commit message."""
    validate_owner_repo(owner, repo)
    run = await _get_json(f"{GITHUB_API}/repos/{owner}/{repo}/actions/runs/{int(artifact.run_id)}", token)
    if not isinstance(run, dict):
        raise GithubError("GitHub's description of that workflow run is missing expected fields")
    sha = artifact.head_sha[:7]
    lines = [f"{run.get('display_title') or run.get('name') or 'Workflow run'} "
             f"(run #{run.get('run_number', '?')}, {run.get('event', '?')})",
             f"{artifact.branch} @ {sha} · https://github.com/{owner}/{repo}/actions/runs/{int(artifact.run_id)}"]
    prs = [p for p in (run.get("pull_requests") or []) if isinstance(p, dict) and isinstance(p.get("number"), int)]
    if prs:
        pr = await _get_json(f"{GITHUB_API}/repos/{owner}/{repo}/pulls/{prs[0]['number']}", token)
        if isinstance(pr, dict):
            lines += ["", f"Pull request #{prs[0]['number']}: {pr.get('title') or ''}".rstrip()]
            if pr.get("body"):
                lines += ["", str(pr["body"]).strip()]
    message = ((run.get("head_commit") or {}).get("message") or "").strip()
    if message:
        lines += ["", f"Commit {sha}:", message]
    return "\n".join(lines)[:MAX_NOTES_CHARS]


MAX_ZIP_DIRECTORY_BYTES = 4 * 1024 * 1024
_ZIP_TAIL_BYTES = 65_536 + 22  # the end record plus the longest possible comment


def _zip_names(directory: bytes) -> list[str]:
    names, i = [], 0
    while directory[i:i + 4] == b"PK\x01\x02" and i + 46 <= len(directory):
        name_len = int.from_bytes(directory[i + 28:i + 30], "little")
        extra_len = int.from_bytes(directory[i + 30:i + 32], "little")
        comment_len = int.from_bytes(directory[i + 32:i + 34], "little")
        names.append(directory[i + 46:i + 46 + name_len].decode("utf-8", "replace"))
        i += 46 + name_len + extra_len + comment_len
    return names


async def artifact_lists_apk(owner: str, repo: str, artifact_id: int, token: str) -> bool | None:
    """Whether an artifact's zip holds an .apk, read from the zip's own file
    list with two small range requests instead of a download. None when it
    can't be told (no ranges served, a zip64 or malformed zip)."""
    validate_owner_repo(owner, repo)
    url = f"{GITHUB_API}/repos/{owner}/{repo}/actions/artifacts/{int(artifact_id)}/zip"
    headers = {"Accept": "application/vnd.github+json", "Authorization": f"Bearer {token}"}
    try:
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=False) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code not in (301, 302, 303, 307, 308) or not resp.headers.get("location"):
                return None
            blob = _validated_redirect(resp.headers["location"], ARTIFACT_REDIRECT_HOSTS)

            async def _range(spec: str) -> tuple[bytes, int] | None:
                async with client.stream("GET", blob, headers={"Range": f"bytes={spec}"}) as r:
                    total = (r.headers.get("content-range") or "").rpartition("/")[2]
                    if r.status_code != 206 or not total.isdigit():
                        return None  # never read a body the server didn't cut to size
                    body = b""
                    async for chunk in r.aiter_bytes(65536):
                        body += chunk
                        if len(body) > MAX_ZIP_DIRECTORY_BYTES:
                            return None
                    return body, int(total)

            # Byte 0 first, for the size: Azure blob storage ignores suffix
            # ranges ("the last N bytes") and would send the whole file.
            got = await _range("0-0")
            if got is None:
                return None
            total = got[1]
            got = await _range(f"{max(total - _ZIP_TAIL_BYTES, 0)}-{total - 1}")
            if got is None:
                return None
            tail = got[0]
            end = tail.rfind(b"PK\x05\x06")
            if end < 0 or end + 22 > len(tail):
                return None
            size = int.from_bytes(tail[end + 12:end + 16], "little")
            offset = int.from_bytes(tail[end + 16:end + 20], "little")
            if size > MAX_ZIP_DIRECTORY_BYTES or offset == 0xFFFFFFFF or offset + size > total:
                return None
            start = total - len(tail)
            if offset >= start:
                directory = tail[offset - start:offset - start + size]
            else:
                got = await _range(f"{offset}-{offset + size - 1}")
                if got is None:
                    return None
                directory = got[0]
    except httpx.HTTPError:
        return None
    return any(n.lower().endswith(".apk") and "__MACOSX" not in n.split("/") for n in _zip_names(directory))


async def download_artifact(owner: str, repo: str, artifact_id: int, dest_path: str, token: str | None) -> str:
    """Downloads an artifact's zip to dest_path and returns its sha256. GitHub
    only serves artifact downloads to an authenticated caller, even for a
    public repo."""
    validate_owner_repo(owner, repo)
    if not token:
        raise GithubError("Downloading workflow artifacts needs a GitHub token (with Actions: read)")
    url = f"{GITHUB_API}/repos/{owner}/{repo}/actions/artifacts/{int(artifact_id)}/zip"
    digest, _total = await _download(url, dest_path, token, "application/vnd.github+json", ARTIFACT_REDIRECT_HOSTS)
    return digest
