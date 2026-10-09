"""Bearer API-key authentication and permission checks (ADR-0002).

An unreachable database makes authentication fail with 503, never succeed.
"""

from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request

from aether.core.models import Contributor, Permission
from aether.storage.contributors import Neo4jContributorStore

CHALLENGE = {"WWW-Authenticate": "Bearer"}


def current_contributor(
    request: Request, authorization: Annotated[str | None, Header()] = None
) -> Contributor:
    scheme, _, key = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not key.strip():
        raise HTTPException(401, "Missing API key", headers=CHALLENGE)
    state = request.app.state
    contributor = Neo4jContributorStore(
        state.driver, state.config.neo4j_database
    ).authenticate(key.strip())
    if contributor is None:
        raise HTTPException(401, "Invalid API key", headers=CHALLENGE)
    return contributor


def require(permission: Permission) -> Callable[[Contributor], Contributor]:
    def check(
        contributor: Annotated[Contributor, Depends(current_contributor)],
    ) -> Contributor:
        if permission not in contributor.permissions:
            raise HTTPException(403, f"Requires the {permission} permission")
        return contributor

    return check
