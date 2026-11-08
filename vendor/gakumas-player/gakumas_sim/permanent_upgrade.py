"""Shared ordinary/interval service eligibility, distinct from exam upgrades."""
from copy import deepcopy
from .core import Unsupported


def upgrade_target(catalog, spec):
    # ProduceCardData.get_IsUpgradable 34aec30..34aec64: nonzero raw
    # upgradeCount returns false; otherwise resolve the same ID at grade 1.
    if type(spec.get('upgrade')) is not int or spec['upgrade'] != 0:
        raise Unsupported('Permanent upgrade requires an unupgraded source; + to ++ is not a shop service')
    catalog.card(spec['card_id'], 1)  # Exact existing version, never a generated definition.
    return dict(deepcopy(spec), upgrade=1)
