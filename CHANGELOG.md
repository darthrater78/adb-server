# Changelog

## 3.9.0 — 2026-10-04

- Fixed: **a phone can pair itself from its own browser.** The steps had you
  read three values off the phone's Settings and type them into this page,
  but Android cancels the pairing code the moment Settings leaves the
  screen, so on the phone itself the code was dead before you could submit
  it. Opened on an Android phone, **Add a device** now tells you to put the
  browser and Settings in split screen, fills in the phone's IP address
  itself, and asks only for the pairing port and code, each with a number
  pad.
- Changed: **pairing asks for less.** The form takes the phone's IP address
  once, the pairing port and the code. The connect port is found by scanning
  that IP after pairing; **Connect port** is there for when it isn't.
- Changed: **on a phone, the theme is picked on Settings → Appearance.** The
  Flashbang / Dark / OLED buttons left the phone's top bar, which now shows
  the app's name and **Log out**. Wide screens keep them in the bar too.
- Fixed: **the edges of fields and outlined buttons are visible.** Their
  border was 1.3:1 to 1.45:1 against the control; it is now at least 3:1 in
  all three themes.
- Security: `oauthlib` moves to 4.0.0 for CVE-2026-49265 (a timing leak in
  its OAuth server code, which this app does not run; it arrives through
  the notification library).
- Changed: **a dev build is announced.** A tag with a `-suffix` (such as
  `v3.9.0-dev.1`) now creates a GitHub pre-release, never "Latest", with the
  notes of the version it leads to. While one is newer than the latest
  release, the top of the README shows a banner for it with how to run it;
  the banner goes when that version ships.
- Security: both images take Debian's published security fixes when they
  are built, and the Python base image moves to its current build. A scan
  found fixable high-severity findings in OpenSSL and PCRE2.
- Internal: CI skips the tests and the image builds when only docs changed,
  and tests on Python 3.14, the version the image runs.
- Internal: a `DESIGN.md` records the tokens, type, shapes and rules the UI
  follows, linted with `@google/design.md`. Corner radii, the dialog and menu
  shadows and the preset swatches are tokens in `style.css` now; four
  off-scale radii and two off-scale text sizes moved onto the scale.
- Fixed: **a failed pairing comes back to the open form**, with the error
  inside it and a reminder that the code only works while the dialog is
  open, instead of a folded panel and a message at the top of the page.

## 3.8.1 — 2026-09-27

- Fixed: **a push's row updates on its own again.** After **Install**,
  **Update**, **Push** or **Update all**, the page was meant to refresh
  itself until the install finished, but a browser treats reloading the
  exact address it's on (`#row` and all) as a scroll, so it never reloaded
  and the row sat at **Installing…** until you refreshed by hand. Each
  reload now asks for a new address and comes back to the same row, on
  Status, Apps, a push's own page and install history (which didn't refresh
  at all before).
- Fixed: **watching a repo installs on the devices you ticked, visibly.**
  The first release's install was queued only after the page had decided
  the check was done, so it stopped reloading before the install started.
- Fixed: **accepting a new signer waits for the release**, like **Check
  now**, instead of leaving the card as it was.
- Fixed: **a check that crashes says so.** **Check now** used to wait a
  minute and then say it was taking a while; the error now shows on the
  repo's card.
- Fixed: **Builds shows what you just did.** Turning **Unsigned builds** on
  or off opens that section with its message, and **Check signing** on an
  older commit opens its fold and says the result on that build's row.
- Fixed: **the theme switcher keeps you on the page you're on.** On Builds,
  a push's page or any other page below the top bar, it used to send you to
  Status.

## 3.8.0 — 2026-09-27

- Changed: **every action brings you back where you were.** Saving a name,
  **Find**, **Reconnect**, **Trust**, auto-update, **Check now**, pre-releases,
  **Update all** and deleting a file used to reload the page at the top with
  every card folded, so you had to scroll back and open the card again. Now
  the page comes back with that card open, scrolled to it (or to the row you
  used), and the result is shown inside the card instead of at the top.
- Changed: **cards that want you start open.** A device with updates, an
  untrusted device, a device with a push running, the setup checklist, or
  the only card on a page start open; quiet ones stay folded. Before,
  everything outside Settings started folded, so even seeing your updates
  took a click.
