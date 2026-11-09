"""Portable Tk presentation. No optional packages or GPU/window-composition service."""
import ctypes
import os
import tkinter as tk
from tkinter import ttk


PALETTES = {
    'light': dict(bg='#fafafa', sidebar='#eceef1', field='#ffffff', log='#ffffff',
                  text='#202126', muted='#616570', line='#d9dce2', selected='#dce5f3',
                  hover='#e5e8ee', blue='#0864d9', blue_hover='#0056c2',
                  disabled='#e6e8ec', disabled_text='#858994', button='#f0f1f4'),
    'dark': dict(bg='#222225', sidebar='#2b2d33', field='#303136', log='#27282c',
                 text='#f1f2f5', muted='#b8bbc5', line='#494c55', selected='#414d63',
                 hover='#363a44', blue='#0864d9', blue_hover='#1874e9',
                 disabled='#373940', disabled_text='#8d919e', button='#383b43'),
}


def system_theme():
    if os.name == 'nt':
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                    r'Software\Microsoft\Windows\CurrentVersion\Themes\Personalize') as key:
                return 'light' if winreg.QueryValueEx(key, 'AppsUseLightTheme')[0] else 'dark'
        except OSError:
            pass
    return 'light'


def native_titlebar(window, dark):
    if os.name != 'nt':
        return
    try:
        user32 = ctypes.windll.user32
        user32.GetParent.argtypes = [ctypes.c_void_p]
        user32.GetParent.restype = ctypes.c_void_p
        hwnd = user32.GetParent(window.winfo_id())
        value = ctypes.c_int(bool(dark))
        # Documented DWM immersive dark-titlebar attribute; unsupported OS is harmless.
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            ctypes.c_void_p(hwnd), 20, ctypes.byref(value), ctypes.sizeof(value))
    except (AttributeError, OSError):
        pass


class Theme:
    def __init__(self, window, mode=None):
        self.window = window
        self.mode = mode or system_theme()
        self.style = ttk.Style(window)
        self.style.theme_use('clam')
        self.images = []
        self.elements = set()
        self._titlebar_job = None
        window.bind('<Destroy>', self._destroy, add='+')
        window.bind('<Map>', self._mapped, add='+')
        self.apply(self.mode)

    def _mapped(self, event):
        if event.widget == self.window:
            native_titlebar(self.window, self.mode == 'dark')

    def _destroy(self, event):
        if event.widget == self.window and self._titlebar_job:
            self.window.after_cancel(self._titlebar_job)
            self._titlebar_job = None

    def _button_element(self, name, normal, hover, disabled):
        element = self.mode + '.' + name
        if element not in self.elements:
            images = []
            for color in (normal, hover, disabled):
                image = tk.PhotoImage(master=self.window, width=24, height=24)
                for y in range(24):
                    dy = max(8-y, y-15, 0)
                    inset = round(8 - (64-dy*dy)**.5) if dy else 0
                    image.put(color, to=(inset, y, 24-inset, y+1))
                images.append(image)
            self.images.extend(images)
            self.style.element_create(element, 'image', images[0],
                                      ('disabled', images[2]), ('active', images[1]),
                                      border=9, sticky='nsew')
            self.elements.add(element)
        return element

    def apply(self, mode):
        self.mode = mode
        c = self.colors = PALETTES[mode]
        s = self.style
        self.window.configure(background=c['bg'])
        s.configure('.', background=c['bg'], foreground=c['text'],
                    font=('맑은 고딕', 10), bordercolor=c['line'],
                    lightcolor=c['line'], darkcolor=c['line'], focuscolor=c['blue'])
        s.configure('TFrame', background=c['bg'])
        s.configure('Sidebar.TFrame', background=c['sidebar'])
        s.configure('TLabel', background=c['bg'], foreground=c['text'])
        s.configure('Muted.TLabel', foreground=c['muted'])
        s.configure('Title.TLabel', font=('맑은 고딕', 20, 'bold'))
        s.configure('Section.TLabel', font=('맑은 고딕', 11, 'bold'))
        s.configure('Sidebar.TLabel', background=c['sidebar'], foreground=c['muted'],
                    font=('Segoe UI', 10, 'bold'))
        s.configure('TEntry', fieldbackground=c['field'], foreground=c['text'],
                    insertcolor=c['text'], padding=(10, 8), borderwidth=1,
                    selectbackground=c['blue'], selectforeground='#ffffff')
        s.map('TEntry', fieldbackground=[('readonly', c['button']), ('disabled', c['bg'])],
              foreground=[('disabled', c['muted'])],
              bordercolor=[('focus', c['blue'])], lightcolor=[('focus', c['blue'])],
              darkcolor=[('focus', c['blue'])])
        for style, fill, hover, text in (
                ('TButton', c['button'], c['hover'], c['text']),
                ('Primary.TButton', c['blue'], c['blue_hover'], '#ffffff')):
            element = self._button_element(style, fill, hover, c['disabled'])
            s.layout(style, [(element, {'sticky':'nsew', 'children':[
                ('Button.focus', {'sticky':'nsew', 'children':[
                    ('Button.padding', {'sticky':'nsew', 'children':[
                        ('Button.label', {'sticky':'nsew'})]})]})]})])
            s.configure(style, foreground=text, padding=(14, 3), anchor='center',
                        background=c['bg'], borderwidth=0, focusthickness=1,
                        font=('맑은 고딕', 10, 'bold') if style.startswith('Primary') else ('맑은 고딕', 10))
            s.map(style, foreground=[('disabled', c['disabled_text'])],
                  background=[('active', c['bg'])])
        for style in ('TRadiobutton', 'TCheckbutton'):
            s.configure(style, background=c['bg'], foreground=c['text'], padding=(0, 5),
                        indicatorbackground=c['field'], indicatorforeground=c['blue'],
                        indicatormargin=(0, 0, 8, 0))
            s.map(style, background=[('active', c['bg'])],
                  foreground=[('disabled', c['disabled_text'])],
                  indicatorbackground=[('selected', c['blue'])])
        s.configure('TSeparator', background=c['line'])
        s.configure('Horizontal.TProgressbar', troughcolor=c['bg'], background=c['blue'],
                    borderwidth=0, lightcolor=c['blue'], darkcolor=c['blue'], thickness=3)
        s.configure('Vertical.TScrollbar', background=c['button'], troughcolor=c['bg'],
                    borderwidth=0, arrowsize=12, gripcount=0)
        s.map('Vertical.TScrollbar', background=[('active', c['hover'])])
        if self._titlebar_job:
            self.window.after_cancel(self._titlebar_job)
        self._titlebar_job = self.window.after_idle(lambda: native_titlebar(self.window, mode == 'dark'))


