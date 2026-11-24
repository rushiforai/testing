#!/usr/bin/env python3
"""Mirror the Morphe community request board into GitHub issues.

New requests come from the full board API
(https://morphe-patches.software/api/requests), which has every request and its
creation time. The RSS feed (requests.xml) is capped at the 50 most recent items, so
it is used only for update events: the patch text, the last-activity date and the
request link. Every request becomes one issue labelled `request-feed`; later runs
find the issue through the `<!-- morphe-request: request-N -->` marker and update it
instead of opening a duplicate.

The feed is optional: when it is unreachable or its XML is malformed the run warns and
continues from the API, and a malformed API entry is skipped instead of failing the run.

An issue created here is inert: `.github/workflows/ai-patch.yml` only starts the
patch agent after a maintainer comments `approve` (optionally `approve <url>`).

At most `--max-new` issues are created per run (default 100, 0 = no limit). The API
keeps every request, so requests over the cap are picked up by a later run.

Usage:
  python3 .github/scripts/requests_sync.py [--dry-run] [--feed URL]
      [--existing-file PATH] [--max-new N] [--no-api]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

FEED_URL = "https://morphe-patches.software/requests.xml"
API_URL = "https://morphe-patches.software/api/requests"
LABEL = "request-feed"
USER_AGENT = "MorphePatchesRequestsSync/1.0 (+https://github.com/rushiforai/testing)"

MARKER_RE = re.compile(r"<!--\s*morphe-request:\s*(request-\d+)\s*-->")
STATE_RE = re.compile(r"<!--\s*morphe-request-state:.*?-->")
SUMMARY_RE = re.compile(
    r"<!--\s*morphe-request-summary\s*-->.*?<!--\s*/morphe-request-summary\s*-->",
    re.DOTALL,
)

# The board API replaces the RSS title with a category slug for patch requests. The feed
# carries the exact wording and wins whenever it has the request; this is the fallback for
# requests that only the API has.
CATEGORY_TEXT = {
    "background": "Background play",
    "theme": "Themes and colours",
    "premium": "Unlock premium features",
    "remove-ads": "Remove ads",
    "download": "Enable downloads",
    "latest-version": "Support the latest version",
    "hide-upsells": "Hide premium upsells",
    "hide-ai": "Hide AI features",
    "privacy": "Improve privacy",
    "no-updates": "Disable update checks",
}


# App names are not ASCII (for example "UOSOŚ"). GitHub runners are UTF-8, but a local
# Windows shell defaults to cp1252, where printing one would abort the sync.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")


def log(message: str) -> None:
    print(message, flush=True)


def warn(message: str) -> None:
    """Workflow warning annotation; annotations have to stay on one line."""
    log("::warning::" + " ".join(str(message).split()))


def fetch(url: str, timeout: int = 60) -> bytes:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "*/*"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def node_text(item: ET.Element, tag: str) -> str:
    found = item.find(tag)
    return (found.text or "").strip() if found is not None else ""


def parse_item(item: ET.Element) -> dict | None:
    guid = node_text(item, "guid")
    match = re.fullmatch(r"request-(\d+)", guid)
    if not match:
        warn(f"Ignoring feed item with unexpected guid: {guid!r}")
        return None

    title = node_text(item, "title")
    link = node_text(item, "link")
    description = node_text(item, "description")

    try:
        updated = parsedate_to_datetime(node_text(item, "pubDate"))
    except (TypeError, ValueError, OverflowError):
        updated = None
    if updated is not None and updated.tzinfo is None:
        updated = updated.replace(tzinfo=timezone.utc)
    if updated is not None:
        updated = updated.astimezone(timezone.utc)

    package = ""
    votes = 0
    description_match = re.match(
        r"^\s*([A-Za-z][A-Za-z0-9_.]*)\s*[\u00b7\u2022]\s*(\d+)\s+votes?",
        description,
    )
    if description_match:
        package = description_match.group(1)
        votes = int(description_match.group(2))
    else:
        votes_match = re.search(r"(\d+)\s+votes?", description)
        if votes_match:
            votes = int(votes_match.group(1))
    if not package:
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(link).query)
        package = (query.get("app") or [""])[0].strip()

    kind = "request"
    app = title
    request_text = title
    patch_match = re.match(r"^Patch request:\s*(.+)$", title, re.IGNORECASE)
    if patch_match:
        rest = patch_match.group(1).strip()
        kind = "patch"
        if " for " in rest:
            request_text, app = rest.rsplit(" for ", 1)
            request_text = request_text.strip()
            app = app.strip()
        else:
            request_text = rest
    else:
        app_match = re.match(r"^App request:\s*(.+)$", title, re.IGNORECASE)
        if app_match:
            kind = "app"
            app = app_match.group(1).strip()
            request_text = f"Add support for {app}."

    return {
        "guid": guid,
        "id": int(match.group(1)),
        "title": title,
        "link": link,
        "package": package,
        "votes": votes,
        "kind": kind,
        "app": app,
        "request_text": request_text,
        "updated": updated,
        "updated_iso": (
            updated.strftime("%Y-%m-%dT%H:%M:%SZ") if updated is not None else ""
        ),
    }


def parse_feed(data: bytes) -> list[dict]:
    root = ET.fromstring(data)
    items = []
    for item in root.iter("item"):
        parsed = parse_item(item)
        if parsed is not None:
            items.append(parsed)
    return items


def load_feed(url: str) -> list[dict]:
    """Fetch and parse update events, treating the feed as optional."""
    try:
        return parse_feed(fetch(url))
    except ET.ParseError as error:
        warn(f"Could not parse {url} as RSS; continuing with the board API: {error}")
    except Exception as error:  # noqa: BLE001 - the API can still run without the feed
        warn(f"Could not read {url}; continuing with the board API: {error}")
    return []


def kind_label(item: dict) -> str:
    return {"app": "App request", "patch": "Patch request"}.get(
        item["kind"], "Request"
    )


def issue_title(item: dict) -> str:
    if item["kind"] == "patch":
        summary = f"{item['request_text']} for {item['app']}"
    else:
        summary = item["app"]
    title = f"[Morphe request {item['id']}] {summary} ({item['package']})"
    return title if len(title) <= 240 else title[:237].rstrip() + "..."


def state_line(item: dict) -> str:
    return (
        f"<!-- morphe-request-state: pubdate={item['updated_iso']} "
        f"votes={item['votes']} -->"
    )


def table_cell(value: str) -> str:
    return str(value).replace("|", "\\|")


def utc_label(value: datetime | None) -> str:
    return value.strftime("%Y-%m-%d %H:%M UTC") if value is not None else "unknown"


def summary_block(item: dict) -> str:
    rows = [
        ("Type", kind_label(item)),
        ("App", f"{table_cell(item['app'])} (`{table_cell(item['package'])}`)"),
        ("Votes", str(item["votes"])),
    ]
    if item.get("status"):
        rows.append(
            (
                "Board status",
                "patch exists" if item["status"] == "exists" else item["status"],
            )
        )
    if item.get("created") is not None:
        rows.append(("Created", utc_label(item["created"])))
    rows.append(("Last activity", utc_label(item.get("updated"))))
    rows.append(("Source", f"[request-{item['id']}]({item['link']})"))
    body = "\n".join(f"| {name} | {value} |" for name, value in rows)
    return f"""<!-- morphe-request-summary -->