- Changed: **Sources and Install are one page: Apps.** Every app this server
  installs, where it comes from and every version staged, on one page
  (`/apps`; `/sources`, `/library`, `/install` and the other old addresses
  lead there). Each watched repo is a card that never folds: its latest
  release and staged CPU builds, how it's signed, what the chosen device has
  (**2.4.0 ✓**, **update available**, **not installed**), and every action
  (**Push**, **Delete**, **Builds**, **Check now**, **Pre-releases**,
  **Remove**) on its face; older versions and each file sit under **Show
  details**. Test builds and uploads follow, one card each. A line above the
  repos sums up how many are up to date, to update or not installed on the
  chosen device. Each device's card on Status still offers every watched app
  (**Update** or **Install**, with **What's in …** release notes under the
  row) and every test build and upload. The nav reads **Status · Apps ·
  Devices · Settings**, and always marks the page you're on, including a
  repo's Builds (under Apps) and a push's page (under Status).
- Changed: **a push shows its progress where you pushed it.** No more
  separate progress page that bounced you back after the install: the row
  shows a moving bar and **Installing 2.4.0…**, the page refreshes itself,
  and when it's done the bar fills green with **✓ Installed 2.4.0 just now**,
  or red with **Push failed** and a link to why. A strip at the top also
  lists pushes running now and those just finished, each with its log. Each
  push's own page (from its log link or install history) no longer sends you
  away.
- Added: **every remove or delete asks first.** Deleting a version or a
  file, removing a repo, forgetting a device, revoking a trusted browser,
  removing a notification service, a saved colour or the GitHub token all
  open a confirmation naming what goes and what stays.
- Added: **a debug build is never pushed over a signed install.** A phone
  that has an app from another key refuses a debug build of it, and getting
  past that means uninstalling the app and losing its data. Such a build now
  shows **Push blocked** with the reason, and the server refuses it however
  it's asked (Apps, Status, **Stage and install**, auto-update). A debug
  build this server pushed with the same key can still be replaced.
- Fixed: **an older version wouldn't install after uninstalling a newer
  one**, not even from the APK by hand, on a phone with a Private space or
  work profile. `adb install` put every push into every profile, so
  uninstalling from the main one left a copy elsewhere, and Android refused
  the older version. Pushes now go only into the profile in use, an app only
  in another profile no longer shows as installed, and a push refused because
  of the copy already on the phone names the profile that has it and offers
  **Remove from every profile** (after a confirmation: it deletes the app's
  data there).
- Fixed: **Check now didn't update the page.** The page asked to reload
  itself at its own address, which a browser takes as a scroll, so it waited
  forever; and the check was marked done a moment before its files were
  saved. It now reloads until the release is staged, then shows it.
- Changed: **Devices shows where each phone stands.** Each card's heading
  shows its live connection (**Connected**, **Offline**, **Key refused**,
  **Not connected**) beside **Trusted**, and its current address; inside, one
  line says what to do about it, with how many apps it has and its last
  push. The connection row reads address, **Reconnect**, **Find**, side by
  side, and **Add a device** asks for the connect address first, then the
  pairing address and code.
- Changed: **Builds never scrolls sideways.** Each test build is a row that
  wraps: its name, version once staged, signing, workflow run, size and
  **Stage**. Every build of the newest commit shows, and the folded **older
  releases** and **older commits** name what's inside them. Long release
  notes and install logs wrap too.
- Changed: **GitHub is asked less, and in parallel.** Builds asks only for
  the workflow runs its builds came from, side by side, instead of the
  repo's 50 latest runs, and sends its other requests together: it loads in
  about half the time, and every build is labelled even when its run is
  older than those 50. A release's CPU builds download a few at a time, a
  poll checks a few repos at once, and requests reuse open connections.
- Added: **a device's tools on its Status card.** **Find** (for a phone whose
  port changed), **Auto-update all** (on for every watched app it has, or
  off for all), and, for an untrusted device, **Trust** right there. When
  **Refresh installed versions** can't reach a phone, its card opens with
  **Find** at hand.
- Added: **trusting a phone takes you to it.** Trust after pairing goes on
  to the device's card on Status, ready to install.
- Added: **watching a repo gets you its app sooner.** Its first check starts
  as soon as you confirm it (the page waits on its card), and you can tick
  the trusted devices to install it on and keep updated.
- Added: **Stage and install** on Builds. Beside each **Stage**, pick a
  trusted device, and the release or test build is staged, then installed on
  it; you land on its row on Status. Staged only, you land on it in the
  app's card on Apps rather than at the top of the page.
- Changed: **Install history** sits under Status (a **History** button there),
  still linked from Settings. Devices folds its explanation of **Find** and
  **Reconnect**.
