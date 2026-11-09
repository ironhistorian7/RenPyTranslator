"""One version source for the desktop, CLI and Windows executable resources."""
import json
from pathlib import Path


def version(root=None):
    if root is None:
        from app_paths import ROOT
        root = ROOT
    root = Path(root)
    return json.loads((root / 'runtime-lock.json').read_text(encoding='utf-8-sig'))['release']['version']


def windows_resource(root):
    value = version(root)
    parts = tuple(int(p) for p in value.split('.'))
    numbers = (parts + (0, 0, 0, 0))[:4]
    return """VSVersionInfo(ffi=FixedFileInfo(filevers=%r, prodvers=%r, mask=0x3f,
flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0,0)),
kids=[StringFileInfo([StringTable('040904B0',[
StringStruct('FileDescription','RenPyTranslator'),
StringStruct('FileVersion',%r),StringStruct('ProductVersion',%r),
StringStruct('ProductName','RenPyTranslator')])]),
VarFileInfo([VarStruct('Translation',[1033,1200])])])""" % (numbers, numbers, value, value)
