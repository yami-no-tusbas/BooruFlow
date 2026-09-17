import sqlite3
from pathlib import Path

DATABASE = Path(r"D:\python\artist_by_tag\data\databases\g_tags_260810.db")
GROUPS = {
    "Computer types": "computer desktop_computer personal_computer laptop notebook_computer tablet_pc tablet gaming_pc gaming_computer workstation server mainframe supercomputer".split(),
    "Displays": "monitor computer_monitor screen display dual_monitors multiple_monitors crt_monitor lcd_monitor flat_screen touchscreen projector".split(),
    "Input": "keyboard computer_keyboard mouse computer_mouse mousepad trackball touchpad drawing_tablet graphics_tablet stylus joystick game_controller".split(),
    "Audio and video": "webcam microphone boom_microphone headset headphones speakers computer_speakers sound_card".split(),
    "Components and storage": "computer_tower computer_case motherboard cpu processor gpu graphics_card ram memory hard_drive ssd solid_state_drive optical_drive floppy_disk_drive computer_fan heatsink power_supply usb_drive flash_drive external_hard_drive".split(),
    "Network and cables": "router modem ethernet ethernet_cable network_cable usb_cable cable power_cable".split(),
    "Furniture": "desk computer_desk office_desk chair office_chair gaming_chair desk_lamp".split(),
    "Connected devices": "smartphone cellphone phone smartwatch smart_watch smart_device handheld_game_console tablet portable_media_player".split(),
    "Brands": "hp dell lenovo acer asus apple microsoft samsung sony razer logitech alienware ibm intel amd nvidia corsair msi".split(),
    "Software and use": "technology software operating_system windows macos linux web_browser typing using_computer programming computer_programming coding gaming video_call livestreaming".split(),
}

connection = sqlite3.connect(f"file:{DATABASE}?mode=ro", uri=True)
for group, names in GROUPS.items():
    print(f"\n[{group}]")
    placeholders = ",".join("?" for _ in names)
    found = {row[0]: row[1:] for row in connection.execute(
        f"SELECT name, post_count, category FROM tags WHERE name IN ({placeholders})", names
    )}
    for name in names:
        print(name, found.get(name, "ABSENT"))

print("\n[Pattern discovery]")
patterns = [
    "%computer%", "%monitor%", "%keyboard%", "%mouse%", "%tablet%",
    "%webcam%", "%smartphone%", "%smartwatch%", "%laptop%", "%headset%",
    "%microphone%", "%hard_drive%", "%graphics_card%", "%motherboard%",
]
discovered = {}
for pattern in patterns:
    for row in connection.execute(
        "SELECT name, post_count, category FROM tags WHERE post_count>=5 AND name LIKE ?",
        (pattern,),
    ):
        discovered[row[0]] = row
for row in sorted(discovered.values(), key=lambda value: (-value[1], value[0]))[:300]:
    print(row)
connection.close()