- Security: **images are scanned before they're published.** A release now
  pushes each image by digest, scans it with Trivy (fixable HIGH and
  CRITICAL in its OS packages), and only then tags it; it then checks every
  tag points at the scanned image and that the image reports its version.
  Each image also carries a signed build provenance attestation.
- Security: **CodeQL** scans the code and workflows, **dependency review**
  fails a pull request that adds a vulnerable dependency, and a weekly scan
  re-checks the released images and the Python lockfile.

## 3.7.1 — 2026-09-25

- Fixed: **Builds opens on the newest.** Releases and Test builds were both
  folded shut, hiding everything. Each is now open, leading with its newest
  item: the latest release as a card with its name, date, APK count, release
  notes and **Stage**, and the newest commit's builds. Older releases and
  older commits sit under a fold below (**N older releases**, **N older
  commits**). Unsigned builds stays folded.
- Fixed: **a dev build next to a release is caught.** A dev tag's images
  (`v3.7.0-dev.1`) reported the plain version (`3.7.0`), so a dev web app
  beside a released adb-server looked matched and no warning showed. The
  release workflow now stamps the tag's full version into both images. Images
  already published keep their old version; this applies from the next tag.
  A dev build's version badge links to its tag, since it has no release page.

## 3.7.0 — 2026-09-25

- Changed: **every push works the same way.** Push on Install now asks you to
  confirm, like Update on Status, naming the version, the device and what it
  replaces. The progress page that follows takes you back where you started
  (Install keeps the device you were pushing to) once the install succeeds;
  a failed one stays put with its log.
- Added: **Update all.** A device with two or more updates gets one button
  at the top of its Status card (and in the phone summary) that installs
  them all, one after another, after one confirmation, with one progress
  page for the lot.
- Added: **trust right after pairing.** Once a phone pairs, Devices asks
  whether to trust it, showing its model, serial, CPU and address, with a
  box to name it in the same step. No more hunting for Trust in the list.
- Changed: **every block folds, and starts folded.** Cards, panels and
  sections on every page (Install's groups, Sources' forms and repos,
  Devices, every Settings section, each commit on Builds) collapse from their
  heading. Outside Settings they all start folded, and each heading sums up
  what's inside; only a question waiting on you (a repo to confirm, a signing
  change, a device to trust) starts open. A card's heading is a full-width
  tinted bar, so it no longer blends into what's below it.
- Changed: **Sources and Devices are cards, not tables.** One folded card
  per watched repo, whose heading shows its most recent staged release, and
  one per paired device. Neither page scrolls sideways any more. Each device
  card explains what **Find** does.
- Changed: **Install's groups sum themselves up.** Releases, Test builds and
  Uploads are folded cards whose heading shows how many apps, the newest,
  and how many are to update, up to date or not installed on the device
  you're pushing to. Picking a group with the pills opens it.
- Changed: **one accent colour.** Settings → Appearance now sets a single
  accent, used for every highlight: solid buttons, links, focus rings, the
  current page in the menu (desktop and phone), the chosen filter pill and
  Settings tab, checkboxes and the edge of an open card. The secondary colour
  is gone (since 3.6 it coloured almost nothing); eight single-colour
  presets replace the pairs, and a pair saved before applies its first
  colour. Status badges keep their own colours; success and Release are now
  green instead of teal, so they never look like the default accent.
- Fixed: **a deleted release couldn't be staged again.** Staging it from
  Builds said it was "already staged", because the deleted files' records
  still counted. It's staged again now, and **Check now** restages a
  current release whose files were deleted (the scheduled poll leaves it
  deleted).
- Fixed: **colour changes could seem to do nothing** in a browser holding an
  old copy of the stylesheet. `style.css` is now linked by a hash of its
  content, so every change to it is fetched.
- Changed: **Sources no longer repeats Install.** Its "Uploads and test
  builds" table is gone; they're listed, pushed and deleted on Install, and
  Sources says how many there are.
- Added: **dev builds.** A tag with a suffix (`v3.7.0-dev.1`) may be pushed
  from any branch: it publishes both images under that exact version only,
  never `:latest` or `:X.Y`, and makes no GitHub release.

## 3.6.0 — 2026-09-24

- Changed: **a new look.** Calmer cards on a warm neutral ground, one teal
  for the button that matters, and one soft chip style for where a build
  came from (Release, Test build, Upload, Not from this server, Debug).
  Text is set in Figtree and versions in JetBrains Mono, both served by the
  app itself. Flashbang is the light palette, Dark its dark twin, OLED the
  same on true black.
