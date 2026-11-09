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
            xsize (_rpt_list[2] - _rpt_layout['scrollbar'])
            ysize _rpt_list[3]
            yadjustment _rpt_state['list_adjustment']
            mousewheel True
            draggable True
            vbox:
                spacing _rpt_layout['gap']
                for _rpt_index, _rpt_row in enumerate(_rpt_rows):
                    if _rpt_row['action'] is not None:
                        button default (_rpt_index == _rpt_state['focus']):
                            id ("rpt_hint_choice_%d" % _rpt_index)
                            style "button"
                            xsize (_rpt_layout['text_width'] + _rpt_pad * 2)
                            ysize _rpt_row['height']
                            left_padding _rpt_pad
                            right_padding _rpt_pad
                            top_padding _rpt_pad
                            bottom_padding _rpt_pad
                            left_margin 0
                            right_margin 0
                            top_margin 0
                            bottom_margin 0
                            background "#14202bee"
                            hover_background "#29465aee"
                            action _rpt_row['action']
                            hovered Function(_rpt_hint_focus, _rpt_index)
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

        vbar:
            xpos (_rpt_list[0] + _rpt_list[2] - _rpt_layout['scrollbar'])
            ypos _rpt_list[1]
            xsize _rpt_layout['scrollbar']
            ysize _rpt_list[3]
            adjustment _rpt_state['list_adjustment']
            unscrollable "hide"
            top_bar "#293947"
            bottom_bar "#293947"
            thumb "#91a7b8"
            thumb_shadow None

        if _rpt_panel:
            frame:
                xpos _rpt_panel[0]
                ypos _rpt_panel[1]
                xsize _rpt_panel[2]
                ysize _rpt_panel[3]
                left_padding _rpt_pad
                right_padding _rpt_pad
                top_padding _rpt_pad
                bottom_padding _rpt_pad
                left_margin 0
                right_margin 0
                top_margin 0
                bottom_margin 0
                background "#101820ee"
                viewport:
                    id "rpt_hint_detail"
                    yadjustment _rpt_state['detail_adjustment']
                    mousewheel True
                    draggable True
                    xsize (_rpt_panel[2] - _rpt_pad * 2 - _rpt_layout['scrollbar'])
                    ysize (_rpt_panel[3] - _rpt_pad * 2)
                    text _rpt_hint_detail(_rpt_state['focus']):
                        style "default"
                        font _rpt_hint_data['font']
                        size _rpt_layout['hint_size']
                        color "#ffffff"
                        xmaximum (_rpt_panel[2] - _rpt_pad * 2 - _rpt_layout['scrollbar'])
                        layout "greedy"
                        line_spacing 0
                        slow_cps 0
                vbar:
                    xpos (_rpt_panel[2] - _rpt_pad * 2 - _rpt_layout['scrollbar'])
                    xsize _rpt_layout['scrollbar']
                    ysize (_rpt_panel[3] - _rpt_pad * 2)
                    adjustment _rpt_state['detail_adjustment']
                    unscrollable "hide"
                    top_bar "#293947"
                    bottom_bar "#293947"
                    thumb "#91a7b8"
                    thumb_shadow None
