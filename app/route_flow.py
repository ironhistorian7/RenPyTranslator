"""Bounded, side-effect-free route traversal. Never execute game Python.

Conditional consequences (URW) and virtual state (UCD) are represented using
the tool's existing events, without importing either mod's runtime hooks/eval.
"""
import ast
from collections import defaultdict
from collections import Counter
from copy import deepcopy

FLOW_VERSION = 2


def substitute(code, state):
    from story_guides import expr
    if not state:
        return code
    node = expr(code)
    if node is None:
        return code

    class Replace(ast.NodeTransformer):
        def visit(self, node):
            if isinstance(node, (ast.Name, ast.Attribute, ast.Subscript)):
                replacement = state.get(ast.unparse(node))
                if replacement is not None:
                    return deepcopy(replacement)
            return super().visit(node)

    return ast.unparse(ast.fix_missing_locations(Replace().visit(node)))


def assign(event, state):
    from story_guides import expr
    value = expr(substitute(event['expression'], state))
    for name in event['variables']:
        result = value
        if event['operator'] != 'Set':
            operation = {'Add': ast.Add, 'Sub': ast.Sub, 'Mult': ast.Mult}.get(event['operator'])
            left = expr(substitute(name, state))
            result = ast.BinOp(left=left, op=operation(), right=value) if operation and value else None
        for key in list(state):
            if key.startswith(name + '.') or key.startswith(name + '['):del state[key]
        # Unknown function results must invalidate an earlier known value.
        if result is None or any(isinstance(n, ast.Call) for n in ast.walk(result)):
            state[name] = ast.Name(id='_rpt_unknown_' + str(event['line']), ctx=ast.Load())
        elif len(ast.dump(result)) < 6000:
            state[name] = result
        else:
            state[name] = ast.Name(id='_rpt_unknown_' + str(event['line']), ctx=ast.Load())


