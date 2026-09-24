"""Runs the app with GitHub faked out, for README screenshots only.

Every GitHub call the pages make is answered from the invented data below,
so a screenshot never shows a real account, repo, token or build, and the
run needs no network. Started by run.sh inside a throwaway app container."""
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, "/app")
os.chdir("/app")

import github_client as gh  # noqa: E402

NOW = datetime.now(timezone.utc)


def ago(days: float) -> str:
    return (NOW - timedelta(days=days)).isoformat().replace("+00:00", "Z")


REPOS = {
    ("example", "weather-app"): gh.RepoInfo(101, "example", "weather-app", 11, "User", ago(900), False, False, 412,
                                            "A small weather app with radar and severe-weather alerts"),
    ("acme-labs", "field-notes"): gh.RepoInfo(202, "acme-labs", "field-notes", 22, "Organization", ago(1400), False,
                                              False, 88, "Offline notes for field crews"),
    ("example", "pocket-timer"): gh.RepoInfo(303, "example", "pocket-timer", 11, "User", ago(40), False, False, 7,
                                             "Interval timer with lap sounds"),
    # For the review screenshot: a look-alike fork, days old.
    ("example-dev", "weather-app"): gh.RepoInfo(404, "example-dev", "weather-app", 44, "User", ago(5), True, False, 0,
                                                "A small weather app with radar and severe-weather alerts"),
}

RELEASES = {
    "weather-app": [
        {"id": 9004, "tag_name": "v2.4.0", "name": "Radar layers", "published_at": ago(3),
         "assets": [{"name": "weather-app-arm64-v8a.apk"}, {"name": "weather-app-armeabi-v7a.apk"}]},
        {"id": 9003, "tag_name": "v2.3.1", "name": "", "published_at": ago(21), "assets": [{"name": "weather-app.apk"}]},
        {"id": 9002, "tag_name": "v2.3.0", "name": "Widgets", "published_at": ago(40), "assets": [{"name": "weather-app.apk"}]},
        {"id": 9001, "tag_name": "v2.2.0", "name": "", "published_at": ago(75), "assets": [{"name": "weather-app.apk"}]},
    ],
    "field-notes": [{"id": 8001, "tag_name": "v1.9.2", "name": "", "published_at": ago(9), "assets": [{"name": "field-notes.apk"}]}],
    "pocket-timer": [],
}
TAG_SHAS = {"v2.4.0": "a41c9e07d2", "v2.3.1": "77e0b3c519"}


def _artifact(i, name, branch, sha, run, days, size_mb):
    return gh.Artifact(i, name, int(size_mb * 1048576), ago(days), branch, sha, run)


ARTIFACTS = {
    "weather-app": [
        _artifact(51, "weather-app-release", "fix/radar-cache", "c7d21f09ab", 7001, 0.2, 9.4),
        _artifact(52, "weather-app-debug", "fix/radar-cache", "c7d21f09ab", 7002, 0.2, 11.8),
        _artifact(53, "weather-app-debug", "feature/pollen-count", "e19b5a3c44", 7003, 1.5, 11.7),
        _artifact(56, "weather-app-release", "feature/pollen-count", "e19b5a3c44", 7003, 1.5, 9.5),  # not checked yet
        _artifact(54, "weather-app-release", "main", "a41c9e07d2", 7004, 3.0, 9.3),  # the v2.4.0 build: hidden
        _artifact(55, "example~weather-app~Q2X8.dockerbuild", "main", "a41c9e07d2", 7005, 3.0, 0.1),  # hidden
    ],
    "pocket-timer": [
        _artifact(61, "pocket-timer-release", "feature/lap-sounds", "3f9c2e1d88", 7101, 0.5, 4.2),
        _artifact(62, "pocket-timer-debug", "feature/lap-sounds", "3f9c2e1d88", 7102, 0.5, 5.0),
    ],
}
RUNS = {
    7001: ("Build", "push", 118, "fix(radar): keep tiles cached across rotation\n\nThe radar layer refetched every tile on rotate."),
    7002: ("Build", "push", 118, "fix(radar): keep tiles cached across rotation\n\nThe radar layer refetched every tile on rotate."),
    7003: ("Build", "pull_request", 117, "feat: pollen count on the daily card"),
    7101: ("CI", "push", 42, "feat: lap sounds with a volume slider"),
    7102: ("CI", "push", 42, "feat: lap sounds with a volume slider"),
}


def _repo(owner, repo):
    try:
        return REPOS[(owner, repo)]
    except KeyError:
        raise gh.GithubError("Repo or release not found (a private repo needs a GitHub token)") from None


async def get_repo_info(owner, repo, token):
    return _repo(owner, repo)


async def list_releases(owner, repo, token, per_page=20):
    _repo(owner, repo)
    return RELEASES.get(repo, [])


async def get_latest_release(owner, repo, token, include_prereleases=False):
    releases = await list_releases(owner, repo, token)
    if not releases:
        raise gh.NoRelease("No published release yet")
    return releases[0]


async def release_commits(owner, repo, tags, token):
    return {TAG_SHAS[t] for t in tags if t in TAG_SHAS}


async def list_artifacts(owner, repo, github_id, token):
    return list(ARTIFACTS.get(repo, []))


async def list_runs(owner, repo, token, per_page=50):
    return {rid: {"title": t, "event": e, "number": n, "message": m, "subject": m.split("\n", 1)[0]}
            for rid, (t, e, n, m) in RUNS.items()}


async def check_token(token):
    return gh.TokenStatus("example-user", 5000, (NOW + timedelta(days=83)).isoformat())


async def can_download_artifact(owner, repo, artifact_id, token):
    return True


async def artifact_lists_apk(owner, repo, artifact_id, token):
    return True


for _fn in (get_repo_info, list_releases, get_latest_release, release_commits, list_artifacts, list_runs,
            check_token, can_download_artifact, artifact_lists_apk):
    assert hasattr(gh, _fn.__name__), _fn.__name__  # the app renamed something: fix the mock
    setattr(gh, _fn.__name__, _fn)

import uvicorn  # noqa: E402

uvicorn.run("main:app", host="0.0.0.0", port=8080, workers=1, log_level="warning")
