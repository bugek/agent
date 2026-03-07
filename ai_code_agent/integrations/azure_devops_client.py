import base64
import json
from urllib import parse, request

class AzureDevOpsClient:
    """Wrapper for ADO to interact with Work Items and PRs."""
    
    def __init__(self, pat: str, org_url: str):
        self.pat = pat
        self.org_url = org_url

    def _headers(self) -> dict[str, str]:
        token = base64.b64encode(f":{self.pat}".encode("utf-8")).decode("utf-8")
        return {
            "Authorization": f"Basic {token}",
            "Content-Type": "application/json",
        }

    def _request(self, method: str, url: str, payload: dict | None = None) -> dict:
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        req = request.Request(url, data=body, headers=self._headers(), method=method)
        with request.urlopen(req) as response:
            return json.loads(response.read().decode("utf-8"))
        
    def get_work_item(self, project: str, temp_id: int) -> dict:
        """Fetch ADO work item details."""
        base_url = f"{self.org_url}/{project}/_apis/wit/workitems/{temp_id}?api-version=7.1"
        return self._request("GET", base_url)
        
    def create_pull_request(self, project: str, repo: str, source_ref: str, target_ref: str, title: str, description: str) -> str:
        """Create a PR in Azure Repos."""
        base_url = f"{self.org_url}/{project}/_apis/git/repositories/{repo}/pullrequests?api-version=7.1"
        data = self._request(
            "POST",
            base_url,
            {
                "sourceRefName": source_ref,
                "targetRefName": target_ref,
                "title": title,
                "description": description,
            },
        )
        return data.get("url", "")
        
    def post_discussion_comment(self, project: str, repo: str, pr_id: int, comment: str):
        """Add a comment to an active PR thread."""
        base_url = (
            f"{self.org_url}/{project}/_apis/git/repositories/{repo}/pullRequests/{pr_id}/threads?api-version=7.1"
        )
        return self._request(
            "POST",
            base_url,
            {"comments": [{"content": comment, "commentType": 1}], "status": 1},
        )
