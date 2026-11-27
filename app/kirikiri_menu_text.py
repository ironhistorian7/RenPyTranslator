"""Conservative extraction of static TJS menu and settings captions.

Offsets are character offsets in the decoded file, and cover the literal body.
No TJS code is evaluated. Ambiguous or nonliteral expressions are ignored.
"""
import re
from kirikiri_text import _decode
from kirikiri_tokens import _TJS_PRINTF_FIELD

_TJS_FIELD = r'\$\{\.?[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*\}'
_JAPANESE_UI = re.compile(r'[\u3040-\u30ff\u3400-\u9fff]')


def _tokens(text):
    result = []
    i = 0
    while i < len(text):
        c = text[i]
        if c.isspace():
            i += 1; continue
        if text.startswith('//', i):
            end = text.find('\n', i + 2); i = len(text) if end < 0 else end; continue
        if text.startswith('/*', i):
            end = text.find('*/', i + 2); i = len(text) if end < 0 else end + 2; continue
        if c in "\"'":
            start = i; i += 1
            while i < len(text):
                if text[i] == '\\': i += 2; continue
                if text[i] == c: i += 1; break
                if text[i] in '\r\n': break
                i += 1
            if i <= len(text) and i > start + 1 and text[i - 1] == c:
                result.append(('string', text[start:i], start, i)); continue
            # An invalid literal makes the remainder unsafe to interpret.
            break
        if c.isalpha() or c in '_$':
            start = i; i += 1
            while i < len(text) and (text[i].isalnum() or text[i] in '_$'): i += 1
            result.append(('id', text[start:i], start, i)); continue
        result.append(('symbol', c, i, i + 1)); i += 1
    return result


def _unescape(value):
    out = []; i = 0
    simple = {'n':'\n', 'r':'\r', 't':'\t', 'b':'\b', 'f':'\f', 'v':'\v', '\\':'\\', '"':'"', "'":"'"}
    while i < len(value):
        if value[i] != '\\': out.append(value[i]); i += 1; continue
        i += 1
        if i >= len(value): return None
        c = value[i]; i += 1
        if c in simple: out.append(simple[c]); continue
        if c in 'xX':
            match = re.match(r'[0-9a-fA-F]{1,4}', value[i:])
            if not match: return None
            digits = match.group(); out.append(chr(int(digits, 16))); i += len(digits); continue
        return None
    return ''.join(out)


def _args(tokens, opening):
    stack = [{'(':')', '[':']', '{':'}'}[tokens[opening][1]]]; args = [[]]
    for token in tokens[opening + 1:]:
        v = token[1]
        if v in ('(', '[', '{'):
            stack.append({'(':')', '[':']', '{':'}'}[v]); args[-1].append(token)
        elif v in (')', ']', '}'):
            if not stack or stack[-1] != v: return []
            stack.pop()
            if not stack: return args
            args[-1].append(token)
        elif v == ',' and len(stack) == 1: args.append([])
        else: args[-1].append(token)
    return []


def _fields(tokens, opening):
    fields = {}
    for part in _args(tokens, opening):
        if len(part) < 3:
            continue
        key = _unescape(part[0][1][1:-1]) if part[0][0] == 'string' else part[0][1]
        if part[1][1] == ':':
            fields[key] = part[2:]
        elif len(part) >= 4 and part[1][1] == '=' and part[2][1] == '>':
            fields[key] = part[3:]
    return fields


def _display_literals(expr):
    if len(expr) == 1 and expr[0][0] == 'string':
        return [expr[0]]
    if len(expr) == 2 and expr[0][1] == '@' and expr[1][0] == 'string':
        value = _unescape(expr[1][1][1:-1]) or ''
        if re.search(_TJS_FIELD, value) and '${' not in re.sub(_TJS_FIELD, '', value):
            return [expr[1]]
    if (len(expr) >= 5 and expr[0][0] == 'string' and
            [token[1].lower() for token in expr[1:4]] == ['.', 'sprintf', '('] and expr[-1][1] == ')'):
        args = _args(expr, 3)
        if sum(map(len, args)) + len(args) + 4 == len(expr):
            return [expr[0]]
    if (len(expr) == 5 and expr[0][0] == 'id' and expr[1][1] == '?' and
            expr[2][0] == 'string' and expr[3][1] == ':' and expr[4][0] == 'string'):
        return [expr[2], expr[4]]
    return []


