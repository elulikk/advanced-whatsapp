# Advanced WhatsApp - Tema oscuro de escritorio (paleta, medidas y hoja de estilos QSS global)
#
# Criterio: aspecto de escritorio moderno y compacto. Controles de 32 px de alto con esquinas de 8 px,
# botones de icono circulares, menús y tarjetas redondeadas, márgenes en pasos de 4 px y un único color
# de acento. Los widgets usan objectName para los estilos particulares, así un estilo nunca "se filtra"
# a los hijos.
import os as _os
import sys as _sys

C = {
    "rail":        "#0d1316",   # barra de navegación
    "panel":       "#12191d",   # lista de chats, paneles laterales
    "surface":     "#182126",   # cabeceras, compositor
    "raised":      "#1f292f",   # campos de texto, botones
    "hover":       "#232e35",
    "selected":    "#2a3840",
    "border":      "#253037",
    "border_soft": "#1c252a",
    "border_hi":   "#36454d",   # borde al pasar el mouse
    "chat_bg":     "#0b1114",
    "bubble_in":   "#1c262c",
    "bubble_out":  "#0d4a3e",
    "text":        "#dce3e6",
    "text_2":      "#8a979e",
    "text_3":      "#5d6a71",
    "accent":      "#1fa37f",
    "accent_hi":   "#25b88f",
    "accent_dim":  "#15382f",
    "on_accent":   "#0b1114",   # texto e iconos sobre el acento
    "link":        "#53bdeb",
    "read":        "#53bdeb",
    "danger":      "#e5534b",
    "danger_dim":  "#3a1d1c",
    "danger_text": "#e58a84",
    "warn":        "#d9a33a",
}

# Medidas compartidas (px lógicos)
HEADER_H = 50        # alto de todas las cabeceras de panel
CONTROL_H = 32       # alto de campos, combos y botones de texto
ICON_BTN = 32        # botón de icono de cabecera
R_SM, R_MD, R_LG = 6, 8, 10   # radios: elementos internos, controles, tarjetas / menús

# Windows 11 redondea y sombrea los menús de forma nativa (ver effects.py); ahí el borde lo pone el sistema
WIN11 = _sys.platform == "win32" and _sys.getwindowsversion().build >= 22000

# Compatibilidad con código que todavía importa DARK_THEME
DARK_THEME = {
    "bg_main": C["panel"], "bg_secondary": C["surface"], "bg_chat": C["chat_bg"],
    "bubble_incoming": C["bubble_in"], "bubble_outgoing": C["bubble_out"],
    "accent_green": C["accent"], "accent_hover": C["accent_hi"],
    "text_primary": C["text"], "text_secondary": C["text_2"], "border_color": C["border"],
    "input_bg": C["raised"], "badge_green": C["accent"], "unread_counter_bg": C["accent"],
    "unread_counter_text": C["on_accent"], "selection_bg": C["selected"], "danger_red": C["danger"],
}

FONT_FAMILY = "Segoe UI"

_ASSETS = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))), "assets")
ARROW_DOWN = _os.path.join(_ASSETS, "arrow_down.svg").replace("\\", "/")
CHECK_MARK = _os.path.join(_ASSETS, "check.svg").replace("\\", "/")

_POPUP_BORDER = "none" if WIN11 else f"1px solid {C['border']}"

# Ítems de menú: Qt dibuja el icono en el borde del ítem (ignora su padding) y el texto a padding-left + 20 px
# (icono de 16 + 4). Con «left» el icono queda a 10 px del borde y a 10 px del texto.
MENU_QSS = f"""
QMenu {{
    background-color: {C['surface']};
    border: {_POPUP_BORDER};
    padding: 5px;
}}
QMenu::item {{
    color: {C['text']};
    padding: 7px 26px 7px 16px;
    border-radius: {R_SM}px;
}}
QMenu::item:selected {{ background-color: {C['selected']}; }}
QMenu::item:disabled {{ color: {C['text_3']}; }}
QMenu::icon {{ left: 10px; }}
QMenu::separator {{ height: 1px; background: {C['border']}; margin: 5px 10px; }}
"""

