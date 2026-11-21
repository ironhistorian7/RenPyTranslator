"""Extract and update only user-visible fields in KiriKiri KAGEX SCN trees."""

import warnings


def extract(root, include_defaults=False, name_aliases=None):
    """Return translatable records with exact paths; helper PSBs return []."""
    if not isinstance(root, dict) or not isinstance(root.get('scenes'), list):
        return []
    result = []
    def add(value, usage, path, internal=None, display=None):
        if isinstance(value, str) and value:
            result.append({'text': value, 'usage': usage, 'location': tuple(path),
                           'speaker_internal': internal, 'speaker_display': display})
    for i, scene in enumerate(root['scenes']):
        if not isinstance(scene, dict):
            continue
        prefix = ('scenes', i)
        add(scene.get('title'), 'title', prefix + ('title',))
        texts = scene.get('texts', [])
        if isinstance(texts, list):
            for j, row in enumerate(texts):
                if not isinstance(row, list) or len(row) not in (6, 9):
                    continue
                internal = row[0] if isinstance(row[0], str) else None
                display = row[1] if isinstance(row[1], str) else None
                add(display, 'speaker', prefix + ('texts', j, 1), internal, display)
                if include_defaults and row[1] is None and internal:
                    try:
                        from .kirikiri_names import resolve_default_name
                    except ImportError:
                        from kirikiri_names import resolve_default_name
                    default = resolve_default_name(internal, name_aliases or {})
                    if isinstance(default, str) and default:
                        result.append({'text': default, 'usage': 'speaker',
                                       'location': prefix + ('texts', j, 1),
                                       'speaker_internal': internal, 'speaker_display': None,
                                       'derived_speaker': True, 'speaker_default': default})
                add(row[2], 'dialogue', prefix + ('texts', j, 2), internal, display)
        selects = scene.get('selects', [])
        if isinstance(selects, list):
            for j, choice in enumerate(selects):
                if isinstance(choice, dict):
                    add(choice.get('text'), 'choice', prefix + ('selects', j, 'text'))
    return result


def apply(root, replacements, derive_search=None, derive_speech=None, default_speakers=None):
    """Apply {exact_path: new_text}; derive_search is required for 9-slot text.

    The callback receives translated dialogue and returns its engine search string.
    Existing string speech fields can be updated with derive_speech. Missing or
    unsupported speech values are preserved and reported via RuntimeWarning.
    """
    allowed = {item['location']: item for item in extract(root)}
    # Only exact caller-anchored null display slots are eligible for defaults.
    for path, internal in (default_speakers or {}).items():
        if (not isinstance(path, tuple) or len(path) != 5 or
                path[0] != 'scenes' or path[2] != 'texts' or path[4] != 1 or
                type(path[1]) is not int or type(path[3]) is not int or
                path[1] < 0 or path[3] < 0 or not isinstance(internal, str) or not internal):
            continue
        try:
            scenes = root['scenes']
            scene = scenes[path[1]]
            rows = scene['texts']
            row = rows[path[3]]
            if (isinstance(scenes, list) and isinstance(scene, dict) and
                    isinstance(rows, list) and isinstance(row, list) and
                    len(row) in (6, 9) and row[0] == internal and row[1] is None):
                allowed[path] = {'usage': 'speaker'}
        except (KeyError, IndexError, TypeError):
            continue
    pending = []
    for path, text in replacements.items():
        path = tuple(path)
        if path not in allowed or not isinstance(text, str):
            raise ValueError('invalid SCN translation path or value')
        item = allowed[path]
        search = None
        speech = None
        if item['usage'] == 'dialogue':
            row = root[path[0]][path[1]][path[2]][path[3]]
            if len(row) == 9 and row[7] is not None and text != row[2]:
                if not isinstance(row[7], str):
                    warnings.warn(f'Unsupported speech field at {path[:-1] + (7,)}; preserved', RuntimeWarning, stacklevel=2)
                elif derive_speech is None:
                    warnings.warn(f'No speech normalizer for {path[:-1] + (7,)}; preserved', RuntimeWarning, stacklevel=2)
                else:
                    try:
                        speech = derive_speech(text)
                        if not isinstance(speech, str):
                            raise ValueError('speech normalizer did not return a string')
                    except Exception:
                        warnings.warn(f'Speech normalization failed at {path[:-1] + (7,)}; preserved', RuntimeWarning, stacklevel=2)
                        speech = None
            if len(row) == 9 and isinstance(row[8], str) and text != row[2]:
                if derive_search is None:
                    raise ValueError('search normalization callback required')
                search = derive_search(text)
                if not isinstance(search, str):
                    raise ValueError('search normalization must return a string')
        pending.append((path, text, search, speech))
    for path, text, search, speech in pending:
        parent = root
        for part in path[:-1]: parent = parent[part]
        parent[path[-1]] = text
        if search is not None: parent[8] = search
        if speech is not None: parent[7] = speech
    return root
