"""Temporary bootstrap compatibility alias for the current #590 inventory render."""

import test_python_inventory_current as target

_ORIGINAL_GROUPS = target._groups


def _fixed_groups(helper):
    groups = dict(helper.DOMAIN_GROUPS)
    groups["runners"] = groups.pop("runner")

    class ReviewedHelper:
        DOMAIN_GROUPS = groups

    return _ORIGINAL_GROUPS(ReviewedHelper)


target._groups = _fixed_groups
