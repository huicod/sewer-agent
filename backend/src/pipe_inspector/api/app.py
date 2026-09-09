"""Application factory: no GPU or remote connection during import."""

from fastapi import FastAPI


def create_app() -> FastAPI:
    app = FastAPI(title="Sewer Agent", version="0.1.0")

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"service": "sewer-agent", "status": "alive", "version": "0.1.0"}

    return app
