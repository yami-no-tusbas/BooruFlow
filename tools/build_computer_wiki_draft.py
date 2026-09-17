from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


DATABASE = Path(r"D:\python\artist_by_tag\data\databases\g_tags_260810.db")
DRAFT = Path(r"D:\python\artist_by_tag\var\wiki_drafts\computer.json")

DESCRIPTION = (
    "A computer is a general purpose device that can be programmed to carry out a set of arithmetic or logical operations automatically. "
    "Since a sequence of operations can be readily changed, the computer can solve more than one kind of problem.\n\n"
    "An electronic device for storing and processing data, typically in binary form, according to instructions given to it in a variable program."
)

GROUPS = [
    ("Computer types and form factors", [
        "computer", "laptop", "tablet_pc", "tablet", "server_(computer)", "server",
        "pc-98_(computer)", "pc98_(computer)", "wrist_computer",
    ]),
    ("Displays and screens", [
        "monitor", "computer_monitor", "screen", "display", "multiple_monitors",
        "holographic_monitor", "crt_monitor", "touchscreen", "vertical_monitor",
        "dual_monitor", "curved_monitor", "ultrawide_monitor", "desktop_monitor",
        "computer_screen", "projector",
    ]),
    ("Keyboards, pointing devices and creative input", [
        "computer_keyboard", "keyboard_(computer)", "keyboard", "mechanical_keyboard",
        "wireless_keyboard", "holographic_keyboard", "computer_mouse", "mouse_(computer)",
        "wireless_mouse", "mousepad_(object)", "mousepad", "mouse_pad", "trackball",
        "drawing_tablet", "graphic_tablet", "pen_tablet", "stylus", "joystick",
        "game_controller",
    ]),
    ("Audio, video and immersive peripherals", [
        "webcam", "microphone", "boom_microphone", "desk_microphone", "studio_microphone",
        "headset", "bluetooth_headset", "headphones", "speakers", "vr_headset",
    ]),
    ("Internal components and storage", [
        "computer_tower", "computer_chip", "motherboard", "cpu", "gpu", "graphics_card",
        "ram_(computer)", "memory", "hard_drive", "solid_state_drive", "flash_drive",
        "external_hard_drive", "computer_fan", "power_supply",
    ]),
    ("Networking, connectors and cables", [
        "router", "modem", "ethernet_cable", "usb_cable", "cable",
    ]),
    ("Desk and workspace", [
        "desk", "chair", "office_chair", "gaming_chair", "desk_lamp", "laptop_bag",
        "tablet_stand",
    ]),
    ("Portable and connected devices", [
        "smartphone", "cellphone", "phone", "flip_smartphone", "smartwatch",
        "handheld_game_console", "portable_media_player", "smartphone_case",
        "smartphone_screen",
    ]),
    ("Computer-related actions and situations", [
        "at_computer", "using_computer", "through_computer", "on_computer", "using_laptop",
        "typing", "gaming", "video_call", "programming", "holding_laptop",
        "holding_tablet_pc", "holding_drawing_tablet", "holding_computer_mouse",
        "holding_computer_keyboard", "nude_in_front_of_computer",
    ]),
    ("Conditions and interface elements", [
        "broken_computer", "computer_virus", "mouse_pointer", "mouse_cursor",
    ]),
]

BRANDS = [
    "dell", "lenovo", "acer", "asus", "apple", "microsoft", "samsung", "sony",
    "razer", "logitech", "ibm", "intel", "amd", "nvidia", "corsair",
]


def decorate(tag: str, count: int) -> str:
    link = f"[[{tag}]]"
    if count >= 10_000:
        return f"[b]{link}[/b]"
    if count >= 1_000:
        return f"[i]{link}[/i]"
    if count < 25:
        return link + "**"
    if count < 50:
        return link + "*"
    return link


def main() -> None:
    connection = sqlite3.connect(f"file:{DATABASE}?mode=ro", uri=True)
    rows = connection.execute("SELECT name, post_count, category FROM tags").fetchall()
    connection.close()
    tags = {name: (count, category) for name, count, category in rows}

    lines = [
        DESCRIPTION,
        "",
        "[b]Navigation note:[/b] The links below cover computer types, components, peripherals, connected devices, workspaces, actions and brands. Brand tags may also return non-computer products.",
        "",
        "[b]Popularity legend:[/b]",
        "* [b][[tag]][/b]: 10,000 images or more",
        "* [i][[tag]][/i]: 1,000 to 9,999 images",
        "* [[tag]]: 50 to 999 images",
        "* [[tag]]*: 25 to 49 images",
        "* [[tag]]**: 5 to 24 images",
        "",
    ]
    included: set[str] = set()
    for heading, requested in GROUPS:
        selected = [tag for tag in requested if tag in tags and tags[tag][0] >= 5]
        if not selected:
            continue
        lines.append(f"[h2]{heading}[/h2]")
        for tag in selected:
            lines.append(f"* {decorate(tag, tags[tag][0])}")
            included.add(tag)
        lines.append("")

    selected_brands = [tag for tag in BRANDS if tag in tags and tags[tag][0] >= 5]
    lines.append("[h2]Manufacturers and technology brands[/h2]")
    lines.append("These tags identify a manufacturer or technology brand, but are not limited to computers.")
    for tag in selected_brands:
        lines.append(f"* {decorate(tag, tags[tag][0])}")
        included.add(tag)
    lines.append("The local hp tag currently has no posts and is therefore not included as a searchable brand link.")
    lines.append("")

    # Preserve every relationship from the existing wiki page.
    lines.extend([
        "[h2]See also[/h2]",
        "* [[technology]]",
        "* [[software]]",
        "* [b][[laptop]][/b]",
    ])
    source = "\n".join(lines)
    source = re.sub(r"\n+(\[h[1-6]\])", r"\1", source)
    source = re.sub(r"(\[/h[1-6]\])\n+", r"\1", source)
    document = {
        "tag": "computer",
        "template": "general",
        "source": source,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    DRAFT.parent.mkdir(parents=True, exist_ok=True)
    DRAFT.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {DRAFT}")
    print(f"Included {len(included)} navigational tags plus preserved See also relations")


if __name__ == "__main__":
    main()
