# SPDX-License-Identifier: GPL-3.0-or-later
# Gameloc — 하진
"""자바스크립트 소스에서 **문자열만** 안전하게 찾아내고 갈아끼웁니다.

플러그인 ``.js`` 를 정규식 하나로 훑으면 반드시 사고가 납니다.

    // 주석 안의 don't 같은 아포스트로피가 문자열 시작으로 보입니다
    const RE = /['"]/;            // 정규식 안의 따옴표도 마찬가지입니다
    const s = `${name}님`;        // 템플릿은 안에 코드가 들어 있습니다

한 글자만 어긋나도 게임은 켜자마자 ``SyntaxError: Invalid or unexpected
token`` 으로 죽습니다. 그래서 여기서는 주석·정규식·템플릿을 구분하는 작은
토크나이저를 씁니다. 그리고 **바꾼 뒤 다시 읽어** 문자열 개수와 내용이
그대로인지 확인합니다. 조금이라도 어긋나면 그 파일은 손대지 않습니다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


class Unsafe(Exception):
    """바꾼 결과가 원래 구조와 어긋납니다. 그 파일은 손대면 안 됩니다."""


@dataclass(frozen=True)
class Lit:
    start: int          # 여는 따옴표 위치
    end: int            # 닫는 따옴표 다음 위치
    quote: str          # ' " `
    raw: str            # 따옴표 사이 원문 (이스케이프 그대로)
    subst: bool = False # 템플릿 안에 ${...} 가 있는가

    @property
    def text(self) -> str:
        """게임이 실제로 보여 주는 글자."""
        return decode(self.raw)


# 이 낱말 뒤의 ``/`` 는 나눗셈이 아니라 정규식입니다.
_KEYWORD_BEFORE_RE = {
    "return", "typeof", "instanceof", "in", "of", "new", "delete", "void",
    "throw", "case", "do", "else", "yield", "await",
}
_PUNCT_BEFORE_RE = set("(,=:[!&|?{};+-*%~^<>")
_WORD_TAIL = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]*$")


def _regex_allowed(text: str, i: int) -> bool:
    """``text[i]`` 의 ``/`` 가 정규식 시작인지."""
    j = i - 1
    while j >= 0 and text[j] in " \t\r\n":
        j -= 1
    if j < 0:
        return True
    ch = text[j]
    if ch in _PUNCT_BEFORE_RE:
        return True
    if ch.isalnum() or ch in "_$":
        m = _WORD_TAIL.search(text[: j + 1])
        return bool(m and m.group(0) in _KEYWORD_BEFORE_RE)
    return False


def _skip_string(text: str, i: int) -> int | None:
    """``text[i]`` 의 따옴표부터 닫는 따옴표 다음까지. 안 닫히면 None."""
    q = text[i]
    n = len(text)
    j = i + 1
    while j < n:
        c = text[j]
        if c == "\\":
            j += 2
            continue
        if c == q:
            return j + 1
        if c in "\r\n":
            return None          # 한 줄짜리 따옴표는 줄을 못 넘습니다
        j += 1
    return None


def _skip_template(text: str, i: int) -> tuple[int, bool] | None:
    """백틱 문자열. ``${...}`` 안은 다시 코드라 재귀로 넘깁니다."""
    n = len(text)
    j = i + 1
    subst = False
    while j < n:
        c = text[j]
        if c == "\\":
            j += 2
            continue
        if c == "`":
            return j + 1, subst
        if c == "$" and j + 1 < n and text[j + 1] == "{":
            subst = True
            depth = 1
            j += 2
            while j < n and depth:
                d = text[j]
                if d in "'\"":
                    e = _skip_string(text, j)
                    j = e if e else j + 1
                    continue
                if d == "`":
                    e = _skip_template(text, j)
                    j = e[0] if e else j + 1
                    continue
                if d == "{":
                    depth += 1
                elif d == "}":
                    depth -= 1
                j += 1
            continue
        j += 1
    return None


def _skip_regex(text: str, i: int) -> int | None:
    n = len(text)
    j = i + 1
    klass = False
    while j < n:
        c = text[j]
        if c == "\\":
            j += 2
            continue
        if c in "\r\n":
            return None
        if c == "[":
            klass = True
        elif c == "]":
            klass = False
        elif c == "/" and not klass:
            j += 1
            while j < n and (text[j].isalpha()):
                j += 1
            return j
        j += 1
    return None


def literals(text: str) -> list[Lit]:
    """주석·정규식을 뺀, 진짜 문자열 리터럴 목록."""
    out: list[Lit] = []
    n = len(text)
    i = 0
    while i < n:
        c = text[i]
        if c == "/" and i + 1 < n:
            nxt = text[i + 1]
            if nxt == "/":
                j = text.find("\n", i)
                i = n if j < 0 else j + 1
                continue
            if nxt == "*":
                j = text.find("*/", i + 2)
                i = n if j < 0 else j + 2
                continue
            if _regex_allowed(text, i):
                j = _skip_regex(text, i)
                if j:
                    i = j
                    continue
            i += 1
            continue
        if c in "'\"":
            j = _skip_string(text, i)
            if j:
                out.append(Lit(i, j, c, text[i + 1: j - 1]))
                i = j
                continue
            i += 1
            continue
        if c == "`":
            r = _skip_template(text, i)
            if r:
                j, subst = r
                out.append(Lit(i, j, "`", text[i + 1: j - 1], subst))
                i = j
                continue
            i += 1
            continue
        i += 1
    return out


# ── 이스케이프 ────────────────────────────────────────────────────────────

_SIMPLE = {"n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f",
           "v": "\v", "0": "\0", "\n": "", "\r": ""}


def decode(raw: str) -> str:
    """소스에 적힌 ``\\n`` 같은 표기를 실제 글자로."""
    out = []
    i, n = 0, len(raw)
    while i < n:
        c = raw[i]
        if c != "\\" or i + 1 >= n:
            out.append(c)
            i += 1
            continue
        e = raw[i + 1]
        if e == "u":
            if i + 2 < n and raw[i + 2] == "{":
                j = raw.find("}", i + 3)
                if j > 0:
                    try:
                        out.append(chr(int(raw[i + 3: j], 16)))
                        i = j + 1
                        continue
                    except ValueError:
                        pass
            hexs = raw[i + 2: i + 6]
            if len(hexs) == 4:
                try:
                    out.append(chr(int(hexs, 16)))
                    i += 6
                    continue
                except ValueError:
                    pass
        elif e == "x":
            hexs = raw[i + 2: i + 4]
            if len(hexs) == 2:
                try:
                    out.append(chr(int(hexs, 16)))
                    i += 4
                    continue
                except ValueError:
                    pass
        if e == "\r" and i + 2 < n and raw[i + 2] == "\n":
            i += 3          # 줄 이어쓰기
            continue
        out.append(_SIMPLE.get(e, e))
        i += 2
    return "".join(out)


_ESCAPE = {"\\": "\\\\", "\n": "\\n", "\r": "\\r", "\t": "\\t",
           "\b": "\\b", "\f": "\\f", "\v": "\\v",
           "\u2028": "\\u2028", "\u2029": "\\u2029"}


def encode(value: str, quote: str) -> str:
    """실제 글자를 그 따옴표 안에 넣어도 되는 표기로.

    줄바꿈·특수문자·짝 잃은 서로게이트까지 전부 막습니다. 여기가 뚫리면
    게임이 ``SyntaxError`` 로 죽습니다.
    """
    out = []
    for ch in value:
        if ch in _ESCAPE:
            out.append(_ESCAPE[ch])
            continue
        if ch == quote:
            out.append("\\" + ch)
            continue
        o = ord(ch)
        if o < 0x20 or o == 0x7F or 0xD800 <= o <= 0xDFFF:
            out.append("\\u%04x" % o)
            continue
        out.append(ch)
    s = "".join(out)
    if quote == "`":
        s = s.replace("${", "\\${")
    return s


def replace(text: str, edits: dict[str, str]) -> tuple[str, int]:
    """문자열 리터럴만 갈아끼웁니다. 확인에 실패하면 원문 그대로 돌려줍니다.

    ``edits`` 는 {원래 글자: 새 글자}. 반환은 (새 소스, 바꾼 개수).
    """
    if not edits:
        return text, 0
    lits = literals(text)
    pieces, last, n = [], 0, 0
    want: list[str] = []
    for lit in lits:
        cur = lit.text
        new = edits.get(cur)
        if new is None or lit.subst or new == cur:
            want.append(cur)
            continue
        want.append(new)
        pieces.append(text[last: lit.start])
        pieces.append(lit.quote + encode(new, lit.quote) + lit.quote)
        last = lit.end
        n += 1
    if not n:
        return text, 0
    pieces.append(text[last:])
    out = "".join(pieces)

    # 다시 읽어 확인합니다 — 개수도 내용도 어긋나면 안 됩니다.
    after = literals(out)
    if len(after) != len(lits):
        raise Unsafe("문자열 개수가 %d → %d 로 어긋났습니다" % (len(lits), len(after)))
    for lit, expect in zip(after, want):
        if lit.text != expect:
            raise Unsafe("바꾼 문자열이 다시 읽히지 않습니다: %.40r" % expect)
    return out, n
