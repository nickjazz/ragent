"""T7.5d — API process entrypoint (B30).

Kept as a backward-compat shim for ``python -m ragent.api``.
Primary path: ``uvicorn ragent.bootstrap.app:create_app --factory``
"""

from __future__ import annotations

import uvicorn

from ragent.utility.env import int_env, str_env

if __name__ == "__main__":  # pragma: no cover
    host = str_env("RAGENT_HOST", "127.0.0.1")
    port = int_env("RAGENT_PORT", 8000)
    log_level = str_env("LOG_LEVEL", "INFO").lower()

    uvicorn.run(
        "ragent.bootstrap.app:create_app",
        factory=True,
        host=host,
        port=port,
        log_level=log_level,
    )
