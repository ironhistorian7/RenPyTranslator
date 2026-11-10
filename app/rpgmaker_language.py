"""Read explicit locale tables without executing a game's localization plugins."""
from collections import defaultdict
import csv
import io
import json
from pathlib import Path


_LOCALES = {
    'en':'en','eng':'en','english':'en','en-us':'en','en-gb':'en','英語':'en','英语':'en',
    'ja':'ja','jp':'ja','jpn':'ja','japanese':'ja','ja-jp':'ja','日本語':'ja','日语':'ja',
    'ko':'ko','kor':'ko','korean':'ko','ko-kr':'ko','한국어':'ko','韓国語':'ko',
    'zh':'zh','chinese':'zh','zh-cn':'zh','zh-tw':'zh','简体中文':'zh','繁體中文':'zh',
    'fr':'fr','french':'fr','de':'de','german':'de','es':'es','spanish':'es',
    'it':'it','italian':'it','ru':'ru','russian':'ru','pt':'pt','portuguese':'pt',
}
_SOURCE = ('source','original','base')
_TRANSLATION = ('translation','translated','target')


def _locale(value):
    return _LOCALES.get(str(value).strip().lower().replace('_','-'))


class SourceMapping:
    """Exact table lookups; this object never changes rows, IDs or cache files."""
    def __init__(self,language):
        self.language=_locale(language)
        if self.language not in ('en','ja'):
            raise ValueError('RPG Maker 원문 언어는 영어 또는 일본어를 선택하세요.')
        self._values=defaultdict(lambda:defaultdict(dict))
        self.tables=[]
        self.issues=[]

    def _group(self,source,values,origin):
        values={lang:text for lang,text in values.items() if isinstance(text,str) and text.strip()}
        aliases={value for value in values.values()}
        if isinstance(source,str) and source.strip():aliases.add(source)
        if not aliases:return
        for alias in aliases:
            for lang,text in values.items():
                self._values[alias][lang].setdefault(text,[]).append(dict(origin,locale=lang))

    def resolve(self,base_text):
        """Return selected model input plus provenance, or None for no exact match."""
        choices=self._values.get(base_text,{}).get(self.language,{})
        if len(choices)==1:
            text=next(iter(choices))
            return dict(text=text,language=self.language,provenance=choices[text])
        if choices or '\n' not in base_text:return None
        lines=base_text.split('\n');converted=[];provenance=[]
        for line in lines:
            values=self._values.get(line,{}).get(self.language,{})
            if len(values)>1:return None
            if values:
                value=next(iter(values));converted.append(value);provenance+=values[value]
            else:converted.append(line)
        return dict(text='\n'.join(converted),language=self.language,provenance=provenance) if provenance else None

    def runtime_aliases(self,base_text):
        """Declared native display variants for the caller's final Korean map."""
        aliases={base_text}
        for values in self._values.get(base_text,{}).values():aliases.update(values)
        if '\n' in base_text:
            lines=base_text.split('\n')
            languages={lang for line in lines for lang in self._values.get(line,{})}
            for lang in languages:
                converted=[]
                for line in lines:
                    values=self._values.get(line,{}).get(lang,{})
                    if len(values)>1:break
                    converted.append(next(iter(values)) if values else line)
                else:aliases.add('\n'.join(converted))
        return sorted(aliases)

    def report(self):
        selected=[locales.get(self.language,{}) for locales in self._values.values()]
        languages=sorted({lang for values in self._values.values() for lang in values})
        return dict(source_locale=self.language,tables=self.tables,issues=self.issues,
                    available_locales=languages,selected_locale_available=self.language in languages,
                    available_keys=len(self._values),mapped_keys=sum(len(v)==1 for v in selected),
                    ambiguous_keys=sum(len(v)>1 for v in selected))


def _record_rows(mapping,rows,filename,fmt,declared_locale=None):
    count=0
    for number,row in enumerate(rows,1):
        if not isinstance(row,dict):continue
        fields={str(key).strip().lower():key for key in row}
        source=next((row[fields[key]] for key in _SOURCE if key in fields),None)
        values={lang:row[key] for key in row if (lang:=_locale(key))}
        target=next((row[fields[key]] for key in _TRANSLATION if key in fields),None)
        language=declared_locale or _locale(row.get('language',row.get('locale','')))
        if language and isinstance(target,str):values[language]=target
        if not values or not isinstance(source,str) and len(values)<2:continue
        mapping._group(source,values,dict(file=filename,format=fmt,row=number))
        count+=1
    return count


