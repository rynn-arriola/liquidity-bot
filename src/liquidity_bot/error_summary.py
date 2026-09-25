"""Bounded diagnostic text, not error classification or execution authority."""
import re


def diagnostic_id(value):
    if value is None:
        return None
    text = str(value)
    return text if re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", text) else None


def error_summary(value):
    text = str(value)
    if re.search(r"<!doctype|<html\b|<head\b|<body\b", text, re.IGNORECASE):
        return "[HTML response omitted]"
    if re.search(r"x-pm-(?:access-key|signature)|authorization\s*:|secret[_ -]?key", text, re.IGNORECASE):
        return "[sensitive diagnostic text omitted]"
    text = " ".join(text.split())
    return text if len(text) <= 512 else text[:509] + "..."
