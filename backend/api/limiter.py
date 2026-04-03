from slowapi import Limiter
from slowapi.util import get_remote_address
from fastapi import Request


def _rate_limit_key(request: Request) -> str:
    """
    Use authenticated user ID when available (higher trust, fair per-user limits).
    Fall back to IP address for anonymous requests.
    """
    user = getattr(request.state, "user", None)
    if user and isinstance(user, dict):
        sub = user.get("sub")
        if sub:
            return f"user:{sub}"
    return get_remote_address(request)


limiter = Limiter(key_func=_rate_limit_key)
