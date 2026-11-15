"""Diálogos del bloqueo de chats: crear/cambiar la contraseña, pedirla para abrir un chat y quitarla."""
from typing import Optional

from PySide6.QtWidgets import QInputDialog, QLineEdit, QMessageBox, QWidget

from app import chat_lock

_TITLE = "Chats bloqueados"


def _ask(parent: Optional[QWidget], label: str) -> Optional[str]:
    text, ok = QInputDialog.getText(parent, _TITLE, label, QLineEdit.Password)
    return text if ok else None


def ensure_password(parent: Optional[QWidget]) -> bool:
    """Si todavía no hay contraseña de bloqueo, pide crearla (dos veces). True si hay una al terminar."""
    if chat_lock.has_password():
        return True
    return create_password(parent)


def create_password(parent: Optional[QWidget], intro: str = "Elegí una contraseña para tus chats bloqueados") -> bool:
    while True:
        first = _ask(parent, f"{intro} (mínimo {chat_lock.MIN_PASSWORD_LEN} caracteres):")
        if first is None:
            return False
        if len(first.strip()) < chat_lock.MIN_PASSWORD_LEN:
            QMessageBox.warning(parent, _TITLE, f"Tiene que tener al menos {chat_lock.MIN_PASSWORD_LEN} caracteres.")
            continue
        second = _ask(parent, "Repetí la contraseña:")
        if second is None:
            return False
        if first != second:
            QMessageBox.warning(parent, _TITLE, "Las contraseñas no coinciden.")
            continue
        return chat_lock.set_password(first)


def ask_current_password(parent: Optional[QWidget], label: str = "Ingresá la contraseña:") -> bool:
    """Pide la contraseña hasta acertar o cancelar."""
    while True:
        pw = _ask(parent, label)
        if pw is None:
            return False
        if chat_lock.verify_password(pw):
            return True
        QMessageBox.warning(parent, _TITLE, "Contraseña incorrecta.")


def ensure_open(parent: Optional[QWidget], jid: str) -> bool:
    """True si el chat se puede abrir: no está bloqueado, ya se desbloqueó o se ingresó bien la contraseña."""
    if chat_lock.is_open(jid):
        return True
    if ask_current_password(parent, "Este chat está bloqueado. Ingresá la contraseña:"):
        chat_lock.mark_open(jid)
        return True
    return False


def change_password(parent: Optional[QWidget]) -> bool:
    if not chat_lock.has_password():
        return create_password(parent)
    if not ask_current_password(parent, "Ingresá la contraseña actual:"):
        return False
    return create_password(parent, "Elegí la contraseña nueva")


def remove_password(parent: Optional[QWidget]) -> bool:
    if not chat_lock.has_password():
        return False
    if not ask_current_password(parent, "Ingresá la contraseña para quitarla:"):
        return False
    chat_lock.remove_password()
    return True


# ── Código secreto de los chats ocultos ──

def ensure_secret_code(parent: Optional[QWidget]) -> bool:
    """Si todavía no hay código secreto para los chats ocultos, pide crearlo. True si hay uno al terminar."""
    if chat_lock.has_secret_code():
        return True
    return create_secret_code(parent)


def create_secret_code(parent: Optional[QWidget]) -> bool:
    while True:
        first = _ask(parent, f"Elegí un código secreto para mostrar tus chats ocultos (mínimo {chat_lock.MIN_SECRET_LEN} "
                             "caracteres).\nLo escribís en el buscador de chats y aparecen:")
        if first is None:
            return False
        if len(first.strip()) < chat_lock.MIN_SECRET_LEN:
            QMessageBox.warning(parent, _TITLE, f"Tiene que tener al menos {chat_lock.MIN_SECRET_LEN} caracteres.")
            continue
        second = _ask(parent, "Repetí el código:")
        if second is None:
            return False
        if first.strip() != second.strip():
            QMessageBox.warning(parent, _TITLE, "Los códigos no coinciden.")
            continue
        return chat_lock.set_secret_code(first)


def change_secret_code(parent: Optional[QWidget]) -> bool:
    if chat_lock.has_secret_code() and not ask_current_password(parent, "Ingresá la contraseña de bloqueo:"):
        return False
    return create_secret_code(parent)