def _local_display_literals(tokens, call, expr):
    """Trace a local caption from its nearest initializer in the same block."""
    if len(expr) != 1 or expr[0][0] != 'id':
        return []
    reference = expr[0]
    name = reference[1]
    for i in range(call-1, max(-1, call-90), -1):
        value = tokens[i][1]
        if value in ('{', '}'):
            break
        if value != name:
            continue
        if i == 0 or tokens[i-1][1] != 'var' or tokens[i+1][1] != '=':
            break
        end = next((j for j in range(i+2, call) if tokens[j][1] == ';'), call)
        # A caption that is reused as a control key or identifier is unsafe.
        for j in range(end+1, len(tokens)):
            token = tokens[j]
            if token[1] in ('{', '}'):
                break
            if token[0] == 'id' and token[1] == name and token != reference:
                return []
        return _display_literals(tokens[i+2:end])
    return []


def extract_ui_text(raw, path='', *, japanese_ui=False):
    text, _, _ = _decode(raw); tokens = _tokens(text); candidates = []
    native_starts = set(); native_prefixes = {}; native_parents = {}; native_suffixes = {}
    readonly_starts = set(); seen_volumes = set(); display_suffixes = {}
    def add(candidate, native=False, prefix='', parent='', suffix=None):
        candidates.append(candidate)
        if native: native_starts.add(candidate[2])
        if prefix: native_prefixes[candidate[2]] = prefix
        if parent: native_parents[candidate[2]] = parent
        if suffix is not None: native_suffixes[candidate[2]] = suffix
    # Build a narrow index of simple text-producing functions.  A helper is
    # eligible only when a known renderer calls it directly; identify literal
    # interpolated returns and arguments returned verbatim as fallback text.
    helpers = {}
    if japanese_ui:
        for j, token in enumerate(tokens):
            if (token[1].lower() != 'function' or j+3 >= len(tokens) or
                    tokens[j+1][0] != 'id' or tokens[j+2][1] != '('): continue
            depth=0; end=None
            for k in range(j+2,len(tokens)):
                if tokens[k][1] == '(': depth+=1
                elif tokens[k][1] == ')':
                    depth-=1
                    if depth==0: end=k; break
            if end is None or end+1 >= len(tokens) or tokens[end+1][1] != '{': continue
            params=_args(tokens,j+2)
            if not all(len(arg)==1 and arg[0][0]=='id' for arg in params): continue
            depth=0; close=None
            for k in range(end+1,len(tokens)):
                if tokens[k][1]=='{': depth+=1
                elif tokens[k][1]=='}':
                    depth-=1
                    if depth==0: close=k; break
            if close is None: continue
            body=tokens[end+2:close]; templates=[]; returned=set()
            for k,t in enumerate(body):
                if t[1]!='return': continue
                if k+2<len(body) and body[k+1][0]=='id' and body[k+2][1]==';':
                    returned.add(body[k+1][1])
                if (k+3<len(body) and body[k+1][1]=='@' and body[k+2][0]=='string'
                        and body[k+3][1]==';'):
                    value=_unescape(body[k+2][1][1:-1])
                    if value and re.search(_TJS_FIELD,value) and '${' not in re.sub(_TJS_FIELD,'',value):
                        templates.append(body[k+2])
            key=tokens[j+1][1].lower()
            value=(params,returned,templates)
            helpers[key]=None if key in helpers else value
    name = str(path).replace('\\', '/').lower()
    settings = name.endswith('sysscn/exmenuintf.tjs')
    menu_file = name.endswith(('system/menus.tjs', 'system/mainwindow.tjs'))
    main_window = name.endswith('system/mainwindow.tjs')
    default_config = name.endswith('main/default.tjs')
    option_file = name.endswith('sysscn/option.tjs')
    speech_dialog = name.endswith('/speech_dlg.tjs')
    speech_menu = name.endswith('/speech.tjs')
    custom_menu = name.endswith(('sysscn/override.tjs', 'sysscn/stayontop.tjs',
                                 'sysscn/stopdeactive.tjs', 'main/custom.tjs', 'main/scnsel.tjs',
                                 'sysscn/speech.tjs'))

    def add_display(expr, *, allow_english=False):
        for candidate in _display_literals(expr):
            value = _unescape(candidate[1][1:-1])
            if value and (allow_english or _JAPANESE_UI.search(value)):
                add(candidate)

    for i, token in enumerate(tokens):
        kind, val, _, _ = token
        if japanese_ui and val == '%' and i+1 < len(tokens) and tokens[i+1][1] == '[':
            fields = _fields(tokens, i+1)
            if 'exp' in fields and 'hint' in fields:
                add_display(fields['hint'])
            if speech_dialog:
                add_display(fields.get('title', []))
                if 'cmd' in fields:
                    add_display(fields.get('text', []))
                for key in ('children', 'detail_children'):
                    children = fields.get(key, [])
                    if children and children[0][1] == '[':
                        for expression in _fields(children, 0).values():
                            add_display(expression)
                filters = fields.get('filter', [])
                if filters and filters[0][1] == '[':
                    for part in _args(filters, 0):
                        for candidate in _display_literals(part):
                            source = _unescape(candidate[1][1:-1]) or ''
                            label, sep, pattern = source.partition('|')
                            if sep and pattern.startswith('*') and _JAPANESE_UI.search(label):
                                add(candidate)
                                display_suffixes[candidate[2]] = sep + pattern
        if (name.endswith('sysscn/movieqsel.tjs') and kind == 'id' and val == 'MovieQualitySelectConfig'
                and i+3 < len(tokens) and tokens[i+1][1] == '='
                and tokens[i+2][1] == '%' and tokens[i+3][1] == '['):
            fields = _args(tokens, i+3); parent = ''
            for part in fields:
                if (len(part) == 3 and part[0][1] == 'menu' and part[1][1] == ':'
                        and part[2][0] == 'string'):
                    parent = _unescape(part[2][1][1:-1]) or ''; add(part[2], True)
            if parent:
                for part in fields:
                    if len(part) >= 5 and part[1][1] == ':' and part[2][1] == '%' and part[3][1] == '[':
                        for child in _args(part, 3):
                            if (len(child) == 3 and child[0][1] == 'menu' and child[1][1] == ':'
                                    and child[2][0] == 'string'):
                                add(child[2], True, parent=parent)
        # Scenario-list records identify a displayed title separately from
        # navigation keys.  Only the title field is editable; tag/name/links
        # and numeric ordering remain byte-for-byte intact.
        if japanese_ui and name.endswith('main/scnlist.tjs') and val == '%' and i+1 < len(tokens) and tokens[i+1][1] == '[':
            fields = {}
            for part in _args(tokens, i+1):
                if len(part) >= 4 and part[0][0] == 'string' and part[1][1] == '=' and part[2][1] == '>':
                    key = _unescape(part[0][1][1:-1])
                    if key in fields: fields[key] = None
                    else: fields[key] = part[3:]
            if all(k in fields and fields[k] is not None for k in ('tag','title1','title2')):
                title = fields['title2']
                if len(title) == 1 and title[0][0] == 'string':
                    value = _unescape(title[0][1][1:-1])
                    if value and re.search(r'[\u3040-\u30ff\u3400-\u9fff]', value): add(title[0])
                volume = fields.get('volume')
                if volume and len(volume) == 1 and volume[0][0] == 'string':
                    value = _unescape(volume[0][1][1:-1])
                    if value and value not in seen_volumes and re.search(r'[\u3040-\u30ff\u3400-\u9fff]', value):
                        seen_volumes.add(value); add(volume[0]); readonly_starts.add(volume[0][2])
        # Only dictionary records with an explicit menu name and action,
        # children or group describe native items in these menu scripts.
        if custom_menu and val == '%' and i+1 < len(tokens) and tokens[i+1][1] == '[':
            entries = _args(tokens, i+1)
            fields = {part[0][1].lower():part[2:] for part in entries
                      if len(part) >= 3 and part[0][0] == 'id' and part[1][1] == ':'}
            caption = fields.get('caption', [])
            if ('name' in fields and any(x in fields for x in ('exp', 'children', 'group'))
                    and len(caption) == 1 and caption[0][0] == 'string'):
                add(caption[0], not speech_menu)
        if kind != 'id': continue
        lower = val.lower()
        if japanese_ui and i+1 < len(tokens) and tokens[i+1][1] == '(':
            receiver = tokens[i-2][1].lower() if i >= 2 and tokens[i-1][1] == '.' else ''
            if lower == 'drawbuttontext':
                args = _args(tokens, i+1)
                index = 1 if len(args) == 3 else 4
                if index < len(args): add_display(args[index])
            elif lower == 'drawtext' and receiver:
                args = _args(tokens, i+1)
                if len(args) > 2:
                    add_display(args[2])
                    for candidate in _local_display_literals(tokens, i, args[2]):
                        add_display([candidate])
            elif lower == 'setitemprop':
                args = _args(tokens, i+1)
                if (len(args) == 3 and len(args[1]) == 1 and args[1][0][0] == 'string'
                        and args[1][0][1][1:-1].lower() == 'hint'):
                    add_display(args[2])
            elif lower == 'inform' and receiver == 'system':
                args = _args(tokens, i+1)
                if args: add_display(args[0])
            elif lower == 'messagebox' and receiver == 'win32dialog':
                args = _args(tokens, i+1)
                if len(args) > 2:
                    add_display(args[1])
                    for candidate in _local_display_literals(tokens, i, args[1]):
                        add_display([candidate])
                        template = _unescape(candidate[1][1:-1]) or ''
                        for field in re.finditer(r'\$\{([A-Za-z_]\w*)\}', template):
                            # A literal loaded into a named template parameter can
                            # itself be a displayed fragment, such as a verb.
                            argument = [('id', field.group(1), 0, 0)]
                            for fragment in _local_display_literals(tokens, i, argument):
                                add_display([fragment])
            elif lower in ('askyesno', 'createbutton') and name.endswith(('yesnodialog.tjs', 'myyesnodialog.tjs')):
                args = _args(tokens, i+1)
                if lower == 'createbutton' and args:
                    add_display(args[0])
                if lower == 'askyesno' and i and tokens[i-1][1].lower() == 'function':
                    if len(args) > 1 and [t[1] for t in args[1][:2]] == ['caption', '=']:
                        add_display(args[1][2:])
            elif speech_dialog and lower == 'speechdicteditdialog':
                args = _args(tokens, i+1)
                if len(args) > 1: add_display(args[1])
        if (japanese_ui and main_window and lower == '_errorinformmessagemap' and i+3 < len(tokens)
                and [t[1] for t in tokens[i+1:i+4]] == ['=', '%', '[']):
            for expr in _fields(tokens, i+3).values():
                add_display(expr)
                if len(expr) >= 2 and [t[1] for t in expr[:2]] == ['%', '[']:
                    fields = _fields(expr, 1)
                    add_display(fields.get('message', []))
                    add_display(fields.get('caption', []), allow_english=True)
                    exp = fields.get('exp', [])
                    for j in range(len(exp)-3):
                        if exp[j][1] == 'return' and exp[j+3][1] == ';':
                            add_display(exp[j+1:j+3])
        if japanese_ui and speech_dialog and i+2 < len(tokens) and tokens[i+1][1] == '=':
            opener = [t[1] for t in tokens[i+2:i+4]]
            if lower in ('children', 'itemtexts') and opener == ['%', '[']:
                for expr in _fields(tokens, i+3).values(): add_display(expr)
            elif lower in ('speechsettingtypetable', '_voiceparamdropdownlist') and tokens[i+2][1] == '[':
                for part in _args(tokens, i+2):
                    if lower == '_voiceparamdropdownlist': add_display(part)
                    elif len(part) > 4 and [t[1] for t in part[1:5]] == ['=', '>', '%', '[']:
                        add_display(_fields(part, 4).get('name', []))
            elif lower == '_helptitle': add_display(tokens[i+2:i+3])
        if japanese_ui and speech_dialog and lower == 'itemtexts' and i+10 < len(tokens):
            if [t[1] for t in tokens[i+1:i+5]] == ['[', 'IDOK', ']', '='] and tokens[i+10][1] == ';':
                add_display(tokens[i+5:i+10])
        # Literal captions passed directly to known text renderers.  Restrict
        # the extension to Japanese projects and proven text argument positions;
        # never translate image names, actions, coordinates or arbitrary code.
        text_arguments = {'drawtextkinsokurect':1, 'drawinfotext':2,
                          'drawitemtext':2}
        if japanese_ui and lower in text_arguments and i+1 < len(tokens) and tokens[i+1][1] == '(':
            # A function declaration describes a parameter, not a call.
            if not (i and tokens[i-1][1].lower() == 'function'):
                args = _args(tokens, i+1); index = text_arguments[lower]
                if index < len(args) and len(args[index]) == 1 and args[index][0][0] == 'string':
                    value = _unescape(args[index][0][1][1:-1])
                    if value and re.search(r'[\u3040-\u30ff\u3400-\u9fff]', value):
                        add(args[index][0])
                elif index < len(args):
                    expression=args[index]
                    if len(expression)>=3 and expression[0][0]=='id' and expression[1][1]=='(' and expression[-1][1]==')':
                        helper=helpers.get(expression[0][1].lower())
                        if helper:
                            params,returned,templates=helper
                            for template in templates:
                                value=_unescape(template[1][1:-1])
                                if value and re.search(r'[\u3040-\u30ff\u3400-\u9fff]',value): add(template)
                            passed=_args(expression,1)
                            for ix,param in enumerate(params):
                                if (param[0][1] in returned and ix<len(passed) and len(passed[ix])==1 and passed[ix][0][0]=='string'):
                                    value=_unescape(passed[ix][0][1][1:-1])
                                    if value and re.search(r'[\u3040-\u30ff\u3400-\u9fff]',value): add(passed[ix][0])
        # The size menu builds a visible caption from numeric placeholders and
        # a separate literal accelerator. Only this proven template shape is
        # catalogued; no expressions are executed or arbitrary templates read.
        if (name.endswith('sysscn/override.tjs') and lower == 'caption' and i >= 3
                and tokens[i-1][1] == '.' and tokens[i-2][1].lower() == 'disablewindowresizemenuitem'
                and i+6 < len(tokens) and tokens[i+1][1] == '=' and tokens[i+2][1] == '@'
                and tokens[i+3][0] == 'string' and tokens[i+4][1] == '+'
                and tokens[i+5][0] == 'string' and tokens[i+6][1] == ';'):
            template = _unescape(tokens[i+3][1][1:-1])
            tail = _unescape(tokens[i+5][1][1:-1])
            if (template and re.fullmatch(r'\$\{[A-Za-z_][A-Za-z0-9_]*\} x \$\{[A-Za-z_][A-Za-z0-9_]*\} [^$&\r\n]+', template)
                    and tail and re.fullmatch(r'\(&[A-Za-z0-9]\)', tail)):
                add(tokens[i+3], True, suffix=tail)
        menu_ctor = (lower in ('kagmenuitem', 'menuitem') and i and tokens[i-1][1].lower() == 'new')
        setting_calls = {'pushbutton':(0,), 'defpushbutton':(0,), 'autocheckbox':(0,),
                         'addgroupbox':(0,), 'addtrackbar':(0, 6, 7)}
        function_indices = [j for j in range(i) if tokens[j][1] == 'function'] if option_file and lower == 'askyesno' else []
        init_confirm = (bool(function_indices) and function_indices[-1] + 1 < i
                        and tokens[function_indices[-1] + 1][1].lower() == 'setdefault')
        if custom_menu and lower in ('getsysargradiomenu', 'getsysargcheckmenuitem') and i+1 < len(tokens) and tokens[i+1][1] == '(':
            args = _args(tokens, i+1)
            if len(args) > 1 and len(args[1]) == 1 and args[1][0][0] == 'string': add(args[1][0], True)
            # Radio choices encode an internal value and optional IDs separated
            # by ASCII colons; only the visible second field is catalogued.
            if lower == 'getsysargradiomenu' and len(args) > 4 and args[4] and args[4][0][1] == '[':
                parent = (_unescape(args[1][0][1][1:-1]) if len(args) > 1 and len(args[1]) == 1
                          and args[1][0][0] == 'string' else '') or ''
                for part in _args(args[4], 0):
                    if len(part) != 1 or part[0][0] != 'string': continue
                    literal = _unescape(part[0][1][1:-1])
                    if literal is None: continue
                    match = re.fullmatch(r'([^:]+:)([^:]+)(?::.*)?', literal)
                    if match and re.fullmatch(r'\*?[A-Za-z0-9_]+\s*:', match.group(1)):
                        add(part[0], True, match.group(1), parent)
        if custom_menu and lower == 'addkaghookcallback' and i+1 < len(tokens) and tokens[i+1][1] == '(':
            args = _args(tokens, i+1)
            if (len(args) > 1 and len(args[0]) == 1 and args[0][0][0] == 'string'
                    and _unescape(args[0][0][1][1:-1]) == 'onKAGSetupMenu'):
                body = args[1]
                for j in range(1, len(body)-3):
                    if (body[j][1].lower() == 'caption' and body[j-1][1] == '.'
                            and body[j+1][1] == '=' and body[j+2][0] == 'string'
                            and body[j+3][1] == ';'):
                        add(body[j+2], True)
        if (menu_ctor or (settings and lower in setting_calls) or init_confirm) and i+1 < len(tokens) and tokens[i+1][1] == '(':
            args = _args(tokens, i+1)
            indexes = (1,) if menu_ctor else ((0, 1) if init_confirm else setting_calls[lower])
            for index in indexes:
                if index < len(args) and len(args[index]) == 1 and args[index][0][0] == 'string':
                    add(args[index][0], bool(menu_ctor))
                elif menu_ctor and index < len(args):
                    # Restrict condition to a simple variable/property chain; both
                    # branches must be single literals, with no calls or code.
                    branch = args[index]
                    q = next((j for j, t in enumerate(branch) if t[1] == '?'), -1)
                    cond = branch[:q] if q >= 0 else []
                    if (cond and re.fullmatch(r'!?[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*', ''.join(t[1] for t in cond))
                            and len(branch) == q + 4 and branch[q+1][0] == 'string'
                            and branch[q+2][1] == ':' and branch[q+3][0] == 'string'):
                        add(branch[q+1], True); add(branch[q+3], True)
        # Popup dictionaries are accepted only inside the known menu bar arrays.
        if (main_window and lower in ('menubarhidepopupmenu', 'menubarhidesysmenu')
                and i+2 < len(tokens) and tokens[i+1][1] == '=' and tokens[i+2][1] == '['):
            for item in _args(tokens, i+2):
                for j in range(len(item)-3):
                    if (item[j][1].lower() == 'caption' and item[j+1][1] == ':'
                            and item[j+2][0] == 'string' and item[j+3][1] in (',', ']')):
                        add(item[j+2], True)
        if (default_config and lower.startswith('shortcut') and i and tokens[i-1][1] == '.'
                and i+2 < len(tokens) and tokens[i+1][1] == '='):
            if (lower == 'shortcutbindcaptions' and i+3 < len(tokens)
                    and tokens[i+2][1] == '%' and tokens[i+3][1] == '['):
                for item in _args(tokens, i+3):
                    if (len(item) == 3 and item[0][0] in ('id', 'string')
                            and item[1][1] == ':' and item[2][0] == 'string'):
                        add(item[2])
            elif lower in ('shortcutnobind', 'shortcutinputbind', 'shortcutinputhelp'):
                j = i + 2; expression = []
                while j < len(tokens) and tokens[j][1] != ';':
                    expression.append(tokens[j]); j += 1
                if (j < len(tokens) and expression and
                        all(t[0] == 'string' or t[1] in ('(', ')') for t in expression)):
                    for t in expression:
                        if t[0] == 'string': add(t)
        if (settings and lower in ('title', 'caption') and i+2 < len(tokens)
                and tokens[i+1][1] == ':' and tokens[i+2][0] == 'string'
                and i+3 < len(tokens) and tokens[i+3][1] in (',', '}')):
            add(tokens[i+2])
        if (menu_file and lower == 'caption' and i and tokens[i-1][1] == '.'
                and i+3 < len(tokens) and tokens[i+1][1] == '='
                and tokens[i+2][0] == 'string' and tokens[i+3][1] == ';'):
            add(tokens[i+2], True)
    rows = []; seen = set()
    for _, literal, start, end in candidates:
        if start in seen: continue
        seen.add(start)
        value = _unescape(literal[1:-1])
        if value is None or not value.strip() or value.strip() == '-': continue
        prefix = native_prefixes.get(start, '')
        if prefix:
            value = value[len(prefix):].split(':', 1)[0].strip()
        # Keep native accelerator, ellipsis and tab shortcut outside model input.
        match = re.search(r'(\s*\(&[A-Za-z0-9]\)(?:\s*\.\.\.)?|\s*\.\.\.)?(\t[^\r\n]*)?$', value)
        suffix = match.group(0) if match else ''
        visible = value[:-len(suffix)] if suffix else value
        if start in native_suffixes:
            if suffix: continue
            suffix = native_suffixes[start]
        if start in display_suffixes:
            suffix = display_suffixes[start]
            visible = value[:-len(suffix)]
        inline = None
        if '&' in visible:
            # Preserve a single Win32 inline accelerator; ambiguous && or
            # multiple markers are left untouched rather than guessed.
            matches = list(re.finditer(r'&([A-Za-z0-9])', visible))
            if len(matches) != 1 or visible.count('&') != 1 or start not in native_starts: continue
            found = matches[0]
            inline = {'index':found.start(), 'key':found.group(1)}
            visible = visible[:found.start()] + visible[found.start()+1:]
        if (not visible.strip() or
                (name.endswith(('system/mainwindow.tjs', 'sysscn/browswindow.tjs'))
                 and visible in ('x', '+', 'RightClick')) or
                re.fullmatch(r'(?i)(?:(?:ctrl|alt|shift)\+)+(?:[a-z0-9]+)', visible.strip())): continue
        row = {'source': visible, 'usage':'ui_label', 'dialect':'kirikiri_tjs_ui',
               'file':str(path), 'location':{'start':start+1, 'end':end-1},
               'ui_suffix':suffix, 'native_menu':start in native_starts}
        if start in readonly_starts: row['ui_readonly_volume'] = True
        if inline: row['ui_inline_accel'] = inline
        if start in native_parents: row['ui_native_parent'] = native_parents[start]
        rows.append(row)
    return sorted(rows, key=lambda row: row['location']['start'])


