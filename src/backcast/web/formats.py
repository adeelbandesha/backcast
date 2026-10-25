"""How numbers are written on the pages: percentages, changes against naive and p-values."""

import math


def percent(value, digits=0) -> str:
    return "—" if math.isnan(value) else f"{value:.{digits}%}"


def signed_percent(value, digits=1) -> str:
    if round(value, digits + 2) == 0:
        return f"{0:.{digits}%}"
    return f"{value:+.{digits}%}".replace("-", "−")


def versus(key, relative) -> dict:
    """A model's score relative to naive as a signed change, coloured by whether it is better or worse."""
    if key == "naive" or math.isnan(relative):
        return {"text": "—", "tone": ""}
    return {"text": signed_percent(relative - 1), "tone": tone(relative - 1)}


def tone(change) -> str:
    # Judged at the precision shown, so "0.0%" is never coloured as a win or a loss.
    change = round(change, 3)
    return "better" if change < 0 else "worse" if change > 0 else ""


def p_value(value) -> str:
    if math.isnan(value):
        return "—"
    return "< 0.001" if value < 0.001 else f"{value:.3f}"
