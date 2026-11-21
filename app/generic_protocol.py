"""One structured first-pass contract for non-HY translation models."""
import json


INSTRUCTION = ('Return only a JSON object with the exact input IDs as keys and '
               'their Korean translations as string values. Never merge or omit items. '
               'Do not return item headers, Markdown or explanations.')


def schema(inputs):
    return {'type': 'object',
            'properties': {item['id']: {'type': 'string', 'minLength': 1} for item in inputs},
            'required': [item['id'] for item in inputs], 'additionalProperties': False}


def parse(content, expected_inputs, *, truncated=False):
    # A complete-looking JSON tail is not evidence of completion after a length stop.
    if truncated:return {}
    def unique(pairs):
        values = {}
        for key, value in pairs:
            if key in values:raise ValueError('Duplicate response ID: '+key)
            values[key] = value
        return values
    values = json.loads(content, object_pairs_hook=unique)
    if (not isinstance(values, dict) or not set(values).issubset(expected_inputs)
            or any(not isinstance(value, str) for value in values.values())):
        raise ValueError('Response must map exact input IDs to translation strings')
    return {key: value.strip() for key, value in values.items() if value.strip()}
