"""Find incomplete Korean drafts without discarding the saved translations."""
def needs_repair(row,item,cfg,*,errors=None):
    """Visible source-script residue, excluding schema-confirmed person names."""
    if cfg.get('engine')!='rpgmaker' or cfg.get('source_language') not in ('japanese','ja'):
        return False
    if item.get('model')=='reviewed override' or item.get('status') in ('source_fallback','ui_original','literal','manual_translation'):
        return False
    if errors is not None:return bool(errors)
    text=item.get('text','')
    from rpgmaker_tokens import protect,language_errors
    if not isinstance(text,str):return False
    visible,_=protect(text)
    source,_=protect(row.get('source',''))
    return bool(language_errors(source,visible,cfg))
