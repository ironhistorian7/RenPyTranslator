"""Explicit actor-class repairs and advisory static reference diagnostics.

No class is selected automatically. The patch checker still owns comparison of
all other database values against the original snapshot.
"""
from copy import deepcopy
import math


def _finite_number(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def _class_index(value):
    # JavaScript array access also accepts 1.0 and the canonical string "1".
    # A populated index zero is usable too, even though standard databases use
    # null there. Do not call such an existing source class a repair candidate.
    if type(value) is int:
        return value if value >= 0 else None
    if type(value) is float and _finite_number(value) and value >= 0 and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.isascii() and value.isdecimal() and str(int(value)) == value:
        return int(value)
    return None


def class_problem(classes, class_id):
    """Return the standard MV/MZ actor-initialization defect, if one is known."""
    index = _class_index(class_id)
    if (index is None or not isinstance(classes, list)
            or index >= len(classes) or not isinstance(classes[index], dict)):
        return 'missing_class'
    params = classes[index].get('expParams')
    if (not isinstance(params, list) or len(params) < 4
            or any(not _finite_number(value) for value in params[:4])):
        return 'invalid_exp_params'
    return None


def apply_overrides(document, cfg, classes):
    """Apply only declared repairs of invalid source class references.

    Returns the original object for a no-op; otherwise copies before changing
    only classId. The same target is idempotent, but another valid source class
    cannot be replaced through this repair setting.
    """
    overrides = cfg.get('rpg_actor_class_overrides')
    if overrides is None or overrides == {}:
        return document, []
    if not isinstance(overrides, dict) or not isinstance(document, list):
        raise ValueError('RPG actor-class overrides require an actor array and an ID mapping')
    changes = []
    for key, target in overrides.items():
        if not isinstance(key, str) or not key.isascii() or not key.isdecimal() or str(int(key)) != key:
            raise ValueError('RPG actor-class override actor IDs must be positive integer strings')
        actor_id = int(key)
        if actor_id <= 0 or actor_id >= len(document) or not isinstance(document[actor_id], dict):
            raise ValueError('RPG actor-class override actor is unavailable: '+key)
        if type(target) is not int or target <= 0 or class_problem(classes, target):
            raise ValueError('RPG actor-class override target class is invalid for actor '+key)
        current = document[actor_id].get('classId')
        if _class_index(current) == target and class_problem(classes, current) is None:
            continue
        reason = class_problem(classes, current)
        if reason is None:
            raise ValueError('RPG actor-class override cannot replace a valid source class: '+key)
        changes.append(dict(actor_id=actor_id, before_class_id=current,
                            after_class_id=target, reason=reason))
    if not changes:
        return document, []
    patched = deepcopy(document)
    for change in changes:
        patched[change['actor_id']]['classId'] = change['after_class_id']
    return patched, changes


def reference_diagnostics(actors, classes, original_actors, original_classes, system, *, actors_file):
    """Describe invalid references without failing unchanged original/dummy rows."""
    if not isinstance(actors, list):
        return {}, 0
    party = system.get('partyMembers', []) if isinstance(system, dict) else []
    party = party if isinstance(party, list) else []
    problems = {}
    count = 0
    for actor_id, actor in enumerate(actors):
        if actor_id == 0 or not isinstance(actor, dict):
            continue
        count += 1
        class_id = actor.get('classId')
        reason = class_problem(classes, class_id)
        if reason is None:
            continue
        original = (original_actors[actor_id] if isinstance(original_actors, list)
                    and actor_id < len(original_actors) else None)
        same = (isinstance(original, dict) and original.get('classId') == class_id
                and class_problem(original_classes, class_id) == reason)
        problems[actor_id] = dict(
            file=actors_file, pointer=f'[{actor_id}].classId',
            error_type='RPGActorClassInvalid', severity='warning',
            attribution='original' if same else 'ours', status='invalid_class_reference',
            reason=reason, actor_id=actor_id, class_id=class_id,
            initial_party=actor_id in party, static_event_references=0,
            static_reference_samples=[],
            error='Actor initialization requires an existing class with numeric experience parameters')
    return problems, count


def observe_actor_reference(command, name, pointer, problems):
    """Count literal actor references during the checker's existing JSON walk.

    These are possible calls, not proof that an event page executes. Variable,
    script and plugin references are deliberately not resolved here.
    """
    code, params = command.get('code'), command.get('parameters')
    if not problems or not isinstance(params, list):
        return
    actor_id = None
    if code in (129, 303, 319, 320, 321, 322, 324, 325) and params:
        actor_id = params[0]
    elif code == 111 and len(params) > 1 and params[0] == 4:
        actor_id = params[1]
    elif code in (311, 312, 313, 314, 315, 316, 317, 318, 326) and len(params) > 1 and params[0] == 0:
        actor_id = params[1]
    if type(actor_id) is not int or actor_id not in problems:
        return
    problem = problems[actor_id]
    problem['static_event_references'] += 1
    if len(problem['static_reference_samples']) < 3:
        problem['static_reference_samples'].append(dict(file=name, pointer=pointer, code=code))
