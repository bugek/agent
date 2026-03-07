import json
from urllib import request

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

    def _request(self, method: str, url: str, payload: dict | None = None) -> dict:
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        req = request.Request(url, data=body, headers=self._headers(), method=method)
        with request.urlopen(req) as response:
            return json.loads(response.read().decode("utf-8"))
        
    def get_issue(self, repo: str, issue_number: int) -> dict:
        """Fetch issue details including description and comments."""
        return self._request("GET", f"https://api.github.com/repos/{repo}/issues/{issue_number}")
        
    def create_pull_request(self, repo: str, branch: str, title: str, body: str) -> str:
        """Create a PR and return its URL."""
        data = self._request(
            "POST",
            f"https://api.github.com/repos/{repo}/pulls",
            {"title": title, "body": body, "head": branch, "base": "main"},
        )
        return data.get("html_url", "")
        
    def post_comment(self, repo: str, issue_or_pr_number: int, comment: str):
        """Post a status update or question to the thread."""
        return self._request(
            "POST",
            f"https://api.github.com/repos/{repo}/issues/{issue_or_pr_number}/comments",
            {"body": comment},
        )