- Changed: **phones get their own layout.** The nav is a tab bar along the
  bottom, rows are 44px+ touch targets with a letter avatar, and each row's
  one action sits at its end. Status opens with a summary of what's ready
  (**1 update ready** · **Update**) and a switcher between devices.
- Changed: **Status rows line up in columns** (app, on device, latest,
  actions) inside each device card, and a device card folds away by its
  header. Untrusted devices start folded.
- Changed: **Install lists apps as rows** with what the chosen device has of
  each (*Not installed*, *Has 2.3.1*, *Installed*); **Push** is the solid
  button only where it would change something. **All · Releases · Test
  builds · Uploads** pills filter the list and keep the chosen device.
- Changed: the test-build chip reads **Test build** (was *Artifact · test
  build*); a normally signed build no longer carries a green *signed* chip on
  phones.

## 3.5.0 — 2026-09-24

- Added: **Status shows where each installed version came from.** Under the
  version on the device: **Release** with its tag, **Test build** with its
  branch and commit linked to the workflow run, **Upload**, or **Not from
  this server** when it was installed some other way or replaced since. Debug
  builds are marked. Each successful push records what it installed along
  with the device's own install time, so a later reinstall of the same
  version from elsewhere is told apart. The record is kept even after the
  staged file is pruned or its repo removed, and dropped when the app is
  uninstalled. Pushes from before 3.5.0 are matched to what the device has by
  version, once, on first start, and marked *(likely)*.
- Changed: **Install cards are collapsed to the essentials**: name,
  version, source, signing, when it was staged, which devices already have
  it, and **Push**. **Details** opens the package name, release notes, every
  staged file and older versions.
- Changed: **one "Pushing to" picker at the top of Install** replaces the
  device dropdown on every card and row. It's a menu of links, so it still
  needs no JavaScript.
- Changed: **Refresh installed versions also re-checks uploaded apps** a
  device has, not only watched repos.
- Fixed: a second release run for the same tag no longer fails at "Create
  GitHub release"; it finds the release already made and stops.

## 3.4.0 — 2026-09-24

- Added: **a warning when the two containers are from different releases.**
  The adb-server image now writes its version into a new `adbinfo`
  directory that the app mounts read-only. The app also compares the adb
  server's protocol version with its own adb client's. Either mismatch, or a
  missing `adbinfo` mount, shows a warning with the fix on every page and
  sends the new **version mismatch** notification once. Settings → General
  shows both versions. Checked at start and every 5 minutes.
- Upgrading: create `/opt/docker/adb-server/adbinfo` (owned by 10001, like
  the other two) and add its two `volumes:` lines from this release's
  `compose.yaml`. Until then, the app warns that it can't tell which version
  adb-server runs.
- Build: the adb-server image is built from the repo root, like the app, so
  both carry the same `VERSION`.

## 3.3.0 — 2026-09-24

- Added: **stage a test build straight from a watched repo's workflow
  runs.** **Artifacts** on a repo lists the builds its recent runs uploaded;
  **Stage** unwraps the zip and checks the APK like an upload, recording the
  run, branch and commit it came from. Its **build notes** on Install take
  the place of release notes: the run, the pull request's description if it
  was built for one, and the full commit message. Artifacts from pull requests opened
  from forks are never offered. Needs `GITHUB_TOKEN` with Actions: read.
- Changed: **Install groups its cards** into Releases, Test builds from
  workflow artifacts, and Uploads. A test build is badged **Artifact · test
  build** and shows its branch and commit, linked to the run.
- Changed: **devices are named by model** where they have no nickname
  (e.g. "Google Pixel 8 · …005KT") instead of a bare serial, in the device
  picker, on Status and on Devices. The model is read over adb alongside the
  CPU type.
- Added: **Builds** on a watched repo (was Artifacts) lists its past
  releases to stage an older one, through every release check, and its test
  builds grouped by commit with each commit's message. A release's own build
  is no longer offered as a test build. **Refresh from GitHub** fetches both
  fresh.
- Changed: **"latest" means the newest release by release date**, not the
  most recent download, so staging an older release never makes auto-update
  or Push latest send it.
- Added: **only repos with APKs can be added**: a release asset matching the
  glob, or (with a token) an artifact whose zip lists an `.apk`, checked from
  the zip's file list without downloading it.
- Added: **unsigned builds, by opt-in**: per repo (on Builds) or per upload,
  with an explanation, the server signs an unsigned build and marks it
  **signed by this server**. Each source gets its own key (one per watched
  repo, kept by its GitHub ID, and one for uploads), so one source's build
  can never pass as an update to another source's app. Settings → Security
  lists each key's fingerprint. Without the opt-in an unsigned build is
  refused with the reason. Adds `zipalign` to the image.
