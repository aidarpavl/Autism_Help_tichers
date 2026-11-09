"""Общие элементы интерфейса."""
from __future__ import annotations

import html
import io
import json

import streamlit as st
import streamlit.components.v1 as components
from PIL import Image

MAX_UPLOAD = 3 * 1024 * 1024
MOODS = {"Радостно": "😊", "Спокойно": "😌", "Грустно": "😢", "Сердито": "😠", "Страшно": "😨", "Устал": "😴"}

CSS = """
<style>
.stButton button, .stDownloadButton button, .stFormSubmitButton button { width: 100%; }
.big-emoji { font-size: 5rem; text-align: center; line-height: 1.2; }
.big-text { font-size: 1.7rem; text-align: center; font-weight: 600; }
.strip { background: #fff; border: 2px solid #2F6F73; border-radius: 12px; padding: 14px;
         min-height: 72px; font-size: 1.5rem; }
</style>
"""


def inject_css() -> None:
    st.markdown(CSS, unsafe_allow_html=True)


def speak(text: str, label: str = "🔊 Послушать", height: int = 56) -> None:
    """Озвучка синтезом речи браузера; кнопка внутри iframe даёт разрешение на звук."""
    payload = json.dumps(text, ensure_ascii=False).replace("<", "\\u003c")
    components.html(f"""
    <button id="s" style="width:100%;height:46px;font-size:18px;border-radius:8px;border:1px solid #2F6F73;
      background:#2F6F73;color:#fff;cursor:pointer">{html.escape(label)}</button>
    <script>
    document.getElementById('s').onclick = function() {{
      if (!window.speechSynthesis) return;
      speechSynthesis.cancel();
      var u = new SpeechSynthesisUtterance({payload});
      u.lang = 'ru-RU'; u.rate = 0.85;
      speechSynthesis.speak(u);
    }};
    </script>""", height=height)


def big(emoji: str, text: str = "") -> None:
    st.markdown(f'<div class="big-emoji">{html.escape(emoji)}</div>', unsafe_allow_html=True)
    if text:
        st.markdown(f'<div class="big-text">{html.escape(text)}</div>', unsafe_allow_html=True)


def read_image(upload) -> bytes | None:
    """Уменьшает картинку, чтобы база и её резервные копии оставались небольшими."""
    if upload is None:
        return None
    try:
        img = Image.open(io.BytesIO(upload.getvalue()))
        img = img.convert("RGB")
        img.thumbnail((512, 512))
        out = io.BytesIO()
        img.save(out, "JPEG", quality=82)
        return out.getvalue()
    except Exception:
        st.warning("Не удалось прочитать изображение, карточка сохранена без него.")
        return None


def read_audio(upload) -> bytes | None:
    if upload is None:
        return None
    data = upload.getvalue()
    if len(data) > MAX_UPLOAD:
        st.warning("Аудиофайл больше 3 МБ и не был сохранён.")
        return None
    return data


def pick_student(students: list[dict], key: str) -> dict | None:
    if not students:
        st.info("Пока нет учеников. Добавьте их в разделе «Ученики» или передайте им код учителя.")
        return None
    return st.selectbox("Ученик", students, key=key, format_func=lambda s: s["full_name"])
