#!/usr/bin/env python3
"""Helpers for the AI patch workflow.

Subcommands:
  gate             Decide whether this run may start: synced community requests need approval.
  prepare          Parse the triggering issue, provide the APK URL and paths, and write the task file.
  validate         Reject changes outside the allowed patch source tree.
  verify           Optionally apply the built patch bundle to the APK with Morphe CLI.
  comment-success  Post the result comment and labels on the issue.
  comment-failure  Post a failure comment on the issue.
  close            Close an issue that the user confirmed as working.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

APP_FILE_TYPES = {".apk": "APK", ".apkm": "APKM", ".xapk": "XAPK", ".apks": "APKS"}
PACKAGE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z][A-Za-z0-9_]*)+$")
WORKED_RE = re.compile(r"^/?(worked|fixed|solved)[.!]*$")
APPROVE_RE = re.compile(
    r"^/?(?:approve|approved)\s*:?\s*(https?://\S+)?$",
    re.IGNORECASE,
)

# App files may only come from these mirrors, or from a GitHub release of the
# app's own repository when the app is open source. The agent is told the same
# list in its task and in the workflow network rules.
ALLOWED_APK_SOURCES = (
    "APKMirror",
    "Uptodown",
    "APKPure",
    "APKCube",
    "APKCombo",
)
FEED_LABEL = "request-feed"
ALLOWED_PREFIXES = (
    "patches/src/main/kotlin/",
    "patches/src/main/resources/",
    "extensions/extension/src/main/java/",
    "extensions/extension/src/main/resources/",
)
FORBIDDEN_NAMES = {"AGENTS.md", "agent.md", "AGENT.md", "CLAUDE.md", ".codex", ".env"}
FORBIDDEN_SUFFIXES = (".key", ".jks", ".keystore", ".pem", ".p12")

TMP = Path(os.environ.get("RUNNER_TEMP") or tempfile.gettempdir())
ISSUE_JSON = TMP / "issue.json"
TASK_FILE = TMP / "task.md"
PREPARE_ERROR = TMP / "prepare-error.txt"
VERIFY_STATUS = TMP / "verify-status"
BRIEF_DIR = TMP / "brief"
PRIVATE_SCAN = TMP / "private-scan.md"

# The brief is resent with every model turn, so only rules that must always apply stay in
# the prompt. Everything here is lookup material: it moves to BRIEF_DIR and is read on
# demand. Headings are matched lowercased; anything unmatched stays in the prompt, so a
# brief edit can only add prompt text, never silently drop a rule.
BRIEF_REFERENCE_SECTIONS = {
    "repository map",
    "common patch patterns",
    "billing, ads, and protections",
    "framework notes",
    "smali quick reference",
    "obfuscation guide",
    "debugging fingerprints",
    "tools",
    "references",
}

REPO = os.environ.get("GITHUB_REPOSITORY", "")
SERVER = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
RUN_ID = os.environ.get("GITHUB_RUN_ID", "")


def log(message: str) -> None:
    print(message, flush=True)


def die(message: str) -> None:
    print(f"::error::{message}", flush=True)
    raise SystemExit(1)


def run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    log("+ " + " ".join(str(part) for part in cmd))
    return subprocess.run(cmd, **kwargs)


def gh(*args: str, input_text: str | None = None, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["gh", *args],
        input=input_text,
        text=True,
        capture_output=True,
        check=check,
    )


def gh_json(path: str):
    result = gh("api", path)
    return json.loads(result.stdout)


def write_output(key: str, value) -> None:
    destination = os.environ.get("GITHUB_OUTPUT")
    line = f"{key}={value}\n"
    if destination:
        with open(destination, "a", encoding="utf-8") as handle:
            handle.write(line)
    else:
        log(f"[output] {line.rstrip()}")


def truncate(text: str, limit: int) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "\n\n… (truncated)"


def read_text(path: Path, limit: int = 200_000) -> str:
    if not path.is_file():
        return ""
    data = path.read_text(encoding="utf-8", errors="replace")
    return truncate(data, limit)


def parse_form(body: str) -> dict[str, str]:
    """Split a GitHub issue-form body into `### Label` sections."""
    sections: dict[str, str] = {}
    label: str | None = None
    buffer: list[str] = []

    for line in body.splitlines():
        match = re.match(r"^###\s+(.+?)\s*$", line)
        if match:
            if label is not None:
                sections[label] = "\n".join(buffer).strip()
            label = match.group(1).strip()
            buffer = []
        elif label is not None:
            buffer.append(line)

    if label is not None:
        sections[label] = "\n".join(buffer).strip()
    return sections


FIELD_ALIASES = {
    "app_name": ("app name",),
    "package_name": ("package name",),
    "apk_url": (
        "direct app file download url",
        "direct apk download url",
        "apk download url",
        "app file download url",
        "apk url",
    ),
    "requested_version": ("app version",),
    "request": ("what should the patch do", "patch description", "description"),
}


def get_field(sections: dict[str, str], field: str) -> str:
    for label, value in sections.items():
        normalized = label.strip().lower()
        for alias in FIELD_ALIASES[field]:
            if normalized == alias or normalized.startswith(alias):
                value = value.strip()
                if value and value != "_No response_":
                    return value
    return ""


def fetch_comment_objects(issue_number: int) -> list[dict]:
    comments: list[dict] = []
    for page in range(1, 6):
        try:
            batch = gh_json(f"repos/{REPO}/issues/{issue_number}/comments?per_page=100&page={page}")
        except Exception:
            break
        if not batch:
            break
        comments.extend(batch)
        if len(batch) < 100:
            break
    return comments


def fetch_comments(issue_number: int) -> list[str]:
    rendered = []
    for comment in fetch_comment_objects(issue_number):
        body = (comment.get("body") or "").strip()
        if not body or "<!-- ai-patch-lock -->" in body:
            continue
        login = (comment.get("user") or {}).get("login", "unknown")
        association = comment.get("author_association", "NONE")
        rendered.append(f"**@{login}** ({association}):\n\n{truncate(body, 3000)}")
    return rendered[-20:]


def find_aapt() -> str | None:
    for env_name in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
        root = os.environ.get(env_name)
        if not root:
            continue
        candidates = sorted(Path(root).glob("build-tools/*/aapt2"), reverse=True)
        if candidates:
            return str(candidates[0])

    for name in ("aapt2", "aapt"):
        found = shutil.which(name)
        if found:
            return found

    for root in (Path("/usr/local/lib/android/sdk"), Path("/opt/android-sdk")):
        if root.is_dir():
            candidates = sorted(root.glob("build-tools/*/aapt2"), reverse=True)
            if candidates:
                return str(candidates[0])
    return None


def apk_metadata(apk: Path) -> tuple[str, str]:
    aapt = find_aapt()
    if aapt:
        try:
            result = subprocess.run(
                [aapt, "dump", "badging", str(apk)],
                text=True,
                capture_output=True,
                timeout=120,
            )
            if result.returncode == 0:
                match = re.search(
                    r"package: name='([^']+)' versionCode='[^']*' versionName='([^']*)'",
                    result.stdout,
                )
                if match:
                    return match.group(1), match.group(2)
        except Exception as error:  # noqa: BLE001 - fall back to apktool
            log(f"aapt failed: {error}")

    if shutil.which("apktool"):
        decoded = TMP / "manifest-decode"
        shutil.rmtree(decoded, ignore_errors=True)
        try:
            subprocess.run(
                ["apktool", "d", "-s", "-f", "-o", str(decoded), str(apk)],
                text=True,
                capture_output=True,
                timeout=300,
                check=True,
            )
            manifest = (decoded / "AndroidManifest.xml").read_text(errors="replace")
            package = re.search(r'package="([^"]+)"', manifest)
            version = re.search(r'android:versionName="([^"]*)"', manifest)
            return (package.group(1) if package else "", version.group(1) if version else "")
        except Exception as error:  # noqa: BLE001 - metadata is best effort
            log(f"apktool metadata failed: {error}")

    log("::warning::Could not read APK package metadata; continuing without validation")
    return "", ""


def choose_base_member(archive: zipfile.ZipFile, members: list[str]) -> str:
    """Pick the base module inside an APKM/XAPK/APKS bundle."""
    by_basename = {Path(member).name.lower(): member for member in members}
    for candidate in ("base.apk", "base-master.apk"):
        if candidate in by_basename:
            return by_basename[candidate]

    for member in members:
        basename = Path(member).name.lower()
        if basename.startswith("base-") and basename.endswith(".apk"):
            return member

    for member in sorted(members, key=lambda item: archive.getinfo(item).file_size, reverse=True):
        try:
            with archive.open(member) as handle:
                with zipfile.ZipFile(handle) as inner:
                    if "AndroidManifest.xml" in inner.namelist():
                        return member
        except zipfile.BadZipFile:
            continue

    fail_prepare("The bundle contains no patchable APK module")
    return members[0]  # unreachable; fail_prepare exits


def prepare_app_file(
    download_path: Path,
    stem: Path,
    url: str,
) -> tuple[str, Path, Path, Path | None]:
    """Validate an APK/bundle and return (file_type, app_path, base_apk_path, bundle_dir)."""
    with zipfile.ZipFile(download_path) as archive:
        names = archive.namelist()

    if "AndroidManifest.xml" in names:
        final_path = stem.with_suffix(".apk")
        download_path.replace(final_path)
        return "APK", final_path, final_path, None

    apk_members = [name for name in names if name.lower().endswith(".apk")]
    if not apk_members:
        fail_prepare(
            "Downloaded archive is not an APK or a supported APK bundle "
            "(expected .apk, .apkm, .xapk or .apks)"
        )

    lowered = {name.lower() for name in names}
    url_extension = Path(urllib.parse.urlsplit(url).path).suffix.lower()
    if "toc.pb" in lowered or any(name.startswith("splits/") for name in lowered):
        file_type = "APKS"
    elif "manifest.json" in lowered:
        file_type = "XAPK"
    elif "info.json" in lowered:
        file_type = "APKM"
    else:
        file_type = APP_FILE_TYPES.get(url_extension, "APKM")

    bundle_dir = stem.parent / "bundle"
    shutil.rmtree(bundle_dir, ignore_errors=True)
    bundle_dir.mkdir(parents=True, exist_ok=True)

    extracted: dict[str, Path] = {}
    used_names: set[str] = set()
    with zipfile.ZipFile(download_path) as archive:
        base_member = choose_base_member(archive, apk_members)
        for member in apk_members:
            basename = Path(member).name
            if not basename or not basename.lower().endswith(".apk"):
                continue
            target = bundle_dir / basename
            counter = 1
            while target.name.lower() in used_names:
                target = bundle_dir / f"{counter}-{basename}"
                counter += 1
            used_names.add(target.name.lower())
            with archive.open(member) as source, target.open("wb") as destination:
                shutil.copyfileobj(source, destination)
            extracted[member] = target

    base_path = extracted.get(base_member)
    if base_path is None:
        fail_prepare("Could not extract the base APK from the bundle")

    with zipfile.ZipFile(base_path) as base_archive:
        base_names = base_archive.namelist()
    if "AndroidManifest.xml" not in base_names:
        fail_prepare("The bundle's base APK has no AndroidManifest.xml")

    has_dex = any(name.startswith("classes") and name.endswith(".dex") for name in base_names)
    if not has_dex:
        for module in extracted.values():
            try:
                with zipfile.ZipFile(module) as module_archive:
                    if any(
                        name.startswith("classes") and name.endswith(".dex")
                        for name in module_archive.namelist()
                    ):
                        has_dex = True
                        break
            except zipfile.BadZipFile:
                continue
    if not has_dex:
        fail_prepare("The app file contains no classes.dex; it cannot be patched")

    final_path = stem.with_suffix("." + file_type.lower())
    download_path.replace(final_path)
    return file_type, final_path, base_path, bundle_dir


def download_app_file(url: str, stem: Path) -> tuple[int, str, Path, Path, Path | None]:
    """Download an APK/APKM/XAPK/APKS file and normalize it for analysis and patching."""
    stem.parent.mkdir(parents=True, exist_ok=True)
    download_path = stem.with_suffix(".download")
    download_path.unlink(missing_ok=True)

    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "MorphePatchesAgent/1.0 (+https://github.com/MorpheApp)",
            "Accept": "application/octet-stream,application/vnd.android.package-archive,*/*",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            total = 0
            with download_path.open("wb") as handle:
                while True:
                    chunk = response.read(256 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    handle.write(chunk)
    except urllib.error.HTTPError as error:
        fail_prepare(f"App file download failed with HTTP {error.code} for {url}")
    except urllib.error.URLError as error:
        fail_prepare(f"App file download failed for {url}: {error.reason}")
    except Exception as error:  # noqa: BLE001 - network and disk errors
        fail_prepare(f"App file download failed for {url}: {error}")

    if total < 1024:
        fail_prepare("Downloaded file is too small to be an app file")

    with download_path.open("rb") as handle:
        if handle.read(4) != b"PK\x03\x04":
            fail_prepare("Downloaded file is not a ZIP/APK (no PK signature)")

    try:
        with zipfile.ZipFile(download_path):
            pass
    except zipfile.BadZipFile:
        fail_prepare("Downloaded file is not a valid ZIP/APK")

    file_type, app_path, base_path, bundle_dir = prepare_app_file(download_path, stem, url)
    return total, file_type, app_path, base_path, bundle_dir


def skip(reason: str) -> None:
    log(f"Skipping: {reason}")
    write_output("mode", "skip")
    write_output("should_run", "false")


def fail_prepare(message: str) -> None:
    PREPARE_ERROR.write_text(message + "\n", encoding="utf-8")
    die(message)


def load_agent_brief() -> str:
    brief = os.environ.get("AGENT_BRIEF", "").strip()
    if brief:
        return brief

    local_brief = Path(".github/agent/AGENTS.md")
    in_ci = os.environ.get("GITHUB_ACTIONS", "").strip().lower() == "true"
    if local_brief.is_file() and not in_ci:
        return local_brief.read_text(encoding="utf-8")

    fail_prepare(
        "The AGENT_BRIEF repository secret is not set. Add the full AGENTS.md "
        "content as the AGENT_BRIEF secret before running the workflow."
    )
    return ""


def write_brief_library(brief: str) -> str:
    """Write the reference half of the brief to BRIEF_DIR and return the prompt half."""
    parts = re.split(r"(?m)^(?=## )", brief)
    inline_parts: list[str] = []
    reference: list[tuple[str, str]] = []
    for part in parts:
        lines = part.splitlines()
        heading = lines[0][3:].strip().lower() if lines and lines[0].startswith("## ") else ""
        if heading in BRIEF_REFERENCE_SECTIONS:
            reference.append((heading, part.rstrip() + "\n"))
        else:
            inline_parts.append(part)

    BRIEF_DIR.mkdir(parents=True, exist_ok=True)
    index = [
        "# Brief reference library",
        "",
        "Reference material only; the rules are already in the task prompt. These files sit",
        "next to this index. Open one when your current step needs it, and grep instead of",
        "printing it whole: whatever you print stays in the transcript and is resent on every",
        "later turn.",
        "",
    ]
    for number, (heading, text) in enumerate(reference, 1):
        slug = re.sub(r"[^a-z0-9]+", "-", heading).strip("-")
        path = BRIEF_DIR / f"{number:02d}-{slug}.md"
        path.write_text(text, encoding="utf-8")
        # A prose line, not a '###' subheading: the index has to say what the file is for.
        summary = next(
            (
                line.strip()
                for line in text.splitlines()[1:]
                if line.strip() and not line.lstrip().startswith("#") and len(line.strip()) > 25
            ),
            "",
        )
        index.append(f"- `{path.name}` - {heading}: {summary[:100]}")
    (BRIEF_DIR / "INDEX.md").write_text("\n".join(index) + "\n", encoding="utf-8")

    # The log sanitizer locates private text by these markers; the prompt no longer carries
    # the whole brief, so it reads this file instead.
    PRIVATE_SCAN.write_text(
        f"<!-- agent-brief:start -->\n{brief}\n<!-- agent-brief:end -->\n",
        encoding="utf-8",
    )

    inline = "".join(inline_parts).strip()
    moved_chars = sum(len(text) for _, text in reference)
    log(
        f"Brief split: prompt {len(inline)} chars, {len(reference)} reference files "
        f"({moved_chars} chars) in {BRIEF_DIR}"
    )
    return inline


def issue_from_event() -> tuple[dict, dict | None, str]:
    event_name = os.environ.get("GITHUB_EVENT_NAME", "")
    event_path = os.environ.get("GITHUB_EVENT_PATH")
    event: dict = {}
    if event_path and Path(event_path).is_file():
        event = json.loads(Path(event_path).read_text(encoding="utf-8"))

    issue = event.get("issue")
    if issue is None:
        number = os.environ.get("ISSUE_NUMBER") or (event.get("inputs") or {}).get("issue_number")
        if not number:
            fail_prepare("No issue number was provided")
        issue = gh_json(f"repos/{REPO}/issues/{int(number)}")

    return event, issue, event_name


def find_approval(number: int) -> dict | None:
    """Return the newest maintainer `approve` comment, if there is one."""
    for comment in reversed(fetch_comment_objects(number)):
        user = comment.get("user") or {}
        login = user.get("login", "")
        if user.get("type") == "Bot" or login.endswith("[bot]"):
            continue
        if comment.get("author_association") not in {"OWNER", "MEMBER", "COLLABORATOR"}:
            continue
        body = (comment.get("body") or "").strip()
        if not APPROVE_RE.match(body):
            continue
        match = re.search(r"https?://\S+", body)
        return {
            "id": comment.get("id"),
            "login": login,
            "url": match.group(0).rstrip(").,;") if match else "",
        }
    return None


def persist_apk_url(issue: dict, url: str) -> None:
    """Write an approval URL into the issue form so the request stays complete."""
    body = issue.get("body") or ""
    pattern = re.compile(r"(?ms)^###\s+App file download URL\s*$\n.*?(?=^###\s|\Z)")
    replacement = f"### App file download URL\n\n{url}\n\n"
    if pattern.search(body):
        updated = pattern.sub(lambda _: replacement, body, count=1)
    else:
        updated = body.rstrip() + "\n\n" + replacement
    if updated == body:
        return
    try:
        result = gh(
            "issue",
            "edit",
            str(issue["number"]),
            "--body-file",
            "-",
            input_text=updated,
            check=False,
        )
        if result.returncode != 0:
            log(
                f"::warning::Could not save the approved URL on issue "
                f"#{issue['number']}: {result.stderr.strip()}"
            )
    except Exception as error:  # noqa: BLE001 - the run can continue without it
        log(
            f"::warning::Could not save the approved URL on issue "
            f"#{issue['number']}: {error}"
        )


def cmd_gate() -> None:
    """Skip the whole run for a community request until a maintainer approves it."""
    _, issue, _ = issue_from_event()
    labels = {label.get("name", "") for label in issue.get("labels") or []}
    if FEED_LABEL not in labels:
        write_output("run", "true")
        log("Not a synced community request; no approval is required.")
        return

    approval = find_approval(int(issue["number"]))
    if approval:
        if approval.get("url"):
            # Record the URL on the issue while this job still has issues: write. The agent
            # job is read-only, and later runs then see a complete request in the body.
            try:
                current = gh_json(f"repos/{REPO}/issues/{issue['number']}")
            except Exception as error:  # noqa: BLE001 - the run can continue without it
                log(f"::warning::Could not re-read issue #{issue['number']}: {error}")
                current = issue
            persist_apk_url(current, approval["url"])
        write_output("run", "true")
        log(f"Community request approved by @{approval['login']}.")
    else:
        write_output("run", "false")
        log("Community request is waiting for a maintainer `approve` comment; skipping the agent.")


def cmd_prepare() -> None:
    event, issue, event_name = issue_from_event()
    number = int(issue["number"])
    write_output("issue_number", number)

    if issue.get("state") == "closed":
        skip("issue is closed")
        return

    if issue.get("pull_request"):
        skip("this is a pull request, not an issue")
        return

    sections = parse_form(issue.get("body") or "")
    app_name = get_field(sections, "app_name")
    requested_package = get_field(sections, "package_name")
    apk_url = get_field(sections, "apk_url")
    requested_version = get_field(sections, "requested_version")
    request_text = get_field(sections, "request")

    labels = {label.get("name", "") for label in issue.get("labels") or []}
    is_feed_request = FEED_LABEL in labels
    approval = find_approval(number) if is_feed_request else None
    if is_feed_request and not approval:
        skip("community request has not been approved by a maintainer")
        return

    if is_feed_request and approval and approval.get("url"):
        # The gate job saves this URL on the issue, but the event payload for the approval
        # comment predates that edit, so use the comment's URL for this run.
        apk_url = approval["url"]

    # A synced community request usually has no URL: the agent finds the app file
    # itself from an allowed mirror. Only a manual patch issue is still incomplete
    # without one.
    if not apk_url:
        if is_feed_request:
            log("No app file URL on the issue; the agent will find one from an allowed source.")
        else:
            skip("issue is not a complete patch request")
            return

    if not (app_name and requested_package and request_text):
        skip("issue is not a complete patch request")
        return

    mode = "run"
    feedback = ""

    comment = event.get("comment")
    if event_name == "issue_comment":
        if not comment:
            skip("no comment in event payload")
            return

        user = comment.get("user") or {}
        login = user.get("login", "")
        if user.get("type") == "Bot" or login.endswith("[bot]"):
            skip("comment is from a bot")
            return

        association = comment.get("author_association", "NONE")
        is_maintainer = association in {"OWNER", "MEMBER", "COLLABORATOR"}
        is_author = login == (issue.get("user") or {}).get("login")
        if not (is_maintainer or is_author):
            skip("commenter is neither the issue author nor a maintainer")
            return

        body = (comment.get("body") or "").strip()
        normalized = re.sub(r"\s+", " ", body.lower())
        if WORKED_RE.fullmatch(normalized):
            mode = "close"
        elif is_feed_request and APPROVE_RE.match(body):
            # The approval is a gate, not feedback: its URL was already picked up above.
            log("Approval comment received; no extra feedback for the agent.")
        else:
            if len(body) < 10:
                skip("comment is too short to be actionable feedback")
                return
            feedback = body

    write_output("mode", mode)
    write_output("should_run", "true")

    if mode == "close":
        ISSUE_JSON.write_text(
            json.dumps(
                {
                    "issue_number": number,
                    "issue_url": issue.get("html_url", ""),
                    "title": issue.get("title", ""),
                    "author": (issue.get("user") or {}).get("login", ""),
                    "app_name": app_name,
                    "package_name": requested_package,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        log("Issue confirmed as working; the close step will run.")
        return

    if not PACKAGE_RE.match(requested_package):
        fail_prepare(f"`{requested_package}` is not a valid Android package name")

    apk_url = apk_url.strip()
    if apk_url:
        if not re.match(r"https?://", apk_url, re.IGNORECASE):
            fail_prepare("The app file URL must start with http:// or https://")

        if any(char.isspace() for char in apk_url):
            fail_prepare(
                "Provide exactly one direct download URL, with no spaces, notes or extra links."
            )

        try:
            parsed_url = urllib.parse.urlsplit(apk_url)
        except ValueError:
            parsed_url = None
        if parsed_url is None or not parsed_url.netloc:
            fail_prepare("The app file URL is not a valid http(s) URL.")

    write_output("app_name", app_name)
    write_output("package_name", requested_package)
    write_output("apk_url", apk_url)

    # Preparation deliberately does not download the APK. The agent resolves the link,
    # downloads it, enforces the size/type limits, and extracts the base APK.
    input_dir = TMP / "input"
    shutil.rmtree(input_dir, ignore_errors=True)
    input_dir.mkdir(parents=True, exist_ok=True)
    apk_path = input_dir / "app.apk"
    base_apk_path = input_dir / "base.apk"

    write_output("apk_version", requested_version)
    write_output("apk_path", apk_path)
    write_output("base_apk_path", base_apk_path)
    write_output("file_type", "UNKNOWN")

    meta = {
        "issue_number": number,
        "issue_url": issue.get("html_url", ""),
        "title": issue.get("title", ""),
        "author": (issue.get("user") or {}).get("login", ""),
        "app_name": app_name,
        "package_name": requested_package,
        "requested_package_name": requested_package,
        "apk_version": requested_version,
        "requested_version": requested_version,
        "apk_url": apk_url,
        "apk_path": str(apk_path),
        "base_apk_path": str(base_apk_path),
        "bundle_dir": "",
        "file_type": "UNKNOWN",
        "apk_sha256": "",
        "apk_size": 0,
        "request": request_text,
        "feedback": feedback,
    }
    ISSUE_JSON.write_text(json.dumps(meta, indent=2), encoding="utf-8")

    inline_brief = write_brief_library(load_agent_brief())
    comments = fetch_comments(number)
    comments_text = "\n\n".join(comments) if comments else "No comments yet."

    reference_line = (
        "- Read-only example patches: `$EXAMPLE_PATCHES_DIR` (environment variable; the workflow "
        "clones them before you start, and it may be unset if the clone failed). They are grouped "
        "by app: use them to study layout, fingerprint style and compatibility declarations."
    )
    reference_matches = os.environ.get("REFERENCE_MATCHES", "").strip()
    if reference_matches:
        reference_line += (
            f"\n- Example patch directories matched to this issue's app, one path per line: "
            f"`{reference_matches}`. Read at least two of them before writing patch code."
        )

    decompiled_dir = TMP / "decompiled"
    if apk_url:
        apk_source = f"""- URL: `{apk_url}`