- Changed: **Check now** reloads the page until the check is done and says
  what it found; a repo with no release yet is "no release yet", not an
  error.
- Changed: on Sources, uploads and test builds are one table, each row
  badged **Artifact** (with branch and commit) or **Upload**.
- Security: the HTTP client no longer logs request URLs, which for a
  download include a signed storage link that works as a short-lived read
  token.
- Added: **signing badges** on every staged build: **signed**, **debug
  build** or **signed by this server**. A debug-signed test build whose commit
  also built something else is flagged **Better not install this debug
  build**, with a button to stage the other one.
- Added: **Settings → General** with the time zone and a 12-hour (default)
  or 24-hour clock for every date and time shown (stored in UTC; `TZ` from
  the environment is the default zone).
- Added: on Builds, each test build is badged by how it's signed: **signed ·
  same key as releases**, **signed · different key**, **debug build** or
  **unsigned**, with a note on which to pick. **Check signing** downloads a
  build to find out, then deletes it; staging records it too.
- Docs: every screenshot retaken from invented data with GitHub mocked, plus
  new ones for the repo review and the Builds page. `scripts/screenshots/`
  regenerates them.
- Added: **a repo is reviewed before it's watched.** Adding one shows what
  GitHub says it is (owner, created date, stars, ID) and warns about the
  signs of a look-alike: a fork, archived, under 30 days old, no release.
- Added: **watched repos are pinned by GitHub ID and owner.** If the name
  later points at a different repo, the repo is transferred, or it's
  renamed, nothing more is staged from it and you're notified. Existing
  repos are pinned on their first poll after upgrading.
- Added: **release assets must be uploaded by the repo's owner or its
  workflows** (`github-actions[bot]`); for an organization's repo, by its
  workflows only. A release with an asset from anyone else is rejected before
  it's downloaded.
- Changed: **Settings is split into pages.** `/settings` is now an overview
  with one card per area and its current state. Security, Notifications,
  GitHub and Appearance each have their own page. The notification add-forms
  fold away until needed.
- Added: **Settings → GitHub.** **Create a token on GitHub** opens GitHub's
  token page pre-filled with read-only Contents, Actions and Metadata and a
  90-day expiry; paste the token back and it's checked, saved encrypted and
  never shown again. It takes precedence over `GITHUB_TOKEN` in `.env`. The
  page shows when it expires (with a new **token expiring** notification a
  week before) and checks each watched repo for real: can the token read it,
  and download its artifacts. The artifact list can hide Docker build records
  and filter by name.
- Security: **secrets in the database are now encrypted at rest**: the
  GitHub token, the TOTP secret and saved notification URLs, with a key
  derived from `SECRET_KEY`. Existing values are encrypted on first start.
  Changing `SECRET_KEY` now also means entering the token and notification
  services again and resetting two-factor (`mfa_admin.py reset`). Adds the
  `cryptography` dependency.
- Docs: `GITHUB_TOKEN` waives the rate limit for unchanged repos only when
  set; the README said this happened without a token too.
- Security: **failed sign-ins from IPv6 are counted per /64**, so rotating
  through one network's addresses no longer buys more guesses. Behind a
  reverse proxy, the new `FORWARDED_ALLOW_IPS` setting names the proxy, so
  each browser is counted by its own address: before, every sign-in seemed to
  come from the proxy, and a few wrong passwords from anyone locked everyone
  out for 5 minutes.
- Security: a `SECRET_KEY` under 32 characters or an `APP_PASSWORD` under 12
  is now logged at startup and warned about on every page. The app still
  starts with them.
- Fixed: the audit log dropped "trusted for 30 days" from a sign-in with a
  recovery code.
- Security: Python dependencies are installed from a lockfile that pins every
  package, direct and transitive, with its hashes (`app/requirements.in` →
  `app/requirements.txt`), so the image holds exactly the tree CI audited.
  CI also runs `bandit` now.
- Changed: both images build on Debian 13 (trixie); the adb server image and
  the tool-fetching stage were still on Debian 12.
- Docs: `.env.example` pointed at Settings → Artifacts (now Settings →
  GitHub) and left `token_expiring` out of the notification events; the
  README still said the TOTP secret and notification URLs weren't encrypted.
- Internal: `main.py` is split into one module per area
  (`routes_*.py`), with shared page helpers in `web.py` and upload staging in
  `uploads.py`. No route changed.