QSS_STYLESHEET = f"""
QMainWindow, QDialog {{
    background-color: {C['panel']};
    color: {C['text']};
}}
QWidget {{
    color: {C['text']};
    font-family: "{FONT_FAMILY}";
    font-size: 13px;
}}
QToolTip {{
    background-color: {C['raised']};
    color: {C['text']};
    border: {_POPUP_BORDER};
    padding: 5px 8px;
}}
QLabel {{ background: transparent; }}

QSplitter::handle {{ background-color: {C['border']}; }}

/* ── Campos de texto ── */
QLineEdit, QTextEdit, QPlainTextEdit {{
    background-color: {C['raised']};
    border: 1px solid {C['border']};
    border-radius: {R_MD}px;
    padding: 5px 10px;
    color: {C['text']};
    selection-background-color: {C['accent']};
    selection-color: {C['on_accent']};
}}
QLineEdit {{ min-height: 20px; }}
QLineEdit:hover, QTextEdit:hover, QPlainTextEdit:hover {{ border-color: {C['border_hi']}; }}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus {{ border-color: {C['accent']}; }}
QLineEdit:disabled, QTextEdit:disabled, QPlainTextEdit:disabled {{ color: {C['text_3']}; }}
QLineEdit:read-only {{ background-color: {C['surface']}; }}

/* Buscadores: sin borde hasta que se usan */
QLineEdit#searchField {{ border-color: transparent; padding-left: 6px; }}
QLineEdit#searchField:hover {{ background-color: {C['hover']}; }}
QLineEdit#searchField:focus {{ border-color: {C['accent']}; background-color: {C['raised']}; }}

/* ── Botones ── */
QPushButton {{
    background-color: {C['raised']};
    border: 1px solid {C['border']};
    border-radius: {R_MD}px;
    padding: 5px 14px;
    color: {C['text']};
    min-height: 20px;
}}
QPushButton:hover {{ background-color: {C['hover']}; border-color: {C['border_hi']}; }}
QPushButton:pressed {{ background-color: {C['selected']}; }}
QPushButton:disabled {{ color: {C['text_3']}; background-color: {C['surface']}; border-color: {C['border_soft']}; }}
QPushButton::menu-indicator {{ image: none; width: 0; }}

QPushButton#btnPrimary, QPushButton#btnSend, QPushButton#btnSave, QPushButton#btnDirectChat {{
    background-color: {C['accent']};
    border: 1px solid {C['accent']};
    color: {C['on_accent']};
    font-weight: 600;
}}
QPushButton#btnPrimary:hover, QPushButton#btnSend:hover, QPushButton#btnSave:hover,
QPushButton#btnDirectChat:hover {{ background-color: {C['accent_hi']}; border-color: {C['accent_hi']}; }}
QPushButton#btnPrimary:pressed, QPushButton#btnSend:pressed, QPushButton#btnSave:pressed,
QPushButton#btnDirectChat:pressed {{ background-color: {C['accent']}; }}
QPushButton#btnPrimary:disabled, QPushButton#btnSend:disabled, QPushButton#btnSave:disabled {{
    background-color: {C['raised']}; border-color: {C['border']}; color: {C['text_3']};
}}

QPushButton#btnDanger {{
    background-color: {C['danger_dim']};
    border: 1px solid #6b2a27;
    color: #f0a39e;
}}
QPushButton#btnDanger:hover {{ background-color: {C['danger']}; border-color: {C['danger']}; color: #ffffff; }}
QPushButton#btnDanger:disabled {{ background-color: {C['surface']}; border-color: {C['border_soft']}; color: {C['text_3']}; }}

/* Botón plano de icono genérico (los de icons.tool_button se pintan solos) */
QToolButton {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: {R_MD}px;
    padding: 3px;
    color: {C['text_2']};
}}
QToolButton:hover {{ background-color: {C['hover']}; }}
QToolButton:pressed, QToolButton:checked {{ background-color: {C['selected']}; }}
QToolButton:disabled {{ color: {C['text_3']}; }}
QToolButton::menu-indicator {{ image: none; width: 0; }}

/* Filtros tipo píldora (Todos / No leídos) */
QToolButton#chip {{
    background-color: {C['raised']};
    color: {C['text_2']};
    border: 1px solid transparent;
    border-radius: 13px;
    padding: 0 12px;
    min-height: 24px;
    max-height: 24px;
    font-size: 12px;
}}
QToolButton#chip:hover {{ background-color: {C['hover']}; color: {C['text']}; }}
QToolButton#chip:checked {{ background-color: {C['accent_dim']}; color: {C['accent_hi']}; border-color: #24584a; }}

/* ── Listas ── */
QListWidget {{
    background-color: {C['panel']};
    border: none;
    outline: none;
}}
QListWidget::item {{ border: none; border-radius: {R_SM}px; color: {C['text']}; }}
QListWidget::item:hover {{ background-color: {C['hover']}; }}
QListWidget::item:selected {{ background-color: {C['selected']}; color: {C['text']}; }}
QListWidget#framedList {{
    border: 1px solid {C['border']};
    border-radius: {R_LG}px;
    padding: 4px;
}}
QListWidget#framedList::item {{ padding: 4px 6px; margin: 1px 0; }}
/* La lista de chats pinta su propio fondo de fila (ChatItemWidget) */
QListWidget#chatList {{ padding: 2px 0; }}
QListWidget#chatList::item, QListWidget#chatList::item:hover,
QListWidget#chatList::item:selected {{ background: transparent; border: none; }}

/* ── Barras de desplazamiento: píldora fina ── */
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; border: none; }}
QScrollBar::handle:vertical {{ background-color: #2e3a41; min-height: 32px; border-radius: 3px; margin: 2px; }}
QScrollBar::handle:vertical:hover, QScrollBar::handle:vertical:pressed {{ background-color: #4a5961; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 0; border: none; }}
QScrollBar::handle:horizontal {{ background-color: #2e3a41; min-width: 32px; border-radius: 3px; margin: 2px; }}
QScrollBar::handle:horizontal:hover, QScrollBar::handle:horizontal:pressed {{ background-color: #4a5961; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; border: none; background: none; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}

QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}

{MENU_QSS}

/* ── Pestañas tipo píldora ── */
QTabWidget::pane {{
    border: 1px solid {C['border']};
    border-radius: {R_LG}px;
    background-color: {C['panel']};
    top: 6px;
}}
QTabBar::tab {{
    background-color: transparent;
    color: {C['text_2']};
    padding: 6px 14px;
    border: none;
    border-radius: {R_MD}px;
    margin-right: 4px;
}}
QTabBar::tab:hover:!selected {{ background-color: {C['hover']}; color: {C['text']}; }}
QTabBar::tab:selected {{ background-color: {C['accent_dim']}; color: {C['accent_hi']}; }}

/* ── Grupos ── */
QGroupBox {{
    border: 1px solid {C['border']};
    border-radius: {R_LG}px;
    margin-top: 12px;
    padding: 14px 12px 10px 12px;
    font-weight: 600;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 12px;
    padding: 0 4px;
    color: {C['accent']};
}}

/* ── Combos ── */
QComboBox {{
    background-color: {C['raised']};
    border: 1px solid {C['border']};
    border-radius: {R_MD}px;
    padding: 5px 26px 5px 10px;
    color: {C['text']};
    min-height: 20px;
}}
QComboBox:hover {{ border-color: {C['border_hi']}; background-color: {C['hover']}; }}
QComboBox:focus, QComboBox:on {{ border-color: {C['accent']}; }}
QComboBox:disabled {{ color: {C['text_3']}; background-color: {C['surface']}; }}
QComboBox QLineEdit {{ border: none; background: transparent; padding: 0; min-height: 0; }}
QComboBox::drop-down {{ border: none; width: 24px; subcontrol-origin: padding; subcontrol-position: center right; }}
QComboBox::down-arrow {{ image: url("{ARROW_DOWN}"); width: 10px; height: 6px; }}
QComboBoxPrivateContainer {{ background-color: {C['surface']}; border: {_POPUP_BORDER}; }}
QComboBox QAbstractItemView {{
    background-color: {C['surface']};
    border: none;
    padding: 4px;
    selection-background-color: {C['selected']};
    selection-color: {C['text']};
    color: {C['text']};
    outline: none;
}}
QComboBox QAbstractItemView::item {{ min-height: 26px; padding: 0 8px; border-radius: {R_SM}px; }}
QComboBox QAbstractItemView::item:hover {{ background-color: {C['hover']}; }}
QComboBox QAbstractItemView::item:selected {{ background-color: {C['selected']}; }}

/* ── Fecha, hora y números ── */
QAbstractSpinBox {{
    background-color: {C['raised']};
    border: 1px solid {C['border']};
    border-radius: {R_MD}px;
    padding: 5px 10px;
    color: {C['text']};
    min-height: 20px;
}}
QAbstractSpinBox:hover {{ border-color: {C['border_hi']}; }}
QAbstractSpinBox:focus {{ border-color: {C['accent']}; }}
QAbstractSpinBox::up-button, QAbstractSpinBox::down-button {{ width: 0; border: none; }}
QDateTimeEdit::drop-down {{ border: none; width: 24px; subcontrol-origin: padding; subcontrol-position: center right; }}
QDateTimeEdit::down-arrow {{ image: url("{ARROW_DOWN}"); width: 10px; height: 6px; }}
QCalendarWidget QWidget {{ alternate-background-color: {C['surface']}; }}
QCalendarWidget QAbstractItemView {{
    background-color: {C['surface']}; color: {C['text']};
    selection-background-color: {C['accent']}; selection-color: {C['on_accent']};
}}
QCalendarWidget QAbstractItemView:disabled {{ color: {C['text_3']}; }}
QCalendarWidget #qt_calendar_navigationbar {{ background-color: {C['raised']}; }}

/* ── Casillas ── */
QCheckBox {{ spacing: 8px; background: transparent; }}
QCheckBox::indicator {{
    width: 16px; height: 16px; border-radius: 5px;
    border: 1px solid #4a585f; background-color: {C['raised']};
}}
QCheckBox::indicator:hover {{ border-color: {C['accent']}; }}
QCheckBox::indicator:checked {{
    background-color: {C['accent']}; border-color: {C['accent']}; image: url("{CHECK_MARK}");
}}

QAbstractItemView::indicator {{
    width: 15px; height: 15px; border-radius: 4px;
    border: 1px solid #4a585f; background-color: {C['raised']};
}}
QAbstractItemView::indicator:hover {{ border-color: {C['accent']}; }}
QAbstractItemView::indicator:checked {{
    background-color: {C['accent']}; border-color: {C['accent']}; image: url("{CHECK_MARK}");
}}

/* ── Deslizadores y progreso ── */
QSlider::groove:horizontal {{ height: 4px; background: {C['raised']}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {C['accent']}; border-radius: 2px; }}
QSlider::handle:horizontal {{
    width: 14px; margin: -6px 0; border-radius: 7px;
    background: {C['text']}; border: 1px solid {C['border']};
}}
QSlider::handle:horizontal:hover {{ background: #ffffff; border-color: {C['accent']}; }}
QProgressBar {{ background-color: {C['raised']}; border: none; border-radius: 2px; }}
QProgressBar::chunk {{ background-color: {C['accent']}; border-radius: 2px; }}

/* ── Piezas comunes de la ventana principal ── */
#paneHeader {{
    background-color: {C['surface']};
    border-bottom: 1px solid {C['border']};
}}
#paneTitle {{ font-size: 16px; font-weight: 600; color: {C['text']}; }}
#subtle {{ color: {C['text_2']}; font-size: 12px; }}
#hint {{ color: {C['text_3']}; font-size: 11px; }}
#sectionLabel {{ color: {C['text_2']}; font-size: 11px; font-weight: 600; }}
#card {{ background-color: {C['surface']}; border: 1px solid {C['border']}; border-radius: {R_LG}px; }}
#hline {{ background-color: {C['border']}; border: none; max-height: 1px; min-height: 1px; }}
"""


def unread_badge_qss() -> str:
    return (f"background-color: {C['accent']}; color: {C['on_accent']}; font-size: 10px; font-weight: 700;"
            " border-radius: 9px; padding: 0 5px;")
