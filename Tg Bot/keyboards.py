from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from database import PERIOD_LABELS

BACK = ("⬅️ Back", "adm:home", "danger")
STYLES = {"none": "⚪ Default", "primary": "🔵 Blue", "success": "🟢 Green", "danger": "🔴 Red"}


def _btn(spec: tuple) -> InlineKeyboardButton:
    text, data, *style = spec
    return InlineKeyboardButton(text=text, callback_data=data, style=style[0] if style else None)


def _build(rows: list[list[tuple]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[_btn(s) for s in row] for row in rows])


def main_menu() -> InlineKeyboardMarkup:
    return _build([
        [("✅ Non / Approve", "adm:mode", "success")],
        [("👋 Wlc Setting", "adm:wlc", "primary"), ("📢 Broadcast", "adm:bc", "success")],
        [("📊 Statistics", "adm:stats", "success"), ("📡 Channels", "adm:channels", "primary")],
    ])


def mode_menu(mode: str) -> InlineKeyboardMarkup:
    mark = lambda m: "✅ " if mode == m else ""
    style = lambda m: "success" if mode == m else "primary"
    return _build([
        [(f"{mark('non')}Non Approve", "mode:non", style("non")), (f"{mark('auto')}Auto Approve", "mode:auto", style("auto"))],
        [BACK],
    ])


# ---------- wlc setting ----------
def wlc_menu() -> InlineKeyboardMarkup:
    return _build([
        [("🚀 Start Msg", "cfg:start", "primary"), ("👋 Wlc Msg", "cfg:welcome", "success")],
        [BACK],
    ])


def kind_menu(kind: str) -> InlineKeyboardMarkup:
    return _build([
        [("📝 Set Text", f"txt:{kind}", "primary"), ("🖼 Set Media", f"med:{kind}", "primary")],
        [("🔘 Set Button", f"btn:{kind}", "success")],
        [("👁 Preview", f"prv:{kind}", "primary"), ("♻️ Reset", f"rst:{kind}", "danger")],
        [("⬅️ Back", "adm:wlc", "danger")],
    ])


def back_to(data: str) -> InlineKeyboardMarkup:
    return _build([[("⬅️ Back", data, "danger")]])


def media_menu(kind: str, has_media: bool) -> InlineKeyboardMarkup:
    rows = [[("🗑 Remove Media", f"medr:{kind}", "danger")]] if has_media else []
    return _build(rows + [[("⬅️ Back", f"cfg:{kind}", "danger")]])


def buttons_list(kind: str, labels: list[str]) -> InlineKeyboardMarkup:
    rows = [[(f"{i + 1}. {label}", f"bte:{kind}:{i}", "primary")] for i, label in enumerate(labels)]
    return _build(rows + [[("➕ Add Button", f"bta:{kind}", "success")], [("⬅️ Back", f"cfg:{kind}", "danger")]])


def button_menu(kind: str, i: int, sample: InlineKeyboardButton) -> InlineKeyboardMarkup:
    kb = _build([
        [("✏️ Edit Name", f"bten:{kind}:{i}", "primary"), ("🔗 Edit Link", f"btel:{kind}:{i}", "primary")],
        [("🎨 Edit Color", f"btec:{kind}:{i}", "success"), ("🗑 Delete", f"btd:{kind}:{i}", "danger")],
        [("⬅️ Back", f"btn:{kind}", "danger")],
    ])
    kb.inline_keyboard.insert(0, [sample])
    return kb


def color_menu(kind: str, i: int) -> InlineKeyboardMarkup:
    opts = [(label, f"btc:{kind}:{i}:{key}", *([key] if key != "none" else [])) for key, label in STYLES.items()]
    return _build([opts[:2], opts[2:], [("⬅️ Back", f"bte:{kind}:{i}", "danger")]])


# ---------- broadcast / stats ----------
def broadcast_menu(counts: dict[str, int]) -> InlineKeyboardMarkup:
    btn = lambda p, st: (f"{PERIOD_LABELS[p]} ({counts[p]})", f"bc:{p}", st)
    return _build([
        [btn("24h", "primary"), btn("7d", "success")],
        [btn("30d", "success"), btn("all", "primary")],
        [BACK],
    ])


def stats_menu() -> InlineKeyboardMarkup:
    return _build([[("🔄 Refresh", "adm:stats", "success")], [BACK]])


def cancel_menu() -> InlineKeyboardMarkup:
    return _build([[("❌ Cancel", "adm:cancel", "danger")]])


def confirm_broadcast() -> InlineKeyboardMarkup:
    return _build([[("🚀 Send Now", "bc:go", "success"), ("❌ Cancel", "adm:cancel", "danger")]])


def back_menu() -> InlineKeyboardMarkup:
    return _build([[BACK]])
