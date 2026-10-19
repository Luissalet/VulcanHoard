"""Persist useful model errors instead of returning an opaque HTTP status."""

import json


def check_response(response, evidence):
    if response.is_success:
        return
    response.read()
    try:
        detail = response.json().get("error", response.text)
    except ValueError:
        detail = response.text
    (evidence / "error.json").write_text(
        json.dumps(
            {"status": response.status_code, "detail": detail}, ensure_ascii=False, indent=2
        ),
        "utf-8",
    )
    raise RuntimeError(
        f"El modelo rechazó la petición ({response.status_code}): {str(detail)[:450]}"
    )
