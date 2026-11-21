"""Use Gameloc's RPG Maker grammar with the existing HY placeholder protocol."""
import re
from gameloc_vendor import load

# Only documented visual/timing escapes, never values such as N/P/V or plugin codes.
POSITION_PREFIX=re.compile(r'^(?:\\(?:PX|PY)\[-?\d+\])+',re.I)
READING_CONTROLS=re.compile(r'\\(?:PX|PY|FS|C)\[-?\d+\]|\\[.!|^><{}]',re.I)
MARKER_FRAGMENT=re.compile(r'(?:[<\[]\s*rpt\s*[_-]?\s*\d+|\brpt\s*[_-]?\s*\d+\s*/[>\]])',re.I)
KANA=re.compile(r'[\u3041-\u3096\u30a1-\u30fa\uff66-\uff9d]')
RESIDUE=re.compile(r'[\u3041-\u3096\u30a1-\u30fa\uff66-\uff9d\u3400-\u9fff\uf900-\ufaff]')


def split_outer_controls(source):
    """Keep leading absolute coordinates outside inference, restoring exact bytes."""
    match=POSITION_PREFIX.match(source)
    if match and match.end()<len(source):return match.group(),source[match.end():],''
    return '',source,''


def readable(source,cfg):
    """Semantic reference only; original controls still round-trip through protect."""
    from source_language import normalize
    text=READING_CONTROLS.sub('',source)
    # Physical message lines often cut Japanese words. This is a reference,
    # not the stored translation or a reconstruction of the screen layout.
    separator='' if normalize(cfg.get('source_language'))=='japanese' else ' '
    return separator.join(text.splitlines())


def protect(source):
    load()
    from gameloc.protect import CODE_RE
    # Literal numeric brackets in a game must not collide with wire anchors.
    from rpgmaker_protocol import WIRE
    from rpgmaker_layout import LAYOUT_RUN
    pattern=re.compile('(?i:'+WIRE.pattern+')|(?i:'+LAYOUT_RUN.pattern+')|'+CODE_RE.pattern)
    tokens=[]
    def substitute(match):
        tokens.append(match.group())
        return '<rpt%03d/>'%(len(tokens)-1)
    return pattern.sub(substitute,source),tokens


def language_errors(source,target,cfg):
    if not cfg or cfg.get('source_language') not in ('japanese','ja'):return []
    from japanese_output_policy import enabled as shared_enabled, language_errors as shared_errors
    if shared_enabled(cfg):
        return shared_errors(source,target,cfg)
    from rpgmaker_names import mask_names
    original=mask_names(source,source,cfg)
    translated=mask_names(source,target,cfg)
    errors=[]
    if RESIDUE.search(translated):errors.append('untranslated Japanese text')
    from rpgmaker_output_policy import enabled
    requires_korean=bool(KANA.search(original) or enabled(cfg) and RESIDUE.search(original))
    # validate() passes the control-protected target here. Confirmed names
    # remain excluded from residue, but still count as Korean translation.
    if requires_korean and not re.search(r'[가-힣]',target):errors.append('no Korean translation')
    return errors


def validate(source, target, cfg=None):
    from translation import PLACEHOLDER, token_signature
    load()
    from gameloc.protect import PATTERNS
    atom=re.compile(r'\r?\n[ \t]*|'+'|'.join(PATTERNS))
    def atomic(tokens):
        result=[]
        for token in tokens:
            pieces=atom.findall(token)
            result.extend(pieces if ''.join(pieces)==token else [token])
        return result
    from rpgmaker_preprocessing import canonicalize_validation
    canonical_source, canonical_target = canonicalize_validation(source, target)
    a, original = protect(canonical_source)
    b, translated = protect(canonical_target)
    errors = []
    if token_signature(atomic(original)) != token_signature(atomic(translated)):
        errors.append('RPG Maker control code mismatch')
    if source.strip() and not target.strip(): errors.append('empty')
    if PLACEHOLDER.search(target): errors.append('unrestored placeholder')
    from rpgmaker_protocol import WIRE
    if WIRE.search(target) and not WIRE.search(source):errors.append('unrestored placeholder')
    if re.search(r'<think>|</think>|```', target): errors.append('model commentary')
    visible_a=PLACEHOLDER.sub('',a); visible_b=PLACEHOLDER.sub('',b)
    if MARKER_FRAGMENT.search(visible_b) and not MARKER_FRAGMENT.search(visible_a):
        errors.append('unrestored placeholder')
    from rpgmaker_output_policy import small_tsu_punctuation_result
    allowed_punctuation = small_tsu_punctuation_result(source, target, cfg)
    if not allowed_punctuation:
        errors.extend(language_errors(visible_a,visible_b,cfg))
    if not allowed_punctuation and any(c.isalpha() for c in visible_a) and not any(c.isalnum() for c in visible_b):
        errors.append('punctuation-only translation')
    return errors
