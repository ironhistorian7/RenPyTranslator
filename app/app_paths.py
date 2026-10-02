"""Locations shared by source-mode CLI and the portable executables."""
from pathlib import Path
import sys

ROOT=Path(sys.executable).resolve().parent if getattr(sys,'frozen',False) else Path(__file__).resolve().parents[1]


def cli_command():
    if getattr(sys,'frozen',False):return [str(ROOT/'RenPyTranslator-cli.exe')]
    interpreter=ROOT/'.venv/Scripts/python.exe'
    return [str(interpreter) if interpreter.exists() else sys.executable,'-B','-X','utf8',str(ROOT/'app/portable_cli.py')]


def resource(name):
    return Path(__file__).with_name(name)
