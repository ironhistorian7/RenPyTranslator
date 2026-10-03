"""Portable desktop presentation; all work remains an explicit CLI invocation."""
import json
import os
from pathlib import Path
import queue
import subprocess
import threading
import time
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from app_paths import ROOT, cli_command
from task_plan import TASKS, FIXES, arguments, plan
from gui_theme import Theme, NavItem, system_theme


class App:
    def __init__(self, window, theme=None):
        self.window = window
        self.proc = None
        self.events = queue.Queue()
        self.cancel_path = self.result = None
        self._theme_override = theme
        self._theme_checked = time.monotonic()
        self._poll_id = None
        self._closing = False
        window.title('RenPyTranslator')
        window.geometry('1080x830')
        window.minsize(880, 700)
        self.theme = Theme(window, theme)
        self.kind = tk.StringVar(value='원본 게임 폴더')
        self.path = tk.StringVar()
        self.output = tk.StringVar(value='project')
        self.suffix = tk.StringVar(value='-kr')
        self.scale = tk.StringVar(value='default')
        self.corner = tk.StringVar(value='right')
        self.margin = tk.StringVar(value='12')
        self.source_language = tk.StringVar(value='영어')
        settings = ROOT / 'data/gui-settings.json'
        if settings.exists():
            try:
                data = json.loads(settings.read_text(encoding='utf-8'))
                from source_language import LANGUAGES,normalize
                self.source_language.set(LANGUAGES[normalize(data.get('source_language'))])
                for var, key, default in ((self.output, 'output', 'project'),
                        (self.suffix, 'suffix', '-kr'), (self.scale, 'scale', 'default'),
                        (self.corner, 'language_corner', 'right'), (self.margin, 'language_margin', 12)):
                    var.set(data.get(key, default))
            except (OSError, ValueError):
                pass
        self.advanced_on = tk.BooleanVar(value=False)
        self.tasks = {key: tk.BooleanVar(value=False) for key in TASKS}
        self.repairs_open = False
        self.summary = tk.StringVar()
        self.output_preview = tk.StringVar()
        self.status = tk.StringVar(value='대기 중')

        window.columnconfigure(1, weight=1)
        window.rowconfigure(0, weight=1)
        self.sidebar = ttk.Frame(window, style='Sidebar.TFrame', width=216, padding=(12, 20))
        self.sidebar.grid(row=0, column=0, sticky='nsew')
        self.sidebar.grid_propagate(False)
        self.sidebar.columnconfigure(0, weight=1)
        ttk.Label(self.sidebar, text='RenPyTranslator', style='Sidebar.TLabel').grid(
            row=0, column=0, sticky='w', padx=12, pady=(3, 26))
        self.basic_nav = NavItem(self.sidebar, '기본', 'home', lambda: self.navigate(False), self.theme)
        self.basic_nav.grid(row=1, column=0, sticky='ew')
        self.advanced_nav = NavItem(self.sidebar, '고급 설정', 'sliders', lambda: self.navigate(True), self.theme)
        self.advanced_nav.grid(row=2, column=0, sticky='ew', pady=(6, 0))
        self.basic_nav.selected = True

        self.main = frame = ttk.Frame(window, padding=(30, 24, 30, 20))
        frame.grid(row=0, column=1, sticky='nsew')
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(7, weight=2, minsize=110)
        header = ttk.Frame(frame)
        header.grid(row=0, column=0, sticky='ew', pady=(0, 18))
        header.columnconfigure(0, weight=1)
        self.heading = ttk.Label(header, text='기본', style='Title.TLabel')
        self.heading.grid(row=0, column=0, sticky='w')
        self.stop = ttk.Button(header, text='취소', command=self.cancel, state='disabled')
        self.stop.grid(row=0, column=1, padx=(8, 10))
        self.start = ttk.Button(header, text='번역 시작', command=self.run, style='Primary.TButton')
        self.start.grid(row=0, column=2)
        ttk.Separator(frame).grid(row=1, column=0, sticky='ew')

        source = ttk.Frame(frame, padding=(0, 18, 0, 14))
        source.grid(row=2, column=0, sticky='ew')
        source.columnconfigure(0, weight=1)
        ttk.Label(source, text='번역할 게임', style='Section.TLabel').grid(row=0, column=0, sticky='w')
        kinds = ttk.Frame(source)
        kinds.grid(row=1, column=0, columnspan=2, sticky='w', pady=(5, 7))
        self.kind_buttons = []
        for title, value in (('원본 게임', '원본 게임 폴더'), ('기존 프로젝트', '기존 프로젝트 / 결과 폴더')):
            item = ttk.Radiobutton(kinds, text=title, variable=self.kind, value=value,command=self.restore_language)
            item.pack(side='left', padx=(0, 24))
            self.kind_buttons.append(item)
        self.entry = ttk.Entry(source, textvariable=self.path)
        self.entry.grid(row=2, column=0, sticky='ew')
        self.entry.bind('<FocusOut>',lambda event:self.restore_language())
        self.browse = ttk.Button(source, text='찾아보기', command=self.select)
        self.browse.grid(row=2, column=1, padx=(10, 0))
        self.source_help = ttk.Label(source, text='원본 게임 또는 기존 번역 결과 폴더를 선택하세요.', style='Muted.TLabel')
        self.source_help.grid(row=3, column=0, columnspan=2, sticky='w', pady=(7, 0))
        langrow=ttk.Frame(source)
        langrow.grid(row=4,column=0,columnspan=2,sticky='w',pady=(10,0))
        ttk.Label(langrow,text='원문 언어').pack(side='left',padx=(0,12))
        self.language_choice=ttk.Combobox(langrow,textvariable=self.source_language,values=('영어','일본어'),state='readonly',width=12)
        self.language_choice.pack(side='left')
        ttk.Label(langrow,text='한국어로 번역합니다.',style='Muted.TLabel').pack(side='left',padx=12)

        self.pages = ttk.Frame(frame)
        self.pages.grid(row=3, column=0, sticky='nsew')
        self.pages.columnconfigure(0, weight=1)
        self.pages.rowconfigure(0, weight=1)
        self.basic = ttk.Frame(self.pages, padding=(0, 8, 0, 12))
        self.basic.grid(row=0, column=0, sticky='nsew')
        self.basic.columnconfigure(0, weight=1)
        ttk.Label(self.basic, text='결과 저장 위치', style='Section.TLabel').grid(row=0, column=0, sticky='w')
        ttk.Entry(self.basic, textvariable=self.output_preview, state='readonly').grid(
            row=1, column=0, sticky='ew', pady=(10, 6))
        ttk.Label(self.basic, text='저장 위치는 고급 설정에서 바꿀 수 있습니다.', style='Muted.TLabel').grid(
            row=2, column=0, sticky='w')

        self.advanced_host = ttk.Frame(self.pages)
        self.advanced_host.columnconfigure(0, weight=1)
        self.advanced_host.rowconfigure(0, weight=1)
        self.advanced_canvas = tk.Canvas(self.advanced_host, height=300, bd=0, highlightthickness=0)
        self.advanced_canvas.grid(row=0, column=0, sticky='nsew')
        adv_scroll = ttk.Scrollbar(self.advanced_host, command=self.advanced_canvas.yview)
        adv_scroll.grid(row=0, column=1, sticky='ns', padx=(8, 0))
        self.advanced_canvas.configure(yscrollcommand=adv_scroll.set)
        self.advanced = ttk.Frame(self.advanced_canvas, padding=(0, 6, 8, 12))
        self._advanced_item = self.advanced_canvas.create_window((0, 0), window=self.advanced, anchor='nw')
        self.advanced.columnconfigure(1, weight=1)
        self.advanced.bind('<Configure>', self._scroll_region)
        self.advanced_canvas.bind('<Configure>', self._scroll_width)
        self.window.bind('<MouseWheel>', self._wheel, add='+')
        self._build_advanced()

        self.summary_label = ttk.Label(frame, textvariable=self.summary, style='Muted.TLabel', wraplength=660)
        self.summary_label.grid(row=4, column=0, sticky='ew', pady=(8, 10))
        progress_host = ttk.Frame(frame, height=3)
        progress_host.grid(row=5, column=0, sticky='ew', pady=(0, 12))
        self.progress = ttk.Progressbar(progress_host, mode='determinate', value=0)
        self.progress.place(x=0, y=0, relwidth=1, height=3)
        loghead = ttk.Frame(frame)
        loghead.grid(row=6, column=0, sticky='ew', pady=(0, 8))
        loghead.columnconfigure(0, weight=1)
        ttk.Label(loghead, text='작업 기록', style='Section.TLabel').grid(row=0, column=0, sticky='w')
        ttk.Label(loghead, textvariable=self.status, style='Muted.TLabel').grid(row=0, column=1, padx=12)
        self.open_button = ttk.Button(loghead, text='결과 폴더 열기', command=self.open_result, state='disabled')
        self.open_button.grid(row=0, column=2)
        logframe = ttk.Frame(frame)
        logframe.grid(row=7, column=0, sticky='nsew')
        logframe.rowconfigure(0, weight=1)
        logframe.columnconfigure(0, weight=1)
        self.log = tk.Text(logframe, wrap='word', state='disabled', height=9, bd=0,
                           highlightthickness=1, padx=14, pady=12, spacing1=2, spacing3=3,
                           font=('맑은 고딕', 10), undo=False)
        self.log.grid(row=0, column=0, sticky='nsew')
        scroll = ttk.Scrollbar(logframe, command=self.log.yview)
        scroll.grid(row=0, column=1, sticky='ns')
        self.log.configure(yscrollcommand=scroll.set)
        self.main.bind('<Configure>', self._content_width)
        for var in (self.path, self.output, self.suffix):
            var.trace_add('write', self.update_summary)
        self._paint()
        self.update_summary()
        self.write('게임 폴더를 선택한 뒤 번역을 시작하세요.\n기존 번역을 보정하려면 고급 설정에서 필요한 작업을 선택하세요.\n')
        window.protocol('WM_DELETE_WINDOW', self.close)
        self._poll_id = window.after(100, self.poll)

    def _build_advanced(self):
        frame = self.advanced
        ttk.Label(frame, text='실행할 작업', style='Section.TLabel').grid(row=0, column=0, columnspan=3, sticky='w')
        ttk.Label(frame, text='번역은 이어서 진행하고, 재번역은 기존 결과를 보관하고 새로 번역합니다.', style='Muted.TLabel').grid(
            row=1, column=0, columnspan=3, sticky='w', pady=(5, 8))
        choices = ttk.Frame(frame)
        choices.grid(row=2, column=0, columnspan=3, sticky='ew')
        choices.columnconfigure((0, 1), weight=1)
        for index, (key, title) in enumerate((k, v) for k, v in TASKS.items() if k not in FIXES):
            ttk.Checkbutton(choices, text=title, variable=self.tasks[key], command=lambda k=key: self.select_task(k)).grid(
                row=index//2, column=index % 2, sticky='w', padx=(0, 16))
        ttk.Separator(frame).grid(row=3, column=0, columnspan=3, sticky='ew', pady=16)
        ttk.Label(frame, text='저장 및 표시', style='Section.TLabel').grid(row=4, column=0, columnspan=3, sticky='w', pady=(0, 8))
        for row, (label, var) in enumerate((('결과 저장 위치', self.output), ('폴더 접미사', self.suffix),
                                            ('대화창 높이 배율', self.scale)), start=5):
            ttk.Label(frame, text=label).grid(row=row, column=0, sticky='w', padx=(0, 18), pady=5)
            ttk.Entry(frame, textvariable=var).grid(row=row, column=1, sticky='ew', pady=5)
        ttk.Button(frame, text='찾아보기', command=self.select_output).grid(row=5, column=2, padx=(10, 0))
        ttk.Label(frame, text='언어 패널').grid(row=8, column=0, sticky='w', pady=8)
        panel = ttk.Frame(frame)
        panel.grid(row=8, column=1, columnspan=2, sticky='w')
        ttk.Radiobutton(panel, text='왼쪽 아래', variable=self.corner, value='left').pack(side='left', padx=(0, 8))
        ttk.Radiobutton(panel, text='오른쪽 아래', variable=self.corner, value='right').pack(side='left')
        ttk.Entry(panel, textvariable=self.margin, width=4).pack(side='left', padx=(10, 5))
        ttk.Label(panel, text='px').pack(side='left')
        self.advanced_help = ttk.Label(frame,
            text='높이: default는 게임 기본값, 1.2는 기본 높이의 120%입니다.\n상대 경로는 이 프로그램 폴더를 기준으로 저장합니다.',
            style='Muted.TLabel', wraplength=590)
        self.advanced_help.grid(row=9, column=0, columnspan=3, sticky='w', pady=(4, 12))
        ttk.Separator(frame).grid(row=10, column=0, columnspan=3, sticky='ew', pady=(4, 14))
        self.repairs_button = ttk.Button(frame, text='▸ 번역 보정', command=self.toggle_repairs)
        self.repairs_button.grid(row=11, column=0, columnspan=3, sticky='w')
        self.repairs_help = ttk.Label(frame,
            text='번역 결과에서 문제가 보이면 펼쳐 보세요. 필요한 부분만 골라 다시 적용할 수 있습니다.',
            style='Muted.TLabel', wraplength=590)
        self.repairs_help.grid(row=12, column=0, columnspan=3, sticky='w', pady=(7, 10))
        self.repairs = ttk.Frame(frame, padding=(4, 2))
        self.repairs.columnconfigure((0, 1), weight=1)
        for index, (key, title) in enumerate((k, v) for k, v in TASKS.items() if k in FIXES):
            ttk.Checkbutton(self.repairs, text=title, variable=self.tasks[key], command=self.update_summary).grid(
                row=index//2, column=index % 2, sticky='w', padx=(0, 16), pady=2)
        def bind_children(parent):
            for child in parent.winfo_children():
                child.bind('<FocusIn>', self._reveal_focus, add='+')
                bind_children(child)
        bind_children(frame)

    def _reveal_focus(self, event):
        if not self.advanced_on.get():
            return
        self.window.update_idletasks()
        top = event.widget.winfo_rooty() - self.advanced.winfo_rooty()
        bottom = top + event.widget.winfo_height()
        visible = self.advanced_canvas.canvasy(0)
        height = self.advanced_canvas.winfo_height()
        total = max(1, self.advanced.winfo_height())
        if top < visible:
            self.advanced_canvas.yview_moveto(max(0, top-8)/total)
        elif bottom > visible+height:
            self.advanced_canvas.yview_moveto(max(0, bottom-height+8)/total)

    def _scroll_region(self, event=None):
        self.advanced_canvas.configure(scrollregion=self.advanced_canvas.bbox('all'))

    def _scroll_width(self, event):
        self.advanced_canvas.itemconfigure(self._advanced_item, width=event.width)
        for label in (self.advanced_help, self.repairs_help):
            label.configure(wraplength=max(250, event.width-15))

    def _wheel(self, event):
        if not self.advanced_on.get():
            return
        widget = self.window.winfo_containing(event.x_root, event.y_root)
        while widget is not None:
            if widget == self.advanced_host:
                self.advanced_canvas.yview_scroll(-int(event.delta/120), 'units')
                return 'break'
            widget = getattr(widget, 'master', None)

    def _content_width(self, event):
        width = max(300, event.width-60)
        self.summary_label.configure(wraplength=width)
        self.source_help.configure(wraplength=width)

    def _paint(self):
        c = self.theme.colors
        self.log.configure(background=c['log'], foreground=c['text'], insertbackground=c['text'],
                           highlightbackground=c['line'], highlightcolor=c['blue'],
                           selectbackground=c['blue'], selectforeground='#ffffff')
        self.advanced_canvas.configure(background=c['bg'])
        self.basic_nav.draw()
        self.advanced_nav.draw()

    def select(self):
        path = filedialog.askdirectory(parent=self.window, title=self.kind.get())
        if path:
            self.path.set(path)
            self.restore_language()

    def restore_language(self):
        if self.proc or not self.path.get().strip():return
        from desktop_bridge import project_language
        from source_language import LANGUAGES
        try:
            result=project_language({'path':self.path.get(),'source':self.kind.get()=='원본 게임 폴더'},root=ROOT)
            if result['source_language']:self.source_language.set(LANGUAGES[result['source_language']])
        except (OSError,ValueError,KeyError):pass

    def select_output(self):
        path = filedialog.askdirectory(parent=self.window, title='결과 저장 위치')
        if path:
            self.output.set(path)

    def navigate(self, advanced):
        if self.advanced_on.get() != advanced:
            self.advanced_on.set(advanced)
            self.toggle()

    def toggle(self):
        if self.advanced_on.get():
            # Preserve the existing deliberate-selection rule when entering advanced mode.
            for var in self.tasks.values():
                var.set(False)
            self.repairs_open = False
            self.repairs.grid_remove()
            self.basic.grid_remove()
            self.advanced_host.grid(row=0, column=0, sticky='nsew')
            self.advanced_canvas.yview_moveto(0)
            self.main.rowconfigure(3, weight=3, minsize=150)
            self.heading.configure(text='고급 설정')
        else:
            self.advanced_host.grid_remove()
            self.basic.grid(row=0, column=0, sticky='nsew')
            self.main.rowconfigure(3, weight=0, minsize=0)
            self.heading.configure(text='기본')
        self.basic_nav.selected = not self.advanced_on.get()
        self.advanced_nav.selected = self.advanced_on.get()
        self.basic_nav.draw()
        self.advanced_nav.draw()
        self.update_summary()

    def toggle_repairs(self):
        self.repairs_open = not self.repairs_open
        if self.repairs_open:
            self.repairs.grid(row=13, column=0, columnspan=3, sticky='ew')
        else:
            self.repairs.grid_remove()
        self.update_summary()

    def select_task(self, key):
        if key in ('run', 'retranslate') and self.tasks[key].get():
            self.tasks['retranslate' if key == 'run' else 'run'].set(False)
        self.update_summary()

    def selected(self):
        return [key for key, var in self.tasks.items() if var.get()] if self.advanced_on.get() else ['run']

    def update_summary(self, *args):
        root = Path(self.output.get() or 'project')
        root = root if root.is_absolute() else ROOT/root
        selected = self.selected()
        repair_count = sum(self.tasks[key].get() for key in FIXES)
        self.repairs_button.configure(text=('▾' if self.repairs_open else '▸')+' 번역 보정'+
                                      (f' · {repair_count}개 선택' if repair_count else ''))
        jobs = plan(selected) if selected else []
        self.output_preview.set(str(root / ('<게임명>'+self.suffix.get())))
        self.summary.set(('실행: '+', '.join(TASKS[k] for k in jobs) if jobs else '실행할 작업을 하나 이상 선택하세요.')
                         if self.advanced_on.get() else '')
        self.start.configure(text='선택 작업 실행' if self.advanced_on.get() else '번역 시작',
                             state='normal' if selected and not self.proc else 'disabled')

    def write(self, text):
        self.log.configure(state='normal')
        self.log.insert('end', text)
        self.log.see('end')
        self.log.configure(state='disabled')

    def run(self):
        if self.proc:
            return
        try:
            args = arguments(self.selected(), self.path.get(), self.kind.get() == '원본 게임 폴더',
                             self.output.get(), self.suffix.get(), self.scale.get(), self.corner.get(), self.margin.get(),
                             'japanese' if self.source_language.get()=='일본어' else 'english')
            scale = self.scale.get().strip() or 'default'
            if scale != 'default' and not .25 <= float(scale) <= 4:
                raise ValueError('높이 배율은 0.25~4 또는 default입니다.')
        except ValueError as exc:
            messagebox.showerror('설정 확인', str(exc), parent=self.window)
            return
        from engine import save_json
        save_json(ROOT/'data/gui-settings.json', {'output': self.output.get(), 'suffix': self.suffix.get(),
                  'scale': scale, 'language_corner': self.corner.get(), 'language_margin': int(self.margin.get()),
                  'source_language':'japanese' if self.source_language.get()=='일본어' else 'english'})
        control = ROOT/'data/control'
        control.mkdir(parents=True, exist_ok=True)
        self.cancel_path = control/(str(os.getpid())+'-'+str(time.time_ns())+'.cancel')
        env = dict(os.environ, RPT_CANCEL_FILE=str(self.cancel_path), PYTHONUTF8='1', PYTHONUNBUFFERED='1')
        try:
            self.proc = subprocess.Popen(cli_command()+args, cwd=ROOT, env=env, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace',
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        except OSError as exc:
            messagebox.showerror('실행 실패', str(exc), parent=self.window)
            self.proc = None
            return
        self.result = None
        self.open_button.configure(state='disabled')
        self.start.configure(state='disabled')
        self.language_choice.configure(state='disabled')
        self.stop.configure(state='normal')
        self.status.set('작업 중')
        self.progress.configure(mode='indeterminate')
        self.progress.start(12)
        self.write('\n실행: '+subprocess.list2cmdline(args)+'\n')
        proc = self.proc

        def read():
            for line in proc.stdout:
                self.events.put(('log', line))
            self.events.put(('done', proc.wait()))
        threading.Thread(target=read, daemon=True).start()

    def cancel(self):
        if self.proc and self.cancel_path:
            self.cancel_path.touch()
            self.stop.configure(state='disabled')
            self.status.set('취소 요청 중')
            self.write('취소 요청: 현재 작업과 사용한 모델을 정리합니다.\n')

    def poll(self):
        if self._poll_id:
            self.window.after_cancel(self._poll_id)
            self._poll_id = None
        if self._closing:
            return
        if self._theme_override is None and time.monotonic()-self._theme_checked > 2:
            self._theme_checked = time.monotonic()
            current = system_theme()
            if current != self.theme.mode:
                self.theme.apply(current)
                self._paint()
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == 'log':
                    self.write(value)
                    if value.startswith('Output folder: '):
                        self.result = Path(value.partition(': ')[2].strip())
                        self.open_button.configure(state='normal')
                else:
                    self.write('완료.\n' if value == 0 else ('취소됨.\n' if value == 130 else '작업 실패. 위 로그를 확인하세요.\n'))
                    self.status.set('완료' if value == 0 else '취소됨' if value == 130 else '작업 실패')
                    self.proc = None
                    self.language_choice.configure(state='readonly')
                    self.progress.stop()
                    self.progress.configure(mode='determinate', value=0)
                    self.update_summary()
                    self.stop.configure(state='disabled')
                    if self.cancel_path:
                        self.cancel_path.unlink(missing_ok=True)
        except queue.Empty:
            pass
        self._poll_id = self.window.after(100, self.poll)

    def open_result(self):
        if self.result and self.result.is_dir():
            os.startfile(self.result)

    def close(self):
        if self.proc:
            self.cancel()
            self.write('정리가 끝나면 창을 닫아주세요.\n')
            return
        self._closing = True
        if self._poll_id:
            self.window.after_cancel(self._poll_id)
        self.window.destroy()


def main():
    window = tk.Tk()
    App(window)
    window.mainloop()


if __name__ == '__main__':
    main()
