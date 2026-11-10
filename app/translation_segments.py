"""Replan model requests without validating or caching incomplete text fragments."""
from collections import deque
from dataclasses import dataclass, field
import math
import re

from request_budget import InputBudgetError, BatchNeedsSplit


ATOMIC = re.compile(r'https?://\S+|<rpt\d+/>|⟦(?:RPT)?\d+⟧|\{[^{}\n]*\}|\[[^\[\]\n]*\]|\\[A-Za-z]+(?:\[[^\]\n]*\])?')
ENDING = re.compile(r'[。！？!?]+[\"\'”’」』】)\]]*|\.(?:[\"\'”’」』】)\]]*)(?=\s|$)|\n[ \t]*\n+')


def split_text(text):
    """Bisect at complete sentences/paragraphs, never at soft display newlines."""
    atoms=list(ATOMIC.finditer(text));cuts=[]
    for match in ENDING.finditer(text):
        start,end=match.span()
        if any(atom.start()<=start<atom.end() or atom.start()<end<atom.end() for atom in atoms):continue
        if match[0].startswith('.'):
            # Keep initials, short abbreviations and ellipses together.
            if re.search(r'(?:\b[A-Za-z]{1,3}|\.)$',text[:start]):continue
            if end<len(text) and text[end]=='.':continue
        left=text[:start if match[0].startswith('\n') else end].rstrip();rest=text[end:]
        whitespace=text[len(left):end]+re.match(r'\s*',rest)[0]
        right=rest[len(re.match(r'\s*',rest)[0]):]
        if left and right:cuts.append((left,whitespace or ' ',right))
    return min(cuts,key=lambda parts:abs(len(parts[0])-len(parts[2]))) if cuts else None


def fragment_input(item, text):
    result=dict(item,text=text)
    # Whole-entry reading copies would ask the model to translate other chunks.
    result.pop('reading_text',None)
    result['name_hints']={k:v for k,v in item.get('name_hints',{}).items() if k in text}
    result['movable_placeholders']=[v for v in item.get('movable_placeholders',[]) if v in text]
    return result


@dataclass
class Node:
    item: dict
    parent_id: str
    minimum: int = 0
    children: list = field(default_factory=list)
    joiner: str = ''
    value: str | None = None
    error: dict | None = None

    def result(self):
        if self.error:return None,self.error
        if not self.children:return self.value,None
        values=[]
        for child in self.children:
            value,error=child.result()
            if error:return None,error
            values.append(value)
        return self.joiner.join(values),None


