"""Reuse explicitly approved general terms, without extra inference."""
import re

def short_text(text):
    return 1<len(text)<=64 and len(text.split())<=5 and not re.search(r'[{}\[\]%\n]|https?://|[/\\]',text)

class TermMemory:
    def __init__(self,rows,known):
        # Frequency, a UI label or a speaker label is not a terminology decision.
        # Only explicitly approved general terms may cross into dialogue.
        self.candidates={r['source'] for r in rows if short_text(r['source']) and
                         r.get('term_kind')=='general' and r.get('term_approved')}
        self.translations={}
        self.observe(known.values())

    def observe(self,entries):
        changed=False
        for entry in entries:
            if entry.get('status')=='source_fallback':continue
            source=entry.get('source','');target=entry['text']
            if source in self.candidates and source not in self.translations and len(target)<=96:
                self.translations[source]=target;changed=True
        return changed

    def relevant(self,rows):
        text='\n'.join(r['source'] for r in rows)
        found={};size=0
        for source,target in sorted(self.translations.items(),key=lambda pair:-len(pair[0])):
            if re.search(r'(?<!\w)'+re.escape(source)+r'(?!\w)',text,re.I):
                cost=len((source+target).encode('utf-8'))
                if size+cost>512:continue
                found[source]=target;size+=cost
                if len(found)==8:break
        return found