class NavItem(tk.Canvas):
    """A keyboard-accessible navigation row, painted in the current palette."""
    def __init__(self, master, title, icon, command, theme):
        super().__init__(master, height=48, highlightthickness=0, bd=0,
                         takefocus=True, cursor='hand2')
        self.title, self.icon, self.command, self.theme = title, icon, command, theme
        self.selected = self.hover = False
        self.bind('<Configure>', self.draw)
        self.bind('<Enter>', lambda e: self.set_hover(True))
        self.bind('<Leave>', lambda e: self.set_hover(False))
        self.bind('<Button-1>', lambda e: self.invoke())
        self.bind('<Return>', lambda e: self.invoke())
        self.bind('<space>', lambda e: self.invoke())
        self.bind('<FocusIn>', self.draw)
        self.bind('<FocusOut>', self.draw)

    def invoke(self):
        self.focus_set()
        self.command()
        return 'break'

    def set_hover(self, value):
        self.hover = value
        self.draw()

    def draw(self, event=None):
        c = self.theme.colors
        self.configure(background=c['sidebar'])
        self.delete('all')
        w = self.winfo_width()
        fill = c['selected'] if self.selected else c['hover'] if self.hover else c['sidebar']
        self.create_polygon(12, 2, w-12, 2, w-2, 2, w-2, 12, w-2, 36,
                            w-2, 46, w-12, 46, 12, 46, 2, 46, 2, 36, 2, 12, 2, 2,
                            smooth=True, splinesteps=24, fill=fill, outline='')
        ink = c['blue'] if self.selected and self.theme.mode == 'light' else c['text']
        if self.icon == 'home':
            self.create_line(17, 24, 26, 16, 35, 24, fill=ink, width=2)
            self.create_line(20, 23, 20, 33, 25, 33, 25, 27, 29, 27, 29, 33, 33, 33,
                             33, 23, fill=ink, width=2)
        else:
            for y, x in ((18, 23), (25, 30), (32, 24)):
                self.create_line(17, y, 36, y, fill=ink, width=2)
                self.create_oval(x-2, y-2, x+2, y+2, outline=ink, fill=fill, width=2)
        self.create_text(49, 24, anchor='w', text=self.title, fill=c['text'],
                         font=('맑은 고딕', 11, 'bold' if self.selected else 'normal'))
        if self.focus_get() == self:
            self.create_rectangle(5, 5, w-5, 43, outline=c['blue'], dash=(2, 2))