def execute(inputs, cfg, send, *, parents=None, event=None):
    """Send prepared text; join it before the caller's single final validation.

    ``send(items, output_minimum)`` fits the real prompt and returns parsed
    values plus truncation/metrics. Preflight splits never spend a model retry.
    Output-limit replans share the original entry's existing retry allowance.
    """
    parents=parents or {item['id']:item['id'] for item in inputs}
    roots=[Node(item,parents[item['id']]) for item in inputs];multiple_parents=len(set(parents.values()))>1
    pending=deque([roots]);attempts={};base_attempt=int(cfg.get('_item_retry_attempt',0))
    retry_limit=int(cfg.get('_item_retry_limit',2))
    terminal_parents=set()
    summary=dict(seconds=0.0,tokens=0,eval_seconds=0.0,request_ids=[],budget={},request_id=None)

    def note(kind,**details):
        if event:event(kind,**details)

    def divide(node,reason):
        pieces=split_text(node.item['text'])
        if not pieces:return False
        left,node.joiner,right=pieces
        node.children=[Node(fragment_input(node.item,text),node.parent_id) for text in (left,right)]
        for child in reversed(node.children):pending.appendleft([child])
        note('source_segment_split',id=node.item['id'],reason=reason,
             source_characters=len(node.item['text']),segment_characters=[len(left),len(right)])
        return True

    while pending:
        nodes=[node for node in pending.popleft() if node.parent_id not in terminal_parents]
        if not nodes:continue
        try:
            reply=send([node.item for node in nodes],max(node.minimum for node in nodes))
        except InputBudgetError as exc:
            if multiple_parents:
                # The outer scheduler owns independent entries and can commit
                # completed siblings before starting another model request.
                split=BatchNeedsSplit(str(exc));split.context_budget=getattr(exc,'context_budget',{})
                raise split from exc
            if len(nodes)>1:
                middle=len(nodes)//2
                pending.appendleft(nodes[middle:]);pending.appendleft(nodes[:middle])
                note('request_batch_split',reason='input_and_output_budget',items=[n.item['id'] for n in nodes])
            elif not divide(nodes[0],'input_and_output_budget'):
                nodes[0].error=dict(error_code='input_budget',error=str(exc),terminal=True,
                                    context_budget=getattr(exc,'context_budget',{}))
                terminal_parents.add(nodes[0].parent_id)
            continue
        summary['request_id']=reply['request_id'];summary['request_ids'].append(reply['request_id'])
        summary['budget']=reply['budget']
        for field in ('seconds','tokens','eval_seconds'):summary[field]+=reply.get(field) or 0
        values=dict(reply['values'])
        if not reply['truncated']:
            for node in nodes:
                if node.item['id'] in values:
                    if isinstance(values[node.item['id']],str):node.value=values[node.item['id']]
                    else:node.error=dict(error_code='response_format',error='Translation item must be a string')
                else:node.error=(dict(error_code='response_format',error=reply['parse_error']) if reply.get('parse_error') else
                    dict(error_code='missing_response_item',expected_id=node.item['id'],received_ids=list(values),
                         error='Missing response item ['+node.item['id']+']; received IDs: '+', '.join(sorted(values))))
                if node.error:terminal_parents.add(node.parent_id)
            continue
        # A length stop cannot establish that its final record is complete.
        # Earlier HY records with a following explicit header remain usable.
        if reply.get('hy') and values:values.pop(next(reversed(values)),None)
        else:values={}
        incomplete=[]
        for node in nodes:
            if node.item['id'] in values and isinstance(values[node.item['id']],str):node.value=values[node.item['id']]
            else:incomplete.append(node)
        if multiple_parents:
            # Return completed original entries now, rather than holding their
            # translations across a later transport failure in a tail request.
            for node in incomplete:
                node.error=dict(error_code='output_limit',error='Translation response reached its output limit',retry_attempt=base_attempt)
            note('output_limit',request_id=reply['request_id'],num_predict=reply['limit'],
                 accepted_model_ids=list(values),incomplete_model_ids=[n.item['id'] for n in incomplete])
            continue
        for parent in {node.parent_id for node in incomplete}:
            attempts[parent]=attempts.get(parent,base_attempt)+1
        note('output_limit',request_id=reply['request_id'],num_predict=reply['limit'],
             accepted_model_ids=list(values),incomplete_model_ids=[n.item['id'] for n in incomplete])
        for node in reversed(incomplete):
            if attempts[node.parent_id]>retry_limit:
                node.error=dict(error_code='output_limit',error='Translation reached its output limit after replanning',terminal=True)
                terminal_parents.add(node.parent_id)
            elif len(nodes)>1:
                # Isolate only the unfinished tail, preserving completed entries.
                pending.appendleft([node])
            elif not divide(node,'output_limit'):
                node.minimum=max(node.minimum+1,math.ceil(reply['limit']*1.5))
                pending.appendleft([node])
    parsed={};errors={}
    for node in roots:
        value,error=node.result()
        if value is None and error is None:
            error=dict(error_code='output_limit',error='Another segment of this item could not be completed',terminal=True)
        if error:errors[node.item['id']]=dict(error,retry_attempt=error.get('retry_attempt',attempts.get(node.parent_id,base_attempt)))
        else:parsed[node.item['id']]=value
    summary['attempts']=attempts
    return parsed,errors,summary
