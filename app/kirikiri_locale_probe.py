"""Pure policy for locale launch comparisons; this module never starts a game.

A monitor must provide initialization outcomes and structured error evidence. A
visible window or a surviving process does not establish initialization success.
"""

_LOCALE_ERROR_CODES = frozenset({
    "encoding_error", "invalid_multibyte", "invalid_multibyte_sequence",
    "path_encoding_error", "filepath_conversion_error", "filename_conversion_error",
    "unicode_decode_error", "unicode_encode_error",
    # Windows ERROR_NO_UNICODE_TRANSLATION, surfaced as a structured Win32 code.
    1113,
})


def classify_boot_error(error):
    """Classify structured evidence; generic exceptions/messages stay unknown.

    Accepted form: {"code": "invalid_multibyte"} or {"win32_code": 1113}.
    Textual search is intentionally avoided: a script may merely quote an error.
    """
    if not isinstance(error, dict):
        return {"category": "unknown", "locale_candidate": False}
    code = error.get("code")
    if isinstance(code, str):
        code = code.strip().lower()
    if ((isinstance(code, (str, int)) and code in _LOCALE_ERROR_CODES)
            or error.get("win32_code") == 1113):
        return {"category": "locale_candidate", "locale_candidate": True}
    return {"category": "unknown", "locale_candidate": False}


def compare_boot_results(native, japanese=None):
    """Classify evidence and say whether a Japanese retry is justified.

    Example: native={"code_page":949,"outcome":"initialization_failed",
    "error":{"code":"invalid_multibyte"}}; japanese={"code_page":932,
    "outcome":"initialization_ready"}. A retry is justified only for this kind
    of candidate error and required only if the retry reaches initialization.
    """
    native = native if isinstance(native, dict) else {}
    japanese = japanese if isinstance(japanese, dict) else {}
    unknown = {"status": "unknown", "locale": None, "should_retry_japanese": False}
    # New probe reports retain actual code_page=None when target ACP was not
    # measured. Mode, PID-bound evidence and configured runtime are evaluated
    # separately from that unknown value. Legacy recorded comparisons keep the
    # original strict 949/932 interpretation.
    observed = native.get("mode") == "native"
    if not observed and native.get("code_page") != 949:
        return unknown
    outcome = native.get("outcome")
    if outcome == "initialization_ready":
        if observed and not native.get("readiness_evidence"):
            return unknown
        return {"status": "not_required", "locale": None, "should_retry_japanese": False}
    if outcome != "initialization_failed" or not classify_boot_error(native.get("error"))["locale_candidate"]:
        return unknown
    if observed:
        error = native.get("error") or {}
        if (not native.get("pid") or error.get("source_pid") != native.get("pid")
                or error.get("source") not in {"owned_dialog", "stderr.log", "stdout.log"}
                or not error.get("proof")):
            return unknown
    if not japanese:
        return {"status": "unknown", "locale": None, "should_retry_japanese": True}
    if observed:
        ready = (japanese.get("mode") == "japanese" and japanese.get("pid")
                 and japanese.get("configured_code_page") == 932
                 and japanese.get("outcome") == "initialization_ready"
                 and japanese.get("readiness_evidence"))
    else:
        ready = japanese.get("code_page") == 932 and japanese.get("outcome") == "initialization_ready"
    if ready:
        return {"status": "required", "locale": "ja-JP", "should_retry_japanese": False}
    return unknown
