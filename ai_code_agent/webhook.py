try:
    import uvicorn
    from fastapi import FastAPI, Request
except ImportError:  # pragma: no cover - optional dependency
    uvicorn = None
    FastAPI = None
    Request = None

if FastAPI is not None:
    app = FastAPI(title="AI Code Agent Webhook Server")

    @app.post("/github/webhook")
    async def github_webhook(request: Request):
        """Handle incoming GitHub events."""
        payload = await request.json()
        return {"status": "received", "source": "github", "event_keys": sorted(payload.keys())}


    @app.post("/ado/webhook")
    async def ado_webhook(request: Request):
        """Handle incoming Azure DevOps service hook events."""
        payload = await request.json()
        return {"status": "received", "source": "ado", "event_keys": sorted(payload.keys())}
else:
    app = None


def start_server(host="0.0.0.0", port=8000):
    if uvicorn is None or app is None:
        raise RuntimeError("fastapi and uvicorn must be installed to start the webhook server")
    uvicorn.run("ai_code_agent.webhook:app", host=host, port=port, reload=True)


if __name__ == "__main__":
    start_server()