| | |
| --- | --- |
{body}
<!-- /morphe-request-summary -->"""


def clean_request_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    text = text.replace("<!--", "&lt;!--")
    # Keep request text from creating a new issue-form section.
    return re.sub(r"(?m)^(#{1,6})\s", r"\\\1 ", text)


def render_body(item: dict) -> str:
    return f"""<!-- morphe-request: {item['guid']} -->
{state_line(item)}

## Community request {item['id']}

{summary_block(item)}

This issue mirrors a request from the Morphe community board. Votes and dates refresh on the
next sync. The patch agent does not start until a maintainer approves the request.

### App name
{item['app']}

### Package name
{item['package']}

### App file download URL
_No response_

### App version (optional)
_No response_

### What should the patch do?
{clean_request_text(item['request_text'])}

### Approval

Add the app file URL above, then comment:

    approve

Or supply the URL in the same comment:

    approve https://example.com/app.apk
"""


def update_body(body: str, item: dict) -> str:
    """Refresh the machine state and visible summary, leaving the form fields alone."""
    updated = STATE_RE.sub(lambda _: state_line(item), body, count=1)
    updated = SUMMARY_RE.sub(lambda _: summary_block(item), updated, count=1)
    return updated


def category_text(category: str, app: str) -> str:
    if not category:
        return f"Add support for {app}."
    return CATEGORY_TEXT.get(category, f"Category: {category.replace('-', ' ')}.")


def api_timestamp(value: object) -> datetime | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        return datetime.fromtimestamp(value / 1000, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def parse_api_request(request: object) -> dict | None:
    """Turn one board API entry into a sync item, or None when it is unusable."""
    if not isinstance(request, dict):
        warn(f"Skipping board API entry that is not an object: {request!r:.120}")
        return None
    try:
        request_id = int(request["id"])
        app = str(request.get("name") or "").strip()
        package = str(request.get("pkg") or "").strip()
        votes = int(request.get("votes") or 0)
    except (KeyError, TypeError, ValueError):
        warn(f"Skipping malformed board API entry: {request!r:.120}")
        return None
    if not package or not app:
        warn(f"Skipping board API entry {request_id} without an app or package.")
        return None

    created = api_timestamp(request.get("created"))
    category = str(request.get("category") or "").strip()
    guid = f"request-{request_id}"
    return {
        "guid": guid,
        "id": request_id,
        "app": app,
        "package": package,
        "votes": votes,
        "created": created,
        "category": category,
        "status": str(request.get("status") or ""),
        "kind": "patch" if category else "app",
        "request_text": category_text(category, app),
        "updated": created,
        "link": f"https://morphe-patches.software/requests?app={package}",
    }


def api_requests() -> dict[str, dict]:
    """Return every board request, keyed by its `request-N` guid."""
    try:
        data = json.loads(fetch(API_URL).decode("utf-8"))
    except Exception as error:  # noqa: BLE001 - the feed can still run without it
        warn(f"Could not read {API_URL}: {error}")
        return {}

    raw_requests = data.get("requests") if isinstance(data, dict) else None
    if not isinstance(raw_requests, list):
        warn(f"{API_URL} did not return a list of requests; continuing without it.")
        return {}

    requests: dict[str, dict] = {}
    skipped = 0
    for request in raw_requests:
        item = parse_api_request(request)
        if item is None:
            skipped += 1
            continue
        requests[item["guid"]] = item
    if skipped:
        warn(f"Skipped {skipped} unusable board API entr{'y' if skipped == 1 else 'ies'}.")
    return requests


def merge_items(api: dict[str, dict], feed: dict[str, dict]) -> dict[str, dict]:
    """Join the API's full request list with the feed's update events."""
    merged = {guid: dict(item) for guid, item in api.items()}
    for guid, event in feed.items():
        item = merged.get(guid)
        if item is None:
            item = {
                "guid": guid,
                "id": event["id"],
                "app": event["app"],
                "package": event["package"],
                "votes": event["votes"],
                "created": None,
                "category": "",
                "status": "",
                "kind": event["kind"],
                "request_text": event["request_text"],
                "updated": event["updated"],
                "link": event["link"],
            }
        else:
            # The feed is the update event: its title carries the exact patch text and its
            # pubDate is the site's last-activity time. Identity fields stay from the API.
            if event["app"]:
                item["app"] = event["app"]
            if event["package"]:
                item["package"] = event["package"]
            item["kind"] = event["kind"]
            item["request_text"] = event["request_text"]
            item["updated"] = event["updated"] or item.get("created")
            item["link"] = event["link"] or item["link"]
        merged[guid] = item

    for item in merged.values():
        if not item.get("link"):
            item["link"] = (
                f"https://morphe-patches.software/requests?app={item['package']}"
            )
        item.setdefault("category", "")
        item.setdefault("status", "")
        item.setdefault("created", None)
        updated = item.get("updated")
        item["updated_iso"] = (
            updated.strftime("%Y-%m-%dT%H:%M:%SZ") if updated is not None else ""
        )
    return merged


def gh(*args: str, input_text: str | None = None, check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(
        ["gh", *args],
        input=input_text,
        text=True,
        encoding="utf-8",
        capture_output=True,
    )
    if check and result.returncode != 0:
        raise SystemExit(
            f"gh {' '.join(args)} failed with {result.returncode}: {result.stderr.strip()}"
        )
    return result


def ensure_label() -> None:
    gh(
        "label",
        "create",
        LABEL,
        "--color",
        "1D76DB",
        "--description",
        "Synced from morphe-patches.software/requests.xml",
        "--force",
        check=False,
    )


def list_tracked_issues() -> list[dict]:
    repo = os.environ.get("GITHUB_REPOSITORY") or os.environ.get("GH_REPO")
    if not repo:
        raise SystemExit("GITHUB_REPOSITORY or GH_REPO must be set to list tracked issues")
    # gh issue list stops at 1000; the tracked backlog grows by up to 50 a day, so page the
    # REST endpoint instead. --paginate applies --jq per page, one JSON object per line.
    result = gh(
        "api",
        "--paginate",
        f"repos/{repo}/issues?labels={LABEL}&state=all&per_page=100",
        "--jq",
        ".[] | {number,title,body,state,locked}",
    )
    issues = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            issues.append(json.loads(line))
        except ValueError as error:
            raise SystemExit(f"Could not parse gh api output: {error}")
    return issues


def create_issue(item: dict) -> None:
    result = gh(
        "issue",
        "create",
        "--title",
        issue_title(item),
        "--body-file",
        "-",
        "--label",
        LABEL,
        input_text=render_body(item),
    )
    log(f"  created: {result.stdout.strip()}")


def close_issue(number: int, item: dict) -> None:
    gh(
        "issue",
        "close",
        str(number),
        "--reason",
        "completed",
        "--comment",
        (
            f"Closing automatically: morphe-patches.software reports that **{item['app']}** "
            f"(`{item['package']}`) already has a patch, so this request is covered.\n\n"
            "Reopen the issue if the existing patch does not do what this request asks for."
        ),
    )
    log(f"  closed: #{number}")


def update_issue(number: int, title: str, body: str) -> None:
    gh(
        "issue",
        "edit",
        str(number),
        "--title",
        title,
        "--body-file",
        "-",
        input_text=body,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--feed", default=FEED_URL)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--existing-file",
        help="Read tracked issues from this JSON file instead of gh (for dry runs).",
    )
    parser.add_argument(
        "--no-api",
        action="store_true",
        help="Use only the RSS feed; do not backfill new requests from the board API.",
    )
    parser.add_argument(
        "--close-exists",
        action="store_true",
        help="Close tracked issues the board reports as already having a patch (status=exists).",
    )
    parser.add_argument(
        "--max-new",
        type=int,
        default=None,
        help=(
            "Maximum new issues per run; 0 means no limit "
            "(default: REQUESTS_MAX_NEW or 100)."
        ),
    )
    args = parser.parse_args()

    max_new = args.max_new
    if max_new is None:
        try:
            max_new = int(os.environ.get("REQUESTS_MAX_NEW", "100"))
        except ValueError:
            max_new = 100
    if max_new < 0:
        max_new = 100

    feed: dict[str, dict] = {}
    for event in load_feed(args.feed):
        feed.setdefault(event["guid"], event)
    log(f"Feed contains {len(feed)} update events.")

    api = {} if args.no_api else api_requests()
    if api:
        log(f"Board API contains {len(api)} requests.")
    elif not args.no_api:
        warn("Board API unavailable; continuing with feed-only sync.")

    merged = merge_items(api, feed)
    if not merged:
        log("No requests in the feed or the API; nothing to sync.")
        return

    if args.existing_file:
        existing = json.loads(Path(args.existing_file).read_text(encoding="utf-8"))
    elif args.dry_run:
        existing = []
        log("Dry run without --existing-file: assuming no tracked issues.")
    else:
        ensure_label()
        existing = list_tracked_issues()
    log(f"Tracking {len(existing)} existing issues.")

    by_guid: dict[str, dict] = {}
    for issue in existing:
        marker = MARKER_RE.search(issue.get("body") or "")
        if marker:
            by_guid[marker.group(1)] = issue

    def updated_ts(item: dict) -> float:
        value = item.get("updated")
        return value.timestamp() if value is not None else 0.0

    def created_ts(item: dict) -> float:
        value = item.get("created")
        return value.timestamp() if value is not None else 0.0

    # The board reports `exists` once a patch for that app/category is available, which makes
    # a tracked issue moot even if nobody here acted on it. Locked issues are skipped: the
    # agent locks an issue while it runs, and closing it mid-run would pull it out from under
    # the workflow. A closed issue simply stops matching on later runs.
    closed_by_board = 0
    if args.close_exists:
        for guid in sorted(merged):
            item = merged[guid]
            if (item.get("status") or "").strip().lower() != "exists":
                continue
            issue = by_guid.get(guid)
            if issue is None or issue.get("state") == "CLOSED":
                continue
            if issue.get("locked"):
                log(f"skip locked #{issue['number']} {guid}: a run is using it")
                continue
            log(f"close #{issue['number']} {guid}: the board says a patch already exists")
            if not args.dry_run:
                close_issue(int(issue["number"]), item)
            closed_by_board += 1

    created = updated = unchanged = skipped = 0

    # Update events come only from the feed. The API is for discovering new requests, so a
    # vote change the site did not publish as a feed event does not rewrite every issue.
    updates = sorted(
        (merged[guid] for guid in feed if guid in by_guid),
        key=updated_ts,
        reverse=True,
    )
    for item in updates:
        issue = by_guid[item["guid"]]
        if issue.get("state") == "CLOSED":
            log(f"skip closed #{issue['number']} {item['guid']}")
            skipped += 1
            continue

        title = issue_title(item)
        body = update_body(issue.get("body") or "", item)
        if title == issue.get("title") and body == (issue.get("body") or ""):
            unchanged += 1
            continue

        log(
            f"update #{issue['number']} {item['guid']} votes={item['votes']} "
            f"({'title' if title != issue.get('title') else 'details'})"
        )
        if not args.dry_run:
            update_issue(int(issue["number"]), title, body)
        updated += 1

    # New requests: the feed's untracked update events first, then the newest requests the
    # feed never showed. The API retains everything, so a per-run cap only defers work.
    untracked_feed = sorted(
        (merged[guid] for guid in feed if guid not in by_guid),
        key=updated_ts,
        reverse=True,
    )
    untracked_api = sorted(
        (
            item
            for guid, item in merged.items()
            if guid not in by_guid and guid not in feed
        ),
        key=created_ts,
        reverse=True,
    )
    new_items = untracked_feed + untracked_api
    if max_new and len(new_items) > max_new:
        log(
            f"Creating {max_new} of {len(new_items)} untracked requests this run; "
            "the rest are picked up next run."
        )
        new_items = new_items[:max_new]

    for item in new_items:
        log(
            f"create {item['guid']}: {item['app']} ({item['package']}) "
            f"votes={item['votes']}"
        )
        if not args.dry_run:
            create_issue(item)
        created += 1

    log(
        f"Done: {created} created, {updated} updated, {unchanged} unchanged, "
        f"{closed_by_board} closed as already patched, {skipped} closed requests skipped."
    )


if __name__ == "__main__":
    main()
