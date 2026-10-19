"""Conservative language checks for provider hints and Kodi audio tracks.

A provider language is only a hint; the player must verify actual audio streams.
Nothing here inspects a movie title, URL, country, or subtitle track.
"""
import re
import unicodedata


_ALIASES = {
    "de": ("deu", "ger", "german", "deutsch"),
    "en": ("eng", "english", "englisch"),
    "fr": ("fra", "fre", "french", "francais", "franzosisch"),
    "it": ("ita", "italian", "italiano", "italienisch"),
    "es": ("spa", "spanish", "espanol", "spanisch"),
    "tr": ("tur", "turkish", "turkce", "turkisch"),
    "pt": ("por", "portuguese", "portugues", "portugiesisch"),
    "nl": ("nld", "dut", "dutch", "nederlands", "niederlandisch"),
    "pl": ("pol", "polish", "polski", "polnisch"),
    "ru": ("rus", "russian", "russisch"),
    "uk": ("ukr", "ukrainian", "ukrainisch"),
    "ja": ("jpn", "japanese", "japanisch"),
    "ko": ("kor", "korean", "koreanisch"),
    "zh": ("zho", "chi", "chinese", "chinesisch"),
    "ar": ("ara", "arabic", "arabisch"),
    "hi": ("hin", "hindi"),
    "el": ("ell", "gre", "greek", "griechisch"),
    "sv": ("swe", "swedish", "schwedisch"),
    "da": ("dan", "danish", "danisch"),
    "fi": ("fin", "finnish", "finnisch"),
    "no": ("nor", "norwegian", "norwegisch"),
    "cs": ("ces", "cze", "czech", "tschechisch"),
    "sk": ("slk", "slo", "slovak", "slowakisch"),
    "hu": ("hun", "hungarian", "ungarisch"),
    "ro": ("ron", "rum", "romanian", "rumanisch"),
    "bg": ("bul", "bulgarian", "bulgarisch"),
    "he": ("heb", "hebrew", "hebraisch"),
    "th": ("tha", "thai"),
    "vi": ("vie", "vietnamese", "vietnamesisch"),
    "id": ("ind", "indonesian", "indonesisch"),
    "fa": ("fas", "per", "persian", "persisch"),
}
_LOOKUP = {alias: code for code, aliases in _ALIASES.items()
           for alias in (code,) + aliases}
_ANY = frozenset(("", "any", "all", "*"))
_SUBTITLE = re.compile(r"\b(?:sub|subs|subbed|subtitle|subtitles|untertitel|omu|gerdubsub)\b")
_SEPARATOR = re.compile(r"[,;/|&+]|\b(?:and|und|with|mit)\b")
_AUDIO_PREFIX = re.compile(r"^(?:audio|sprache|language)\s*[:=]\s*")
_AUDIO_SUFFIX = re.compile(r"\s+(?:audio|dub|dubbed|synchronisiert|synchro)$")
_TRACK_SUFFIX = re.compile(
    r"^(?:audio|dub|dubbed|aac|ac3|eac3|e-ac-3|dts|dts-hd|truehd|atmos|"
    r"mp3|mpeg|pcm|flac|opus|stereo|mono|surround|default|original|main|"
    r"commentary|kommentar|descriptive|description|[0-9]+(?:\.[0-9]+)?)$"
)


def _text(value):
    if not isinstance(value, str) or len(value) > 256:
        return ""
    value = unicodedata.normalize("NFKD", value.casefold().strip())
    return "".join(char for char in value if not unicodedata.combining(char))


def normalize_language(value):
    """Return a known ISO 639-1 code, or '' for unknown/any/subtitle labels."""
    value = _text(value)
    if value in _LOOKUP:
        return _LOOKUP[value]
    # Kodi may supply a regional/script tag such as de-DE or zh-Hant-TW.
    if ("-" in value or "_" in value) and re.fullmatch(
            r"[a-z]{2,3}(?:[-_][a-z]{4})?(?:[-_](?:[a-z]{2}|[0-9]{3}))?", value):
        base = re.split(r"[-_]", value, maxsplit=1)[0]
        # SUB is not a region, and must never turn DE-SUB into German audio.
        if not _SUBTITLE.search(value.replace("_", "-")):
            return _LOOKUP.get(base, "")
    return ""


def _audio_label_languages(value):
    value = _text(value)
    if not value:
        return frozenset()
    direct = normalize_language(value)
    if direct:
        return frozenset((direct,))
    found = set()
    for segment in _SEPARATOR.split(value):
        segment = segment.strip(" ()[]{}")
        if not segment or _SUBTITLE.search(segment):
            continue
        segment = _AUDIO_PREFIX.sub("", segment)
        segment = _AUDIO_SUFFIX.sub("", segment).strip()
        # Dual/Multi alone say nothing. Explicit DE/EN still supplies both.
        segment = re.sub(r"^(?:dual|multi)(?:[- ](?:audio|language))?\s*[:(]\s*", "", segment)
        code = normalize_language(segment.strip(" ()[]{}"))
        if code:
            found.add(code)
    return frozenset(found)


def provider_audio_languages(source):
    """Read explicit provider audio labels, never the host or film title.

    Supports a source dict, a language string, or a bounded list of strings.
    DE-SUB/GER SUB and unspecified OV/Dual/Multi deliberately give no proof
    of German audio. Mixed EN / DE-SUB supplies only English audio.
    """
    if isinstance(source, dict):
        source = source.get("lang") or source.get("language") or source.get("languages")
    if isinstance(source, (list, tuple)):
        found = set()
        for value in source[:16]:
            found.update(_audio_label_languages(value))
        return frozenset(found)
    return _audio_label_languages(source)


def provider_matches_language(source, wanted):
    """True for a confirmed requested language, or an explicit 'any' setting."""
    if wanted is None or (isinstance(wanted, str) and _text(wanted) in _ANY and len(wanted) <= 256):
        return True
    language = normalize_language(wanted)
    return bool(language and language in provider_audio_languages(source))


def _track_language(stream):
    code = normalize_language(stream.get("language"))
    if code:
        return code
    # A track name can explicitly label its language, but arbitrary prose
    # such as a film title containing 'German' is not language evidence.
    name = _text(stream.get("name"))
    if not name or _SUBTITLE.search(name):
        return ""
    direct = normalize_language(name)
    if direct:
        return direct
    parts = re.split(r"[\s(),\[\]:]+", name)
    code = normalize_language(parts[0])
    if code and all(_TRACK_SUFFIX.fullmatch(part) for part in parts[1:] if part):
        return code
    return ""


def _tracks(streams):
    if not isinstance(streams, (list, tuple)):
        return
    for stream in streams[:256]:
        if isinstance(stream, dict):
            yield stream


def audio_stream_languages(streams):
    """Return explicit languages reported by Kodi's audio stream metadata."""
    return frozenset(code for code in (_track_language(stream) for stream in _tracks(streams)) if code)


def matching_audio_stream(streams, wanted):
    """Return the first matching Kodi audio index, or None; 'any' stays put."""
    language = normalize_language(wanted)
    if not language:
        return None
    for stream in _tracks(streams):
        index = stream.get("index")
        if (isinstance(index, int) and not isinstance(index, bool) and 0 <= index < 256
                and _track_language(stream) == language):
            return index
    return None
