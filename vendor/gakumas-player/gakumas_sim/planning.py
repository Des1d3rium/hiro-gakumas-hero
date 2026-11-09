"""Cheap forks of OWNED public simulation worlds with read-only rule sharing.

Engine operations read document/definitions/drink_definitions/public_definitions;
version changes update cards/support state, not those tables. Reset rebinds the
public projection. New rule-rewriting operations
must copy on write. Main Battle.step keeps its ordinary atomic deepcopy.
"""
from copy import deepcopy
from .core import Unsupported


def fork_simulation(model):
    if not (model.known_only or model.document.get('id')=='public-belief-sample'):
        raise Unsupported('Rule-sharing forks require an owned public simulation world')
    memo={id(value):value for value in [model.document,model.definitions,model.drink_definitions,model.public_definitions]}
    return deepcopy(model,memo)
