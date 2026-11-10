"""Pure output-reserve estimates from the items the model actually receives.

Known source strings can be tokenized. Their future Korean translation cannot:
the expansion margin below is an estimate, not a measured output-token count.
The request-window fitter decides whether this reserve fits; never silently
shrink it to num_ctx here, which would turn an oversized item into truncation.
"""
import math

from source_context import token_estimate


MINIMUM_OUTPUT = 1800
OUTPUT_MARGIN = 256
EXPANSION_MARGIN = 2.5


def estimate_output(inputs, cfg=None, *, count=None):
    """Return a Korean-output reserve for final model items, including their IDs.

    ``count`` may be an existing owned tokenizer's count function for these
    known input strings. No tokenizer, model, network or filesystem is opened
    here. Keep the conservative character estimate when it is larger: a source
    language tokenizing compactly does not imply equally compact Korean output.
    An internal minimum can raise the reserve after an observed output limit.
    """
    source_tokens = 0
    for item in inputs:
        record = '[' + str(item['id']) + '] ' + item['text'] + '\n'
        estimated = token_estimate(record)
        if count is not None:estimated = max(estimated, count(record))
        source_tokens += estimated
    minimum = max(MINIMUM_OUTPUT, int((cfg or {}).get('_output_minimum', MINIMUM_OUTPUT)))
    return max(minimum, math.ceil(OUTPUT_MARGIN + source_tokens * EXPANSION_MARGIN))