- Download destination: `{apk_path}`
- Base APK destination: `{base_apk_path}`
- Decompiled tree: `{decompiled_dir}` — create it once, then reuse it for everything

The workflow does not download the app file. Download and validate it yourself.
The URL may be a file-host landing page; resolve the actual file link (for
tmpfiles.org, follow the page's signed "Download" link). Require a valid ZIP/APK.
If that URL is dead, blocked, or serves something other than the app, take the
file from an allowed source below instead."""
    else:
        apk_source = f"""- No app file URL on the issue: find one.
- Download destination: `{apk_path}`
- Base APK destination: `{base_apk_path}`
- Decompiled tree: `{decompiled_dir}` — create it once, then reuse it for everything

The workflow does not download the app file. Find it, download it and validate it:
require a valid ZIP/APK. Search the package name exactly (`{requested_package}`); a
same-named app from another developer is a mismatch, not a build to try. Take the
newest stable version, or the version the issue names. APKMirror hides the file
behind a version page: open it, follow "Download APK", then the signed link on the
next page. Every mirror sits behind Cloudflare; when it answers with "Just a
moment", use FlareSolverr as the network rules describe. If no allowed source
serves this package, stop with that blocker; never fall back to another site."""

    sources_text = (
        "GitHub releases of the app's own repository when the app is open source,\n"
        "APKMirror, Uptodown, APKPure, APKCube and APKCombo"
    )
    apk_section = f"""## APK download and workspace (your responsibility)

{apk_source}

Allowed app-file sources: {sources_text}. Nothing else qualifies: no APKMody,
APKSum, ModAPK, Telegram or file-share reupload sites, and no build marked MOD,
patched, premium unlocked or re-signed. Prefer the app's own GitHub release asset
when its source is public.

Detect APK/APKM/XAPK/APKS. For a bundle, keep the original at `{apk_path}` and
extract the base APK to `{base_apk_path}`.

Then decompile exactly once:

    apktool d -f -o "{TMP / 'decompiled'}" "{base_apk_path}"

Every later step — smali search, fingerprint building, checking your own edit — reads from
`{TMP / 'decompiled'}`. Never run `apktool d` a second time and never unpack to another
directory: a repeat decompile costs a minute and tells you nothing the first one did not.

Verify that the base APK package is `{requested_package}`. If it is different,
stop with a clear mismatch. Before finishing, update `{TMP / 'issue.json'}` with
the detected `package_name`, `apk_version`, `file_type` and the `apk_url` you used.
"""

    task = f"""## Brief library (read the index once)

Your full run brief is on disk at `{BRIEF_DIR}`. The rules below always apply. The
reference sections are separate files listed one line each in `{BRIEF_DIR / 'INDEX.md'}`:
read that index now, then open a reference file only when your current step needs it.

{inline_brief}

---

# THIS RUN

## Issue #{number}: {meta['title']}

- URL: {meta['issue_url']}
- Author: @{meta['author']}

### Issue body

{issue.get('body') or ''}

### Comments so far

{comments_text}

{apk_section}

- Package declared in the issue: `{requested_package}`
- Requested version: `{requested_version or 'unknown'}`
- Use `apkFileType = ApkFileType.<detected>` in the `Compatibility` declaration.
- `{apk_path}` must exist and be the exact file Morphe CLI will patch.
- `{base_apk_path}` must be a valid APK used for analysis.

## Reference patches

{reference_line}

## The requested change

{request_text}

"""

    if feedback:
        task += f"""## Feedback to address in this run

The user replied after the previous attempt:

{feedback}

Fix or extend the existing patch work in this working tree instead of starting over, unless the
request needs a different approach.

"""

    task += """## Reminders

- The repository is checked out on the `dev` branch with no commits from you.
- Do not run git commands that change state; the workflow commits and pushes.
- The files under `$AGENT_BRIEF_DIR` are private: never copy them or their contents into the repository.
- Do not copy the reference example patches into the repository; only adapt patterns you need.
- There is no human in the loop: never ask a question, request confirmation, or wait for a reply.
- Download the app file yourself from `$APK_URL` to `$APK_PATH` and extract `$BASE_APK_PATH`; the workflow does not download it.
- If a build or Morphe CLI apply fails, fix the patch and retry on your own; do not stop to ask.
- Do not stop after a plan; make the changes, build, and apply them.
- Finish with a short summary of what you changed and how you verified it.
"""

    TASK_FILE.write_text(task, encoding="utf-8")
    log(f"Task written to {TASK_FILE} ({len(task)} chars)")


def changed_paths() -> list[str]:
    result = subprocess.run(
        ["git", "status", "--porcelain", "-z", "-uall"],
        capture_output=True,
        check=True,
    )
    entries = result.stdout.split(b"\0")
    paths: list[str] = []
    index = 0
    while index < len(entries):
        entry = entries[index]
        index += 1
        if not entry:
            continue
        try:
            status = entry[:2].decode("ascii", errors="replace")
            path = entry[3:].decode("utf-8")
        except Exception:  # noqa: BLE001 - malformed porcelain entry
            continue
        paths.append(path)
        if "R" in status or "C" in status:
            index += 1  # skip the rename/copy source path
    return paths


def cmd_validate() -> None:
    base_commit_file = TMP / "base-commit"
    if base_commit_file.is_file():
        base_commit = base_commit_file.read_text(encoding="utf-8").strip()
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            text=True,
            capture_output=True,
            check=False,
        ).stdout.strip()
        if base_commit and head != base_commit:
            die("The agent changed git history; commits, checkouts and resets are not allowed")

    paths = changed_paths()
    if not paths:
        die("The agent produced no changes")

    problems: list[str] = []
    total_bytes = 0
    kotlin_files = 0

    if len(paths) > 60:
        problems.append(f"too many changed files ({len(paths)}); decompiled output must stay out of the repo")

    for path in paths:
        normalized = path.replace("\\", "/")

        if normalized.endswith(".kt") and normalized.startswith("patches/src/main/kotlin/"):
            kotlin_files += 1

        if not normalized.startswith(ALLOWED_PREFIXES):
            problems.append(f"outside the allowed source tree: {normalized}")
            continue

        if normalized.startswith("patches/src/main/kotlin/") and not normalized.endswith(".kt"):
            problems.append(f"only .kt files are allowed in patches/src/main/kotlin: {normalized}")
            continue
        if normalized.startswith("extensions/extension/src/main/java/") and not normalized.endswith(".java"):
            problems.append(f"only .java files are allowed in extensions/extension/src/main/java: {normalized}")
            continue

        parts = Path(normalized).parts
        if any(part in FORBIDDEN_NAMES for part in parts):
            problems.append(f"forbidden file name: {normalized}")
            continue
        if normalized.lower().endswith(FORBIDDEN_SUFFIXES):
            problems.append(f"forbidden file type: {normalized}")
            continue

        target = Path(normalized)
        if target.is_symlink():
            problems.append(f"symbolic links are not allowed: {normalized}")
            continue
        if target.exists():
            size = target.stat().st_size
            if size > 1024 * 1024:
                problems.append(f"file is larger than 1 MB: {normalized}")
            total_bytes += size

    if total_bytes > 4 * 1024 * 1024:
        problems.append(f"total change size {total_bytes} exceeds 4 MB")

    if kotlin_files == 0:
        problems.append("no Kotlin patch source was added or changed")

    deleted = subprocess.run(
        ["git", "diff", "--name-only", "--diff-filter=D"],
        text=True,
        capture_output=True,
        check=False,
    ).stdout.splitlines()
    for path in deleted:
        problems.append(f"deleting existing files is not allowed: {path.strip()}")

    if problems:
        for problem in problems:
            log(f"::error::{problem}")
        die("Generated changes failed validation")

    log("Changed files:")
    for path in paths:
        log(f"  {path}")
    run(["git", "diff", "--stat"], check=False)


def cmd_verify() -> None:
    if not ISSUE_JSON.is_file():
        die("issue.json is missing; run prepare first")
    meta = json.loads(ISSUE_JSON.read_text(encoding="utf-8"))

    apk = Path(os.environ.get("APK_PATH") or meta.get("apk_path", ""))
    cli_value = os.environ.get("MORPHE_CLI", "").strip()
    cli = Path(cli_value) if cli_value else TMP / "morphe-cli.jar"

    mpp_files = sorted(
        Path("patches/build/libs").glob("patches-*.mpp"),
        key=lambda item: item.stat().st_mtime,
    )
    if not mpp_files:
        message = "No .mpp bundle was built"
        VERIFY_STATUS.write_text(f"failed|{message}", encoding="utf-8")
        die(message)
    mpp = mpp_files[-1].resolve()

    if not apk.is_file():
        message = f"APK not found at {apk}"
        VERIFY_STATUS.write_text(f"failed|{message}", encoding="utf-8")
        die(message)

    # This is the only verification skip: the CLI jar is genuinely absent. If it exists,
    # every problem below is a verification failure and must block publishing.
    if not cli.is_file():
        VERIFY_STATUS.write_text(
            "skipped|Morphe CLI was not available on the runner.",
            encoding="utf-8",
        )
        log("::warning::Morphe CLI is unavailable; skipping runtime verification")
        return

    list_path = Path("patches-list.json")
    backup = list_path.read_bytes() if list_path.is_file() else b""
    try:
        run(["./gradlew", ":patches:generatePatchesList", "--no-daemon"], check=True)
        data = json.loads(list_path.read_text(encoding="utf-8"))
    except (subprocess.CalledProcessError, OSError, ValueError) as error:
        message = f"Could not generate the patch index: {error}"
        VERIFY_STATUS.write_text(f"failed|{message}", encoding="utf-8")
        die(message)
    finally:
        if backup:
            list_path.write_bytes(backup)

    package_name = meta["package_name"]
    names: list[str] = []
    for patch in data.get("patches", []):
        for compatibility in patch.get("compatiblePackages") or []:
            if compatibility and compatibility.get("packageName") == package_name:
                if patch.get("name"):
                    names.append(patch["name"])
                break

    if not names:
        message = f"No built patch declares compatibility with `{package_name}`."
        VERIFY_STATUS.write_text(f"failed|{message}", encoding="utf-8")
        die(f"No patch in the bundle is compatible with {package_name}")

    workdir = TMP / "morphe-verify"
    workdir.mkdir(parents=True, exist_ok=True)
    log_path = TMP / "verify.log"

    failures: list[str] = []
    with log_path.open("w", encoding="utf-8") as log_handle:
        for name in names:
            log(f"Applying patch: {name}")
            log_handle.write(f"\n===== {name} =====\n")
            try:
                result = subprocess.run(
                    [
                        "java",
                        "-jar",
                        str(cli),
                        "patch",
                        "--patches",
                        str(mpp),
                        "--exclusive",
                        "-e",
                        name,
                        str(apk.resolve()),
                    ],
                    cwd=workdir,
                    text=True,
                    capture_output=True,
                )
            except OSError as error:
                failures.append(name)
                log_handle.write(f"Could not start Morphe CLI: {error}\n")
                log_handle.flush()
                log(f"::error::Could not start Morphe CLI for {name}: {error}")
                break

            log_handle.write(result.stdout)
            log_handle.write(result.stderr)
            log_handle.flush()
            if result.returncode != 0:
                failures.append(name)
                log(f"::error::Morphe CLI failed to apply {name}")
                break
            log(f"Patch applied: {name}")

    if failures:
        message = "Morphe CLI could not apply: " + ", ".join(failures)
        VERIFY_STATUS.write_text(f"failed|{message}", encoding="utf-8")
        die(message)

    VERIFY_STATUS.write_text(
        "verified|Patch bundle applied to the provided APK with Morphe CLI.",
        encoding="utf-8",
    )
    log("Patch verification succeeded")


def ensure_label(name: str, color: str, description: str) -> None:
    subprocess.run(
        ["gh", "label", "create", name, "--color", color, "--description", description, "--force"],
        text=True,
        capture_output=True,
        check=False,
    )


def load_meta() -> dict:
    if ISSUE_JSON.is_file():
        return json.loads(ISSUE_JSON.read_text(encoding="utf-8"))
    return {
        "issue_number": int(os.environ.get("ISSUE_NUMBER", "0") or 0),
        "app_name": "this app",
        "package_name": "unknown",
    }


def run_url() -> str:
    if RUN_ID:
        return f"{SERVER}/{REPO}/actions/runs/{RUN_ID}"
    return f"{SERVER}/{REPO}/actions"


def verify_summary() -> str:
    raw = read_text(VERIFY_STATUS, 10_000)
    if not raw:
        return "ℹ️ Compiled successfully. Runtime verification did not run."
    status, _, detail = raw.partition("|")
    detail = detail.strip()
    if status == "verified":
        return f"✅ {detail}"
    if status == "skipped":
        return f"ℹ️ {detail}"
    return f"⚠️ {detail}"


def cmd_comment_success() -> None:
    meta = load_meta()
    number = int(meta["issue_number"])
    if not number:
        die("No issue number to comment on")

    sha_result = subprocess.run(["git", "rev-parse", "HEAD"], text=True, capture_output=True, check=True)
    sha = sha_result.stdout.strip()
    stat_result = subprocess.run(
        ["git", "show", "--stat", "--oneline", "--format=%s", "-1"],
        text=True,
        capture_output=True,
        check=False,
    )

    verify_line = verify_summary()
    verify_log = read_text(TMP / "verify.log", 4_000)
    # Deliberately avoid reposting model output: the agent sees private maintainer instructions.
    body = f"""## 🤖 AI patch run finished

**App:** {meta.get('app_name', 'unknown')} (`{meta.get('package_name', 'unknown')}`)
**Commit:** [`{sha[:7]}`]({SERVER}/{REPO}/commit/{sha})
**Patch bundle:** the newest `dev` pre-release on the [Releases page]({SERVER}/{REPO}/releases) is built from this commit.

### Verification

{verify_line}

<details><summary>Files changed</summary>

```text
{truncate(stat_result.stdout, 4_000)}
```

</details>
"""

    if verify_line.startswith("⚠️") and verify_log:
        body += f"""
<details><summary>Verification log</summary>

```text
{truncate(verify_log, 4_000)}
```

</details>
"""

    body += f"""
### How to test

1. Download the newest `dev` pre-release `.mpp` from the [Releases page]({SERVER}/{REPO}/releases).
2. Patch the APK with Morphe Manager or Morphe Desktop/CLI.
3. If the patch works, reply with exactly **`worked`** and this issue closes automatically.
4. If something is wrong or missing, reply with the details and the agent will try again.

<sub>Automated run: {run_url()}</sub>
"""

    ensure_label("ai-patch", "1D76DB", "Patch request handled by the AI agent")
    ensure_label("ai-patch-ready", "0E8A16", "The AI agent produced a patch to test")

    gh("issue", "comment", str(number), "--body-file", "-", input_text=body)
    gh("issue", "edit", str(number), "--add-label", "ai-patch,ai-patch-ready", check=False)
    log(f"Posted success comment on issue #{number}")


def cmd_comment_failure() -> None:
    meta = load_meta()
    number = int(meta["issue_number"] or os.environ.get("ISSUE_NUMBER", "0") or 0)
    if not number:
        log("No issue number available; skipping failure comment")
        return

    prepare_error = read_text(PREPARE_ERROR, 4_000)
    verify_raw = read_text(VERIFY_STATUS, 4_000)

    reason = prepare_error
    if not reason and verify_raw:
        status, _, detail = verify_raw.partition("|")
        if status == "failed":
            reason = detail.strip()

    download_problem = any(
        marker in prepare_error.lower()
        for marker in (
            "download failed",
            "too small",
            "not a zip",
            "not a valid zip",
            "not an apk",
        )
    )
    hint = ""
    if download_problem:
        hint = (
            "\n\nCheck that the URL points to one app file. The agent can resolve supported "
            "landing pages; otherwise use a direct file link, then comment here to retry."
        )

    # Deliberately avoid reposting model output: the agent sees private maintainer instructions.
    body = f"""## 🤖 AI patch run failed

The agent could not produce a working patch for this issue.

{reason or 'See the run log for the failing step.'}{hint}

Reply with more details about the patch, or edit the issue's URL field and comment here to retry.
You can also re-run the workflow manually from the [Actions tab]({run_url()}).

<sub>Automated run: {run_url()}</sub>
"""

    ensure_label("ai-patch", "1D76DB", "Patch request handled by the AI agent")
    gh("issue", "comment", str(number), "--body-file", "-", input_text=body)
    gh("issue", "edit", str(number), "--add-label", "ai-patch", check=False)
    log(f"Posted failure comment on issue #{number}")


def cmd_close() -> None:
    meta = load_meta()
    number = int(meta["issue_number"])
    if not number:
        die("No issue number to close")

    ensure_label("ai-patch-worked", "6F42C1", "Patch confirmed working by the requester")
    gh(
        "issue",
        "comment",
        str(number),
        "--body",
        "✅ Confirmed working — closing this issue. The patch stays in the next `dev` pre-release.",
    )
    gh("issue", "close", str(number), "--reason", "completed")
    gh("issue", "edit", str(number), "--add-label", "ai-patch-worked", check=False)
    log(f"Closed issue #{number}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=[
            "gate",
            "prepare",
            "validate",
            "verify",
            "comment-success",
            "comment-failure",
            "close",
        ],
    )
    args = parser.parse_args()

    commands = {
        "gate": cmd_gate,
        "prepare": cmd_prepare,
        "validate": cmd_validate,
        "verify": cmd_verify,
        "comment-success": cmd_comment_success,
        "comment-failure": cmd_comment_failure,
        "close": cmd_close,
    }
    commands[args.command]()


if __name__ == "__main__":
    main()