def _escape(value, quote):
    out = []
    for c in value:
        n = ord(c)
        if c == '\\': out.append('\\\\')
        elif c == quote: out.append('\\' + c)
        elif c == '\n': out.append('\\n')
        elif c == '\r': out.append('\\r')
        elif c == '\t': out.append('\\t')
        elif n < 32 or n >= 127:
            if n <= 0xffff: out.append('\\x%04X' % n)
            else:
                n -= 0x10000; out.append('\\x%04X\\x%04X' % (0xD800+(n>>10), 0xDC00+(n&1023)))
        else: out.append(c)
    return ''.join(out)


def render_ui_text(raw, rows):
    text, encoding, bom = _decode(raw)
    edits = []
    for row in rows:
        if row.get('native_menu') or row.get('ui_readonly_volume'): continue
        translation = row.get('translation')
        if not isinstance(translation, str) or not translation or translation == row['source']: continue
        a, b = row['location']['start'], row['location']['end']
        if not (0 < a <= b < len(text) and text[a-1] in "\"'" and text[b] == text[a-1]): raise ValueError('Invalid TJS literal span')
        if re.findall(_TJS_FIELD, row['source']) != re.findall(_TJS_FIELD, translation):
            raise ValueError('TJS template placeholder mismatch')
        if re.findall(_TJS_PRINTF_FIELD, row['source']) != re.findall(_TJS_PRINTF_FIELD, translation):
            raise ValueError('TJS format placeholder mismatch')
        original = _unescape(text[a:b])
        if original != row['source'] + row.get('ui_suffix', ''): raise ValueError('TJS source changed')
        edits.append((a,b,_escape(translation + row.get('ui_suffix', ''), text[a-1])))
    edits.sort(reverse=True); last = len(text)
    for a,b,value in edits:
        if b > last: raise ValueError('Overlapping TJS literal spans')
        text = text[:a] + value + text[b:]; last = a
    return bom + text.encode(encoding)


