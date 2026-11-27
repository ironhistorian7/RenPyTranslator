"""Propagate exact, unambiguous reading evidence in Japanese KAGEX scripts."""

import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field

import kirikiri_ruby
import kirikiri_tokens
from source_language import normalize


_HAN = r'\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\U00020000-\U000323af'
_HIRAGANA = r'\u3041-\u309f'
_KATAKANA = r'\u30a1-\u30ff\uff66-\uff9f'
_NAME = re.compile(rf'[{_HAN}{_HIRAGANA}{_KATAKANA}々〆]+')
_IDEOGRAPH = re.compile(rf'[{_HAN}]')
_KANA = re.compile(rf'[{_HIRAGANA}{_KATAKANA}]+')
_JOINED = re.compile(rf'[{_HAN}{_KATAKANA}々〆a-zA-Z0-9]')
_HIRAGANA_CHAR = re.compile(rf'[{_HIRAGANA}]')
_HONORIFIC = re.compile(
    r'(?:さん|ちゃん|くん|さま|どの|せんせい|様|殿|君|先生)'
    r'(?=$|[^\w]|[がはをのにへともでやか]|\s)')
_SUFFIX = re.compile(
    r'(?:から|まで|より|だけ|だって|について|として|という|のこと)'
    r'|[がはをのにへとでもやか](?![ぁ-ゖ])')
_PREFIX_PARTICLE = frozenset('がはをのにへとでもやか')
_HANGUL = re.compile(r'[ㄱ-ㅣ가-힣]')
_BODY_USAGES = frozenset(('dialogue', 'choice', 'title'))
_DIALECTS = frozenset(('kag', 'kagex_psb'))


def _name_text(text):
    return isinstance(text, str) and _NAME.fullmatch(text) is not None


def _kana_key(text):
    return ''.join(
        chr(ord(char) - 0x60) if 'ァ' <= char <= 'ヶ' or char in 'ヽヾ' else char
        for char in unicodedata.normalize('NFKC', text))


@dataclass(frozen=True)
class ReadingIndex:
    readings: dict[str, str]
    ambiguous: frozenset[str]
    guarded: frozenset[str]
    aliases: dict[str, str]
    _whole_readings: dict[str, str] = field(default_factory=dict, repr=False)
    _body_approved: frozenset[str] = field(default_factory=frozenset, repr=False)
    _matcher: re.Pattern | None = field(default=None, repr=False, compare=False)


def _japanese_kirikiri(cfg):
    if not isinstance(cfg, dict) or cfg.get('engine') != 'kirikiri':
        return False
    try:
        return normalize(cfg.get('source_language')) == 'japanese'
    except ValueError:
        return False


def _scn_nameplate(location):
    return (isinstance(location, (tuple, list)) and len(location) == 5
            and location[0] == 'scenes' and type(location[1]) is int
            and location[2] == 'texts' and type(location[3]) is int
            and type(location[4]) is int and location[4] == 1
            and location[1] >= 0 and location[3] >= 0)


def collect(rows, cfg, approved_names=None, *, nameplate_names=None):
    """Index literal ruby bases and corroborated speaker aliases across a catalogue."""
    readings = defaultdict(dict)
    by_speaker = defaultdict(set)
    by_label = defaultdict(set)
    if not _japanese_kirikiri(cfg):
        return ReadingIndex({}, frozenset(), frozenset(), {})
    base_cfg = dict(cfg, _kirikiri_dialect='kagex_psb')

    for row in rows:
        dialect = row.get('dialect', 'kag')
        if dialect not in _DIALECTS:
            continue
        source = row.get('source')
        if not isinstance(source, str):
            continue
        if dialect == 'kagex_psb' and '[' in source:
            _, explicit, _ = kirikiri_ruby.prepare(source, base_cfg)
            for item in explicit:
                base, reading = item['base'], item['reading']
                readings[base].setdefault(_kana_key(reading), reading)
        speaker = row.get('speaker_internal')
        if (dialect == 'kagex_psb' and row.get('usage', row.get('kind')) in ('speaker', 'speaker_label')
                and _scn_nameplate(row.get('location')) and _name_text(source)
                and isinstance(speaker, str) and speaker):
            by_speaker[speaker].add(source)
            by_label[source].add(speaker)

    ambiguous = frozenset(base for base, forms in readings.items() if len(forms) > 1)
    whole_readings = {
        base: next(iter(forms.values())) for base, forms in readings.items() if base not in ambiguous
    }
    verified = {
        base: reading for base, reading in whole_readings.items()
        if _name_text(base) and len(base) > 1 and _IDEOGRAPH.search(base)
    }
    aliases = {
        base: target.strip()
        for base, target in {**(nameplate_names or {}), **(approved_names or {})}.items()
        if base not in ambiguous and _name_text(base)
        and isinstance(target, str) and _HANGUL.search(target)
        and '<' not in target and '>' not in target
        and not any(kirikiri_tokens.spans(target, 'kagex_psb'))
    }
    body_approved = frozenset(aliases.keys() & (approved_names or {}).keys())
    # A matching pronunciation alone cannot distinguish two characters or
    # ordinary prose. Require the engine to connect both literal nameplates.
    structured = defaultdict(set)
    for speaker, labels in by_speaker.items():
        for base in labels & aliases.keys() & whole_readings.keys():
            reading = _kana_key(whole_readings[base])
            for label in labels - {base}:
                if (by_label[label] == {speaker} and by_label[base] == {speaker}
                        and label not in aliases and label not in ambiguous
                        and _KANA.fullmatch(label) and _kana_key(label) == reading):
                    structured[label].add(aliases[base])
    aliases.update(
        (label, next(iter(targets))) for label, targets in structured.items()
        if len(targets) == 1)

    guarded = frozenset(readings)
    candidates = guarded | verified.keys() | aliases.keys()
    matcher = re.compile('|'.join(re.escape(name) for name in
                         sorted(candidates, key=lambda name: (-len(name), name)))) if candidates else None
    return ReadingIndex(verified, ambiguous, guarded, aliases, whole_readings, body_approved, matcher)


