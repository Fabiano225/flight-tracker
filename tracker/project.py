"""Where this copy of the project lives: its GitHub repository and website.

GitHub Actions sets GITHUB_REPOSITORY, so a fork links to itself without code
changes. A custom domain can be set with the repository variable DASHBOARD_URL.
"""
import os
import re
import subprocess

FALLBACK = "Fabiano225/flight-tracker"
REPOSITORY = re.compile(r"[A-Za-z0-9-]+/[A-Za-z0-9._-]+")


def from_git():
    """owner/name from the origin remote of a local checkout, if there is one."""
    try:
        url = subprocess.run(["git", "config", "--get", "remote.origin.url"], capture_output=True,
                             text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""
    match = re.search(r"[:/]([A-Za-z0-9-]+)/([A-Za-z0-9._-]+?)(?:\.git)?/?$", url)
    return f"{match.group(1)}/{match.group(2)}" if match else ""


def repository():
    for value in (os.environ.get("GITHUB_REPOSITORY", ""), from_git()):
        if REPOSITORY.fullmatch(value):
            return value
    return FALLBACK


def repository_url():
    return f"https://github.com/{repository()}"


def dashboard_url():
    """The GitHub Pages address, or DASHBOARD_URL for a custom domain; ends with a slash."""
    custom = os.environ.get("DASHBOARD_URL", "").strip()
    if re.fullmatch(r"https://[^\s\"'<>()]+", custom):
        return custom.rstrip("/") + "/"
    owner, name = repository().split("/")
    # A repository named <owner>.github.io is published at the domain root.
    if name.lower() == f"{owner.lower()}.github.io":
        return f"https://{owner.lower()}.github.io/"
    return f"https://{owner.lower()}.github.io/{name}/"
