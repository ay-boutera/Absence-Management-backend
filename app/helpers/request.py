from fastapi import Request


def get_client_ip(request: Request) -> str:
    """Helper to extract the client's IP from headers if behind a proxy, else request.client."""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"