## 3.2.0 — 2026-09-24

- Added: **upload a zip holding an APK**, such as the artifact a GitHub
  Actions run hands back, without unzipping it first. The zip must hold
  exactly one APK; anything else in it (`output-metadata.json`, a mapping
  file) is ignored. The APK inside goes through every check a bare upload
  does (signature, package, debug flag), and the zip is deleted as soon as
  the APK is out of it. The same APK uploaded bare and zipped counts as one.
- Fixed: APKs built against current Android SDKs could not be staged at all,
  uploaded or polled. The legacy `aapt` failed reading them ("ERROR getting
  'android:icon'"); package info now comes from `aapt2`, from the same pinned
  build-tools release.

## 3.1.0 — 2026-09-23

- Changed: a simpler flow. The top bar is now **Status · Sources · Devices ·
  Install · Settings**, in the order you set things up, down from eight tabs.
  - **Sources** replaces Repos and Upload: watch a GitHub repo and upload an
    APK on the same page, with one list of repos and one of uploads.
  - **Install** replaces Staged: one card per app with its latest version and
    a **Push** button that picks the right build for the device's CPU. Older
    versions and individual files are one click down.
  - **Status** is one card per device: every app on it (installed against
    latest, **Update** / **Install**, auto-update) plus uploads pushed to it,
    and its last few installs. Until everything is set up it shows a
    checklist: add a source, pair and trust a device, install an app.
  - **Devices** explains pairing step by step, and trust is a plain
    **Trust** / **Revoke** button.
  - Install history and the audit log moved to **Settings → Activity**.
  - The old addresses (`/repos`, `/upload`, `/staged`) redirect to the new pages.
- Changed: `docker-compose.yml` is now `compose.yaml`. It pulls the published
  images, pinned to the release, sets fixed container names, and keeps one
  comment line per setting at the bottom. Building from a checkout moved to
  `compose.build.yaml`
  (`docker compose -f compose.yaml -f compose.build.yaml up -d --build`).
- Docs: the quickstart no longer needs a clone. It treats the stack
  directory (`compose.yaml` and `.env`) and the data directory as separate
  places, which they often are. It generates `.env` from a copy-paste block
  with the server's IP and hostname in `ALLOWED_HOSTS` (the cause of "Invalid
  host header"), and explains each setting. Screenshots retaken in the Dark
  theme.

## 3.0.0 — 2026-09-23

- **Breaking:** removed QR-code pairing and the `mdns` container it needed.
  That container ran on the host network (mDNS multicast can't cross Docker's
  bridge), and no container in this stack may. Nothing runs on host
  networking any more. Pair with a pairing code instead. Finding a phone again
  after its port changes still works, by scanning its last known IP.
  To upgrade: pull the new compose file, run
  `docker compose up -d --build --remove-orphans`, then
  `sudo rm -rf /opt/docker/adb-server/mdns`. Paired phones and the database
  are untouched.
- Removed: automatic trust for QR-paired phones. Every newly paired device
  now starts untrusted, until you trust it on the Devices page.
- Removed: the `ghcr.io/darthrater78/adb-server/mdns` image is no longer
  built or published.
- Docs: the README now links to the repo and this version's release notes at
  the top.

## 2.0.0 — 2026-09-23

- **Breaking:** data now lives in bind mounts under `/opt/docker/adb-server`
  (`adbkeys`, `appdata`, `mdns`) instead of named volumes. Before recreating
  the stack, create the directories and hand them to uid 10001 (see the README
  quickstart), then copy the old volumes across, or the adb key (and with it
  every phone pairing) and the database are lost:
  `docker run --rm -v <project>_adbkeys:/src -v /opt/docker/adb-server/adbkeys:/dst alpine cp -a /src/. /dst/`,
  and the same for `appdata`. `mdns` needs no copy.
- Changed: the explanatory comments in `docker-compose.yml` moved to a Notes
  block after the services, so the compose block itself is clean.
- Fixed: a test that could fail at random (its sample APK carried the current
  time, so two copies made a second apart were not identical).

## 1.0.0 — 2026-09-23

First stable release.

- Added: Settings page. **Notifications** — point ADB Server at an Apprise
  API server (its notify URL or address + config key, with optional tags), or
  add individual Apprise services, alongside any in `.env`; choose which
  events notify; send a test to one service or all, with a delivered/failed
  result for each, or test a server or URL before saving it; a URL guide for common services; tokens are always masked
  and never logged. **Appearance** — an app-wide two-tone colour scheme
  (primary and secondary), from preset pairs or two colour pickers, with a
  live preview, adjusted per theme to stay readable. Custom pairs can be
  saved under a name, re-applied and deleted.
- Added: two-factor sign-in (TOTP, any authenticator app) in Settings →
  Security, with 10 single-use recovery codes, an option to trust a browser
  for 30 days (revocable from Settings), a 15-minute lockout after 5 wrong
  codes, and `mfa_admin.py unlock|reset` for getting back in from the server.
- Added: pairing with a QR code, as in Android Studio: scan the code on the
  Devices page and the phone is paired, added and trusted by itself (the QR
  page says so; pairing with a code still leaves trusting to you). Needs the new
  `mdns` container, which listens on the host's network for the phone's
  announcement (multicast can't reach the bridge network) and shares only a
  read-only file with the app; the adb server stays off the LAN.
- Fixed: "Find" and pushes never recognised a phone after its wireless
  debugging port changed, so they reported it "not found". Devices are now
  identified by their hardware serial rather than adb's ip:port name, and
  ones paired before this are upgraded in place, keeping their nickname,
  trust and history. Re-pairing one keeps its nickname and history but asks
  you to trust it again: the same IP doesn't prove it's the same phone.
- Changed: paired phones list this server as `@adbserver` instead of a
  random container ID (re-pair to see it).
- Changed: on desktop every table row is one line, sized to its contents;
  long names are shortened with a tooltip. Stacked cards are for phones only.
- Security: request bodies are limited before they're read. Previously any
  POST — including the unauthenticated login form — accepted an unbounded
  multipart upload, which FastAPI spooled to disk before checking the
  session, so anyone who could reach the port could fill the data volume.
  Forms are now capped at 64 KB, only the upload route takes files, and only
  from a signed-in session.
- Security: logging out invalidates every issued session cookie, not just
  the browser's copy.
- Security: a push re-checks the device's trust when it runs, not only when
  it's queued, and never reaches a different paired phone that has taken over
  an old device's IP.
- Security: two-factor codes are checked atomically — a burst of parallel
  sign-in attempts can neither spend one code twice nor get more than five
  guesses before the lockout.
- Security: the `mdns` listener drops its oldest entry when full, so a host
  flooding the LAN with announcements can't block QR pairing.
- Added: a detailed Security section and screenshots in the README.
- Added: the header links to this version's release notes and to the GitHub
  repository.
- Fixed: a non-ASCII username, password or CSRF token caused a server error
  instead of a normal rejection.
- Fixed: a push interrupted by a restart stayed "installing" forever; it's
  now marked failed at startup.
- Fixed: network errors talking to GitHub left partial downloads behind and
  weren't shown on the Repos page.
- Changed: the GitHub token is only ever sent to `api.github.com`.
- Changed: Android tool downloads are pinned by SHA-256 rather than SHA-1.
- Added: manual APK upload on the Staged page, for builds not published as a
  GitHub release. Verified like a polled release and stored by content hash;
  debug-signed builds are allowed on this path only, and marked.
- Added: `release.yml` — pushing a `v*` tag publishes all three images to GHCR and
  creates the GitHub release, after checking the tag is on the default
  branch, matches `VERSION`, and CI passed for that commit.
- Added: CI checks that `VERSION` matches `CHANGELOG.md`, that the compose
  file is valid, and runs `pip-audit` on the Python dependencies.
- Changed: release notes on the Staged page open in a full-width panel, and
  the Status page links to them.
- Changed: phone layout — the nav wraps into tap-sized links with the current
  page highlighted, and every table becomes a stack of labelled cards (up to
  960px wide, so tablets get it too). Buttons never wrap their text. Uploads
  have their own page and "Upload" nav link.
- Added: Update/Install on the Status page asks for confirmation first,
  showing the version and device.
- Changed: the app has its own identity — "ADB Server" name ("APK Pusher" subtitle) and logo (also
  the browser-tab icon), a teal palette across all three themes, and a
  redesigned sign-in page. Secondary and destructive buttons are outlined so
  each row has one primary action. Timestamps read as `Sep 23, 11:25 UTC`,
  with the exact value on hover.
- Fixed: only an APK's first signer was checked for the debug certificate and
  covered by the pin; every signer is now. The fingerprint is read from the
  certificate digest line only, never the public-key digest.
- Fixed: the login rate limiter kept every IP that ever failed a login in
  memory forever; expired entries are swept and the table is capped.
- Fixed: `POST /login` now refuses a cross-origin form post.
- Fixed: an asset download follows a redirect only over HTTPS, and only to
  GitHub's own hosts.
- Fixed: bracketed IPv6 device addresses (`[fd00::5]:5555`) are parsed
  instead of split at the wrong colon.
- Changed: the `app` container runs with a read-only root filesystem.
- Changed: indexes on the columns the listings join and sort on; the app logs
  its accepted `ALLOWED_HOSTS` at startup.
- Fixed: a release that failed signature verification or the pin check was
  re-downloaded (up to 500 MB) on every poll. It is now checked once and
  skipped until a newer release appears; the error stays visible.
- Fixed: APK verification (`apksigner`/`aapt`) no longer blocks the web UI
  while it runs.
- Fixed: a release tag containing `/` crashed its repo's check, and any
  unexpected error in one repo stopped every repo after it from being polled.
- Fixed: removing a repo now deletes its staged APK files from disk.
- Fixed: error messages sent to the Staged page (e.g. "Device is not
  trusted") were never displayed.
- New: staged APK retention — only the newest `KEEP_RELEASES_PER_REPO`
  (default 3) files per repo are kept; install history is preserved. Staged
  files can also be deleted by hand.
- New: Status page (now the home page) — each watched app's installed
  version on every trusted device next to the latest staged version, with a
  one-click Update/Install.
- New: pushes find a device again after its wireless debugging port changes,
  by scanning its last known IP and confirming its serial; "Find" on the
  Devices page does it on demand.
- New: releases with several APKs (per-ABI builds) stage every variant; the
  Status page picks the right one for each device's CPU, and a push of an
  incompatible APK is refused. Existing databases are migrated automatically.
- New: signing-key rotation review — a pin mismatch shows both certificates
  and whether the new APK proves the rotation (APK Signature Scheme v3
  lineage); the new certificate can be accepted as the pin.
- New: notifications through Apprise (`APPRISE_URLS`) for staged releases,
  rejected releases and push results.
- New: per-device auto-update — a device following an app gets each new
  release pushed to it automatically.
- New: per-repo "Include pre-releases" option.
- New: audit log page recording logins, trust changes, re-pins, repo and
  device changes and pushes.
- New: `/healthz` and Docker health checks for both containers.
- Changed: "Check now" and accepting a new signer run in the background
  instead of holding the page open during the download; a repo is never
  checked twice at once.
- Changed: GitHub polls use conditional requests (ETags), so unchanged repos
  don't count against the API rate limit.
- New: automated test suite (`pytest`, run with `scripts/test.sh`).
- New: CI builds both images and runs the tests on every push and PR;
  actionlint checks workflow edits; Dependabot watches pip, Docker base images
  and Actions. Base images are now pinned by digest.

## 0.2.0 — 2026-09-16

- Push is now a background job: submitting a push redirects immediately to a
  status page (`/installs/<id>`) that auto-refreshes (no JavaScript — CSP
  stays `script-src 'none'`) and shows an animated progress bar through
  pending → installing → success/failed. A crash mid-install now always
  resolves the install row instead of leaving it stuck "installing" forever.
- Staged APKs now capture and display each release's GitHub release notes
  (new `release_notes` column, auto-migrated for existing databases).
- Repos page gets a visual pass: status badges, tag pills, and a
  watched/error-count summary line.
- The running app version now shows in the page header, read from `VERSION`
  at container build time.
- Theme switcher: Flashbang (light), Dark, and OLED black, chosen via a
  CSRF-protected form and stored as a plain cookie — no client-side JS. With
  no preference set, the app still follows the OS `prefers-color-scheme` as
  before.

## 0.1.0 — 2026-09-16

Initial scaffold: two-container system (`adb-server` for the ADB protocol,
`app` for the FastAPI web UI + poller) that watches GitHub repos for new APK
releases and pushes verified builds to trusted, wireless-paired Android
devices.

- GitHub release polling with signer-certificate and package-name pinning on
  the first release seen per repo; every later release must match both or is
  rejected and surfaced, never silently skipped.
- Device pairing/connect/trust flow over wireless ADB, with device identity
  re-confirmed immediately before every push (address drift protection).
- Session-cookie auth (signed, `HttpOnly`, `SameSite=Strict`) with CSRF
  tokens on every state-changing request, login rate limiting, and
  `TrustedHostMiddleware` + CSP/security headers.
- `adb`, `apksigner`, and `aapt` built into the images from Google's official
  releases with pinned URLs and verified SHA-1 checksums, not a third-party
  pre-built image.
- `adb-server`'s port is confined to the internal Docker network — never
  published to the host or LAN.
