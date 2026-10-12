"""Keep host alternatives diverse within the existing source preference order."""


def select_diverse(candidates, max_total=16, max_per_family=2):
    """Select ranked tuples whose second field identifies their host family.

    Take one candidate from each family before taking a second. Preserve the
    original tuple objects and the input order both between and within families.
    """
    if max_total <= 0 or max_per_family <= 0:
        return []
    families = {}
    for candidate in candidates:
        bucket = families.setdefault(candidate[1], [])
        if len(bucket) < max_per_family:
            bucket.append(candidate)
    selected = []
    rounds = max((len(bucket) for bucket in families.values()), default=0)
    for index in range(rounds):
        for bucket in families.values():
            if index < len(bucket):
                selected.append(bucket[index])
                if len(selected) >= max_total:
                    return selected
    return selected