def _json_table(mapping,document,filename):
    if isinstance(document,list):return _record_rows(mapping,document,filename,'json')
    if not isinstance(document,dict):return 0
    for wrapper in ('translations','locales'):
        if isinstance(document.get(wrapper),(dict,list)):
            return _json_table(mapping,document[wrapper],filename)
    locales={lang:value for key,value in document.items()
             if (lang:=_locale(key)) and isinstance(value,dict)}
    if locales:
        keys={key for values in locales.values() for key,value in values.items() if isinstance(value,str)}
        for key in keys:
            values={lang:table[key] for lang,table in locales.items() if isinstance(table.get(key),str)}
            mapping._group(key,values,dict(file=filename,format='json',key=key))
        return len(keys)
    count=0
    for source,row in document.items():
        if not isinstance(row,dict):continue
        values={lang:value for key,value in row.items() if (lang:=_locale(key)) and isinstance(value,str)}
        if values:
            mapping._group(source,values,dict(file=filename,format='json',key=source));count+=1
    return count


def _csv_table(mapping,text,filename,declared_locale=None):
    rows=list(csv.reader(io.StringIO(text)))
    if not rows:return 0
    fields=[value.strip().lower() for value in rows[0]]
    if any(key in _SOURCE or _locale(key) for key in fields):
        return _record_rows(mapping,[dict(zip(fields,row)) for row in rows[1:]],filename,'csv',declared_locale)
    if declared_locale and all(len(row)==2 for row in rows):
        return _record_rows(mapping,[dict(source=row[0],translation=row[1]) for row in rows],
                            filename,'csv',declared_locale)
    return 0


def _safe(root,relative):
    relative=Path(relative)
    target=(root/relative).resolve()
    if relative.is_absolute() or not target.is_relative_to(root):
        raise ValueError('번역 테이블 경로가 게임 작업 폴더 밖을 가리킵니다.')
    return target


def discover_tables(game_base,*,issues=None):
    """Read active known plugin declarations; return paths relative to game_base."""
    base=Path(game_base).resolve()
    registry=_safe(base,'js/plugins.js')
    if not registry.is_file():return []
    from gameloc_vendor import load
    load()
    from gameloc.containers import _plugins_doc
    try:
        plugins=_plugins_doc(registry.read_text(encoding='utf-8-sig'))
    except (OSError,ValueError,UnicodeError) as exc:
        if issues is not None:
            issues.append(dict(file='js/plugins.js',type=type(exc).__name__,error=str(exc)))
        return []
    tables=[]
    for plugin in plugins:
        if not isinstance(plugin,dict) or plugin.get('status') is not True:continue
        if str(plugin.get('name','')).casefold()!='multilanguagetranslation':continue
        parameters=plugin.get('parameters',{})
        if not isinstance(parameters,dict):continue
        filename=parameters.get('translationFile') or 'translations/ja.csv'
        locale=_locale(parameters.get('startupLanguage') or 'ja')
        if not isinstance(filename,str) or locale is None:continue
        try:relative=_safe(base,filename).relative_to(base).as_posix()
        except (OSError,ValueError) as exc:
            if issues is None:raise
            issues.append(dict(file=filename,type=type(exc).__name__,error=str(exc)))
            continue
        tables.append(dict(file=relative,locale=locale,plugin='MultiLanguageTranslation'))
    return tables


def prepare_sources(snapshot,source_language,*,base='.',files=(),declared_tables=()):
    """Load only supplied snapshot files and explicitly declared locale tables."""
    mapping=SourceMapping(source_language)
    snapshot=Path(snapshot).resolve()
    declared={str(item['file']).replace('\\','/'):item for item in declared_tables}
    game_base=_safe(snapshot,base)
    for item in discover_tables(game_base,issues=mapping.issues):
        relative=(game_base/item['file']).relative_to(snapshot).as_posix()
        declared[relative]=dict(item,file=relative)
    candidates={str(name).replace('\\','/') for name in files}|set(declared)
    for filename in sorted(candidates):
        suffix=Path(filename).suffix.lower()
        if suffix not in ('.json','.csv'):continue
        try:
            text=_safe(snapshot,filename).read_text(encoding='utf-8-sig')
            metadata=declared.get(filename,{})
            locale=_locale(metadata.get('locale',''))
            count=(_json_table(mapping,json.loads(text),filename) if suffix=='.json'
                   else _csv_table(mapping,text,filename,locale))
            if count:mapping.tables.append(dict(file=filename,format=suffix[1:],rows=count))
        except (OSError,ValueError,UnicodeError) as exc:
            mapping.issues.append(dict(file=filename,type=type(exc).__name__,error=str(exc)))
    return mapping