def _explicit_spans(ruby_plan):
    removed = 0
    for record in ruby_plan:
        removed += record['end'] - record['start']
        yield record['end'] - removed, record['base_end'] - removed, record


def _has_name_boundary(body, start, end):
    if start:
        previous = body[start - 1]
        if (_JOINED.fullmatch(previous)
                or (_KANA.fullmatch(body[start:end]) and _HIRAGANA_CHAR.fullmatch(previous)
                    and previous not in _PREFIX_PARTICLE)):
            return False
    following = body[end:end + 1]
    if _HONORIFIC.match(body, end):
        return True
    if _JOINED.fullmatch(following):
        return False
    if _HIRAGANA_CHAR.fullmatch(following):
        return _SUFFIX.match(body, end) is not None
    return True


def _matched_spans(body, ruby_plan, index, dialect):
    if not index or index._matcher is None:
        return
    ruby_ranges = list(_explicit_spans(ruby_plan))
    controls = list(kirikiri_tokens.spans(body, dialect))
    for match in index._matcher.finditer(body):
        start, end = match.span()
        if any(start < right and end > left for left, right in controls):
            continue
        for left, right, record in ruby_ranges:
            if start < right and end > left:
                if (start, end) == (left, right):
                    yield start, end, match[0], record['reading']
                break
        else:
            if _has_name_boundary(body, start, end):
                yield start, end, match[0], None


def evidence_and_names(source, cfg, index, *, prepared=None):
    """Return reading hints and approved substitutions from one visible-text scan."""
    body, explicit, ruby_plan = prepared if prepared is not None else kirikiri_ruby.prepare(source, cfg)
    if not isinstance(body, str) or not index or not _japanese_kirikiri(cfg):
        return explicit, []
    dialect = cfg.get('_kirikiri_dialect', cfg.get('kirikiri_dialect', 'kag'))
    if dialect not in _DIALECTS or cfg.get('_kirikiri_usage', 'dialogue') not in _BODY_USAGES:
        return explicit, []
    # Local evidence wins even when the catalogue contains another reading.
    seen = {item['base'] for item in explicit}
    evidence = list(explicit)
    substitutions = []
    for start, end, base, reading in _matched_spans(body, ruby_plan, index, dialect):
        expected = index.readings.get(base)
        target = index.aliases.get(base)
        if dialect == 'kagex_psb' and expected and base not in seen:
            evidence.append({'base': base, 'reading': expected})
            seen.add(base)
        # A local annotation may explicitly give a different reading, e.g.
        # after a project update or where a kana alias has its own ruby.
        if not target:
            continue
        whole_reading = index._whole_readings.get(base)
        if reading:
            if not whole_reading and _KANA.fullmatch(base):
                whole_reading = base
            if whole_reading and _kana_key(reading) != _kana_key(whole_reading):
                continue
        if (len(base) > 1 and base in index._body_approved
                or reading
                or _HONORIFIC.match(body, end)
                or all(c.isspace() or unicodedata.category(c).startswith('P')
                       for c in body[:start] + body[end:])):
            substitutions.append((start, end, target))
    return evidence, substitutions


def name_spans(body, ruby_plan, cfg, index):
    """Yield approved Korean substitutions at visible source offsets."""
    explicit = [{'base': record['base'], 'reading': record['reading']} for record in ruby_plan]
    yield from evidence_and_names(None, cfg, index, prepared=(body, explicit, ruby_plan))[1]


def evidence_for_row(source, cfg, index, *, prepared=None):
    """Give original ruby and exact cross-row reading hints for one request."""
    return evidence_and_names(source, cfg, index, prepared=prepared)[0]
