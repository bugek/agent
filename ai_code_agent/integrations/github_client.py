import json
from urllib import error, request
from urllib.parse import quote


class GitHubRequestError(RuntimeError):
    def __init__(self, status_code: int, message: str, *, payload: object | None = None, body: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload
        self.body = body or ""
        self.message = message

class GitHubClient:
    """Wrapper for PyGithub to interact with issues and PRs."""
    
    def __init__(self, token: str):
        self.token = token

    def _headers(self) -> dict[str, str]:
        return {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self.token}",
            "User-Agent": "ai-code-agent",
        }

    def _request(self, method: str, url: str, payload: dict | None = None) -> object:
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        req = request.Request(url, data=body, headers=self._headers(), method=method)
        try:
            with request.urlopen(req) as response:
                return json.loads(response.read().decode("utf-8"))
        except error.HTTPError as exc:
            error_body = exc.read().decode("utf-8", errors="replace")
            parsed_payload: object | None = None
            try:
                parsed_payload = json.loads(error_body)
            except json.JSONDecodeError:
                parsed_payload = None
            message = (
                parsed_payload.get("message")
                if isinstance(parsed_payload, dict) and isinstance(parsed_payload.get("message"), str)
                else error_body or str(exc)
            )
            raise GitHubRequestError(exc.code, message, payload=parsed_payload, body=error_body) from exc
        
    def get_issue(self, repo: str, issue_number: int) -> dict:
        """Fetch issue details including description and comments."""
        return self._request("GET", f"https://api.github.com/repos/{repo}/issues/{issue_number}")

    def list_issue_comments(self, repo: str, issue_number: int) -> list[dict]:
        """Fetch issue comments in ascending order."""
        data = self._request("GET", f"https://api.github.com/repos/{repo}/issues/{issue_number}/comments")
        if not isinstance(data, list):
            return []
        results: list[dict] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            user = item.get("user") if isinstance(item.get("user"), dict) else {}
            results.append({"author": user.get("login") or "unknown", "body": item.get("body") or ""})
        return results
        
    def create_pull_request(self, repo: str, branch: str, title: str, body: str, base_branch: str = "main") -> str:
        """Create a PR and return its URL."""
        data = self._request(
            "POST",
            f"https://api.github.com/repos/{repo}/pulls",
            {"title": title, "body": body, "head": branch, "base": base_branch},
        )
        return data.get("html_url", "") if isinstance(data, dict) else ""

    def find_open_pull_request(self, repo: str, branch: str, base_branch: str | None = None) -> dict | None:
        owner = repo.split("/", 1)[0] if "/" in repo else repo
        url = f"https://api.github.com/repos/{repo}/pulls?state=open&head={quote(f'{owner}:{branch}', safe=':')}"
        if base_branch:
            url += f"&base={quote(base_branch, safe='')}"
        data = self._request("GET", url)
        if not isinstance(data, list):
            return None
        for item in data:
            if isinstance(item, dict):
                return item
        return None
        
    def post_comment(self, repo: str, issue_or_pr_number: int, comment: str):
        """Post a status update or question to the thread."""
        return self._request(
            "POST",
            f"https://api.github.com/repos/{repo}/issues/{issue_or_pr_number}/comments",
            {"body": comment},
        )
