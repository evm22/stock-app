"""
data_store.py -- where the app keeps its own data (portfolio, transactions,
score history, daily-job results).

Two backends, picked automatically:

  * "local"  -- a folder on disk (./local_data by default). Used when no
                private data repo is configured. This is what runs today.
  * "github" -- a JSON/CSV file inside a PRIVATE GitHub repo, read and written
                through the GitHub Contents API. Switches on by itself once
                DATA_REPO ("owner/name") and DATA_REPO_TOKEN are configured.

Pure Python on purpose: NO streamlit import, so the headless daily job can use
it too. The Streamlit layer passes repo/token in from st.secrets.

The token is never put into an error message or printed.
"""

import base64
import json
import os
import time

import requests

GITHUB_API = "https://api.github.com"
DEFAULT_LOCAL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "local_data")
TIMEOUT = 20  # seconds, for every HTTP request


class DataStoreError(Exception):
    """Raised when reading or writing the data store fails."""


def backend_name(repo=None, token=None) -> str:
    """'github' when a data repo + token are configured, otherwise 'local'."""
    return "github" if (repo and token) else "local"


# ---------------------------------------------------------------------------
# Local backend
# ---------------------------------------------------------------------------

def _local_path(path, local_dir):
    base = os.path.abspath(local_dir or DEFAULT_LOCAL_DIR)
    full = os.path.abspath(os.path.join(base, path))
    # Refuse paths that escape the data folder (e.g. "../secrets.toml").
    if os.path.commonpath([base, full]) != base:
        raise DataStoreError(f"Path is outside the data folder: {path}")
    return full


def _local_read(path, local_dir):
    full = _local_path(path, local_dir)
    if not os.path.exists(full):
        return None
    with open(full, "r", encoding="utf-8") as fh:
        return fh.read()


def _local_write(path, text, local_dir):
    full = _local_path(path, local_dir)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    tmp = full + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)
    os.replace(tmp, full)  # atomic on the same disk: no half-written files


# ---------------------------------------------------------------------------
# GitHub backend
# ---------------------------------------------------------------------------

def _headers(token):
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _safe_error(what, path, response=None, exc=None):
    """Build an error message that never contains the token."""
    detail = ""
    if response is not None:
        detail = f"HTTP {response.status_code}"
    elif exc is not None:
        detail = type(exc).__name__
    return DataStoreError(f"Data store: could not {what} '{path}' ({detail}).")


def _gh_get(path, repo, token, session):
    """Return (text, sha) for a file in the repo, or (None, None) if missing."""
    url = f"{GITHUB_API}/repos/{repo}/contents/{path}"
    try:
        r = session.get(url, headers=_headers(token), timeout=TIMEOUT)
    except requests.RequestException as exc:
        raise _safe_error("read", path, exc=exc) from None
    if r.status_code == 404:
        return None, None
    if r.status_code != 200:
        raise _safe_error("read", path, response=r)
    body = r.json()
    content = body.get("content")
    if content is None or body.get("encoding") != "base64" or (body.get("size", 0) > 0 and content == ""):
        # Files over 1 MB come back without inline content: use the raw media type.
        try:
            raw = session.get(url, headers={**_headers(token), "Accept": "application/vnd.github.raw"},
                              timeout=TIMEOUT)
        except requests.RequestException as exc:
            raise _safe_error("read", path, exc=exc) from None
        if raw.status_code != 200:
            raise _safe_error("read", path, response=raw)
        return raw.text, body.get("sha")
    return base64.b64decode(content).decode("utf-8"), body.get("sha")


def _gh_put(path, text, message, repo, token, session, sha):
    url = f"{GITHUB_API}/repos/{repo}/contents/{path}"
    payload = {
        "message": message,
        "content": base64.b64encode(text.encode("utf-8")).decode("ascii"),
    }
    if sha:
        payload["sha"] = sha
    try:
        return session.put(url, headers=_headers(token), json=payload, timeout=TIMEOUT)
    except requests.RequestException as exc:
        raise _safe_error("write", path, exc=exc) from None


def _gh_write(path, text, message, repo, token, session):
    _, sha = _gh_get(path, repo, token, session)
    r = _gh_put(path, text, message, repo, token, session, sha)
    if r.status_code in (409, 422):
        # Someone else (e.g. the daily job) changed the file since we read it:
        # read the new version id and try once more.
        time.sleep(1)
        _, sha = _gh_get(path, repo, token, session)
        r = _gh_put(path, text, message, repo, token, session, sha)
    if r.status_code not in (200, 201):
        raise _safe_error("write", path, response=r)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def read_text(path, *, repo=None, token=None, local_dir=None, session=None):
    """Return a file's text, or None if it doesn't exist yet."""
    if backend_name(repo, token) == "github":
        text, _ = _gh_get(path, repo, token, session or requests.Session())
        return text
    return _local_read(path, local_dir)


def write_text(path, text, message="Update data", *, repo=None, token=None,
               local_dir=None, session=None):
    """Create or replace a file with `text`."""
    if backend_name(repo, token) == "github":
        _gh_write(path, text, message, repo, token, session or requests.Session())
    else:
        _local_write(path, text, local_dir)


def read_json(path, default=None, *, repo=None, token=None, local_dir=None, session=None):
    """Return the parsed JSON in `path`, or `default` if the file doesn't exist."""
    text = read_text(path, repo=repo, token=token, local_dir=local_dir, session=session)
    if text is None or not text.strip():
        return default
    try:
        return json.loads(text)
    except ValueError:
        raise DataStoreError(f"Data store: '{path}' is not valid JSON.") from None


def write_json(path, data, message="Update data", *, repo=None, token=None,
               local_dir=None, session=None):
    """Save `data` as pretty JSON (Hebrew kept readable)."""
    text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    write_text(path, text, message, repo=repo, token=token,
               local_dir=local_dir, session=session)
