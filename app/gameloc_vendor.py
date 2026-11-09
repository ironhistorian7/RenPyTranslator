"""Load the pinned, source-only Gameloc package in source and portable builds."""
import sys
from app_paths import ROOT


def load():
    path = str(ROOT / 'vendor/gameloc')
    if path not in sys.path:
        sys.path.insert(0, path)