def branches(events, diagnostics=None, roots=None):
    from story_guides import expr
    from story_hints import bound_value
    labels = defaultdict(list)
    checks = defaultdict(list)
    groups = defaultdict(list)
    choices = roots if roots is not None else [e for e in events if e['kind'] == 'choice']
    for event in events:
        if event['label'] and event['kind'] != 'label':
            labels[event['label']].append(event)
        if event['kind'] == 'condition':
            for variable in event['variables']:
                checks[variable].append(event)
    paths = {}; limited = set()
    for choice in choices:
        cid = choice['id']; found = []; budget = [1200]
        groups[(choice['file'], choice['menu_line'])].append(choice)

        def stop(reason, event=None):
            if diagnostics is None:
                return
            row = {'reason': reason}
            if event:
                row.update({key: event.get(key) for key in ('file', 'line', 'label', 'target') if key in event})
            records = diagnostics.setdefault(cid, [])
            if row not in records:
                records.append(row)

        def walk(seq, guards, state, trail, depth=0):
            if depth > 24 or budget[0] <= 0:
                stop('depth_limit' if depth > 24 else 'event_budget')
                limited.add(cid); return []
            i = 0
            while i < len(seq):
                event = seq[i]; kind = event['kind']; budget[0] -= 1
                if budget[0] < 0:
                    stop('event_budget', event); limited.add(cid); return []
                if kind in ('menu', 'choice', 'input', 'screen_call', 'unsupported_flow'):
                    if kind == 'screen_call':
                        found.append(dict(event, guards=guards[:]))
                    stop('next_' + kind, event); return []
                if kind == 'condition':
                    cases = []; j = i
                    while j < len(seq) and seq[j]['kind'] == 'condition' and seq[j]['group'] == event['group']:
                        condition = seq[j]; end = j + 1
                        while end < len(seq) and seq[end]['indent'] > condition['indent']:
                            end += 1
                        cases.append((condition, seq[j + 1:end])); j = end
                    outcomes = []
                    predicates = []
                    for condition, body in cases:
                        predicate = substitute(condition['condition'], state)
                        predicates.append(predicate)
                        verdict = bound_value(expr(predicate), {})
                        if verdict is not None and not verdict:
                            continue
                        path = guards if verdict is not None else guards + [predicate]
                        outcomes.extend(walk(body + seq[j:], path, dict(state), trail, depth + 1))
                    if not any(c['code'] == 'else:' for c, _ in cases):
                        absent = ' and '.join('not (' + p + ')' for p in predicates)
                        verdict = bound_value(expr(absent), {})
                        if verdict is None or verdict:
                            path = guards if verdict is not None else guards + [absent]
                            outcomes.extend(walk(seq[j:], path, dict(state), trail, depth + 1))
                    return outcomes
                found.append(dict(event, guards=list(dict.fromkeys(guards))))
                if kind == 'assignment':
                    assign(event, state)
                if kind in ('jump', 'call'):
                    if not event['static']:
                        stop('dynamic_target', event); return []
                    target = event['target']
                    if target in trail:
                        stop('cycle', event); return []
                    if target not in labels:
                        stop('missing_label', event); return []
                    outcomes = walk(labels[target], guards, dict(state), trail + (target,), depth + 1)
                    if kind == 'jump':
                        return outcomes
                    resumed = []
                    for outcome, path, values in outcomes:
                        if outcome == 'return':
                            resumed.extend(walk(seq[i + 1:], path, values, trail, depth + 1))
                    return resumed
                if kind == 'return':
                    stop('return', event); return [('return', guards, state)]
                i += 1
            stop('end_of_block', seq[-1] if seq else None)
            return [('end', guards, state)]

        body = [e for e in labels[choice['label']] if cid in e['choices']]
        # Continue past the enclosing menu, keeping later conditions sensitive to
        # this choice's writes. Sibling choice bodies are never concatenated.
        enclosing = next((e for e in labels[choice['label']] if e['kind'] == 'menu'
                          and e['line'] == choice['menu_line']), None)
        if enclosing:
            seq = labels[choice['label']]; start = seq.index(enclosing) + 1
            while start < len(seq) and seq[start]['indent'] > enclosing['indent']:
                start += 1
            body += seq[start:]
        walk(body, choice['guards'][:], {}, (choice['label'],))
        # Preserve the existing flag-gated future-scene candidates. Their outer
        # conditions remain attached; these are not current-state guarantees.
        followed = set()
        for position,assignment in enumerate(list(found)):
            if assignment['kind'] != 'assignment' or assignment['operator'] != 'Set' or len(assignment['variables']) != 1:
                continue
            if type(assignment['value']) not in (bool, int):
                continue
            variable = assignment['variables'][0]
            if any(e['kind']=='assignment' and variable in e['variables'] and e['guards']==assignment['guards']
                   for e in found[position+1:]):
                continue
            for check in checks[variable]:
                if bound_value(expr(check['condition']), {variable: assignment['value']}) is not True:
                    continue
                for edge in check['branches']:
                    if (not edge['static'] or edge['target'] in followed or edge['branch_ids'][-1] != check['id']
                            or edge['target'] in {e.get('target') for e in found if e['kind'] in ('jump', 'call')}):
                        continue
                    followed.add(edge['target'])
                    if edge['target'] not in labels:
                        stop('missing_label', edge); continue
                    guards = list(dict.fromkeys(assignment['guards'] + [g for g in edge['guards'] if g != check['condition']]))
                    walk(labels[edge['target']], guards, {}, (choice['label'], edge['target']))
        paths[cid] = found
    result = []
    for choice in choices:
        peers = groups[(choice['file'], choice['menu_line'])]
        # A line under different conditions is not a common unconditional result.
        # Repeated calls represent repeated effects and must not be de-duplicated.
        key=lambda e:(e['file'],e['line'],tuple(e['guards']))
        own=Counter(key(e) for e in paths[choice['id']])
        siblings=[Counter(key(e) for e in paths[c['id']]) for c in peers if c['id']!=choice['id']]
        unique=[e for e in paths[choice['id']] if not any(counts[key(e)]==own[key(e)] for counts in siblings)]
        result.append((choice, unique, paths[choice['id']], choice['id'] in limited))
    return result
