"""One-time activation of the movie sources requested for this release."""

MARKER = "_movie_provider_defaults_20261019_1"


def apply():
    from streamhub.utils import getSetting, setSetting, log

    try:
        if getSetting(MARKER) == "true":
            return False
        # Kodi can retain an older saved false even after its XML default changes.
        # Only this first run enables them; subsequent user choices are preserved.
        for key in ("movie2k", "kinox"):
            if getSetting(key) != "true":
                setSetting(key, "true")
            if getSetting(key) != "true":
                return False
        setSetting(MARKER, "true")
        return getSetting(MARKER) == "true"
    except Exception as exc:
        log("Movie provider defaults could not be applied: %s" % exc)
        return False
