"""Small GitHub gateway. Model/reviewer code never receives this client."""

import re
import time
from urllib.parse import quote

import requests

REPO_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*$")


class GitHubError(RuntimeError):
    def __init__(self, status: int, message: str, *, line_error: bool = False):
        super().__init__(message)
        self.status = status
        self.line_error = line_error


class UncertainPost(RuntimeError):
    """A POST may have committed; reconcile its marker before doing anything else."""


class GitHub:
    def __init__(
        self,
        token: str,
        session=None,
        api_url="https://api.github.com",
        sleep=time.sleep,
        app_slug=None,
    ):
        if not token:
            raise ValueError("GITHUB_TOKEN is required (read access is sufficient for a dry run).")
        self.token = token
        self.session = session or requests.Session()
        self.api_url = api_url.rstrip("/")
        self.sleep = sleep
        self.app_slug = app_slug

    def request(self, method, path, *, missing_ok=False, **kwargs):
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        for attempt in range(3 if method == "GET" else 1):
            try:
                response = self.session.request(
                    method, self.api_url + path, headers=headers, timeout=30, **kwargs
                )
            except (requests.Timeout, requests.ConnectionError) as exc:
                if method == "POST":
                    raise UncertainPost(
                        "GitHub did not confirm the write; checking for the run marker."
                    ) from exc
                if method == "GET" and attempt < 2:
                    self.sleep(2**attempt)
                    continue
                raise GitHubError(
                    0, "GitHub could not be reached. Retry the command later."
                ) from exc
            if response.status_code == 404 and missing_ok:
                return None
            if response.ok:
                return response.json() if response.content else None
            if (
                method == "GET"
                and (
                    response.status_code in {429, 500, 502, 503, 504}
                    or (
                        response.status_code == 403
                        and (
                            response.headers.get("Retry-After")
                            or response.headers.get("X-RateLimit-Remaining") == "0"
                        )
                    )
                )
                and attempt < 2
            ):
                try:
                    delay = min(10, max(1, float(response.headers.get("Retry-After", 2**attempt))))
                except ValueError:
                    delay = 2**attempt
                self.sleep(delay)
                continue
            if method == "POST" and response.status_code >= 500:
                raise UncertainPost(
                    "GitHub returned a server error after a write; reconcile before retrying."
                )
            if response.status_code == 401:
                message = "GitHub authentication failed. Generate a fresh App installation token."
            elif response.status_code == 403:
                message = "GitHub denied access or rate-limited the request. Check App installation permissions and retry later."
            else:
                message = f"GitHub rejected the request (HTTP {response.status_code})."
            line_error = response.status_code == 422 and any(
                word in response.text.lower() for word in ("line", "diff", "position")
            )
            raise GitHubError(response.status_code, message, line_error=line_error)
        raise GitHubError(0, "GitHub request exhausted retries.")

    def pages(self, path):
        page = 1
        while True:
            batch = self.request("GET", path, params={"per_page": 100, "page": page})
            if not isinstance(batch, list):
                raise GitHubError(0, "Unexpected paginated GitHub response.")
            yield from batch
            if len(batch) < 100:
                return
            page += 1

    @staticmethod
    def validate_repo(repo):
        if not REPO_RE.fullmatch(repo):
            raise ValueError("Repository must be OWNER/REPO.")

    def pull_request(self, repo, number):
        self.validate_repo(repo)
        return self.request("GET", f"/repos/{repo}/pulls/{number}")

    def permission(self, repo, user):
        data = self.request("GET", f"/repos/{repo}/collaborators/{quote(user, safe='')}/permission")
        return data.get("permission", "none")

    def compare(self, repo, base, head):
        return self.request("GET", f"/repos/{repo}/compare/{base}...{head}", params={"per_page": 1})

    def trusted_ref(self, repo):
        metadata = self.request("GET", f"/repos/{repo}")
        commit = self.request(
            "GET", f"/repos/{repo}/commits/{quote(metadata['default_branch'], safe='')}"
        )
        return commit["sha"]

    def text_file(self, repo, path, ref):
        import base64

        data = self.request(
            "GET",
            f"/repos/{repo}/contents/{quote(path, safe='/')}",
            params={"ref": ref},
            missing_ok=True,
        )
        if not data:
            return None
        if (
            not isinstance(data, dict)
            or data.get("type") != "file"
            or data.get("size", 0) > 32_000
            or data.get("encoding") != "base64"
        ):
            raise GitHubError(0, "Trusted configuration is not a small, regular text file.")
        try:
            return base64.b64decode(data["content"]).decode("utf-8")
        except (ValueError, UnicodeDecodeError) as exc:
            raise GitHubError(0, "Trusted configuration is not UTF-8 text.") from exc

    def app_identity(self, repo):
        """Fail before Bedrock if publishing was given a PAT or native Actions token."""
        if not self.app_slug or not re.fullmatch(r"[a-zA-Z0-9-]+", self.app_slug):
            raise GitHubError(0, "Posting requires GITHUB_APP_SLUG from the App token action.")
        # This endpoint requires an installation token, unlike /user. Getting a
        # repository installation by name would instead require an App JWT.
        page = 1
        while True:
            data = self.request(
                "GET", "/installation/repositories", params={"per_page": 100, "page": page}
            )
            repos = data.get("repositories", [])
            if any(item.get("full_name", "").lower() == repo.lower() for item in repos):
                return f"{self.app_slug}[bot]"
            if len(repos) < 100:
                raise GitHubError(0, "The App token does not grant access to this repository.")
            page += 1

    def find_review(self, repo, pr, marker, bot_login, any_run=False):
        for review in self.pages(f"/repos/{repo}/pulls/{pr}/reviews"):
            body = review.get("body") or ""
            matches = (
                bool(
                    re.search(
                        re.escape(marker.removesuffix(" -->")) + r"(?::[a-zA-Z0-9_-]+)? -->", body
                    )
                )
                if any_run
                else marker in body
            )
            if (
                review.get("user", {}).get("login") == bot_login
                and review.get("state") != "PENDING"
                and matches
            ):
                return review
        return None

    def post_review(self, repo, pr, head_sha, body, comments):
        return self.request(
            "POST",
            f"/repos/{repo}/pulls/{pr}/reviews",
            json={"commit_id": head_sha, "event": "COMMENT", "body": body, "comments": comments},
        )

    def reply(self, repo, pr, body):
        return self.request("POST", f"/repos/{repo}/issues/{pr}/comments", json={"body": body})

    def react(self, repo, comment_id, content):
        return self.request(
            "POST",
            f"/repos/{repo}/issues/comments/{comment_id}/reactions",
            json={"content": content},
        )

    def remove_reaction(self, repo, comment_id, reaction_id):
        return self.request(
            "DELETE", f"/repos/{repo}/issues/comments/{comment_id}/reactions/{reaction_id}"
        )

    def start_check(self, repo, head_sha, run_id):
        return self.request(
            "POST",
            f"/repos/{repo}/check-runs",
            json={
                "name": "bandwidth-infra-reviewer",
                "head_sha": head_sha,
                "status": "in_progress",
                "external_id": run_id,
                "output": {
                    "title": "Reviewing infrastructure consumption",
                    "summary": "Collecting pinned code context and checking nine cost-driver categories.",
                },
            },
        )["id"]

    def finish_check(self, repo, check_id, conclusion, title, summary):
        return self.request(
            "PATCH",
            f"/repos/{repo}/check-runs/{check_id}",
            json={
                "status": "completed",
                "conclusion": conclusion,
                "output": {"title": title, "summary": summary[:60_000]},
            },
        )
