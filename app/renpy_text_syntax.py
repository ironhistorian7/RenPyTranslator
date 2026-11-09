"""Non-executing Ren'Py text lexer, based on the official 8.5.4 text guide.

https://www.renpy.org/doc/html/text.html
The registry documents built-ins; unknown/custom tags are preserved too. This
lexer does not validate, evaluate, or substitute Python expressions. It replaces
the old flat-bracket regex, retaining a small compiled-pattern-like interface.
"""
import re


PURE_VISUAL_TAG_NAMES = frozenset({
    'alpha', 'b', 'color', 'feature', 'font', 'i', 'k', 'outlinecolor',
    'plain', 's', 'size', 'u',
})
ACCESSIBILITY_TAG_NAMES = frozenset({'alt', 'noalt'})
RUBY_TAG_NAMES = frozenset({'art', 'rb', 'rt'})
DIALOGUE_TAG_NAMES = frozenset({'w', 'p', 'nw', 'fast', 'done', 'clear'})
GENERAL_TAG_NAMES = PURE_VISUAL_TAG_NAMES | ACCESSIBILITY_TAG_NAMES | RUBY_TAG_NAMES | frozenset({
    'a', 'cps', 'image', 'shader', 'space', 'vspace', '#',
})
STYLE_REFERENCE_TAG_NAMES = frozenset({''})  # {=style_name} ... {/}
VERTICAL_TAG_NAMES = frozenset({'horiz', 'vert'})
BUILTIN_TAG_NAMES = GENERAL_TAG_NAMES | DIALOGUE_TAG_NAMES | STYLE_REFERENCE_TAG_NAMES | VERTICAL_TAG_NAMES
FUNCTIONAL_TAG_NAMES = BUILTIN_TAG_NAMES - PURE_VISUAL_TAG_NAMES
INTERPOLATION_CONVERSIONS = frozenset('rstiqulcf')
RUBY_DELIMITERS = frozenset({'〖', '|', '｜', '〗'})
ESCAPE_UNITS = frozenset({'{{', '[[', '〖〖', '\\%', '\\ '})

_NAMED_FORMAT = re.compile(r'%\([^)]+\)[#0 +\-]*\d*(?:\.\d+)?[a-zA-Z]')
_ANGLE_START = re.compile(r'</?[A-Za-z][A-Za-z0-9:_-]*(?=[\s/>])')


class TextTokenMatch:
    """A group-0 match record; offsets always refer to the original string."""
    __slots__ = ('string', 'pos', 'endpos', '_start', '_end')

    def __init__(self, string, start, end, pos, endpos):
        self.string = string
        self._start, self._end = start, end
        self.pos, self.endpos = pos, endpos

    def group(self, *groups):
        if not groups:
            return self.string[self._start:self._end]
        values = []
        for group in groups:
            if group != 0:
                raise IndexError('no such group')
            values.append(self.string[self._start:self._end])
        return values[0] if len(values) == 1 else tuple(values)

    def __getitem__(self, group):
        return self.group(group)

    def start(self, group=0):
        if group != 0:
            raise IndexError('no such group')
        return self._start

    def end(self, group=0):
        if group != 0:
            raise IndexError('no such group')
        return self._end

    def span(self, group=0):
        return self.start(group), self.end(group)

    def groups(self, default=None):
        return ()

    def groupdict(self, default=None):
        return {}

    def expand(self, template):
        # Delegate replacement-string escapes to Python, without evaluating text.
        return re.fullmatch(r'[\s\S]*', self.group()).expand(template)


def _interpolation_end(text, start, end):
    """Balance brackets and quoted Python strings without executing expressions."""
    stack = [']']
    quote = None
    i = start + 1
    while i < end:
        char = text[i]
        if quote is not None:
            if char == '\\':
                i += 2
                continue
            if text.startswith(quote, i):
                i += len(quote)
                quote = None
                continue
            i += 1
            continue
        if char in "\"'":
            quote = char * 3 if text.startswith(char * 3, i) else char
            i += len(quote)
            continue
        if char == '\\':
            i += 2
            continue
        if char in '[({':
            stack.append({'[': ']', '(': ')', '{': '}'}[char])
        elif char in '])}':
            if char != stack[-1]:
                return None
            stack.pop()
            if not stack:
                return i + 1
        i += 1
    return None


