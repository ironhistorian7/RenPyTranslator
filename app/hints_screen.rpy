# Only the presentation of matched story choices is changed. Original actions run.
screen _rpt_hint_choice(items, dialogue=None, **kwargs):
    if _rpt_hint_session is not None:
        $ _rpt_layout = _rpt_hint_screen_layout(items, dialogue)
        $ _rpt_rows = _rpt_layout['rows']
        $ _rpt_list = _rpt_layout['list']
        $ _rpt_panel = _rpt_layout['panel']
        $ _rpt_pad = _rpt_layout['padding']
        $ _rpt_size = _rpt_layout['size']
        $ _rpt_state = _rpt_hint_session

        viewport:
            id "rpt_hint_choices"
            xpos _rpt_list[0]
            ypos _rpt_list[1]
            xsize _rpt_list[2]
            ysize _rpt_list[3]
            yadjustment _rpt_state['list_adjustment']
            mousewheel True
            draggable True
            arrowkeys False
            pagekeys True
            scrollbars "vertical"
            vscrollbar_xsize _rpt_layout['scrollbar']
            vscrollbar_unscrollable "hide"
            vscrollbar_top_bar "#293947"
            vscrollbar_bottom_bar "#293947"
            vscrollbar_thumb "#91a7b8"
            vscrollbar_thumb_shadow None
            vbox:
                spacing _rpt_layout['gap']
                for _rpt_index, _rpt_row in enumerate(_rpt_rows):
                    if _rpt_row['action'] is not None:
                        button:
                            id ("rpt_hint_choice_%d" % _rpt_index)
                            style "button"
                            xsize (_rpt_layout['text_width'] + _rpt_pad * 2)
                            ysize _rpt_row['height']
                            padding (_rpt_pad, _rpt_pad)
                            margin (0, 0)
                            background "#14202bee"
                            hover_background "#29465aee"
                            action _rpt_row['action']
                            hovered Function(_rpt_hint_focus, _rpt_index)
                            default_focus (_rpt_index == _rpt_state['focus'])
                            text _rpt_row['caption']:
                                style "default"
                                font _rpt_hint_data['font']
                                size _rpt_size
                                color "#ffffff"
                                xmaximum _rpt_layout['text_width']
                                layout "greedy"
                                line_spacing 0
                                slow_cps 0
                    else:
                        text _rpt_row['caption']:
                            style "default"
                            font _rpt_hint_data['font']
                            size _rpt_size
                            color "#ffffff"
                            xmaximum _rpt_layout['text_width']
                            layout "greedy"
                            line_spacing 0
                            slow_cps 0

        if _rpt_panel:
            frame:
                xpos _rpt_panel[0]
                ypos _rpt_panel[1]
                xsize _rpt_panel[2]
                ysize _rpt_panel[3]
                padding (_rpt_pad, _rpt_pad)
                margin (0, 0)
                background "#101820ee"
                viewport:
                    id "rpt_hint_detail"
                    yadjustment _rpt_state['detail_adjustment']
                    mousewheel True
                    draggable True
                    scrollbars "vertical"
                    vscrollbar_xsize _rpt_layout['scrollbar']
                    vscrollbar_unscrollable "hide"
                    vscrollbar_top_bar "#293947"
                    vscrollbar_bottom_bar "#293947"
                    vscrollbar_thumb "#91a7b8"
                    vscrollbar_thumb_shadow None
                    text _rpt_hint_detail(_rpt_state['focus']):
                        style "default"
                        font _rpt_hint_data['font']
                        size _rpt_layout['hint_size']
                        color "#ffffff"
                        xmaximum (_rpt_panel[2] - _rpt_pad * 2 - _rpt_layout['scrollbar'])
                        layout "greedy"
                        line_spacing 0
                        slow_cps 0
