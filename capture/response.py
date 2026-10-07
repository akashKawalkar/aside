# capture/response.py

def envelope(status: str, message: str, detail: str = "", data: dict | None = None) -> dict:
    assert status in ("ok", "ambiguous", "error")
    return {"status": status, "message": message, "detail": detail, "data": data or {}}