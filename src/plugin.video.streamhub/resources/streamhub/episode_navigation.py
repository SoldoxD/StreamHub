"""Choose only episodes actually listed by a provider; legacy counts still work."""
import json


def next_episode_number(current, total=0, available=None):
    try:
        current, total = int(current), int(total or 0)
    except (TypeError, ValueError):
        return None
    if current <= 0:
        return None
    if available is not None:
        if isinstance(available, str):
            if len(available) > 65536:
                return None
            try:
                available = json.loads(available)
            except (TypeError, ValueError):
                return None
        if not isinstance(available, list):
            return None
        numbers = []
        for value in available[:10000]:
            if isinstance(value, bool):
                continue
            if isinstance(value, int) or (isinstance(value, str) and value.isdigit()):
                number = int(value)
                if number > current:
                    numbers.append(number)
        return min(numbers) if numbers else None
    candidate = current + 1
    return candidate if not total or candidate <= total else None