def _angle_end(text, start, end):
    head = _ANGLE_START.match(text, start, end)
    if head is None:
        return None
    quote = None
    i = head.end()
    while i < end:
        char = text[i]
        if quote:
            if char == '\\':
                i += 2
                continue
            if char == quote:
                quote = None
        elif char in "\"'":
            quote = char
        elif char == '>':
            return i + 1
        elif char == '<':
            return None
        i += 1
    return None


class TextTokens:
    """Recognize syntax units; optional printf and response tags share the pass."""

    def finditer(self, string, pos=0, endpos=None, *, formats=None, angle_tags=False):
        if not isinstance(string, str):
            raise TypeError('expected string')
        pos = max(0, pos)
        endpos = len(string) if endpos is None else min(len(string), max(0, endpos))
        # Ruby delimiters remain tentative until a balanced separator/close pair
        # is seen. Their payload is scanned normally, including interpolation.
        spans = []
        ruby_open = ruby_sep = None
        no_brace_end = no_bracket_end = False
        i = pos
        while i < endpos:
            char = string[i]
            if string[i:i + 2] in ESCAPE_UNITS:
                spans.append([i, i + 2, True])
                i += 2
                continue
            stop = None
            if char == '{' and not no_brace_end:
                close = string.find('}', i + 1, endpos)
                if close >= 0:
                    stop = close + 1
                    # Lenticular ruby cannot contain text tags.
                    ruby_open = ruby_sep = None
                else:
                    no_brace_end = True
            elif char == '[' and not no_bracket_end:
                stop = _interpolation_end(string, i, endpos)
                if stop is None:
                    # Avoid rescanning every nested opener in an unfinished unit.
                    no_bracket_end = True
            elif char == '%':
                match = (formats if formats is not None else _NAMED_FORMAT).match(string, i, endpos)
                if match is not None:
                    stop = match.end()
            elif char == '<' and angle_tags:
                stop = _angle_end(string, i, endpos)
            elif char == '〖':
                ruby_open = len(spans)
                ruby_sep = None
                spans.append([i, i + 1, False])
            elif char in '|｜' and ruby_open is not None and ruby_sep is None:
                ruby_sep = len(spans)
                spans.append([i, i + 1, False])
            elif char == '〗' and ruby_open is not None:
                if ruby_sep is not None:
                    spans[ruby_open][2] = spans[ruby_sep][2] = True
                    spans.append([i, i + 1, True])
                ruby_open = ruby_sep = None
            if stop is not None and stop > i:
                spans.append([i, stop, True])
                i = stop
            else:
                i += 1
        for start, stop, enabled in spans:
            if enabled:
                yield TextTokenMatch(string, start, stop, pos, endpos)

    def findall(self, string, pos=0, endpos=None, **kwargs):
        return [match.group() for match in self.finditer(string, pos, endpos, **kwargs)]

    def search(self, string, pos=0, endpos=None, **kwargs):
        return next(self.finditer(string, pos, endpos, **kwargs), None)

    def match(self, string, pos=0, endpos=None, **kwargs):
        found = self.search(string, pos, endpos, **kwargs)
        return found if found is not None and found.start() == max(0, pos) else None

    def fullmatch(self, string, pos=0, endpos=None, **kwargs):
        found = self.match(string, pos, endpos, **kwargs)
        end = len(string) if endpos is None else min(len(string), max(0, endpos))
        return found if found is not None and found.end() == end else None

    def sub(self, repl, string, count=0, **kwargs):
        if count < 0:
            return string
        parts = []
        cursor = replaced = 0
        for found in self.finditer(string, **kwargs):
            if count and replaced >= count:
                break
            parts.append(string[cursor:found.start()])
            parts.append(repl(found) if callable(repl) else found.expand(repl))
            cursor = found.end()
            replaced += 1
        parts.append(string[cursor:])
        return ''.join(parts)


TOKENS = TextTokens()
