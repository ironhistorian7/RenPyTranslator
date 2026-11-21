"""RPG Maker compatibility facade for the shared Japanese output policy."""
from japanese_output_policy import JAPANESE, HANGUL, PLACEHOLDER, SMALL_TSU
import japanese_output_policy as shared


def enabled(cfg):
    # Preserve the original RPG-only public gate.
    return bool(cfg and cfg.get('engine') == 'rpgmaker'
                and cfg.get('source_language') in ('ja', 'japanese'))


def _visible(source, target, cfg):
    return shared.visible_view(source, target, cfg)


def small_tsu_only_source(source, cfg):
    return enabled(cfg) and shared.small_tsu_only_source(source, cfg)


def small_tsu_punctuation_result(source, target, cfg):
    return enabled(cfg) and shared.small_tsu_punctuation_result(source, target, cfg)


def classify(source, target, cfg):
    if not enabled(cfg):
        return dict(kind='not_applicable', hangul_count=0, japanese_count=0, spans=[])
    return shared.classify(source, target, cfg)


def prepare_output(source, target, cfg):
    if not enabled(cfg):
        return target, classify(source, target, cfg)
    return shared.prepare_output(source, target, cfg)
