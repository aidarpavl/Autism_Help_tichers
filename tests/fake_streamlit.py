"""Имитация Streamlit для прогона всех страниц без сервера.

Строже настоящего API там, где это ловит ошибки: неизвестные вызовы падают,
кнопка внутри формы и дубли виджетов дают исключение.
"""
from __future__ import annotations

import types
from datetime import date


class Stop(BaseException):
    pass


class Rerun(BaseException):
    pass


class State(dict):
    __getattr__ = dict.__getitem__
    __setattr__ = dict.__setitem__

    def __delattr__(self, k):
        del self[k]


class Upload:
    def __init__(self, name, data):
        self.name, self._d, self.size = name, data, len(data)

    def getvalue(self):
        return self._d


class Ctx:
    def __init__(self, st, kind="box"):
        self._st, self._kind = st, kind

    def __enter__(self):
        self._st.stack.append(self._kind)
        return self

    def __exit__(self, *a):
        self._st.stack.pop()

    def progress(self, *a, **k):
        pass

    def __getattr__(self, name):
        return getattr(self._st, name)


class Fake:
    def __init__(self):
        self.session_state = State()
        self.secrets = {}
        self.stack, self.buttons, self.ids, self.errors = [], [], set(), []
        self.click, self.nav, self.upload = None, None, None
        self.counter = 0
        self.column_config = types.SimpleNamespace(TextColumn=lambda *a, **k: {})

    def begin(self, click=None):
        self.stack, self.buttons, self.ids, self.errors = [], [], set(), []
        self.click = click

    def _id(self, kind, label, key, extra=None):
        wid = key if key else (kind, label, extra)
        assert wid not in self.ids, f"дубль виджета: {wid}"
        self.ids.add(wid)

    def _press(self, label, key):
        self.buttons.append(key or label)
        return self.click == (key or label)

    def button(self, label, key=None, disabled=False, type="secondary", **k):
        assert "form" not in self.stack, f"кнопка внутри формы: {label}"
        self._id("button", label, key)
        return self._press(label, key) and not disabled

    def download_button(self, label, data, file_name, key=None, **k):
        assert "form" not in self.stack
        assert isinstance(data, (bytes, str)) and file_name
        self._id("dl", label, key)
        return False

    def form_submit_button(self, label, **k):
        assert "form" in self.stack, f"отправка вне формы: {label}"
        return self._press(label, None)

    def form(self, key, clear_on_submit=False):
        assert "form" not in self.stack, "вложенная форма"
        self._id("form", key, key)
        return Ctx(self, "form")

    def expander(self, label, **k):
        assert "expander" not in self.stack, "вложенный expander"
        return Ctx(self, "expander")

    def container(self, **k):
        return Ctx(self)

    def spinner(self, *a, **k):
        return Ctx(self)

    @property
    def sidebar(self):
        return Ctx(self)

    def columns(self, spec, **k):
        return [Ctx(self) for _ in range(spec if isinstance(spec, int) else len(spec))]

    def tabs(self, labels):
        assert labels
        return [Ctx(self) for _ in labels]

    def text_input(self, label, value="", key=None, type="default", **k):
        self._id("text", label, key, value)
        if value:
            return value
        low = label.lower()
        if "код" in low or "или своё" in low:
            return ""
        if "парол" in low:
            return "password123"
        if "логин" in low:
            self.counter += 1
            return f"user{self.counter}"
        return "Тестовое значение"

    def text_area(self, label, value="", key=None, **k):
        self._id("area", label, key, value)
        return value or "Тестовый текст"

    def selectbox(self, label, options, index=0, format_func=str, key=None, **k):
        self._id("select", label, key, str(len(options)))
        options = list(options)
        for o in options:
            assert isinstance(format_func(o), str)
        return options[index] if options else None

    def multiselect(self, label, options, default=None, format_func=str, key=None):
        self._id("multi", label, key)
        options = list(options)
        for o in options:
            assert isinstance(format_func(o), str)
        for d in default or []:
            assert d in options
        return options

    def radio(self, label, options, key=None, **k):
        assert self.nav in options, self.nav
        return self.nav

    def checkbox(self, label, value=False, key=None, **k):
        self._id("check", label, key)
        return True

    def number_input(self, label, min_value=None, max_value=None, value=None, step=None, **k):
        self._id("num", label, k.get("key"))
        return value

    def slider(self, label, min_value=None, max_value=None, value=None, step=None, **k):
        self._id("slider", label, k.get("key"))
        return value

    def date_input(self, label, value=None, key=None, **k):
        self._id("date", label, key)
        assert isinstance(value, date)
        return value

    def time_input(self, label, value=None, key=None, **k):
        self._id("time", label, key)
        return value

    def file_uploader(self, label, type=None, **k):
        self._id("upload", label, k.get("key"))
        return self.upload if self.upload and self.upload.name.rsplit(".")[-1] in type else None

    def data_editor(self, df, **k):
        self._id("editor", "e", k.get("key"))
        return df

    def progress(self, value, text=None):
        assert 0 <= value <= 1, value
        return Ctx(self)

    def image(self, data, **k):
        assert data

    def audio(self, data, **k):
        assert data

    def error(self, msg, **k):
        self.errors.append(str(msg))

    def _noop(self, *a, **k):
        pass

    set_page_config = markdown = write = caption = header = subheader = title = _noop
    info = success = warning = metric = code = dataframe = line_chart = bar_chart = _noop

    def stop(self):
        raise Stop()

    def rerun(self):
        raise Rerun()

    def cache_resource(self, fn):
        return fn


def install(sys_modules) -> Fake:
    fake = Fake()
    module = types.ModuleType("streamlit")
    module.__getattr__ = lambda name: getattr(fake, name)
    components = types.ModuleType("streamlit.components")
    v1 = types.ModuleType("streamlit.components.v1")
    v1.html = lambda *a, **k: None
    components.v1 = v1
    module.components = components
    sys_modules.update({"streamlit": module, "streamlit.components": components, "streamlit.components.v1": v1})
    return fake