def native_caption_pairs(rows):
    """Return full native captions for the external Unicode menu bridge.

    Conflicting translations for the same original caption are rejected, since
    a text-only lookup cannot safely distinguish their contexts.
    """
    pairs = {}; ordered = []; children = []
    for row in rows:
        if not row.get('native_menu'): continue
        target = row.get('translation')
        if not isinstance(target, str) or not target.strip(): continue
        placeholders = r'\$\{[A-Za-z_][A-Za-z0-9_]*\}'
        if re.findall(placeholders, row['source']) != re.findall(placeholders, target):
            raise ValueError('TJS template placeholder mismatch')
        suffix = row.get('ui_suffix', '')
        source = row['source']
        inline = row.get('ui_inline_accel')
        if inline:
            index, key = inline['index'], inline['key']
            if index < 0 or index >= len(source) or source[index] != key:
                raise ValueError('Invalid native menu accelerator')
            source = source[:index] + '&' + source[index:]
            pos = next((i for i, c in enumerate(target) if c.lower() == key.lower()), -1)
            if pos >= 0: target = target[:pos] + '&' + target[pos:]
            else: target += '(&' + key + ')'
        source += suffix
        target += suffix
        if source in pairs and pairs[source] != target:
            raise ValueError('Conflicting native menu translations')
        if source not in pairs:
            pairs[source] = target
            ordered.append({'source':source, 'translation':target})
        if row.get('ui_native_parent'):
            children.append((row['ui_native_parent'], source, target))
    # Some menu builders flatten a radio submenu and prepend its original
    # caption in brackets. Derive both parts only when each has a translation.
    for parent, source, target in children:
        if parent not in pairs: continue
        combined = '[' + parent + '] ' + source
        translated = '[' + pairs[parent] + '] ' + target
        if combined in pairs and pairs[combined] != translated:
            raise ValueError('Conflicting native menu translations')
        if combined not in pairs:
            pairs[combined] = translated
            ordered.append({'source':combined, 'translation':translated})
    return ordered
