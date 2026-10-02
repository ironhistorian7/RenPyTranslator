# Embedded in Ren'Py 7/8. Python 2.7 compatible; no eval or game function calls.
import ast as _rpt_condition_ast


def _rpt_hint_condition(code, namespace):
    unknown = object()
    ast = _rpt_condition_ast
    budget = [160]

    def value(node):
        budget[0] -= 1
        if budget[0] < 0:
            return unknown
        if isinstance(node, ast.Name):
            if node.id in ('True', 'False', 'None'):
                return {'True': True, 'False': False, 'None': None}[node.id]
            if node.id.startswith('_'):
                return unknown
            return namespace.get(node.id, unknown)
        if hasattr(ast, 'Constant'):
            if isinstance(node, ast.Constant):return node.value
        else:
            if isinstance(node, ast.Num):return node.n
            if isinstance(node, ast.Str):return node.s.decode('utf-8') if isinstance(node.s, bytes) else node.s
            if hasattr(ast, 'NameConstant') and isinstance(node, ast.NameConstant):return node.value
        if isinstance(node, ast.Attribute):
            base = value(node.value)
            if base is unknown or node.attr.startswith('_'):
                return unknown
            # Do not invoke properties or game-defined __getattr__ callbacks.
            try:
                fields = object.__getattribute__(base, '__dict__')
                return fields.get(node.attr, unknown)
            except (AttributeError, TypeError):
                return unknown
        if isinstance(node, ast.Subscript):
            base = value(node.value)
            index = node.slice
            if hasattr(ast, 'Index') and isinstance(index, ast.Index):
                index = index.value
            key = value(index)
            if not isinstance(base, (dict, list, tuple)) or key is unknown:
                return unknown
            try:
                if isinstance(base, dict):return dict.__getitem__(base, key)
                if isinstance(base, list):return list.__getitem__(base, key)
                return tuple.__getitem__(base, key)
            except (KeyError, IndexError, TypeError):
                return unknown
        if isinstance(node, (ast.List, ast.Tuple)):
            members = [value(n) for n in node.elts]
            return unknown if any(v is unknown for v in members) else members
        if isinstance(node, ast.UnaryOp):
            item = value(node.operand)
            if item is unknown:
                return unknown
            if isinstance(node.op, ast.Not):
                return not item
            if type(item) in (int, float):
                if isinstance(node.op, ast.USub):return -item
                if isinstance(node.op, ast.UAdd):return item
        if isinstance(node, ast.BinOp):
            left, right = value(node.left), value(node.right)
            if type(left) not in (int, float) or type(right) not in (int, float):
                return unknown
            if isinstance(node.op, ast.Add):return left + right
            if isinstance(node.op, ast.Sub):return left - right
            if isinstance(node.op, ast.Mult):return left * right
        if isinstance(node, ast.BoolOp):
            values = [value(n) for n in node.values]
            if isinstance(node.op, ast.And):
                if any(v is not unknown and not v for v in values):return False
                return unknown if any(v is unknown for v in values) else True
            if any(v is not unknown and v for v in values):return True
            return unknown if any(v is unknown for v in values) else False
        if isinstance(node, ast.Compare) and len(node.ops) == 1:
            left, right = value(node.left), value(node.comparators[0])
            if left is unknown or right is unknown:
                return unknown
            op = node.ops[0]
            if isinstance(op, ast.Eq):return left == right
            if isinstance(op, ast.NotEq):return left != right
            if isinstance(op, ast.Lt):return left < right
            if isinstance(op, ast.LtE):return left <= right
            if isinstance(op, ast.Gt):return left > right
            if isinstance(op, ast.GtE):return left >= right
            if isinstance(op, ast.In):return left in right
            if isinstance(op, ast.NotIn):return left not in right
            if isinstance(op, ast.Is):return left is right
            if isinstance(op, ast.IsNot):return left is not right
        return unknown

    try:
        if len(code) > 6000:return None
        result = value(ast.parse(code, mode='eval').body)
        return None if result is unknown else bool(result)
    except (ValueError, TypeError, SyntaxError, OverflowError, RuntimeError, UnicodeError):
        return None


def _rpt_hint_current(entry):
    if not entry:
        return entry
    hints = []
    for hint in entry.get('hints', []):
        guards = hint.get('guards', [])
        verdict = _rpt_hint_condition(' and '.join('(' + g + ')' for g in guards), globals()) if guards else True
        if verdict is False:
            continue
        item = dict(hint)
        if verdict is True and hint.get('base_text'):
            item['text'] = hint['base_text']
        hints.append(item)
    return dict(entry, hints=hints) if hints else None
