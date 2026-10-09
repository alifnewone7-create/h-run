from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


BACK = ("⬅️ Back", "adm:home", "danger")
LAYOUTS = {1: "1 per line", 2: "2 per line"}
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
        [("👁 Preview", f"prv:{kind}", "primary")],
        [("⬅️ Back", "adm:wlc", "danger")],
    ])


def back_to(data: str) -> InlineKeyboardMarkup:
    return _build([[("⬅️ Back", data, "danger")]])


def media_menu(kind: str, has_media: bool) -> InlineKeyboardMarkup:
    rows = [[("🗑 Remove Media", f"medr:{kind}", "danger")]] if has_media else []
    return _build(rows + [[("⬅️ Back", f"cfg:{kind}", "danger")]])


def _rows(items: list[tuple], per_row: int) -> list[list[tuple]]:
    return [items[i:i + per_row] for i in range(0, len(items), per_row)]


def _layout_btn(layout: int, data: str) -> tuple:
    return (f"{'↔️' if layout == 2 else '↕️'} Layout: {LAYOUTS[layout]} (tap to change)", data, "primary")


def buttons_list(kind: str, labels: list[str], layout: int = 1) -> InlineKeyboardMarkup:
    rows = _rows([(f"{i + 1}. {label}", f"bte:{kind}:{i}", "primary") for i, label in enumerate(labels)], layout)
    if labels:
        rows.append([_layout_btn(layout, f"btl:{kind}")])
    return _build(rows + [[("➕ Add Button", f"bta:{kind}", "success")], [("⬅️ Back", f"cfg:{kind}", "danger")]])


def _move_row(i: int, total: int, prefix: str, ref: str) -> list[list[tuple]]:
    row = [("⬆️ Move Up", f"{prefix}u:{ref}", "primary")] if i > 0 else []
    row += [("⬇️ Move Down", f"{prefix}d:{ref}", "primary")] if i < total - 1 else []
    return [row] if row else []


def button_menu(kind: str, i: int, sample: InlineKeyboardButton, total: int = 1) -> InlineKeyboardMarkup:
    kb = _build([
        [("✏️ Edit Name", f"bten:{kind}:{i}", "primary"), ("🔗 Edit Link", f"btel:{kind}:{i}", "primary")],
        [("🎨 Edit Color", f"btec:{kind}:{i}", "success"), ("🗑 Delete", f"btd:{kind}:{i}", "danger")],
        *_move_row(i, total, "btm", f"{kind}:{i}"),
        [("⬅️ Back", f"btn:{kind}", "danger")],
    ])
    kb.inline_keyboard.insert(0, [sample])
    return kb


def color_menu(prefix: str, back: str) -> InlineKeyboardMarkup:
    opts = [(label, f"{prefix}:{key}", *([key] if key != "none" else [])) for key, label in STYLES.items()]
    return _build([opts[:2], opts[2:], [("⬅️ Back", back, "danger")]])


# ---------- broadcast / stats ----------
AUDIENCES = {"joined": "🟢 Joined Users", "pending": "⏳ Pending Users", "leaved": "🚪 Leaved Users", "all": "👥 All Users"}


def broadcast_menu(counts: dict[str, int]) -> InlineKeyboardMarkup:
    btn = lambda a, st: (f"{AUDIENCES[a]} ({counts[a]})", f"bc:{a}", st)
    return _build([
        [btn("joined", "success"), btn("pending", "primary")],
        [btn("leaved", "danger"), btn("all", "success")],
        [BACK],
    ])


def bc_buttons_menu(labels: list[str], layout: int = 1) -> InlineKeyboardMarkup:
    rows = _rows([(f"{i + 1}. {label}", f"bce:{i}", "primary") for i, label in enumerate(labels)], layout)
    if labels:
        rows.append([_layout_btn(layout, "bcb:layout")])
    rows.append([("➕ Add Button", "bcb:add", "success")])
    rows.append([("✅ Done" if labels else "⏭ Skip", "bcb:done", "primary")])
    return _build(rows + [[("❌ Cancel", "adm:cancel", "danger")]])


def bc_button_menu(i: int, sample: InlineKeyboardButton, total: int = 1) -> InlineKeyboardMarkup:
    kb = _build([
        [("✏️ Edit Name", f"bcen:{i}", "primary"), ("🔗 Edit Link", f"bcel:{i}", "primary")],
        [("🎨 Edit Color", f"bcec:{i}", "success"), ("🗑 Delete", f"bcd:{i}", "danger")],
        *_move_row(i, total, "bcm", str(i)),
        [("⬅️ Back", "bcb:menu", "danger")],
    ])
    kb.inline_keyboard.insert(0, [sample])
    return kb


def stats_menu() -> InlineKeyboardMarkup:
    return _build([[("🔄 Refresh", "adm:stats", "success")], [BACK]])


def cancel_menu() -> InlineKeyboardMarkup:
    return _build([[("❌ Cancel", "adm:cancel", "danger")]])


def confirm_broadcast() -> InlineKeyboardMarkup:
    return _build([[("🚀 Send Now", "bc:go", "success"), ("❌ Cancel", "adm:cancel", "danger")]])


def back_menu() -> InlineKeyboardMarkup:
    return _build([[BACK]])
