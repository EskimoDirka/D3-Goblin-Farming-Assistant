"""
Goblin Farming automation overlay for Diablo III.

This script owns four broad areas:
- A small always-on-top Tk overlay with route buttons and status state.
- Windows input/window helpers for activating Diablo, sending clicks/keys, and locking/releasing the cursor.
- Image-template recognition for map travel, location detection, start/leave game, repair, salvage, stash, and combat cues.
- High-level flows such as Make New Game, Exit Game, teleport routing, combat loops, repair/salvage, and Gibbering Gemstone stash handling.

Most coordinates are loaded from text files or inferred from the green-box template images under ImageSearchTemplates. The constants below are the tuning knobs: template thresholds, timeouts, polling intervals, and fallback coordinates.
"""
import ctypes
from ctypes import wintypes
import glob
import logging
from logging.handlers import RotatingFileHandler
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import tkinter as tk
from tkinter import messagebox
from pathlib import Path

import cv2
import mss
import numpy as np


# Filesystem layout. These paths are the source of almost every image template
# and coordinate fallback used by the automation.
SCRIPT_DIR = Path(__file__).resolve().parent
IMAGE_TEMPLATE_DIR = SCRIPT_DIR / "ImageSearchTemplates"
TELEPORT_TEMPLATE_DIR = IMAGE_TEMPLATE_DIR / "Teleport Function"
MAP_COORDINATES_PATH = TELEPORT_TEMPLATE_DIR / "Map X Y Coordinates.txt"
REPAIR_TEMPLATE_DIR = IMAGE_TEMPLATE_DIR / "Repair"
REPAIR_COORDINATES_PATH = REPAIR_TEMPLATE_DIR / "Repair Station Coordinates.txt"
SALVAGE_TEMPLATE_DIR = IMAGE_TEMPLATE_DIR / "Salvage"
SALVAGE_COORDINATES_PATH = SALVAGE_TEMPLATE_DIR / "Salvage Coordinates.txt"
START_GAME_TEMPLATE_DIR = IMAGE_TEMPLATE_DIR / "Start Game"
START_GAME_COORDINATES_PATH = START_GAME_TEMPLATE_DIR / "Start Game Button Coordinates.txt"
LEAVE_GAME_TEMPLATE_DIR = IMAGE_TEMPLATE_DIR / "Leave Game"
LEAVE_GAME_COORDINATES_PATH = LEAVE_GAME_TEMPLATE_DIR / "Leave Game Button Coordinates.txt"
CURRENT_LOCATION_TEMPLATE_DIR = IMAGE_TEMPLATE_DIR / "Current Location"
COMBAT_TEMPLATE_DIR = IMAGE_TEMPLATE_DIR / "Combat"
# Controller layout overlay is disabled. The controller input mappings remain active.
# CONTROLLER_LAYOUT_IMAGE_PATH = IMAGE_TEMPLATE_DIR / "Xbox Controller Layout Transparent.png"

# External process/window identity and launch behavior.
DIABLO_WINDOWS = ("Diablo III",)
DIABLO_PROCESS_NAMES = ("diablo iii.exe", "diablo iii64.exe")
BATTLE_NET_WINDOWS = ("Battle.net",)
DIABLO_LAUNCH_PATH = Path(os.environ.get(
    "DIABLO_LAUNCH_PATH",
    r"C:\Program Files (x86)\Diablo III\Diablo III.exe",
))
BATTLE_NET_PLAY_BUTTON = {"x": 154, "y": 1280}
BATTLE_NET_PLAY_BUTTON_THRESHOLD = 0.80
BATTLE_NET_PLAY_RETRY_SECONDS = 10
BATTLE_NET_LAUNCH_WAIT_SECONDS = 30
DIABLO_LAUNCH_WAIT_SECONDS = 90
TELEPORT_REMINDER_STATE = Path(tempfile.gettempdir()) / "GoblinFarming-teleport-reminders.txt"
LAST_TELEPORT_STATE = Path(tempfile.gettempdir()) / "GoblinFarming-last-teleport.txt"
OVERLAY_POSITION_STATE = Path(tempfile.gettempdir()) / "GoblinFarming-overlay-position.txt"
CONTROLLER_TUNING_STATE = Path(tempfile.gettempdir()) / "GoblinFarming-controller-tuning.txt"
APP_INSTANCE_LOCK = Path(tempfile.gettempdir()) / "GoblinFarming-instance.lock"
LOG_DIR = Path(tempfile.gettempdir()) / "GoblinFarming"
LOG_PATH = LOG_DIR / f"GoblinFarming-{os.getpid()}.log"
LEGACY_LOG_PATTERN = "GoblinFarming.log*"
DIABLO_WINDOW_CACHE = {"hwnd": 0, "pid": 0, "reject_hwnd": 0, "reject_pid": 0}

# Overlay sizing, colors, and default position. The default position ratios are
# based on a 2560x1440 layout but scale to the current display size.
OVERLAY_WIDTH = 500
OVERLAY_HEIGHT = 834
OVERLAY_MARGIN_X = 10
OVERLAY_MARGIN_Y = 8
OVERLAY_HEADER_ACTION_HEIGHT = 24
OVERLAY_COLUMN_GAP = 8
OVERLAY_CONTROLLER_INFO_WIDTH = 180
OVERLAY_PANEL_GAP = 10
ACTION_BUTTON_HEIGHT = 31
ACTION_ROW_GAP = 6
STATUS_HEIGHT = 25
HELP_HEIGHT = 21
CONTROLLER_TUNING_HEIGHT = 112
SECTION_HEADER_HEIGHT = 22
SECTION_GAP = 5
LOCATION_BUTTON_HEIGHT = 32
LOCATION_BUTTON_GAP = 3
OVERLAY_BG = "#1B1712"
OVERLAY_BUTTON_BG = "#2B251E"
OVERLAY_BUTTON_ACTIVE_BG = "#5C7A34"
OVERLAY_BUTTON_QUEUED_BG = "#C86F1F"
OVERLAY_TEXT = "#D8CFB9"
OVERLAY_HEADER = "#D4B45F"
OVERLAY_BUTTON_TEXT = "#F0E4C2"
OVERLAY_CLOSE_BG = "#8B1E1E"
OVERLAY_CLOSE_HOVER_BG = "#B32626"
OVERLAY_CLOSE_TEXT = "#FFFFFF"
# Default the overlay to the left monitor, away from the right-side Diablo
# screen. These offsets match the saved "other screen" placement.
OVERLAY_DEFAULT_LEFT_MONITOR_RIGHT_MARGIN = 20
OVERLAY_DEFAULT_TOP = 532
OVERLAY_VISIBILITY_REFRESH_MS = 1500
# CONTROLLER_LAYOUT_REFRESH_MS = 250
# CONTROLLER_LAYOUT_REGION_RATIO = {
#     "left": 16 / 2560,
#     "top": 215 / 1440,
#     "width": 700 / 2560,
#     "height": 467 / 1440,
# }
TELEPORT_FAILSAFE_BLOCKED_LOCATIONS = {
    "Caldeum Bazaar",
    "Caverns of Frost Level 1",
    "Cave Of The Moon Clan Level 1",
    "Cathedral Level 1",
    "Cathedral Level 2",
    "Eastern Channel Level 1",
    "Flooded Causeway",
    "Leoric's Passage",
    "Sewers of Caldeum",
    "Stinging Winds",
}

# Image matching thresholds. Higher values reduce false positives but make the
# flow more sensitive to UI scale, lighting, or template drift.
TELEPORT_THRESHOLD = 0.68
ACT_HEADER_THRESHOLD = 0.92
WORLD_MAP_THRESHOLD = 0.80
CURRENT_LOCATION_THRESHOLD = 0.82
START_GAME_COLOR_THRESHOLD = 0.88
START_GAME_STABLE_SECONDS = 1.10
START_GAME_CLICK_SETTLE_SECONDS = 0.35
START_GAME_RETRY_COOLDOWN_SECONDS = 1.25
START_GAME_MENU_SETTLE_SECONDS = 0.75
LEAVE_GAME_THRESHOLD = 0.80
REPAIR_THRESHOLD = 0.80
REPAIR_STATION_COORDINATES = (
    (1822, 197),
)
REPAIR_STATION_PATH_RECLICK_SECONDS = 2.0

# Salvage/Gibbering Gemstone tuning. The inventory grid is scanned with both
# template matching and simple brightness/variance checks so it can find filled
# slots even when item art differs.
SALVAGE_THRESHOLD = 0.80
SALVAGE_TAB_FALLBACK_THRESHOLD = 0.55
SALVAGE_BLANK_TILE_THRESHOLD = 0.86
SALVAGE_INVENTORY_OPEN_MIN_BLANK_TILES = 3
SALVAGE_EMPTY_SLOT_MATCH_THRESHOLD = 0.78
SALVAGE_TIMEOUT = 20
SALVAGE_CLICK_RESULT_TIMEOUT = 0.8
SALVAGE_CLICK_RESULT_POLL_SECONDS = 0.04
SALVAGE_MAX_ITEMS = 60
SALVAGE_GRID_COLUMNS = 10
SALVAGE_GRID_ROWS = 6
SALVAGE_INVENTORY_REGION_RATIO = {
    "left": 1864 / 2560,
    "top": 725 / 1440,
    "width": 687 / 2560,
    "height": 423 / 1440,
}
SALVAGE_SLOT_FILLED_MEAN_THRESHOLD = 18.0
SALVAGE_SLOT_FILLED_STD_THRESHOLD = 10.0
SALVAGE_BUTTON_X_RATIO = 219 / 2560
SALVAGE_BUTTON_Y_RATIO = 390 / 1440
SALVAGE_TAB_REGION_RATIO = {
    "left": 642 / 2560,
    "top": 560 / 1440,
    "width": 75 / 2560,
    "height": 170 / 1440,
}
SALVAGE_BUTTON_REGION_RATIO = {
    "left": 147 / 2560,
    "top": 326 / 1440,
    "width": 139 / 2560,
    "height": 139 / 1440,
}
SALVAGE_CONFIRMATION_REGION_RATIO = {
    "left": 988 / 2560,
    "top": 450 / 1440,
    "width": 287 / 2560,
    "height": 92 / 1440,
}
GG_TEMPLATE_THRESHOLD = 0.78
GG_TEMPLATE_NMS_DISTANCE = 28
GG_STASH_TIMEOUT = 12
GG_STASH_CLICK_MENU_TIMEOUT = 3.0
GG_STASH_MENU_REGION_RATIO = {
    "left": 298 / 2560,
    "top": 154 / 1440,
    "width": 111 / 2560,
    "height": 55 / 1440,
}
GG_TAB_REGION_RATIO = {
    "left": 641 / 2560,
    "top": 621 / 1440,
    "width": 77 / 2560,
    "height": 160 / 1440,
}
GG_STASH_PLACEMENT_REGION_RATIO = {
    "left": 77 / 2560,
    "top": 289 / 1440,
    "width": 549 / 2560,
    "height": 778 / 1440,
}

# General timing. These values are intentionally small/polling-based so
# stop_requested can interrupt most long waits quickly.
POLL_SECONDS = 0.10
CURRENT_LOCATION_POLL_SECONDS = 0.25
TELEPORT_ARRIVAL_TIMEOUT = 30
NEW_TRISTRAM_ALREADY_THERE_CONFIRM_SECONDS = 1.0
NEW_TRISTRAM_THRESHOLD = 0.78
NEW_TRISTRAM_PREP_DETECT_TIMEOUT = 1.0
LEAVE_GAME_TIMEOUT = 30
LEAVE_GAME_BUTTON_TIMEOUT = 10
LEAVE_GAME_MENU_ATTEMPTS = 3
LEAVE_GAME_BUTTON_IMAGE_ATTEMPT_TIMEOUT = 1.5
LEAVE_GAME_BUTTON_POLL_SECONDS = 0.04
LEAVE_GAME_MENU_OPEN_DELAY = 0.20
AUTOMATION_ESCAPE_ALLOW_SECONDS = 1.0
START_GAME_TIMEOUT = 90
GAME_START_MAP_OPEN_DELAY = 0.20
GAME_START_MAP_OPEN_TIMEOUT = 90
REPAIR_TIMEOUT = 20

# Combat automation tuning by class/template.
MONK_COMBAT_KEY_POLL_SECONDS = 0.03
MONK_COMBAT_KEY_DELAY_SECONDS = 0.05
MONK_CURSOR_POLL_SECONDS = 0.09
MONK_CURSOR_CLICK_GAP_SECONDS = 0.12
WITCH_DOCTOR_CHARACTER_THRESHOLD = 0.78
WITCH_DOCTOR_HEX_THRESHOLD = 0.82
WITCH_DOCTOR_HEX_SCAN_SECONDS = 0.05
WITCH_DOCTOR_HEX_CAST_SETTLE_SECONDS = 0.18
WITCH_DOCTOR_HEX_DISAPPEAR_TIMEOUT = 0.70
WITCH_DOCTOR_MOUSE_WHEEL_SECONDS = 0.05
WITCH_DOCTOR_MOUSE_WHEEL_DELTA = -120
WITCH_DOCTOR_SCROLL_STOP_TIMEOUT = 0.30
WITCH_DOCTOR_HEX_STOP_PRESS_ATTEMPTS = 3
WITCH_DOCTOR_HEX_STOP_PRESS_SETTLE_SECONDS = 0.15
AUTO_ROUTE_TELEPORT_SETTLE_SECONDS = 0.60
AUTO_ROUTE_TELEPORT_RETRY_SECONDS = 1.25
AUTO_ROUTE_TELEPORT_REARM_SECONDS = 2.50
AUTO_ROUTE_TELEPORT_MAX_ATTEMPTS = 2
DEMON_HUNTER_CHARACTER_THRESHOLD = 0.78
DEMON_HUNTER_KEY_SECONDS = 0.10
DEMON_HUNTER_MOMENTUM_SCAN_SECONDS = 0.05
DEMON_HUNTER_MOMENTUM_RECOVERY_SETTLE_SECONDS = 0.35
DEMON_HUNTER_MOMENTUM_BUILD_TIMEOUT = 6.0
DEMON_HUNTER_MOMENTUM_THRESHOLD = 0.86
DEMON_HUNTER_MOMENTUM_REGION_RATIO = {
    "left": 0.325,
    "top": 0.835,
    "width": 0.354,
    "height": 0.072,
}
BOUNTY_MENU_THRESHOLD = 0.74
BOUNTY_MENU_POLL_SECONDS = 0.10
BOUNTY_MENU_ESCAPE_COOLDOWN_SECONDS = 1.0
BOUNTY_MENU_REGION_RATIO = {
    "left": 1000 / 2560,
    "top": 982 / 1440,
    "width": 560 / 2560,
    "height": 280 / 1440,
}
BOUNTY_MENU_TITLE_CROP_RATIO = {
    "left": 0.37,
    "top": 0.05,
    "width": 0.56,
    "height": 0.18,
}
FOLLOWER_MENU_THRESHOLD = 0.74
FOLLOWER_MENU_REGION_RATIO = {
    "left": 1003 / 2560,
    "top": 31 / 1440,
    "width": 556 / 2560,
    "height": 472 / 1440,
}
FOLLOWER_MENU_TITLE_CROP_RATIO = {
    "left": 0.30,
    "top": 0.04,
    "width": 0.58,
    "height": 0.16,
}
LOOT_CLICK_SECONDS = 0.05
KADALA_RIGHT_CLICK_SECONDS = 0.05
CONTROLLER_POLL_SECONDS = 0.008
CONTROLLER_RECONNECT_SECONDS = 1.0
CONTROLLER_TRIGGER_THRESHOLD = 80
CONTROLLER_LEFT_STICK_DEADZONE = 4000
CONTROLLER_CURSOR_SPEED_PIXELS_PER_SECOND = 2850
CONTROLLER_CURSOR_RESPONSE_POWER = 1.38
CONTROLLER_CURSOR_SPEED_MIN = 1600
CONTROLLER_CURSOR_SPEED_MAX = 4600
CONTROLLER_CURSOR_SPEED_STEP = 50
CONTROLLER_RESPONSE_POWER_MIN = 0.90
CONTROLLER_RESPONSE_POWER_MAX = 2.00
CONTROLLER_RESPONSE_POWER_STEP = 0.05
CONTROLLER_COMBAT_CURSOR_REGION_RATIO = {
    "left": 760 / 2560,
    "top": 380 / 1440,
    "width": 1040 / 2560,
    "height": 680 / 1440,
}

# Screen regions where combat automation should avoid clicking because they are
# UI/HUD elements rather than the game world.
MONK_COMBAT_NO_CLICK_REGION_RATIOS = (
    {"name": "player_portrait", "left": 0 / 2560, "top": 0 / 1440, "width": 166 / 2560, "height": 226 / 1440},
    {"name": "follower_portrait", "left": 141 / 2560, "top": 4 / 1440, "width": 135 / 2560, "height": 159 / 1440},
    {"name": "chat_button", "left": 6 / 2560, "top": 1270 / 1440, "width": 118 / 2560, "height": 134 / 1440},
    {"name": "left_bottom_hud", "left": 430 / 2560, "top": 1220 / 1440, "width": 390 / 2560, "height": 220 / 1440},
    {"name": "skill_bar", "left": 818 / 2560, "top": 1300 / 1440, "width": 930 / 2560, "height": 130 / 1440},
    {"name": "right_bottom_hud", "left": 1690 / 2560, "top": 1220 / 1440, "width": 410 / 2560, "height": 220 / 1440},
    {"name": "right_bottom_menu", "left": 2310 / 2560, "top": 1278 / 1440, "width": 214 / 2560, "height": 124 / 1440},
    {"name": "objectives_collapse", "left": 2448 / 2560, "top": 472 / 1440, "width": 44 / 2560, "height": 62 / 1440},
    {"name": "top_right_buff_icons", "left": 2237 / 2560, "top": 28 / 1440, "width": 115 / 2560, "height": 50 / 1440},
)

# Win32 function bindings and constants. These make the rest of the script read
# like Python while still using native foreground-window, input, hook, and cursor APIs.
user32 = ctypes.windll.user32
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.SetForegroundWindow.restype = wintypes.BOOL
user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
user32.FindWindowW.restype = wintypes.HWND
user32.GetSystemMetrics.argtypes = [ctypes.c_int]
user32.GetSystemMetrics.restype = ctypes.c_int
user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
user32.SetCursorPos.restype = wintypes.BOOL
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetWindowRect.restype = wintypes.BOOL
user32.ClipCursor.argtypes = [ctypes.POINTER(wintypes.RECT)]
user32.ClipCursor.restype = wintypes.BOOL
user32.mouse_event.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
user32.keybd_event.argtypes = [wintypes.BYTE, wintypes.BYTE, wintypes.DWORD, ctypes.c_void_p]
user32.EnumWindows.argtypes = [ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM), wintypes.LPARAM]
user32.EnumWindows.restype = wintypes.BOOL
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsWindowVisible.restype = wintypes.BOOL
user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetWindowTextLengthW.restype = ctypes.c_int
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.restype = ctypes.c_int
user32.GetForegroundWindow.argtypes = []
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.PostMessageW.restype = wintypes.BOOL
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.restype = wintypes.SHORT
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.ShowWindow.restype = wintypes.BOOL
user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
user32.GetAncestor.restype = wintypes.HWND
user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetWindowLongW.restype = ctypes.c_long
user32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]
user32.SetWindowLongW.restype = ctypes.c_long
user32.SetWindowPos.argtypes = [
    wintypes.HWND,
    wintypes.HWND,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    wintypes.UINT,
]
user32.SetWindowPos.restype = wintypes.BOOL
user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
user32.GetCursorPos.restype = wintypes.BOOL
user32.SetWindowsHookExW.argtypes = [ctypes.c_int, ctypes.c_void_p, wintypes.HINSTANCE, wintypes.DWORD]
user32.SetWindowsHookExW.restype = wintypes.HHOOK
user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
user32.CallNextHookEx.restype = wintypes.LPARAM
user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
user32.UnhookWindowsHookEx.restype = wintypes.BOOL
user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
user32.GetMessageW.restype = wintypes.BOOL
user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.TranslateMessage.restype = wintypes.BOOL
user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.DispatchMessageW.restype = wintypes.LPARAM
user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.PostThreadMessageW.restype = wintypes.BOOL
kernel32 = ctypes.windll.kernel32
kernel32.GetConsoleWindow.argtypes = []
kernel32.GetConsoleWindow.restype = wintypes.HWND
kernel32.GetCurrentThreadId.argtypes = []
kernel32.GetCurrentThreadId.restype = wintypes.DWORD
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = [
    wintypes.HANDLE,
    wintypes.DWORD,
    wintypes.LPWSTR,
    ctypes.POINTER(wintypes.DWORD),
]
kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.CloseHandle.restype = wintypes.BOOL

SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79
WH_KEYBOARD_LL = 13
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000
GWL_EXSTYLE = -20
GA_ROOT = 2
HWND_TOPMOST = -1
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040
SW_SHOWNOACTIVATE = 4
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_WHEEL = 0x0800
KEYEVENTF_KEYUP = 0x0002
VK_SHIFT = 0x10
VK_MENU = 0x12
VK_I = 0x49
VK_M = 0x4D
VK_RETURN = 0x0D
VK_ESCAPE = 0x1B
VK_TAB = 0x09
VK_UP = 0x26
VK_OEM_3 = 0xC0
VK_1 = 0x31
VK_2 = 0x32
VK_3 = 0x33
VK_4 = 0x34
LLKHF_INJECTED = 0x10
WM_CLOSE = 0x0010
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105
WM_MOUSEWHEEL = 0x020A
WM_QUIT = 0x0012
SW_MINIMIZE = 6

ROUTE_NEXT_TELEPORTS = {
    "Southern Highlands": "Northern Highlands",
    "Northern Highlands": "The Weeping Hollow",
    "The Weeping Hollow": "The Festering Woods",
    "The Festering Woods": "Cathedral",
    "Cathedral": "Royal Crypts",
    "Royal Crypts": "City Of Caldeum",
    "City Of Caldeum": "Ancient Waterway",
    "Ancient Waterway": "Stinging Winds",
    "Stinging Winds": "Battlefields",
    "Battlefields": "Rakkis Crossing",
    "Rakkis Crossing": "Pandemonium Fortress Level 1",
    "Pandemonium Fortress Level 1": "Pandemonium Fortress Level 2",
}
ROUTE_END_LOCATION = "Pandemonium Fortress Level 2"
CALDEUM_ROUTE_LOCATION = "City Of Caldeum"
CALDEUM_AUTO_TELEPORT_READY_LOCATION = "Ruined Cistern"
CALDEUM_AUTO_TELEPORT_HOLD_LOCATIONS = {
    "City Of Caldeum",
    "Caldeum Bazaar",
    "Sewers of Caldeum",
    "Flooded Causeway",
}
BATTLEFIELDS_ROUTE_LOCATION = "Battlefields"
BATTLEFIELDS_AUTO_TELEPORT_READY_LOCATION = "Caverns of Frost Level 2"
BATTLEFIELDS_AUTO_TELEPORT_HOLD_LOCATIONS = {
    "Battlefields",
    "The Battlefields",
    "Fields of Slaughter",
    "Caverns of Frost Level 1",
}
STINGING_WINDS_ROUTE_LOCATION = "Stinging Winds"
STINGING_WINDS_AUTO_TELEPORT_READY_LOCATION = "Black Canyon Mines"
ROUTE_ARRIVAL_LOCATION_ALIASES = {
    "Ancient Waterway": {
        "Ancient Waterway",
        "Eastern Channel Level 1",
        "Eastern Channel Level 2",
        "Western Channel Level 1",
        "Western Channel Level 2",
    },
    "Battlefields": {
        "Battlefields",
        "The Battlefields",
        "Fields of Slaughter",
        "Caverns of Frost Level 1",
        "Caverns of Frost Level 2",
    },
    "Cathedral": {"Cathedral", "Cathedral Level 1", "Cathedral Level 2", "Cathedral Level 3"},
    "City Of Caldeum": {
        "City Of Caldeum",
        "Caldeum Bazaar",
        "Sewers of Caldeum",
        "Flooded Causeway",
        "Ruined Cistern",
    },
    "Northern Highlands": {"Northern Highlands", "Highlands Cave", "Leoric's Hunting Grounds"},
    "Royal Crypts": {"Royal Crypts", "The Royal Crypts"},
    "Stinging Winds": {"Stinging Winds", "Black Canyon Mines"},
}


class CURSORINFO(ctypes.Structure):
    """ctypes mirror of the Win32 CURSORINFO structure used to inspect the current cursor handle."""
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("hCursor", wintypes.HANDLE),
        ("ptScreenPos", wintypes.POINT),
    ]


class KBDLLHOOKSTRUCT(ctypes.Structure):
    """ctypes mirror of the low-level keyboard hook payload used by the Escape watcher."""
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", wintypes.WPARAM),
    ]


class XINPUT_GAMEPAD(ctypes.Structure):
    """ctypes mirror of the XInput gamepad payload for Xbox controllers."""
    _fields_ = [
        ("wButtons", wintypes.WORD),
        ("bLeftTrigger", ctypes.c_ubyte),
        ("bRightTrigger", ctypes.c_ubyte),
        ("sThumbLX", ctypes.c_short),
        ("sThumbLY", ctypes.c_short),
        ("sThumbRX", ctypes.c_short),
        ("sThumbRY", ctypes.c_short),
    ]


class XINPUT_STATE(ctypes.Structure):
    """ctypes mirror of the XInput controller state."""
    _fields_ = [
        ("dwPacketNumber", wintypes.DWORD),
        ("Gamepad", XINPUT_GAMEPAD),
    ]


LowLevelKeyboardProc = ctypes.WINFUNCTYPE(wintypes.LPARAM, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)

XINPUT_ERROR_SUCCESS = 0
XINPUT_GAMEPAD_DPAD_UP = 0x0001
XINPUT_GAMEPAD_DPAD_RIGHT = 0x0008
XINPUT_GAMEPAD_START = 0x0010
XINPUT_GAMEPAD_BACK = 0x0020
XINPUT_GAMEPAD_A = 0x1000
XINPUT_GAMEPAD_B = 0x2000
XINPUT_GAMEPAD_X = 0x4000
XINPUT_GAMEPAD_Y = 0x8000
xinput = None
for xinput_dll_name in ("xinput1_4", "xinput1_3", "xinput9_1_0"):
    try:
        xinput = ctypes.windll.LoadLibrary(xinput_dll_name)
        break
    except OSError:
        continue
if xinput:
    xinput.XInputGetState.argtypes = [wintypes.DWORD, ctypes.POINTER(XINPUT_STATE)]
    xinput.XInputGetState.restype = wintypes.DWORD


user32.GetCursorInfo.argtypes = [ctypes.POINTER(CURSORINFO)]
user32.GetCursorInfo.restype = wintypes.BOOL


def cleanup_legacy_script_logs():
    """Remove old project-folder logs now that run logs live in temp storage."""
    for log_file in SCRIPT_DIR.glob(LEGACY_LOG_PATTERN):
        try:
            log_file.unlink(missing_ok=True)
        except OSError:
            pass


def setup_logging():
    """Configure temporary rotating file logging and return the shared application logger."""
    logger = logging.getLogger("GoblinFarming")
    logger.setLevel(logging.INFO)
    if logger.handlers:
        return logger
    cleanup_legacy_script_logs()
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(LOG_PATH, maxBytes=512_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    logger.propagate = False
    return logger


LOGGER = setup_logging()


def cleanup_logging(remove_logs=True):
    """Close log handlers and remove this run's temporary log files when the overlay exits."""
    logger = logging.getLogger("GoblinFarming")
    for handler in list(logger.handlers):
        handler.flush()
        handler.close()
        logger.removeHandler(handler)
    if not remove_logs:
        return
    for log_file in glob.glob(str(LOG_PATH) + "*"):
        try:
            Path(log_file).unlink(missing_ok=True)
        except OSError:
            pass


def minimize_console_window():
    """Hide/minimize the Python console so the overlay is the visible control surface."""
    hwnd = kernel32.GetConsoleWindow()
    if hwnd:
        user32.ShowWindow(hwnd, SW_MINIMIZE)
        LOGGER.debug("Console window minimized")


def screen_size():
    """Return the current primary screen dimensions from Win32."""
    return user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)


def virtual_screen_bounds():
    """Return the full desktop bounds across all connected monitors."""
    return {
        "left": user32.GetSystemMetrics(SM_XVIRTUALSCREEN),
        "top": user32.GetSystemMetrics(SM_YVIRTUALSCREEN),
        "width": user32.GetSystemMetrics(SM_CXVIRTUALSCREEN),
        "height": user32.GetSystemMetrics(SM_CYVIRTUALSCREEN),
    }


def default_overlay_position():
    """Place the overlay at the saved left-monitor home position."""
    bounds = virtual_screen_bounds()
    try:
        saved_text = OVERLAY_POSITION_STATE.read_text(encoding="utf-8", errors="ignore").strip()
        match = re.fullmatch(r"\s*(-?\d+)\s*,\s*(-?\d+)\s*", saved_text)
        if match:
            saved_x = int(match.group(1))
            saved_y = int(match.group(2))
            return {
                "x": max(bounds["left"], min(saved_x, bounds["left"] + bounds["width"] - OVERLAY_WIDTH)),
                "y": max(bounds["top"], min(saved_y, bounds["top"] + bounds["height"] - OVERLAY_HEIGHT)),
            }
    except OSError:
        pass

    if bounds["left"] < 0:
        left_monitor_left = bounds["left"]
        left_monitor_width = abs(bounds["left"])
        x = left_monitor_left + left_monitor_width - OVERLAY_WIDTH - OVERLAY_DEFAULT_LEFT_MONITOR_RIGHT_MARGIN
    else:
        x = bounds["left"] + bounds["width"] - OVERLAY_WIDTH - OVERLAY_DEFAULT_LEFT_MONITOR_RIGHT_MARGIN
    y = bounds["top"] + OVERLAY_DEFAULT_TOP
    return {
        "x": max(bounds["left"], min(x, bounds["left"] + bounds["width"] - OVERLAY_WIDTH)),
        "y": max(bounds["top"], min(y, bounds["top"] + bounds["height"] - OVERLAY_HEIGHT)),
    }


def clamp(value, minimum, maximum):
    """Clamp a numeric value to an inclusive range."""
    return max(minimum, min(value, maximum))


def find_window_by_titles(titles):
    """Find the first top-level window whose title contains one of the provided names."""
    titles_lower = tuple(title.lower() for title in titles)
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def enum_proc(hwnd, _lparam):
        """Collect the first matching visible window during EnumWindows traversal."""
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        window_title = buffer.value.lower()
        if any(title in window_title for title in titles_lower):
            found.append(hwnd)
            return False
        return True

    user32.EnumWindows(enum_proc, 0)
    return found[0] if found else 0


def window_process_id(hwnd):
    """Return the process id that owns a window handle."""
    pid = wintypes.DWORD()
    if not hwnd:
        return 0
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return int(pid.value)


def process_image_name(pid):
    """Return the executable basename for a process id."""
    if not pid:
        return ""
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(1024)
        buffer = ctypes.create_unicode_buffer(size.value)
        if not kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return ""
        return Path(buffer.value).name.lower()
    finally:
        kernel32.CloseHandle(handle)


def window_process_name_is(hwnd, process_names):
    """Return True when a window belongs to one of the expected executable names."""
    return process_image_name(window_process_id(hwnd)) in process_names


def window_title_contains(hwnd, titles):
    """Return True when a window title contains any expected title fragment."""
    if not hwnd or not user32.IsWindowVisible(hwnd):
        return False
    length = user32.GetWindowTextLengthW(hwnd)
    if length <= 0:
        return False
    buffer = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buffer, length + 1)
    title = buffer.value.lower()
    return any(expected.lower() in title for expected in titles)


def hwnd_is_diablo_window(hwnd):
    """Return True when a hwnd is Diablo's actual game window."""
    return bool(
        hwnd
        and window_title_contains(hwnd, DIABLO_WINDOWS)
        and window_process_name_is(hwnd, DIABLO_PROCESS_NAMES)
    )


def find_diablo_window():
    """Find Diablo's real game window, excluding Explorer folders with matching titles."""
    cached_hwnd = DIABLO_WINDOW_CACHE.get("hwnd", 0)
    cached_pid = DIABLO_WINDOW_CACHE.get("pid", 0)
    if cached_hwnd and user32.IsWindowVisible(cached_hwnd) and window_process_id(cached_hwnd) == cached_pid:
        return cached_hwnd

    foreground = user32.GetForegroundWindow()
    if hwnd_is_diablo_window(foreground):
        DIABLO_WINDOW_CACHE["hwnd"] = foreground
        DIABLO_WINDOW_CACHE["pid"] = window_process_id(foreground)
        return foreground

    titles_lower = tuple(title.lower() for title in DIABLO_WINDOWS)
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def enum_proc(hwnd, _lparam):
        """Collect Diablo-titled windows until the real Diablo process is found."""
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        window_title = buffer.value.lower()
        if any(title in window_title for title in titles_lower) and window_process_name_is(hwnd, DIABLO_PROCESS_NAMES):
            found.append(hwnd)
            return False
        return True

    user32.EnumWindows(enum_proc, 0)
    if found:
        DIABLO_WINDOW_CACHE["hwnd"] = found[0]
        DIABLO_WINDOW_CACHE["pid"] = window_process_id(found[0])
        DIABLO_WINDOW_CACHE["reject_hwnd"] = 0
        DIABLO_WINDOW_CACHE["reject_pid"] = 0
        return found[0]
    DIABLO_WINDOW_CACHE["hwnd"] = 0
    DIABLO_WINDOW_CACHE["pid"] = 0
    return 0


def activate_diablo():
    """Bring Diablo III to the foreground if its window exists."""
    hwnd = find_diablo_window()
    if hwnd:
        user32.SetForegroundWindow(hwnd)
        time.sleep(0.05)
        return True
    return False


def diablo_is_running():
    """Report whether any Diablo III process/window is currently available."""
    return bool(find_diablo_window())


def process_id_is_goblin_farming_instance(pid):
    """Check whether a process id belongs to this GoblinFarming Python script."""
    if not pid or pid == os.getpid():
        return False
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not handle:
        return False
    kernel32.CloseHandle(handle)
    try:
        command = [
            "powershell.exe",
            "-NoProfile",
            "-Command",
            f"(Get-CimInstance Win32_Process -Filter \"ProcessId = {int(pid)}\").CommandLine",
        ]
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
            timeout=3,
        )
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return False

    command_line = result.stdout or ""
    return "GoblinFarming.py" in command_line


def claim_single_instance():
    """Create the temp-file process lock so duplicate overlay instances can be cleaned up."""
    try:
        existing_pid_text = APP_INSTANCE_LOCK.read_text(encoding="utf-8", errors="ignore").strip()
        existing_pid = int(existing_pid_text) if existing_pid_text else 0
    except (OSError, ValueError):
        existing_pid = 0

    if process_id_is_goblin_farming_instance(existing_pid):
        LOGGER.info("Another GoblinFarming instance is already running: pid=%s", existing_pid)
        return False
    if existing_pid:
        LOGGER.info("Ignoring stale GoblinFarming instance lock: pid=%s", existing_pid)

    try:
        APP_INSTANCE_LOCK.write_text(str(os.getpid()), encoding="utf-8")
    except OSError:
        LOGGER.exception("Could not write instance lock")
        return False

    return True


def diablo_is_active():
    """Return True when Diablo is the foreground root window."""
    foreground = user32.GetForegroundWindow()
    if not foreground:
        return False
    foreground_pid = window_process_id(foreground)
    cached_hwnd = DIABLO_WINDOW_CACHE.get("hwnd", 0)
    if foreground == cached_hwnd and foreground_pid == DIABLO_WINDOW_CACHE.get("pid", 0):
        return True
    if (
        foreground == DIABLO_WINDOW_CACHE.get("reject_hwnd", 0)
        and foreground_pid == DIABLO_WINDOW_CACHE.get("reject_pid", 0)
    ):
        return False
    if hwnd_is_diablo_window(foreground):
        DIABLO_WINDOW_CACHE["hwnd"] = foreground
        DIABLO_WINDOW_CACHE["pid"] = foreground_pid
        DIABLO_WINDOW_CACHE["reject_hwnd"] = 0
        DIABLO_WINDOW_CACHE["reject_pid"] = 0
        return True
    DIABLO_WINDOW_CACHE["reject_hwnd"] = foreground
    DIABLO_WINDOW_CACHE["reject_pid"] = foreground_pid
    return False


def lock_cursor_to_diablo_window():
    """Confine the cursor to Diablo while combat automation is active."""
    hwnd = find_diablo_window()
    if not hwnd:
        return False
    rect = wintypes.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return False
    if rect.right <= rect.left or rect.bottom <= rect.top:
        return False
    if not user32.ClipCursor(ctypes.byref(rect)):
        return False
    LOGGER.info(
        "Cursor locked to Diablo window: left=%s top=%s right=%s bottom=%s",
        rect.left,
        rect.top,
        rect.right,
        rect.bottom,
    )
    return True


def diablo_window_rect():
    """Return Diablo's window rectangle or None when the window is unavailable."""
    hwnd = find_diablo_window()
    if not hwnd:
        return None
    rect = wintypes.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return None
    if rect.right <= rect.left or rect.bottom <= rect.top:
        return None
    return rect


def get_xinput_state(controller_index=0):
    """Return the current XInput controller state, or None when unavailable/disconnected."""
    if not xinput:
        return None
    state = XINPUT_STATE()
    result = xinput.XInputGetState(controller_index, ctypes.byref(state))
    if result != XINPUT_ERROR_SUCCESS:
        return None
    return state


def release_cursor_lock():
    """Release any active Win32 cursor clipping."""
    if user32.ClipCursor(None):
        LOGGER.info("Cursor lock released")
        return True
    return False


def activate_battle_net():
    """Bring Battle.net to the foreground when Diablo needs to be launched."""
    hwnd = find_window_by_titles(BATTLE_NET_WINDOWS)
    if hwnd:
        user32.SetForegroundWindow(hwnd)
        time.sleep(0.05)
        return True
    return False


def close_battle_net():
    """Close Battle.net after Diablo has successfully launched."""
    hwnd = find_window_by_titles(BATTLE_NET_WINDOWS)
    if hwnd:
        user32.PostMessageW(hwnd, 0x0010, 0, 0)
        return True
    return False


def press_vk(vk):
    """Send a virtual-key press/release pair through Win32."""
    user32.keybd_event(vk, 0, 0, None)
    time.sleep(0.03)
    user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, None)


def click(x, y, button="left"):
    """Move the cursor and send a left or right mouse click."""
    user32.SetCursorPos(int(x), int(y))
    time.sleep(0.03)
    if button == "right":
        user32.mouse_event(MOUSEEVENTF_RIGHTDOWN, 0, 0, 0, None)
        time.sleep(0.03)
        user32.mouse_event(MOUSEEVENTF_RIGHTUP, 0, 0, 0, None)
    else:
        user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, None)
        time.sleep(0.03)
        user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, None)


def left_click_clean(x, y):
    """Release held inputs before performing a left click at the target point."""
    user32.SetCursorPos(int(x), int(y))
    time.sleep(0.08)
    user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, None)
    time.sleep(0.03)
    user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, None)
    time.sleep(0.04)
    user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, None)


def left_click_current_position():
    """Click the current cursor position without moving it."""
    user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, None)
    time.sleep(0.02)
    user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, None)


def right_click_current_position():
    """Right-click the current cursor position without moving it."""
    user32.mouse_event(MOUSEEVENTF_RIGHTDOWN, 0, 0, 0, None)
    time.sleep(0.02)
    user32.mouse_event(MOUSEEVENTF_RIGHTUP, 0, 0, 0, None)


def scroll_mouse_wheel(delta):
    """Send a mouse-wheel delta through Win32."""
    user32.mouse_event(MOUSEEVENTF_WHEEL, 0, 0, int(delta), None)


def post_mouse_wheel_to_window(hwnd, delta):
    """Post a mouse-wheel message directly to a target window."""
    point = current_cursor_point()
    if point is None:
        x = 0
        y = 0
    else:
        x = point.x
        y = point.y
    wparam = (int(delta) & 0xFFFF) << 16
    lparam = (int(y) & 0xFFFF) << 16 | (int(x) & 0xFFFF)
    return bool(user32.PostMessageW(hwnd, WM_MOUSEWHEEL, wparam, lparam))


def scroll_diablo_mouse_wheel(delta):
    """Scroll the Diablo window directly when foreground input is unreliable."""
    hwnd = find_diablo_window()
    if hwnd and post_mouse_wheel_to_window(hwnd, delta):
        return True
    scroll_mouse_wheel(delta)
    return False


def park_cursor_away_from_menu_buttons():
    """Move the cursor away from common menu buttons to avoid accidental hover/click issues."""
    sw, sh = screen_size()
    user32.SetCursorPos(max(0, sw - 30), max(0, sh - 30))
    time.sleep(0.08)


def release_inputs(release_mouse=True, release_right=True):
    """Release modifier and mouse buttons that automation may have held down."""
    user32.keybd_event(VK_SHIFT, 0, KEYEVENTF_KEYUP, None)
    user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, None)
    if release_mouse:
        user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, None)
        if release_right:
            user32.mouse_event(MOUSEEVENTF_RIGHTUP, 0, 0, 0, None)


def current_cursor_handle():
    """Return the active cursor handle for class-specific combat logic."""
    cursor_info = CURSORINFO()
    cursor_info.cbSize = ctypes.sizeof(CURSORINFO)
    if not user32.GetCursorInfo(ctypes.byref(cursor_info)):
        return 0
    return int(cursor_info.hCursor or 0)


def current_cursor_point():
    """Return the current cursor coordinates."""
    point = wintypes.POINT()
    if not user32.GetCursorPos(ctypes.byref(point)):
        return None
    return point


def region_from_ratio(left, top, width, height):
    """Convert normalized screen ratios into an mss-compatible region dictionary."""
    sw, sh = screen_size()
    return {
        "left": round(sw * left),
        "top": round(sh * top),
        "width": round(sw * width),
        "height": round(sh * height),
    }


def region_from_green_box_image(path, default_region, scale_to_screen=True):
    """Read a green-box guide image and convert the marked rectangle into a screen region."""
    path = Path(path)
    if not path.exists():
        return default_region

    image = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
    if image is None or image.ndim < 3:
        return default_region

    b, g, r = image[:, :, 0], image[:, :, 1], image[:, :, 2]
    mask = ((g > 120) & (r < 90) & (b < 90)).astype("uint8")
    component_count, _labels, stats, _centroids = cv2.connectedComponentsWithStats(mask, 8)
    sw, sh = screen_size()
    image_height, image_width = image.shape[:2]
    default_box = {
        "left": round(default_region["left"] * image_width / sw),
        "top": round(default_region["top"] * image_height / sh),
        "right": round((default_region["left"] + default_region["width"]) * image_width / sw),
        "bottom": round((default_region["top"] + default_region["height"]) * image_height / sh),
    }
    best = None
    for index in range(1, component_count):
        x, y, width, height, area = stats[index]
        if area < 100:
            continue
        overlap_width = max(0, min(int(x + width), default_box["right"]) - max(int(x), default_box["left"]))
        overlap_height = max(0, min(int(y + height), default_box["bottom"]) - max(int(y), default_box["top"]))
        overlap_area = overlap_width * overlap_height
        if (
            best is None
            or overlap_area > best["overlap_area"]
            or (overlap_area == best["overlap_area"] and area > best["area"])
        ):
            best = {
                "left": int(x),
                "top": int(y),
                "width": int(width),
                "height": int(height),
                "area": int(area),
                "overlap_area": int(overlap_area),
            }

    if not best or best["overlap_area"] <= 0:
        return default_region

    if not scale_to_screen:
        return {
            "left": best["left"],
            "top": best["top"],
            "width": best["width"],
            "height": best["height"],
        }

    return {
        "left": round(best["left"] * sw / image_width),
        "top": round(best["top"] * sh / image_height),
        "width": round(best["width"] * sw / image_width),
        "height": round(best["height"] * sh / image_height),
    }


def crop_gray_from_green_box_image(path, default_region, crop_ratio):
    """Load a grayscale crop from a marked green-box image."""
    path = Path(path)
    if not path.exists():
        return None

    image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if image is None:
        return None

    box = region_from_green_box_image(path, default_region, scale_to_screen=False)
    x = box["left"] + round(box["width"] * crop_ratio["left"])
    y = box["top"] + round(box["height"] * crop_ratio["top"])
    width = round(box["width"] * crop_ratio["width"])
    height = round(box["height"] * crop_ratio["height"])
    if width <= 0 or height <= 0:
        return None

    crop = image[y : y + height, x : x + width]
    return crop if crop.size else None


def gg_stash_placement_points_from_image(path):
    """Derive candidate stash placement points from the GG stash placement guide image."""
    path = Path(path)
    sw, sh = screen_size()
    default_region = region_from_ratio(
        GG_STASH_PLACEMENT_REGION_RATIO["left"],
        GG_STASH_PLACEMENT_REGION_RATIO["top"],
        GG_STASH_PLACEMENT_REGION_RATIO["width"],
        GG_STASH_PLACEMENT_REGION_RATIO["height"],
    )
    if not path.exists():
        return []

    image = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
    if image is None or image.ndim < 3:
        return []

    image_height, image_width = image.shape[:2]
    b, g, r = image[:, :, 0], image[:, :, 1], image[:, :, 2]
    mask = ((g > 120) & (r < 90) & (b < 90)).astype("uint8")
    component_count, _labels, stats, centroids = cv2.connectedComponentsWithStats(mask, 8)

    stash_box = None
    vertical_lines = []
    for index in range(1, component_count):
        x, y, width, height, area = stats[index]
        if area < 100:
            continue
        if width > 200 and height > 200:
            if stash_box is None or area > stash_box["area"]:
                stash_box = {"left": x, "top": y, "width": width, "height": height, "area": area}
        elif width <= 14 and height >= 80:
            vertical_lines.append({
                "x": float(centroids[index][0]),
                "top": int(y),
                "bottom": int(y + height - 1),
                "height": int(height),
            })

    if not vertical_lines:
        return []

    if stash_box:
        row_step = max(1, round(stash_box["height"] / 10))
    else:
        row_step = max(1, round(default_region["height"] * image_height / sh / 10))

    points = []
    for line in sorted(vertical_lines, key=lambda item: item["height"]):
        y = line["top"]
        line_points = []
        while y <= line["bottom"]:
            line_points.append({
                "x": round(line["x"] * sw / image_width),
                "y": round(y * sh / image_height),
            })
            y += row_step
        if line_points and line["bottom"] - round(line_points[-1]["y"] * image_height / sh) > row_step * 0.5:
            line_points.append({
                "x": round(line["x"] * sw / image_width),
                "y": round(line["bottom"] * sh / image_height),
            })
        for point in line_points:
            points.append({
                "x": point["x"],
                "y": point["y"],
            })

    return points


def normalize_location_name(name):
    """Normalize template/location labels into the display names used by the route maps."""
    name = re.sub(r"\s+", " ", name.replace("\ufeff", "").strip())
    aliases = {
        "ancient waterway": "Waterway",
        "battlefields": "Battlefields",
        "battelfields": "Battlefields",
        "city of caldeum": "City Of Caldeum",
        "the battlefields": "Battlefields",
        "the royal crypts": "Royal Crypts",
        "the weeping hollow": "Weeping Hollow",
    }
    return aliases.get(name.lower(), name)


def location_key(name):
    """Produce a case-insensitive key for location lookup dictionaries."""
    return re.sub(r"[^a-z0-9]+", " ", normalize_location_name(name).lower()).strip()


def location_matches_route_target(current_location, target_location):
    """Return True when a detected location is an accepted alias for a route target."""
    current_key = location_key(current_location)
    if not current_key:
        return False
    aliases = ROUTE_ARRIVAL_LOCATION_ALIASES.get(target_location, {target_location})
    return current_key in {location_key(alias) for alias in aliases}


def parse_coordinates(path):
    """Parse act and location waypoint coordinates from the map coordinate text file."""
    acts = {}
    locations = {}
    current_act = ""
    if not path.exists():
        return acts, locations

    for raw_line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw_line.strip()
        header = re.fullmatch(r"=+\s*(Act\s+\d+)(?:\s+Locations)?\s*=+", line, re.I)
        if header:
            current_act = header.group(1).title()
            continue

        match = re.match(r"(.+?)\s*-\s*(\d+)\s*,\s*(\d+)", line)
        if not match:
            continue

        name = normalize_location_name(match.group(1))
        point = {"x": int(match.group(2)), "y": int(match.group(3))}
        if re.fullmatch(r"Act\s+\d+", name, re.I):
            acts[name.title()] = point
        elif current_act:
            point["act"] = current_act
            locations[location_key(name)] = point | {"name": name}

    return acts, locations


def parse_single_coordinate(path, default_point):
    """Parse one named coordinate file with a fallback point."""
    if not path.exists():
        return default_point
    match = re.search(r"(\d+)\s*,\s*(\d+)", path.read_text(encoding="utf-8", errors="ignore"))
    if not match:
        return default_point
    return {"x": int(match.group(1)), "y": int(match.group(2))}


def parse_named_coordinates(path, defaults):
    """Parse named coordinate rows and merge them with defaults."""
    points = dict(defaults)
    if not path.exists():
        return points

    current_name = ""
    for raw_line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw_line.strip()
        header = re.search(r"=+\s*(.+?)\s*=+", line)
        if header:
            current_name = header.group(1).strip()
            continue

        match = re.match(r"(\d+)\s*,\s*(\d+)", line)
        if current_name and match:
            points[current_name] = {"x": int(match.group(1)), "y": int(match.group(2))}

    return points


def powershell_quote(value):
    """Escape a value for embedding in a PowerShell command string."""
    return "'" + str(value).replace("'", "''") + "'"


class TemplateMatcher:
    """Small OpenCV template-matching cache used by every image-recognition flow."""
    def __init__(self):
        """Initialize image regions, state flags, overlay widgets, and background watchers."""
        self.cache = {}
        self.color_cache = {}

    def template_path(self, directory, name):
        """Return a template image path, adding .png when the caller passed a bare name."""
        for suffix in (".png", ".jpg", ".jpeg"):
            path = directory / f"{name}{suffix}"
            if path.exists():
                return path
        return directory / f"{name}.png"

    def load(self, path):
        """Load and cache a grayscale template image."""
        path = Path(path)
        stat = path.stat()
        cache_key = (stat.st_mtime_ns, stat.st_size)
        cached = self.cache.get(path)
        if cached is None or cached["key"] != cache_key:
            image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
            if image is None:
                raise ValueError(f"Could not read template: {path}")
            self.cache[path] = {"key": cache_key, "image": image}
        return self.cache[path]["image"]

    def load_color(self, path):
        """Load and cache a color template image for state-sensitive UI buttons."""
        path = Path(path)
        stat = path.stat()
        cache_key = (stat.st_mtime_ns, stat.st_size)
        cached = self.color_cache.get(path)
        if cached is None or cached["key"] != cache_key:
            image = cv2.imread(str(path), cv2.IMREAD_COLOR)
            if image is None:
                raise ValueError(f"Could not read template: {path}")
            self.color_cache[path] = {"key": cache_key, "image": image}
        return self.color_cache[path]["image"]

    def find(self, template_path, region):
        """Find the best template match in a screen region."""
        template_path = Path(template_path)
        if not template_path.exists():
            return None

        template = self.load(template_path)
        return self.find_loaded(template, region)

    def find_color(self, template_path, region):
        """Find the best color template match in a screen region."""
        template_path = Path(template_path)
        if not template_path.exists():
            return None

        template = self.load_color(template_path)
        return self.find_color_loaded(template, region)

    def find_loaded(self, template, region):
        """Run OpenCV matching for an already-loaded template against a live screenshot."""
        if template is None:
            return None
        with mss.mss() as sct:
            screenshot = np.array(sct.grab(region))

        gray = cv2.cvtColor(screenshot, cv2.COLOR_BGRA2GRAY)
        if gray.shape[0] < template.shape[0] or gray.shape[1] < template.shape[1]:
            return None
        result = cv2.matchTemplate(gray, template, cv2.TM_CCOEFF_NORMED)
        _, confidence, _, location = cv2.minMaxLoc(result)
        return {
            "confidence": confidence,
            "x": region["left"] + location[0],
            "y": region["top"] + location[1],
            "width": template.shape[1],
            "height": template.shape[0],
        }

    def find_color_loaded(self, template, region):
        """Run OpenCV matching without discarding button color information."""
        if template is None:
            return None
        with mss.mss() as sct:
            screenshot = np.array(sct.grab(region))

        bgr = cv2.cvtColor(screenshot, cv2.COLOR_BGRA2BGR)
        if bgr.shape[0] < template.shape[0] or bgr.shape[1] < template.shape[1]:
            return None
        result = cv2.matchTemplate(bgr, template, cv2.TM_CCOEFF_NORMED)
        _, confidence, _, location = cv2.minMaxLoc(result)
        return {
            "confidence": confidence,
            "x": region["left"] + location[0],
            "y": region["top"] + location[1],
            "width": template.shape[1],
            "height": template.shape[0],
        }

    def find_all(self, template_path, region, threshold, min_distance=20):
        """Find multiple non-overlapping template matches above a threshold."""
        template_path = Path(template_path)
        if not template_path.exists():
            return []

        template = self.load(template_path)
        with mss.mss() as sct:
            screenshot = np.array(sct.grab(region))

        gray = cv2.cvtColor(screenshot, cv2.COLOR_BGRA2GRAY)
        if gray.shape[0] < template.shape[0] or gray.shape[1] < template.shape[1]:
            return []

        result = cv2.matchTemplate(gray, template, cv2.TM_CCOEFF_NORMED)
        locations = np.where(result >= threshold)
        candidates = []
        for y, x in zip(locations[0], locations[1]):
            candidates.append({
                "confidence": float(result[y, x]),
                "x": region["left"] + int(x),
                "y": region["top"] + int(y),
                "width": template.shape[1],
                "height": template.shape[0],
            })

        matches = []
        for candidate in sorted(candidates, key=lambda item: item["confidence"], reverse=True):
            candidate_center = (
                candidate["x"] + candidate["width"] / 2,
                candidate["y"] + candidate["height"] / 2,
            )
            if any(
                abs(candidate_center[0] - (match["x"] + match["width"] / 2)) <= min_distance
                and abs(candidate_center[1] - (match["y"] + match["height"] / 2)) <= min_distance
                for match in matches
            ):
                continue
            matches.append(candidate)
        return matches

    def find_best_in_dir(self, directory, region):
        """Find the strongest template match among every PNG in a directory."""
        best_match = None
        with mss.mss() as sct:
            screenshot = np.array(sct.grab(region))

        gray = cv2.cvtColor(screenshot, cv2.COLOR_BGRA2GRAY)
        for path in sorted(directory.glob("*")):
            if path.suffix.lower() not in (".png", ".jpg", ".jpeg"):
                continue
            template = self.load(path)
            if gray.shape[0] < template.shape[0] or gray.shape[1] < template.shape[1]:
                continue
            result = cv2.matchTemplate(gray, template, cv2.TM_CCOEFF_NORMED)
            _, confidence, _, location = cv2.minMaxLoc(result)
            if best_match is None or confidence > best_match["confidence"]:
                best_match = {
                    "name": path.stem,
                    "confidence": confidence,
                    "x": region["left"] + location[0],
                    "y": region["top"] + location[1],
                    "width": template.shape[1],
                    "height": template.shape[0],
                }

        return best_match

    def visible(self, template_path, region, threshold):
        """Return True when a template is visible in a region at the requested confidence."""
        match = self.find(template_path, region)
        return bool(match and match["confidence"] >= threshold)


class GoblinFarmingApp:
    """Main Tk overlay application and owner of all automation state and worker flows."""
    def __init__(self):
        """Initialize image regions, state flags, overlay widgets, and background watchers."""
        # Template matcher and coordinate files are initialized first because
        # nearly every later flow depends on these shared helpers.
        self.matcher = TemplateMatcher()
        self.act_coords, self.location_coords = parse_coordinates(MAP_COORDINATES_PATH)
        sw, sh = screen_size()
        self.start_game_coords = parse_named_coordinates(START_GAME_COORDINATES_PATH, {
            "Battle Net Play Button Coordinates": BATTLE_NET_PLAY_BUTTON,
            "Diablo Start Game Button Coordinates": {"x": round(sw * 0.122), "y": round(sh * 0.493)},
        })
        self.repair_coords = parse_named_coordinates(REPAIR_COORDINATES_PATH, {
            "Repair Tab": {"x": 688, "y": 812},
            "Repair Button": {"x": 349, "y": 717},
        })
        self.salvage_coords = parse_named_coordinates(SALVAGE_COORDINATES_PATH, {
            "Salvage Tab": {"x": 683, "y": 638},
            "Salvage Button": {"x": round(sw * SALVAGE_BUTTON_X_RATIO), "y": round(sh * SALVAGE_BUTTON_Y_RATIO)},
            "Stash Coordinates": {"x": 287, "y": 471},
            "GG Stash Tab Coordinates": {"x": 703, "y": 702},
        })

        # Flow state. "running" covers map/game-management flows; "combat_running"
        # covers continuous combat loops. stop_requested is the cooperative cancel flag.
        self.running = False
        self.combat_running = False
        self.stop_requested = False
        self.overlay_locked = True
        self.repair_already_executed = False
        self.pending_auto_teleport = None
        self.pending_hotkey_teleport = None
        self.auto_route_teleport_thread = None
        self.auto_route_retry_after_id = None

        # Route-button state used to color the last completed teleport and the
        # next suggested teleport in the farming route.
        self.last_button = None
        self.teleport_buttons = {}
        self.queued_button = None
        self.diablo_running_at_start = diablo_is_running()
        self.reminder_button_key = self.load_teleport_reminder() if self.diablo_running_at_start else ""
        self.last_teleport_key = self.load_last_teleport_location() if self.diablo_running_at_start else ""
        self.drag_start = None
        self.stop_watcher_active = True
        self.ignore_esc_until = 0.0
        self.allow_game_escape_until = 0.0
        self.escape_hook = None
        self.escape_hook_thread_id = 0
        self.escape_hook_proc = None

        # Background combat threads are kept as attributes so their running
        # state can be inspected and stopped cleanly.
        self.hotkey_watcher_active = True
        self.controller_watcher_active = True
        self.controller_enabled = True
        self.controller_enabled_var = None
        self.controller_connected = False
        self.controller_a_mouse_down = False
        self.controller_x_mouse_down = False
        self.controller_exit_confirmation_requested = False
        self.exit_confirmation_open = False
        self.controller_cursor_remainder_x = 0.0
        self.controller_cursor_remainder_y = 0.0
        self.controller_last_move_at = time.perf_counter()
        controller_tuning = self.load_controller_tuning()
        self.controller_cursor_speed = controller_tuning["speed"]
        self.controller_response_curve = controller_tuning["curve"]
        self.controller_speed_value_label = None
        self.controller_response_value_label = None
        self.combat_class = ""
        self.monk_key_thread = None
        self.monk_cursor_thread = None
        self.witch_doctor_thread = None
        self.witch_doctor_scroll_thread = None
        self.witch_doctor_original_hex_ready = None
        self.demon_hunter_key_thread = None
        self.demon_hunter_shift_click_thread = None
        self.demon_hunter_right_mouse_thread = None
        self.combat_menu_thread = None
        self.monk_key_index = 1
        self.monk_last_cursor_click_at = 0.0
        self.monk_original_cursor_handle = 0
        self.click_through = False
        self.splash = None
        self.splash_label = None
        self.splash_after_id = None
        # Controller layout overlay disabled; keep controller input mappings only.
        # self.controller_layout_overlay = None
        # self.controller_layout_canvas = None
        # self.controller_layout_image = None
        # self.controller_layout_hwnd = 0
        # self.controller_layout_region = None
        self.last_detected_location = ""
        self.teleport_failsafe_bypass = False

        # Protected screen regions keep automated combat clicks away from HUD,
        # skill bar, portraits, and other non-world UI.
        self.monk_no_click_regions = [
            {
                "name": region["name"],
                "left": round(sw * region["left"]),
                "top": round(sh * region["top"]),
                "width": round(sw * region["width"]),
                "height": round(sh * region["height"]),
            }
            for region in MONK_COMBAT_NO_CLICK_REGION_RATIOS
        ]
        overlay_position = default_overlay_position()
        self.overlay_x = overlay_position["x"]
        self.overlay_y = overlay_position["y"]
        self.overlay_home_x = self.overlay_x
        self.overlay_home_y = self.overlay_y
        self.inner_width = OVERLAY_WIDTH - (OVERLAY_MARGIN_X * 2)
        self.controls_x = OVERLAY_MARGIN_X + OVERLAY_CONTROLLER_INFO_WIDTH + OVERLAY_PANEL_GAP
        self.controls_width = OVERLAY_WIDTH - self.controls_x - OVERLAY_MARGIN_X
        self.half_button_width = (self.controls_width - OVERLAY_COLUMN_GAP) // 2

        # Image-search regions. Most use green-box guide images so recalibrating
        # a UI area can be done by replacing the image rather than rewriting code.
        self.map_scan_region = region_from_ratio(0.3801, 0.0389, 0.2391, 0.0757)
        self.world_map_title_region = region_from_ratio(0.40, 0.04, 0.20, 0.08)
        self.current_location_region = region_from_ratio(0.6290, 0.0, 0.3710, 0.0537)
        self.game_loaded_location_title_region = region_from_ratio(0.82, 0.0, 0.18, 0.04)
        self.start_game_button_region = region_from_green_box_image(
            self.matcher.template_path(START_GAME_TEMPLATE_DIR, "Start Game Scan Region"),
            region_from_ratio(0.0336, 0.4424, 0.1758, 0.0688),
        )
        self.battle_net_play_button_region = region_from_green_box_image(
            self.matcher.template_path(START_GAME_TEMPLATE_DIR, "Battle Net Play Button Scan Region"),
            {
                "left": max(0, self.start_game_coords["Battle Net Play Button Coordinates"]["x"] - 90),
                "top": max(0, self.start_game_coords["Battle Net Play Button Coordinates"]["y"] - 60),
                "width": 180,
                "height": 120,
            },
            scale_to_screen=False,
        )
        self.leave_game_button_region = region_from_green_box_image(
            self.matcher.template_path(LEAVE_GAME_TEMPLATE_DIR, "Leave Game Scan Region"),
            region_from_ratio(0.0438, 0.4208, 0.1578, 0.0521),
        )
        self.blacksmith_shop_region = region_from_green_box_image(
            self.matcher.template_path(REPAIR_TEMPLATE_DIR, "Blacksmith Menu Scan Region"),
            region_from_ratio(0.0855, 0.0799, 0.1055, 0.0771),
        )
        self.repair_menu_region = region_from_green_box_image(
            self.matcher.template_path(REPAIR_TEMPLATE_DIR, "Repair Menu Scan Region"),
            region_from_ratio(0.109, 0.1, 0.057, 0.0458),
        )
        self.repair_station_region = region_from_ratio(0.0, 0.0, 1.0, 1.0)
        self.character_scan_region = region_from_green_box_image(
            self.matcher.template_path(COMBAT_TEMPLATE_DIR, "Character Scan Region"),
            region_from_ratio(49 / 2560, 73 / 1440, 69 / 2560, 68 / 1440),
        )
        self.witch_doctor_hex_region = region_from_green_box_image(
            self.matcher.template_path(COMBAT_TEMPLATE_DIR, "Witch Doctor Hex Scan Region"),
            region_from_ratio(831 / 2560, 1325 / 1440, 96 / 2560, 97 / 1440),
        )
        self.demon_hunter_momentum_region = region_from_green_box_image(
            self.matcher.template_path(COMBAT_TEMPLATE_DIR, "Momentum Stack Scan Region"),
            region_from_ratio(
                DEMON_HUNTER_MOMENTUM_REGION_RATIO["left"],
                DEMON_HUNTER_MOMENTUM_REGION_RATIO["top"],
                DEMON_HUNTER_MOMENTUM_REGION_RATIO["width"],
                DEMON_HUNTER_MOMENTUM_REGION_RATIO["height"],
            ),
        )
        # Combat can accidentally open modal panels when clicks hit the bounty
        # tracker or follower portrait. Each boxed source image defines where to
        # scan, then a small stable title crop is matched inside that region.
        self.combat_menu_watch_targets = []
        bounty_menu_path = self.matcher.template_path(COMBAT_TEMPLATE_DIR, "Bounty Menu")
        bounty_menu_default_region = region_from_ratio(
            BOUNTY_MENU_REGION_RATIO["left"],
            BOUNTY_MENU_REGION_RATIO["top"],
            BOUNTY_MENU_REGION_RATIO["width"],
            BOUNTY_MENU_REGION_RATIO["height"],
        )
        self.bounty_menu_region = region_from_green_box_image(bounty_menu_path, bounty_menu_default_region)
        bounty_menu_title_path = self.matcher.template_path(COMBAT_TEMPLATE_DIR, "Bounty Menu Title")
        if bounty_menu_title_path.exists():
            self.bounty_menu_template = self.matcher.load(bounty_menu_title_path)
        else:
            self.bounty_menu_template = crop_gray_from_green_box_image(
                bounty_menu_path,
                bounty_menu_default_region,
                BOUNTY_MENU_TITLE_CROP_RATIO,
            )
        if self.bounty_menu_template is not None:
            self.combat_menu_watch_targets.append({
                "name": "Bounty menu",
                "template": self.bounty_menu_template,
                "region": self.bounty_menu_region,
                "threshold": BOUNTY_MENU_THRESHOLD,
            })
        follower_menu_path = self.matcher.template_path(COMBAT_TEMPLATE_DIR, "Follower Menu")
        follower_menu_default_region = region_from_ratio(
            FOLLOWER_MENU_REGION_RATIO["left"],
            FOLLOWER_MENU_REGION_RATIO["top"],
            FOLLOWER_MENU_REGION_RATIO["width"],
            FOLLOWER_MENU_REGION_RATIO["height"],
        )
        self.follower_menu_region = region_from_green_box_image(follower_menu_path, follower_menu_default_region)
        self.follower_menu_template = crop_gray_from_green_box_image(
            follower_menu_path,
            follower_menu_default_region,
            FOLLOWER_MENU_TITLE_CROP_RATIO,
        )
        if self.follower_menu_template is not None:
            self.combat_menu_watch_targets.append({
                "name": "Follower menu",
                "template": self.follower_menu_template,
                "region": self.follower_menu_region,
                "threshold": FOLLOWER_MENU_THRESHOLD,
            })
        self.repair_station_points = [
            {"x": x, "y": y}
            for x, y in REPAIR_STATION_COORDINATES
        ]
        self.salvage_tab_region = region_from_green_box_image(
            self.matcher.template_path(SALVAGE_TEMPLATE_DIR, "Salvage Tab Scan Region"),
            region_from_ratio(
                SALVAGE_TAB_REGION_RATIO["left"],
                SALVAGE_TAB_REGION_RATIO["top"],
                SALVAGE_TAB_REGION_RATIO["width"],
                SALVAGE_TAB_REGION_RATIO["height"],
            ),
        )
        self.salvage_button_region = region_from_green_box_image(
            self.matcher.template_path(SALVAGE_TEMPLATE_DIR, "Salvage Button Scan Region"),
            region_from_ratio(
                SALVAGE_BUTTON_REGION_RATIO["left"],
                SALVAGE_BUTTON_REGION_RATIO["top"],
                SALVAGE_BUTTON_REGION_RATIO["width"],
                SALVAGE_BUTTON_REGION_RATIO["height"],
            ),
        )
        self.salvage_confirmation_region = region_from_green_box_image(
            self.matcher.template_path(SALVAGE_TEMPLATE_DIR, "Salvage Confirmation Scan Region"),
            region_from_ratio(
                SALVAGE_CONFIRMATION_REGION_RATIO["left"],
                SALVAGE_CONFIRMATION_REGION_RATIO["top"],
                SALVAGE_CONFIRMATION_REGION_RATIO["width"],
                SALVAGE_CONFIRMATION_REGION_RATIO["height"],
            ),
        )
        self.salvage_button_point = {
            "x": self.salvage_coords["Salvage Button"]["x"],
            "y": self.salvage_coords["Salvage Button"]["y"],
        }
        self.salvage_tab_point = self.salvage_coords["Salvage Tab"]
        self.salvage_inventory_grid = region_from_green_box_image(
            self.matcher.template_path(SALVAGE_TEMPLATE_DIR, "Inventory Scan Region"),
            {
                "left": round(sw * SALVAGE_INVENTORY_REGION_RATIO["left"]),
                "top": round(sh * SALVAGE_INVENTORY_REGION_RATIO["top"]),
                "width": round(sw * SALVAGE_INVENTORY_REGION_RATIO["width"]),
                "height": round(sh * SALVAGE_INVENTORY_REGION_RATIO["height"]),
            },
        )
        self.stash_menu_region = region_from_green_box_image(
            self.matcher.template_path(SALVAGE_TEMPLATE_DIR, "Stash Menu Scan Region"),
            region_from_ratio(
                GG_STASH_MENU_REGION_RATIO["left"],
                GG_STASH_MENU_REGION_RATIO["top"],
                GG_STASH_MENU_REGION_RATIO["width"],
                GG_STASH_MENU_REGION_RATIO["height"],
            ),
        )
        self.gg_tab_region = region_from_green_box_image(
            self.matcher.template_path(SALVAGE_TEMPLATE_DIR, "GG Tab Scan Region"),
            region_from_ratio(
                GG_TAB_REGION_RATIO["left"],
                GG_TAB_REGION_RATIO["top"],
                GG_TAB_REGION_RATIO["width"],
                GG_TAB_REGION_RATIO["height"],
            ),
        )
        self.gg_stash_placement_region = region_from_green_box_image(
            self.matcher.template_path(SALVAGE_TEMPLATE_DIR, "GG Stash Scan Region and Placement"),
            region_from_ratio(
                GG_STASH_PLACEMENT_REGION_RATIO["left"],
                GG_STASH_PLACEMENT_REGION_RATIO["top"],
                GG_STASH_PLACEMENT_REGION_RATIO["width"],
                GG_STASH_PLACEMENT_REGION_RATIO["height"],
            ),
        )
        self.stash_point = self.salvage_coords["Stash Coordinates"]
        self.stash_points = [
            self.stash_point,
            {"x": self.stash_point["x"] + 20, "y": self.stash_point["y"]},
            {"x": self.stash_point["x"] - 20, "y": self.stash_point["y"]},
            {"x": self.stash_point["x"], "y": self.stash_point["y"] + 20},
            {"x": self.stash_point["x"], "y": self.stash_point["y"] - 20},
        ]
        self.left_panel_close_point = {"x": round(sw * 0.266), "y": round(sh * 0.018)}
        self.gg_stash_tab_point = self.salvage_coords["GG Stash Tab Coordinates"]
        LOGGER.info(
            "Loaded salvage coordinates: stash=%s,%s gg_tab=%s,%s",
            self.stash_point["x"],
            self.stash_point["y"],
            self.gg_stash_tab_point["x"],
            self.gg_stash_tab_point["y"],
        )
        self.gg_stash_placement_points = gg_stash_placement_points_from_image(
            self.matcher.template_path(SALVAGE_TEMPLATE_DIR, "GG Stash Scan Region and Placement")
        )
        self.start_game_button_point = self.start_game_coords["Diablo Start Game Button Coordinates"]
        self.battle_net_play_button_point = self.start_game_coords["Battle Net Play Button Coordinates"]
        self.leave_game_button_point = parse_single_coordinate(
            LEAVE_GAME_COORDINATES_PATH,
            {"x": round(sw * 0.125), "y": round(sh * 0.448)},
        )
        self.map_right_click_point = {"x": 1272, "y": 594}

        # Tk overlay setup. The window is borderless/topmost and later receives
        # Win32 styles so Diablo can remain the active input target.
        self.root = tk.Tk()
        self.root.title("Goblin Farming")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.configure(bg=OVERLAY_BG, highlightthickness=1, highlightbackground="#5A5143")
        self.root.geometry(f"{OVERLAY_WIDTH}x{OVERLAY_HEIGHT}+{self.overlay_x}+{self.overlay_y}")
        self.root.update_idletasks()
        self.root_hwnd = self.get_root_hwnd()
        self.apply_window_styles()
        self.build_overlay()
        self.root.bind("<ButtonPress-1>", self.start_overlay_drag)
        self.root.bind("<B1-Motion>", self.drag_overlay)
        self.root.bind("<ButtonRelease-1>", self.end_overlay_drag)
        self.root.protocol("WM_DELETE_WINDOW", self.exit_app)
        self.root.bind("<Escape>", lambda _event: self.request_stop())
        self.root.after(1000, self.make_new_game_if_diablo_closed)
        self.root.after(OVERLAY_VISIBILITY_REFRESH_MS, self.refresh_overlay_visibility)
        # Controller layout overlay disabled.
        # self.create_controller_layout_overlay()
        # self.root.after(CONTROLLER_LAYOUT_REFRESH_MS, self.refresh_controller_layout_overlay)
        threading.Thread(target=self.escape_stop_watcher, daemon=True).start()
        threading.Thread(target=self.combat_hotkey_watcher, daemon=True).start()
        threading.Thread(target=self.controller_watcher, daemon=True).start()
        LOGGER.info("Overlay started")

    def build_overlay(self):
        """Construct the overlay layout and route/action buttons."""
        self.add_controller_hint()
        self.add_close_button()
        y = OVERLAY_MARGIN_Y + OVERLAY_HEADER_ACTION_HEIGHT
        self.add_action_pair(y, ("Restore", self.restore_overlay), ("Lock/Unlock", self.toggle_lock))
        y += ACTION_BUTTON_HEIGHT + ACTION_ROW_GAP
        self.add_action_pair(y, ("Make New Game", self.make_new_game), ("Set Overlay Position", self.save_overlay_position))
        y += ACTION_BUTTON_HEIGHT + ACTION_ROW_GAP
        self.add_action_pair(y, ("Exit Game", self.exit_game_and_close), ("Reload Script", self.reload_app))
        y += ACTION_BUTTON_HEIGHT + 8

        self.status_label = tk.Label(self.root, bg=OVERLAY_BG, fg="#D24D45", font=("Segoe UI", 10), anchor="center")
        self.status_label.place(x=self.controls_x, y=y, width=self.controls_width, height=STATUS_HEIGHT)
        y += STATUS_HEIGHT
        y += SECTION_GAP
        y = self.add_controller_tuning(y)

        y = self.add_section(y, "Act 1", [
            "Northern Highlands",
            "The Weeping Hollow",
            "The Festering Woods",
            "Cathedral",
            "Royal Crypts",
        ])
        y = self.add_section(y + 6, "Act 2", [
            "City Of Caldeum",
            "Ancient Waterway",
            "Stinging Winds",
        ])
        y = self.add_section(y + 6, "Act 3", [
            "Battlefields",
            "Rakkis Crossing",
        ])
        self.add_section(y + 6, "Act 5", [
            "Pandemonium Fortress Level 1",
            "Pandemonium Fortress Level 2",
        ])
        self.apply_last_teleport_location()
        self.update_status("idle")

    def add_controller_hint(self):
        """Add controller mapping text in a left-side info panel."""
        tk.Label(
            self.root,
            text="Controller",
            bg=OVERLAY_BG,
            fg=OVERLAY_HEADER,
            font=("Segoe UI", 10, "bold"),
            anchor="center",
        ).place(x=OVERLAY_MARGIN_X, y=OVERLAY_MARGIN_Y + OVERLAY_HEADER_ACTION_HEIGHT, width=OVERLAY_CONTROLLER_INFO_WIDTH, height=SECTION_HEADER_HEIGHT)
        left_items = [
            ("LT", "Combat"),
            ("RT", "Teleport"),
            ("Y", "New Game"),
            ("B", "Exit Game"),
            ("X", "Right Click"),
        ]
        right_items = [
            ("A", "Hold Click"),
            ("View", "Level Map"),
            ("Menu", "Esc"),
            ("D-Up", "Inventory"),
            ("D-Right", "Waypoint"),
        ]
        col_width = (OVERLAY_CONTROLLER_INFO_WIDTH - OVERLAY_COLUMN_GAP) // 2
        start_y = OVERLAY_MARGIN_Y + OVERLAY_HEADER_ACTION_HEIGHT + SECTION_HEADER_HEIGHT + 6
        item_gap = 44
        for index, (button, action) in enumerate(left_items):
            self.add_controller_hint_item(OVERLAY_MARGIN_X, start_y + (index * item_gap), col_width, button, action)
        for index, (button, action) in enumerate(right_items):
            self.add_controller_hint_item(
                OVERLAY_MARGIN_X + col_width + OVERLAY_COLUMN_GAP,
                start_y + (index * item_gap),
                col_width,
                button,
                action,
            )

    def add_controller_hint_item(self, x, y, width, button, action):
        """Add one centered controller mapping item."""
        tk.Label(
            self.root,
            text=button,
            bg=OVERLAY_BUTTON_BG,
            fg=OVERLAY_HEADER,
            font=("Segoe UI", 9, "bold"),
            relief="solid",
            bd=1,
            anchor="center",
        ).place(x=x, y=y, width=width, height=21)
        tk.Label(
            self.root,
            text=action,
            bg=OVERLAY_BG,
            fg=OVERLAY_TEXT,
            font=("Segoe UI", 8),
            anchor="center",
        ).place(x=x, y=y + 22, width=width, height=19)

    def add_close_button(self):
        """Add the overlay close button and its hover styling."""
        button = tk.Label(
            self.root,
            text="X",
            bg=OVERLAY_CLOSE_BG,
            fg=OVERLAY_CLOSE_TEXT,
            font=("Segoe UI", 9, "bold"),
            relief="solid",
            bd=1,
            anchor="center",
        )
        button.place(x=OVERLAY_WIDTH - 24, y=4, width=18, height=18)
        button.bind("<Button-1>", lambda _event: self.confirm_kill_all_goblin_processes())
        button.bind("<Enter>", lambda _event: button.configure(bg=OVERLAY_CLOSE_HOVER_BG))
        button.bind("<Leave>", lambda _event: button.configure(bg=OVERLAY_CLOSE_BG))
        button.lift()

    def add_action_pair(self, y, left, right):
        """Add a two-button action row to the overlay."""
        self.add_button(left[0], left[1], self.controls_x, y, self.half_button_width)
        self.add_button(right[0], right[1], self.controls_x + self.half_button_width + OVERLAY_COLUMN_GAP, y, self.half_button_width)

    def add_controller_tuning(self, y):
        """Add live controller cursor tuning sliders to the overlay."""
        tk.Label(
            self.root,
            text="Controller",
            bg=OVERLAY_BG,
            fg=OVERLAY_HEADER,
            font=("Segoe UI", 10, "bold"),
            anchor="center",
        ).place(x=self.controls_x, y=y, width=self.controls_width, height=SECTION_HEADER_HEIGHT)
        self.controller_enabled_var = tk.BooleanVar(value=self.controller_enabled)
        tk.Checkbutton(
            self.root,
            text="Enabled",
            variable=self.controller_enabled_var,
            command=self.set_controller_enabled,
            bg=OVERLAY_BG,
            fg=OVERLAY_TEXT,
            activebackground=OVERLAY_BG,
            activeforeground=OVERLAY_TEXT,
            selectcolor=OVERLAY_BUTTON_BG,
            font=("Segoe UI", 9),
            anchor="e",
            relief="flat",
            bd=0,
            highlightthickness=0,
        ).place(x=self.controls_x + self.controls_width - 88, y=y, width=88, height=SECTION_HEADER_HEIGHT)
        y += SECTION_HEADER_HEIGHT + 2

        self.add_controller_slider_row(
            y,
            "Speed",
            CONTROLLER_CURSOR_SPEED_MIN,
            CONTROLLER_CURSOR_SPEED_MAX,
            CONTROLLER_CURSOR_SPEED_STEP,
            self.controller_cursor_speed,
            self.set_controller_cursor_speed,
            "controller_speed_value_label",
        )
        y += 42
        self.add_controller_slider_row(
            y,
            "Curve",
            CONTROLLER_RESPONSE_POWER_MIN,
            CONTROLLER_RESPONSE_POWER_MAX,
            CONTROLLER_RESPONSE_POWER_STEP,
            self.controller_response_curve,
            self.set_controller_response_curve,
            "controller_response_value_label",
        )
        return y + 45 + SECTION_GAP

    def add_controller_slider_row(self, y, label, minimum, maximum, step, value, command, value_label_name):
        """Create one compact slider row with a live numeric value label."""
        tk.Label(
            self.root,
            text=label,
            bg=OVERLAY_BG,
            fg=OVERLAY_TEXT,
            font=("Segoe UI", 9),
            anchor="w",
        ).place(x=self.controls_x, y=y, width=54, height=18)
        value_label = tk.Label(
            self.root,
            text=self.format_controller_slider_value(label, value),
            bg=OVERLAY_BG,
            fg=OVERLAY_TEXT,
            font=("Segoe UI", 9),
            anchor="e",
        )
        value_label.place(x=self.controls_x + self.controls_width - 70, y=y, width=70, height=18)
        setattr(self, value_label_name, value_label)

        slider = tk.Scale(
            self.root,
            from_=minimum,
            to=maximum,
            orient="horizontal",
            resolution=step,
            showvalue=False,
            command=command,
            bg=OVERLAY_BG,
            fg=OVERLAY_TEXT,
            troughcolor=OVERLAY_BUTTON_BG,
            activebackground=OVERLAY_BUTTON_ACTIVE_BG,
            highlightthickness=0,
            bd=0,
        )
        slider.set(value)
        slider.place(x=self.controls_x, y=y + 17, width=self.controls_width, height=24)

    def format_controller_slider_value(self, label, value):
        """Format controller tuning values for the overlay labels."""
        if label == "Speed":
            return f"{float(value):.0f}"
        return f"{float(value):.2f}"

    def load_controller_tuning(self):
        """Load saved controller cursor tuning values."""
        default_speed = float(CONTROLLER_CURSOR_SPEED_PIXELS_PER_SECOND)
        default_curve = float(CONTROLLER_CURSOR_RESPONSE_POWER)
        try:
            values = {}
            for line in CONTROLLER_TUNING_STATE.read_text(encoding="utf-8", errors="ignore").splitlines():
                if "=" not in line:
                    continue
                key, value = line.split("=", 1)
                values[key.strip().lower()] = float(value.strip())
            return {
                "speed": clamp(values.get("speed", default_speed), CONTROLLER_CURSOR_SPEED_MIN, CONTROLLER_CURSOR_SPEED_MAX),
                "curve": clamp(values.get("curve", default_curve), CONTROLLER_RESPONSE_POWER_MIN, CONTROLLER_RESPONSE_POWER_MAX),
            }
        except (OSError, ValueError):
            return {"speed": default_speed, "curve": default_curve}

    def save_controller_tuning(self):
        """Persist controller cursor tuning values."""
        try:
            CONTROLLER_TUNING_STATE.write_text(
                f"speed={self.controller_cursor_speed:.0f}\ncurve={self.controller_response_curve:.2f}\n",
                encoding="utf-8",
            )
        except OSError:
            LOGGER.exception("Failed to save controller tuning")

    def set_controller_cursor_speed(self, value):
        """Apply controller cursor speed slider changes immediately."""
        self.controller_cursor_speed = float(value)
        if self.controller_speed_value_label:
            self.controller_speed_value_label.configure(text=self.format_controller_slider_value("Speed", value))
        self.save_controller_tuning()

    def set_controller_response_curve(self, value):
        """Apply controller response curve slider changes immediately."""
        self.controller_response_curve = float(value)
        if self.controller_response_value_label:
            self.controller_response_value_label.configure(text=self.format_controller_slider_value("Curve", value))
        self.save_controller_tuning()

    def set_controller_enabled(self):
        """Enable or disable every controller mapping without restarting the overlay."""
        self.controller_enabled = bool(self.controller_enabled_var.get()) if self.controller_enabled_var else True
        if not self.controller_enabled:
            # The checkbox is the master gate for XInput. Release held mouse
            # buttons immediately so disabling cannot leave a stuck click.
            self.release_controller_a_mouse()
            self.release_controller_x_mouse()
            self.reset_controller_cursor_motion()
        LOGGER.info("Controller input enabled=%s", self.controller_enabled)

    def add_section(self, y, title, locations):
        """Add a labeled section of teleport buttons."""
        tk.Label(
            self.root,
            text=title,
            bg=OVERLAY_BG,
            fg=OVERLAY_HEADER,
            font=("Segoe UI", 10, "bold"),
            anchor="center",
        ).place(x=self.controls_x, y=y, width=self.controls_width, height=SECTION_HEADER_HEIGHT)
        y += SECTION_HEADER_HEIGHT + 3
        for location in locations:
            button = self.add_button(location, lambda loc=location: self.run_teleport(loc), self.controls_x, y, self.controls_width)
            self.teleport_buttons[location_key(location)] = button
            y += LOCATION_BUTTON_HEIGHT + LOCATION_BUTTON_GAP
        self.apply_teleport_reminder()
        return y

    def add_button(self, text, command, x, y, width):
        """Create one overlay button and wire it to a locked-interaction-safe command wrapper."""
        button = tk.Label(
            self.root,
            text=text,
            bg=OVERLAY_BUTTON_BG,
            fg=OVERLAY_BUTTON_TEXT,
            font=("Segoe UI", 10),
            relief="solid",
            bd=1,
            anchor="center",
        )
        height = ACTION_BUTTON_HEIGHT if width == self.half_button_width else LOCATION_BUTTON_HEIGHT
        button.place(x=x, y=y, width=width, height=height)
        button.bind("<Button-1>", lambda _event: self.run_overlay_command(command))
        button.bind("<B1-Motion>", lambda _event: "break")
        return button

    def get_root_hwnd(self):
        """Return the Win32 handle for the Tk root window."""
        hwnd = self.root.winfo_id()
        try:
            root_hwnd = user32.GetAncestor(hwnd, GA_ROOT)
            if root_hwnd:
                return root_hwnd
        except OSError:
            pass
        return hwnd

    def apply_window_styles(self):
        """Apply topmost/tool/no-activate/click-through styles to the overlay window."""
        style = user32.GetWindowLongW(self.root_hwnd, GWL_EXSTYLE)
        style |= WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
        if self.click_through:
            style |= WS_EX_TRANSPARENT
        else:
            style &= ~WS_EX_TRANSPARENT
        user32.SetWindowLongW(self.root_hwnd, GWL_EXSTYLE, style)
        user32.SetWindowPos(
            self.root_hwnd,
            HWND_TOPMOST,
            0,
            0,
            0,
            0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW,
        )

    def set_click_through(self, enabled):
        """Toggle whether the overlay ignores mouse input."""
        if enabled == self.click_through:
            return
        self.click_through = enabled
        self.apply_window_styles()

    def refresh_overlay_visibility(self):
        """Periodically keep the second-screen overlay topmost and styled."""
        try:
            self.root.deiconify()
            self.root.lift()
            self.root.attributes("-topmost", True)
            user32.ShowWindow(self.root_hwnd, SW_SHOWNOACTIVATE)
            self.apply_window_styles()
        except tk.TclError:
            return
        try:
            self.root.after(OVERLAY_VISIBILITY_REFRESH_MS, self.refresh_overlay_visibility)
        except tk.TclError:
            pass

    def create_controller_layout_overlay(self):
        """Create a click-through in-game controller cheat sheet over Diablo."""
        # Controller layout overlay disabled.
        return
        try:
            self.controller_layout_overlay = tk.Toplevel(self.root)
            self.controller_layout_overlay.overrideredirect(True)
            self.controller_layout_overlay.attributes("-topmost", True)
            self.controller_layout_overlay.configure(bg="#071016", highlightthickness=1, highlightbackground="#78D56D")
            try:
                self.controller_layout_overlay.attributes("-alpha", 0.88)
            except tk.TclError:
                pass
            self.controller_layout_overlay.withdraw()
            self.draw_controller_layout_overlay()
            self.controller_layout_overlay.update_idletasks()
            self.controller_layout_hwnd = (
                user32.GetAncestor(self.controller_layout_overlay.winfo_id(), GA_ROOT)
                or self.controller_layout_overlay.winfo_id()
            )
            self.apply_controller_layout_window_styles()
            self.refresh_controller_layout_overlay(reschedule=False)
        except tk.TclError:
            self.controller_layout_overlay = None
            self.controller_layout_canvas = None
            self.controller_layout_image = None
            self.controller_layout_hwnd = 0

    def draw_controller_layout_overlay(self):
        """Draw the controller image and current bindings onto the in-game helper overlay."""
        width = 700
        height = 467
        canvas = tk.Canvas(
            self.controller_layout_overlay,
            width=width,
            height=height,
            bg="#071016",
            highlightthickness=0,
            bd=0,
        )
        canvas.pack(fill="both", expand=True)
        self.controller_layout_canvas = canvas
        try:
            self.controller_layout_image = tk.PhotoImage(file=str(CONTROLLER_LAYOUT_IMAGE_PATH))
            canvas.create_image(0, 0, image=self.controller_layout_image, anchor="nw")
        except tk.TclError:
            canvas.create_text(
                width // 2,
                22,
                text="CONTROLLER",
                fill="#F1D58A",
                font=("Segoe UI", 12, "bold"),
            )

        self.draw_controller_layout_label(64, 46, "LB: Unused")
        self.draw_controller_layout_label(64, 102, "L-stick: Move")
        self.draw_controller_layout_label(64, 216, "D-up: Inventory")
        self.draw_controller_layout_label(313, 156, "View: Map")
        self.draw_controller_layout_label(386, 156, "Menu: Esc")
        self.draw_controller_layout_label(89, 354, "LB: Unused")
        self.draw_controller_layout_label(89, 396, "LT: Combat")
        self.draw_controller_layout_label(346, 26, "Guide: Unused")
        self.draw_controller_layout_label(618, 46, "RB: Unused")
        self.draw_controller_layout_label(623, 91, "Y: Unused")
        self.draw_controller_layout_label(623, 132, "B: Exit")
        self.draw_controller_layout_label(623, 173, "A: Hold click")
        self.draw_controller_layout_label(623, 216, "R-stick: Cursor")
        self.draw_controller_layout_label(610, 354, "RB: Unused")
        self.draw_controller_layout_label(610, 396, "RT: Teleport")

    def draw_controller_layout_label(self, x, y, text):
        """Draw one high-contrast controller binding label on the layout canvas."""
        if not self.controller_layout_canvas:
            return
        font = ("Segoe UI", 9, "bold")
        self.controller_layout_canvas.create_text(x + 1, y + 1, text=text, fill="#020403", font=font, anchor="center")
        self.controller_layout_canvas.create_text(
            x,
            y,
            text=text,
            fill="#FFF4B8",
            font=font,
            anchor="center",
        )

    def apply_controller_layout_window_styles(self):
        """Make the in-game controller layout topmost, no-activate, and click-through."""
        if not self.controller_layout_hwnd:
            return
        style = user32.GetWindowLongW(self.controller_layout_hwnd, GWL_EXSTYLE)
        style |= WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE | WS_EX_TRANSPARENT
        user32.SetWindowLongW(self.controller_layout_hwnd, GWL_EXSTYLE, style)
        user32.SetWindowPos(
            self.controller_layout_hwnd,
            HWND_TOPMOST,
            0,
            0,
            0,
            0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW,
        )

    def controller_layout_should_show(self):
        """Return True when the in-game controller layout should be visible."""
        # Controller layout overlay disabled.
        return False

    def refresh_controller_layout_overlay(self, reschedule=True):
        """Position/show the controller layout over Diablo, hiding it during scripted flows."""
        # Controller layout overlay disabled.
        return
        try:
            if not self.controller_layout_overlay or not self.controller_layout_overlay.winfo_exists():
                return
            rect = diablo_window_rect()
            if not rect or not self.controller_layout_should_show():
                self.controller_layout_overlay.withdraw()
                self.controller_layout_region = None
            else:
                width = round((rect.right - rect.left) * CONTROLLER_LAYOUT_REGION_RATIO["width"])
                height = round((rect.bottom - rect.top) * CONTROLLER_LAYOUT_REGION_RATIO["height"])
                x = rect.left + round((rect.right - rect.left) * CONTROLLER_LAYOUT_REGION_RATIO["left"])
                y = rect.top + round((rect.bottom - rect.top) * CONTROLLER_LAYOUT_REGION_RATIO["top"])
                self.controller_layout_region = {"left": x, "top": y, "width": width, "height": height}
                self.controller_layout_overlay.geometry(f"{width}x{height}+{x}+{y}")
                self.controller_layout_overlay.deiconify()
                self.controller_layout_overlay.lift()
                self.controller_layout_overlay.attributes("-topmost", True)
                self.apply_controller_layout_window_styles()
        except tk.TclError:
            return
        finally:
            if reschedule:
                try:
                    self.root.after(CONTROLLER_LAYOUT_REFRESH_MS, self.refresh_controller_layout_overlay)
                except tk.TclError:
                    pass

    def run_overlay_command(self, command):
        """Run a button command only when overlay interactions are currently allowed."""
        if self.overlay_interaction_locked():
            return "break"
        command()
        return "break"

    def overlay_interaction_locked(self):
        """Return True when the overlay should ignore mouse interaction."""
        return self.running or self.combat_running

    def cursor_is_in_combat_no_click_region(self):
        """Avoid combat autoclicks over UI regions that should not be clicked."""
        point = current_cursor_point()
        if point is None:
            return False
        for region in self.monk_no_click_regions:
            if (
                region["left"] <= point.x < region["left"] + region["width"]
                and region["top"] <= point.y < region["top"] + region["height"]
            ):
                return True
        return False

    def combat_mouse_click_is_safe(self):
        """Return True when combat loops are allowed to click inside Diablo."""
        return not self.cursor_is_in_combat_no_click_region()

    def start_overlay_drag(self, event):
        """Begin dragging the overlay when it is unlocked."""
        if self.overlay_locked or self.overlay_interaction_locked():
            self.drag_start = None
            return
        self.drag_start = {
            "mouse_x": event.x_root,
            "mouse_y": event.y_root,
            "overlay_x": self.overlay_x,
            "overlay_y": self.overlay_y,
        }

    def drag_overlay(self, event):
        """Move the overlay with the mouse while dragging."""
        if not self.drag_start:
            return
        self.overlay_x = self.drag_start["overlay_x"] + event.x_root - self.drag_start["mouse_x"]
        self.overlay_y = self.drag_start["overlay_y"] + event.y_root - self.drag_start["mouse_y"]
        self.root.geometry(f"{OVERLAY_WIDTH}x{OVERLAY_HEIGHT}+{self.overlay_x}+{self.overlay_y}")

    def end_overlay_drag(self, _event):
        """Clear drag state after moving the overlay."""
        self.drag_start = None

    def set_button_normal(self, button):
        """Apply the normal visual state to an overlay button."""
        if not button:
            return
        button.configure(bg=OVERLAY_BUTTON_BG, fg=OVERLAY_BUTTON_TEXT, font=("Segoe UI", 10))

    def set_button_active(self, button):
        """Mark a button as the most recently executed route/location."""
        if not button:
            return
        button.configure(bg=OVERLAY_BUTTON_ACTIVE_BG, fg=OVERLAY_BUTTON_TEXT, font=("Segoe UI", 10, "bold"))

    def set_button_queued(self, button):
        """Mark a button as the suggested/queued next route step."""
        if not button:
            return
        button.configure(bg=OVERLAY_BUTTON_QUEUED_BG, fg=OVERLAY_BUTTON_TEXT, font=("Segoe UI", 10, "bold"))

    def restore_button_state(self, button):
        """Reapply the correct visual state for a button based on active/queued state."""
        if not button:
            return
        if button is self.last_button:
            self.set_button_active(button)
        elif button is self.queued_button:
            self.set_button_queued(button)
        elif self.teleport_buttons.get(self.reminder_button_key) is button:
            self.set_button_queued(button)
        else:
            self.set_button_normal(button)

    def load_teleport_reminder(self):
        """Load the persisted queued teleport reminder from temp storage."""
        if not TELEPORT_REMINDER_STATE.exists():
            return ""
        try:
            for line in TELEPORT_REMINDER_STATE.read_text(encoding="utf-8", errors="ignore").splitlines():
                key = location_key(line)
                if key:
                    return key
        except OSError:
            pass
        return ""

    def save_teleport_reminder(self):
        """Persist the queued teleport reminder to temp storage."""
        try:
            TELEPORT_REMINDER_STATE.write_text(self.reminder_button_key or "", encoding="utf-8")
        except OSError:
            pass

    def apply_teleport_reminder(self):
        """Apply the persisted reminder to the matching overlay button."""
        self.set_button_queued(self.teleport_buttons.get(self.reminder_button_key))

    def load_last_teleport_location(self):
        """Load the persisted last teleport location from temp storage."""
        if not LAST_TELEPORT_STATE.exists():
            return ""
        try:
            text = LAST_TELEPORT_STATE.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return ""
        return location_key(text.strip())

    def save_last_teleport_location(self, location_name):
        """Persist the last teleport location to temp storage."""
        try:
            LAST_TELEPORT_STATE.write_text(location_name or "", encoding="utf-8")
        except OSError:
            pass

    def apply_last_teleport_location(self):
        """Restore the last route button and queue the next route step on startup."""
        button = self.teleport_buttons.get(self.last_teleport_key)
        self.last_button = button
        self.restore_button_state(button)
        self.queue_next_for_route_key(self.last_teleport_key)

    def set_last_teleport_location(self, location_name):
        """Update UI and persistence for the most recent teleport location."""
        key = location_key(location_name)
        if not key:
            return
        self.last_teleport_key = key
        previous_button = self.last_button
        self.last_button = self.teleport_buttons.get(key)
        self.save_last_teleport_location(location_name)
        self.restore_button_state(previous_button)
        self.restore_button_state(self.last_button)
        self.queue_next_for_route_key(key)

    def record_last_teleport(self, location_name):
        """Persist route progress after a successful teleport."""
        self.save_last_teleport_location(location_name)
        try:
            self.root.after(0, lambda loc=location_name: self.set_last_teleport_location(loc))
        except (AttributeError, tk.TclError):
            self.set_last_teleport_location(location_name)

    def set_teleport_reminder(self, location_name):
        """Persist and show the next route button the user should press."""
        key = location_key(location_name)
        if not key or key == self.reminder_button_key:
            return
        previous_queued_button = self.queued_button
        previous_button = self.teleport_buttons.get(self.reminder_button_key)
        self.reminder_button_key = key
        self.queued_button = self.teleport_buttons.get(key)
        self.save_teleport_reminder()
        if previous_queued_button is not previous_button:
            self.restore_button_state(previous_queued_button)
        self.restore_button_state(previous_button)
        self.restore_button_state(self.queued_button)

    def clear_teleport_reminder(self, location_name):
        """Clear a queued reminder when the user manually selects that location."""
        key = location_key(location_name)
        if key != self.reminder_button_key:
            return
        button = self.teleport_buttons.get(key)
        self.reminder_button_key = ""
        if self.queued_button is button:
            self.queued_button = None
        self.save_teleport_reminder()
        self.restore_button_state(button)

    def clear_route_button_state(self):
        """Reset active/queued route state at the beginning of a full automation flow."""
        self.last_teleport_key = ""
        self.last_button = None
        self.queued_button = None
        self.reminder_button_key = ""
        self.pending_auto_teleport = None
        self.pending_hotkey_teleport = None
        self.save_last_teleport_location("")
        self.save_teleport_reminder()
        for button in self.teleport_buttons.values():
            self.set_button_normal(button)

    def prepare_teleport_button_state(self, location_name):
        """Clear stale route UI state before launching a manual teleport."""
        self.clear_teleport_reminder(location_name)
        self.clear_queued_button()

    def queue_next_teleport_button(self, location_name):
        """Highlight one route button as the next suggested teleport."""
        button = self.teleport_buttons.get(location_key(location_name))
        if not button:
            return
        self.clear_queued_button(except_button=button)
        self.set_button_queued(button)
        self.queued_button = button

    def set_only_queued_button(self, location_name):
        """Clear current queue state and mark a single location as queued."""
        self.queued_button = None
        for button in self.teleport_buttons.values():
            self.restore_button_state(button)
        self.queue_next_teleport_button(location_name)
        self.set_teleport_reminder(location_name)

    def route_next_for_key(self, key):
        """Return the configured next location in the farming route."""
        if not key:
            return ""
        for location_name, next_location in ROUTE_NEXT_TELEPORTS.items():
            if location_key(location_name) == key:
                return next_location
        return ""

    def queue_next_for_route_key(self, key):
        """Queue the next route step for a normalized location key."""
        next_location = self.route_next_for_key(key)
        if next_location:
            self.set_only_queued_button(next_location)

    def route_location_for_current_location(self, current_location):
        """Map a detected in-game location title back to the closest route stop."""
        if not current_location:
            return ""
        route_locations = []
        for location_name, next_location in ROUTE_NEXT_TELEPORTS.items():
            if location_name not in route_locations:
                route_locations.append(location_name)
            if next_location not in route_locations:
                route_locations.append(next_location)
        for route_location in route_locations:
            if location_matches_route_target(current_location, route_location):
                return route_location
        return ""

    def reconcile_hotkey_route_progress(self, current_location):
        """Repair route state and return a target that must be retried, if any."""
        pending = self.pending_hotkey_teleport
        if pending:
            target = pending.get("target", "")
            from_key = pending.get("from_key", "")
            if location_matches_route_target(current_location, target):
                LOGGER.info("Hotkey route reconciled: current location confirmed pending target %s", target)
                self.pending_hotkey_teleport = None
                if self.last_teleport_key != location_key(target):
                    self.set_last_teleport_location(target)
                return ""
            from_route_location = pending.get("from_route_location", pending.get("from_location", ""))
            LOGGER.info(
                "Hotkey route pending target %s not confirmed at current location %s; retrying from %s",
                target,
                current_location or "unknown",
                from_route_location or from_key or "unknown",
            )
            if from_key:
                self.last_teleport_key = from_key
                self.last_button = self.teleport_buttons.get(from_key)
                self.save_last_teleport_location(from_route_location)
                for button in self.teleport_buttons.values():
                    self.restore_button_state(button)
            if target:
                self.set_only_queued_button(target)
            return target

        route_location = self.route_location_for_current_location(current_location)
        if route_location and location_key(route_location) != self.last_teleport_key:
            LOGGER.info(
                "Hotkey route reconciled current location %s to route stop %s",
                current_location,
                route_location,
            )
            self.pending_hotkey_teleport = None
            self.set_last_teleport_location(route_location)
            return ""
        if self.last_teleport_key and not self.queued_button:
            self.queue_next_for_route_key(self.last_teleport_key)
        return ""

    def auto_route_next_location(self, current_location):
        """Return the next auto-route target using current in-game area exceptions."""
        current_key = location_key(current_location)
        current_route_location = self.route_location_for_current_location(current_location)
        if current_route_location and self.last_teleport_key != location_key(current_route_location):
            LOGGER.info(
                "Auto route reconciled current location %s to route stop %s",
                current_location,
                current_route_location,
            )
            self.set_last_teleport_location(current_route_location)
        if current_key == location_key("Western Channel Level 2"):
            # Western Channel 2 is the wrong branch for advancing the route, so
            # go back to the Ancient Waterway waypoint and try the other side.
            return "Ancient Waterway"
        if self.last_teleport_key == location_key("Ancient Waterway"):
            if current_key == location_key("Eastern Channel Level 2"):
                return "Stinging Winds"
            LOGGER.info(
                "Auto route teleport held in Ancient Waterway branch at %s until Eastern Channel Level 2 is reached",
                current_location or "unknown",
            )
            return ""
        if self.last_teleport_key == location_key(STINGING_WINDS_ROUTE_LOCATION):
            if current_key == location_key(STINGING_WINDS_AUTO_TELEPORT_READY_LOCATION):
                return "Battlefields"
            LOGGER.info(
                "Auto route teleport held in %s at %s until %s is reached",
                STINGING_WINDS_ROUTE_LOCATION,
                current_location or "unknown",
                STINGING_WINDS_AUTO_TELEPORT_READY_LOCATION,
            )
            return ""
        return self.route_next_for_key(self.last_teleport_key)

    def route_end_is_active(self):
        """Return True when the current active button is the route end marker."""
        key = location_key(ROUTE_END_LOCATION)
        return self.last_teleport_key == key and self.last_button is self.teleport_buttons.get(key)

    def auto_teleport_allowed_after_combat(self, current_location):
        """Return True when combat toggle-off should move to the next route stop."""
        current_route_location = self.route_location_for_current_location(current_location)
        active_route_key = self.last_teleport_key
        if current_route_location in {
            CALDEUM_ROUTE_LOCATION,
            BATTLEFIELDS_ROUTE_LOCATION,
            STINGING_WINDS_ROUTE_LOCATION,
        }:
            active_route_key = location_key(current_route_location)
        # City Of Caldeum spans several connected areas. Stopping combat in the
        # early city/sewer/causeway areas should not advance the route; Ruined
        # Cistern is the explicit "done here, move on" signal for that stop.
        if active_route_key == location_key(CALDEUM_ROUTE_LOCATION):
            current_key = location_key(current_location)
            if current_key == location_key(CALDEUM_AUTO_TELEPORT_READY_LOCATION):
                return True
            hold_keys = {location_key(name) for name in CALDEUM_AUTO_TELEPORT_HOLD_LOCATIONS}
            if current_key in hold_keys:
                LOGGER.info(
                    "Auto route teleport held at %s until %s is reached",
                    current_location or "unknown Caldeum area",
                    CALDEUM_AUTO_TELEPORT_READY_LOCATION,
                )
                return False
            LOGGER.info(
                "Auto route teleport held for %s because current location is %s, not %s",
                CALDEUM_ROUTE_LOCATION,
                current_location or "unknown",
                CALDEUM_AUTO_TELEPORT_READY_LOCATION,
            )
            return False
        if active_route_key == location_key(BATTLEFIELDS_ROUTE_LOCATION):
            current_key = location_key(current_location)
            if current_key == location_key(BATTLEFIELDS_AUTO_TELEPORT_READY_LOCATION):
                return True
            hold_keys = {location_key(name) for name in BATTLEFIELDS_AUTO_TELEPORT_HOLD_LOCATIONS}
            if current_key in hold_keys:
                LOGGER.info(
                    "Auto route teleport held at %s until %s is reached",
                    current_location or "unknown Battlefields area",
                    BATTLEFIELDS_AUTO_TELEPORT_READY_LOCATION,
                )
                return False
            LOGGER.info(
                "Auto route teleport held for %s because current location is %s, not %s",
                BATTLEFIELDS_ROUTE_LOCATION,
                current_location or "unknown",
                BATTLEFIELDS_AUTO_TELEPORT_READY_LOCATION,
            )
            return False
        if active_route_key == location_key(STINGING_WINDS_ROUTE_LOCATION):
            current_key = location_key(current_location)
            if current_key == location_key(STINGING_WINDS_AUTO_TELEPORT_READY_LOCATION):
                return True
            LOGGER.info(
                "Auto route teleport held for %s because current location is %s, not %s",
                STINGING_WINDS_ROUTE_LOCATION,
                current_location or "unknown",
                STINGING_WINDS_AUTO_TELEPORT_READY_LOCATION,
            )
            return False
        return True

    def auto_teleport_after_combat_toggle(self):
        """Teleport to the next route location after the user toggles combat off."""
        if self.running or self.combat_running:
            return
        current_location = self.detect_current_location_by_image()
        if location_key(current_location) == location_key("Whimsydale"):
            LOGGER.info("Combat toggle-off auto route skipped in Whimsydale; use an overlay button manually")
            return
        pending = self.pending_auto_teleport
        if pending and location_matches_route_target(current_location, pending.get("target", "")):
            LOGGER.info("Auto route pending target %s is already current; committing route progress", pending["target"])
            self.pending_auto_teleport = None
            self.record_last_teleport(pending["target"])
            return
        if not self.auto_teleport_allowed_after_combat(current_location):
            return
        if self.route_end_is_active():
            LOGGER.info("Combat toggle-off auto route: route complete; starting Make New Game flow")
            self.make_new_game()
            return
        if pending and pending.get("from_key") == self.last_teleport_key:
            next_location = pending["target"]
            LOGGER.info("Combat toggle-off auto route retrying pending target %s", next_location)
        else:
            next_location = self.auto_route_next_location(current_location)
        if next_location:
            LOGGER.info(
                "Combat toggle-off auto route pending teleport: current=%s active_route=%s next=%s",
                current_location or "unknown",
                self.last_button.cget("text") if self.last_button else self.last_teleport_key or "unknown",
                next_location,
            )
            self.start_auto_route_teleport(next_location)
            return
        LOGGER.info("Auto route teleport skipped: no next route step for %s", self.last_teleport_key or "unknown")

    def run_next_route_teleport_hotkey(self):
        """Run the next route teleport from the number-1 hotkey."""
        if self.running or self.combat_running:
            return
        current_location = self.detect_current_location_by_image()
        retry_location = self.reconcile_hotkey_route_progress(current_location)
        if location_key(current_location) == location_key("Whimsydale"):
            LOGGER.info("Number-1 next teleport skipped in Whimsydale")
            self.show_splash("Clear this area before teleporting")
            return
        if not self.auto_teleport_allowed_after_combat(current_location):
            self.show_splash("Clear this area before teleporting")
            return
        if not self.teleport_failsafe_allows():
            blocked_location = self.teleport_failsafe_blocked_location()
            self.show_splash("Clear this area before teleporting")
            return
        if self.route_end_is_active():
            LOGGER.info("Number-1 next teleport reached route end; starting Make New Game flow")
            self.make_new_game()
            return

        next_location = retry_location or self.auto_route_next_location(current_location)
        if not next_location:
            LOGGER.info("Number-1 next teleport skipped: no next route step for %s", self.last_teleport_key or "unknown")
            if self.route_next_for_key(self.last_teleport_key):
                self.show_splash("Clear this area before teleporting")
            return
        LOGGER.info("Number-1 next teleport to %s", next_location)
        previous_route_location = self.last_button.cget("text") if self.last_button else ""
        self.pending_hotkey_teleport = {
            "from_location": current_location,
            "from_key": self.last_teleport_key,
            "from_route_location": previous_route_location,
            "target": next_location,
        }
        self.set_last_teleport_location(next_location)
        self.run_teleport(next_location, verify=False)

    def start_auto_route_teleport(self, location_name):
        """Start a verified automatic route teleport without committing route progress yet."""
        if self.running or self.combat_running:
            return
        if self.auto_route_teleport_thread and self.auto_route_teleport_thread.is_alive():
            LOGGER.info("Auto route teleport skipped: worker already running")
            return
        if self.auto_route_retry_after_id:
            try:
                self.root.after_cancel(self.auto_route_retry_after_id)
            except tk.TclError:
                pass
            self.auto_route_retry_after_id = None
        from_key = self.last_teleport_key
        if not from_key:
            LOGGER.info("Auto route teleport skipped: no active route checkpoint")
            return
        pending = self.pending_auto_teleport
        if not pending or pending.get("from_key") != from_key or pending.get("target") != location_name:
            # Auto teleports are transactions: the route stays on from_key until
            # the target location title confirms arrival.
            pending = {"from_key": from_key, "target": location_name, "attempts": 0}
            self.pending_auto_teleport = pending
        else:
            pending["attempts"] = 0
        thread = threading.Thread(target=self.auto_route_teleport_worker, args=(pending,), daemon=True)
        self.auto_route_teleport_thread = thread
        thread.start()

    def schedule_pending_auto_route_retry(self, pending):
        """Retry an unfinished auto-route teleport after the worker has gone idle."""
        if self.pending_auto_teleport is not pending:
            return

        def retry():
            self.auto_route_retry_after_id = None
            if self.pending_auto_teleport is not pending:
                return
            if self.running or self.combat_running:
                self.schedule_pending_auto_route_retry(pending)
                return
            current_location = self.detect_current_location_by_image()
            if location_matches_route_target(current_location, pending.get("target", "")):
                LOGGER.info("Auto route retry saw target %s is already current; committing route progress", pending["target"])
                self.pending_auto_teleport = None
                self.record_last_teleport(pending["target"])
                return
            if pending.get("from_key") != self.last_teleport_key:
                LOGGER.info("Auto route retry cancelled: active route checkpoint changed")
                return
            if not self.auto_teleport_allowed_after_combat(current_location):
                LOGGER.info("Auto route retry held at current location %s", current_location or "unknown")
                self.schedule_pending_auto_route_retry(pending)
                return
            LOGGER.info("Auto route retry re-arming pending target %s", pending["target"])
            self.start_auto_route_teleport(pending["target"])

        self.run_on_ui_thread(
            lambda: setattr(
                self,
                "auto_route_retry_after_id",
                self.root.after(int(AUTO_ROUTE_TELEPORT_REARM_SECONDS * 1000), retry),
            )
        )

    def auto_route_teleport_worker(self, pending):
        """Retry a pending auto teleport and commit route progress only after arrival."""
        self.running = True
        self.update_status("running")
        try:
            while pending["attempts"] < AUTO_ROUTE_TELEPORT_MAX_ATTEMPTS and not self.stop_requested:
                pending["attempts"] += 1
                target = pending["target"]
                LOGGER.info(
                    "Auto route teleport attempt %s/%s from %s to %s",
                    pending["attempts"],
                    AUTO_ROUTE_TELEPORT_MAX_ATTEMPTS,
                    pending["from_key"],
                    target,
                )
                arrived = self.teleport_to_location(
                    target,
                    verify_arrival=True,
                    record_progress=False,
                    bypass_failsafe=True,
                )
                if arrived:
                    LOGGER.info("Auto route teleport image-confirmed arrival at %s; committing route progress", target)
                    self.pending_auto_teleport = None
                    self.record_last_teleport(target)
                    return
                LOGGER.info("Auto route teleport to %s was not image-confirmed", target)
                if pending["attempts"] < AUTO_ROUTE_TELEPORT_MAX_ATTEMPTS:
                    if self.safe_sleep(AUTO_ROUTE_TELEPORT_RETRY_SECONDS) is False:
                        break
            # Keep the same target queued so the next combat toggle-off retries
            # the unfinished transaction instead of skipping ahead. Also arm a
            # delayed retry so an interrupted auto-teleport does not wait
            # forever for another combat toggle.
            self.run_on_ui_thread(lambda loc=pending["target"]: self.set_only_queued_button(loc))
            self.schedule_pending_auto_route_retry(pending)
        finally:
            self.running = False
            self.stop_requested = False
            self.update_status("idle")

    def clear_queued_button(self, except_button=None):
        """Remove queued styling except for an optional button to preserve."""
        if not self.queued_button:
            return
        button = self.queued_button
        self.queued_button = None
        if except_button is not None and button is except_button:
            self.restore_button_state(button)
            self.queued_button = button
            return
        self.restore_button_state(button)

    def reset_last_pressed_button(self):
        """Clear route queue state when a broad automation flow begins."""
        self.clear_queued_button()

    def update_status(self, state=None):
        """Update the status label and interaction lock based on idle/running/combat state."""
        if state:
            self.status_state = state
        lock_text = "Locked" if self.overlay_locked else "Unlocked"
        if self.status_state == "running":
            self.status_label.configure(text=f"Status: Running    Overlay: {lock_text}", fg="#7DBB61")
        elif self.status_state == "combat":
            combat_name = {
                "demon_hunter": "Demon Hunter",
                "witch_doctor": "Witch Doctor",
            }.get(self.combat_class, "Monk")
            self.status_label.configure(text=f"Status: {combat_name} Combat    Overlay: {lock_text}", fg="#7DBB61")
        else:
            self.status_label.configure(text=f"Status: Idle    Overlay: {lock_text}", fg="#D24D45")
        self.set_click_through(self.overlay_interaction_locked())
        # Controller layout overlay disabled.
        # self.refresh_controller_layout_overlay(reschedule=False)

    def show_splash(self, message, duration=5.0):
        """Show a red, click-through blocked-teleport notification centered on Diablo."""
        def render():
            if self.splash_after_id:
                try:
                    self.root.after_cancel(self.splash_after_id)
                except tk.TclError:
                    pass
                self.splash_after_id = None
            try:
                if self.splash and self.splash.winfo_exists():
                    self.splash_label.configure(text=message)
                else:
                    self.splash = tk.Toplevel(self.root)
                    self.splash.overrideredirect(True)
                    self.splash.attributes("-topmost", True)
                    self.splash.configure(bg="#210707", highlightthickness=2, highlightbackground="#E23B3B")
                    try:
                        self.splash.attributes("-alpha", 0.90)
                    except tk.TclError:
                        pass
                    self.splash_label = tk.Label(
                        self.splash,
                        text=message,
                        bg="#210707",
                        fg="#FFE8E8",
                        font=("Segoe UI", 15, "bold"),
                        padx=28,
                        pady=16,
                    )
                    self.splash_label.pack(fill="both", expand=True)
                self.position_splash()
                self.splash.update_idletasks()
                hwnd = user32.GetAncestor(self.splash.winfo_id(), GA_ROOT) or self.splash.winfo_id()
                style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
                style |= WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE | WS_EX_TRANSPARENT
                user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)
                user32.SetWindowPos(
                    hwnd,
                    HWND_TOPMOST,
                    0,
                    0,
                    0,
                    0,
                    SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW,
                )
                self.splash_after_id = self.root.after(int(duration * 1000), self.hide_splash)
            except tk.TclError:
                pass

        self.run_on_ui_thread(render)

    def position_splash(self):
        """Center the splash over the Diablo window, with screen-center fallback."""
        if not self.splash:
            return
        self.splash.update_idletasks()
        width = max(260, self.splash.winfo_reqwidth())
        height = max(72, self.splash.winfo_reqheight())
        rect = diablo_window_rect()
        if rect:
            center_x = rect.left + ((rect.right - rect.left) / 2)
            center_y = rect.top + ((rect.bottom - rect.top) / 2)
        else:
            sw, sh = screen_size()
            center_x = sw / 2
            center_y = sh / 2
        x = round(center_x - (width / 2))
        y = round(center_y - (height / 2))
        self.splash.geometry(f"{width}x{height}+{x}+{y}")

    def center_window_over_diablo(self, window, width, height):
        """Center a Tk window over Diablo, falling back to the primary screen."""
        rect = diablo_window_rect()
        if rect:
            center_x = rect.left + ((rect.right - rect.left) / 2)
            center_y = rect.top + ((rect.bottom - rect.top) / 2)
        else:
            sw, sh = screen_size()
            center_x = sw / 2
            center_y = sh / 2
        x = round(center_x - (width / 2))
        y = round(center_y - (height / 2))
        window.geometry(f"{width}x{height}+{x}+{y}")

    def hide_splash(self):
        """Hide the active hotkey notification."""
        self.splash_after_id = None
        if not self.splash:
            return
        try:
            self.splash.destroy()
        except tk.TclError:
            pass
        self.splash = None
        self.splash_label = None

    def request_stop(self):
        """Handle Escape/manual stop requests from the overlay or keyboard hook."""
        LOGGER.info("Stop requested")
        if self.combat_running:
            self.stop_monk_combat("overlay stop request")
            return
        if self.running:
            self.stop_requested = True
            release_inputs()
            self.release_controller_a_mouse()
            self.release_controller_x_mouse()
            self.update_status("idle")
        else:
            self.press_escape_for_game()

    def request_stop_from_watcher(self):
        """Schedule a stop request from the background Escape watcher thread."""
        if not self.running and not self.combat_running:
            return
        LOGGER.info("Stop requested by Esc watcher")
        if self.combat_running:
            self.stop_monk_combat("Esc watcher")
        self.stop_requested = True
        release_inputs()
        self.release_controller_a_mouse()
        self.release_controller_x_mouse()
        try:
            self.root.after(0, lambda: self.update_status("idle"))
        except tk.TclError:
            pass

    def escape_stop_watcher(self):
        """Install a low-level keyboard hook so Escape and automation hotkeys work globally."""
        self.escape_hook_thread_id = kernel32.GetCurrentThreadId()
        automation_hotkeys_down = set()

        @LowLevelKeyboardProc
        def hook_proc(code, wparam, lparam):
            """Handle global Escape and number-row automation hotkeys."""
            if code >= 0:
                keyboard = ctypes.cast(lparam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                is_escape = keyboard.vkCode == VK_ESCAPE
                is_escape_message = wparam in (WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP)
                is_automation_hotkey = keyboard.vkCode in (VK_1, VK_2)
                is_key_message = wparam in (WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP)
                is_injected = bool(keyboard.flags & LLKHF_INJECTED)

                if is_automation_hotkey and is_key_message and not is_injected and diablo_is_active():
                    if wparam in (WM_KEYUP, WM_SYSKEYUP):
                        automation_hotkeys_down.discard(keyboard.vkCode)
                        return 1

                    if keyboard.vkCode not in automation_hotkeys_down and not self.running and not self.combat_running:
                        automation_hotkeys_down.add(keyboard.vkCode)
                        if keyboard.vkCode == VK_1:
                            self.root.after(0, self.run_next_route_teleport_hotkey)
                        elif keyboard.vkCode == VK_2:
                            self.root.after(0, self.confirm_exit_game_hotkey)
                    elif keyboard.vkCode not in automation_hotkeys_down:
                        automation_hotkeys_down.add(keyboard.vkCode)
                    return 1
                if is_escape and is_escape_message and self.overlay_interaction_locked():
                    if is_injected or time.time() < self.allow_game_escape_until:
                        return user32.CallNextHookEx(self.escape_hook, code, wparam, lparam)
                    if wparam in (WM_KEYDOWN, WM_SYSKEYDOWN) and time.time() >= self.ignore_esc_until:
                        self.ignore_esc_until = time.time() + 0.35
                        self.request_stop_from_watcher()
                    return 1
            return user32.CallNextHookEx(self.escape_hook, code, wparam, lparam)

        self.escape_hook_proc = hook_proc
        self.escape_hook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, self.escape_hook_proc, None, 0)
        if not self.escape_hook:
            LOGGER.warning("Esc keyboard hook failed; falling back to polling watcher")
            self.escape_stop_polling_watcher()
            return

        message = wintypes.MSG()
        while self.stop_watcher_active and user32.GetMessageW(ctypes.byref(message), None, 0, 0):
            user32.TranslateMessage(ctypes.byref(message))
            user32.DispatchMessageW(ctypes.byref(message))

        if self.escape_hook:
            user32.UnhookWindowsHookEx(self.escape_hook)
            self.escape_hook = None

    def escape_stop_polling_watcher(self):
        """Fallback polling watcher for Escape if the low-level hook cannot be used."""
        esc_was_down = False
        while self.stop_watcher_active:
            esc_is_down = bool(user32.GetAsyncKeyState(VK_ESCAPE) & 0x8000)
            if (
                esc_is_down
                and not esc_was_down
                and time.time() >= self.ignore_esc_until
                and time.time() >= self.allow_game_escape_until
            ):
                self.request_stop_from_watcher()
            esc_was_down = esc_is_down
            time.sleep(0.05)

    def stop_escape_watcher(self):
        """Stop the Escape watcher and unblock its message loop."""
        self.stop_watcher_active = False
        if self.escape_hook_thread_id:
            user32.PostThreadMessageW(self.escape_hook_thread_id, WM_QUIT, 0, 0)

    def combat_hotkey_watcher(self):
        """Watch combat hotkeys and toggle the class-specific combat loop."""
        hotkey_was_down = False
        loot_was_down = False
        next_loot_click_at = 0.0
        next_kadala_right_click_at = 0.0
        while self.hotkey_watcher_active:
            backtick_is_down = bool(user32.GetAsyncKeyState(VK_OEM_3) & 0x8000)
            alt_is_down = bool(user32.GetAsyncKeyState(VK_MENU) & 0x8000)
            loot_is_down = alt_is_down and backtick_is_down
            now = time.time()

            if backtick_is_down and not hotkey_was_down and not alt_is_down:
                self.toggle_combat()
            hotkey_was_down = backtick_is_down

            if loot_is_down:
                if not loot_was_down:
                    LOGGER.info("Loot started")
                if diablo_is_active() and now >= next_loot_click_at:
                    left_click_current_position()
                    next_loot_click_at = now + LOOT_CLICK_SECONDS
            elif loot_was_down:
                LOGGER.info("Loot stopped")
                next_loot_click_at = 0.0
            loot_was_down = loot_is_down

            kadala_is_down = bool(user32.GetAsyncKeyState(VK_UP) & 0x8000)
            if kadala_is_down and diablo_is_active() and now >= next_kadala_right_click_at:
                right_click_current_position()
                next_kadala_right_click_at = now + KADALA_RIGHT_CLICK_SECONDS
            elif not kadala_is_down:
                next_kadala_right_click_at = 0.0

            time.sleep(MONK_COMBAT_KEY_POLL_SECONDS)

    def controller_watcher(self):
        """Poll an Xbox/XInput controller and map it to movement plus automation hotkeys."""
        if not xinput:
            LOGGER.warning("Xbox controller watcher disabled: XInput DLL not available")
            return

        a_was_down = False
        x_was_down = False
        view_was_down = False
        menu_was_down = False
        dpad_up_was_down = False
        dpad_right_was_down = False
        right_trigger_was_down = False
        left_trigger_was_down = False
        b_was_down = False
        y_was_down = False

        while self.controller_watcher_active:
            state = get_xinput_state(0)
            if not state:
                if self.controller_connected:
                    LOGGER.info("Xbox controller disconnected")
                self.controller_connected = False
                self.release_controller_a_mouse()
                self.release_controller_x_mouse()
                a_was_down = False
                x_was_down = False
                view_was_down = False
                menu_was_down = False
                dpad_up_was_down = False
                dpad_right_was_down = False
                right_trigger_was_down = False
                left_trigger_was_down = False
                b_was_down = False
                y_was_down = False
                time.sleep(CONTROLLER_RECONNECT_SECONDS)
                continue

            if not self.controller_connected:
                LOGGER.info("Xbox controller connected")
            self.controller_connected = True
            gamepad = state.Gamepad
            # Master controller toggle: when disabled, no XInput button,
            # trigger, D-pad, or stick action is allowed past this point.
            if not self.controller_enabled:
                self.release_controller_a_mouse()
                self.release_controller_x_mouse()
                self.reset_controller_cursor_motion()
                # Clear edge states so a held input cannot fire when controller
                # support is turned back on.
                a_was_down = False
                x_was_down = False
                view_was_down = False
                menu_was_down = False
                dpad_up_was_down = False
                dpad_right_was_down = False
                right_trigger_was_down = False
                left_trigger_was_down = False
                b_was_down = False
                y_was_down = False
                time.sleep(CONTROLLER_POLL_SECONDS)
                continue
            diablo_active = diablo_is_active()
            controller_click_allowed = diablo_active or self.exit_confirmation_open

            a_is_down = bool(gamepad.wButtons & XINPUT_GAMEPAD_A)
            if a_is_down and controller_click_allowed:
                self.press_controller_a_mouse()
            elif not a_is_down:
                self.release_controller_a_mouse()
            a_was_down = a_is_down

            x_is_down = bool(gamepad.wButtons & XINPUT_GAMEPAD_X)
            if x_is_down and controller_click_allowed:
                self.press_controller_x_mouse()
            elif not x_is_down:
                self.release_controller_x_mouse()
            x_was_down = x_is_down

            view_is_down = bool(gamepad.wButtons & XINPUT_GAMEPAD_BACK)
            if view_is_down and not view_was_down and diablo_active:
                press_vk(VK_TAB)
            view_was_down = view_is_down

            dpad_right_is_down = bool(gamepad.wButtons & XINPUT_GAMEPAD_DPAD_RIGHT)
            if dpad_right_is_down and not dpad_right_was_down and diablo_active:
                press_vk(VK_M)
            dpad_right_was_down = dpad_right_is_down

            menu_is_down = bool(gamepad.wButtons & XINPUT_GAMEPAD_START)
            if menu_is_down and not menu_was_down and diablo_active:
                self.run_on_ui_thread(self.controller_toggle_game_menu)
            menu_was_down = menu_is_down

            dpad_up_is_down = bool(gamepad.wButtons & XINPUT_GAMEPAD_DPAD_UP)
            if dpad_up_is_down and not dpad_up_was_down and diablo_active:
                press_vk(VK_I)
            dpad_up_was_down = dpad_up_is_down

            left_trigger_is_down = gamepad.bLeftTrigger >= CONTROLLER_TRIGGER_THRESHOLD
            if left_trigger_is_down and not left_trigger_was_down and diablo_active:
                self.run_on_ui_thread(self.toggle_combat)
            left_trigger_was_down = left_trigger_is_down

            right_trigger_is_down = gamepad.bRightTrigger >= CONTROLLER_TRIGGER_THRESHOLD
            if (
                right_trigger_is_down
                and not right_trigger_was_down
                and diablo_active
                and not self.running
                and not self.combat_running
            ):
                self.run_on_ui_thread(self.run_next_route_teleport_hotkey)
            right_trigger_was_down = right_trigger_is_down

            b_is_down = bool(gamepad.wButtons & XINPUT_GAMEPAD_B)
            if b_is_down and not b_was_down and diablo_active:
                self.run_on_ui_thread(self.controller_confirm_exit_game)
            b_was_down = b_is_down

            y_is_down = bool(gamepad.wButtons & XINPUT_GAMEPAD_Y)
            if y_is_down and not y_was_down and diablo_active:
                self.run_on_ui_thread(self.confirm_make_new_game_hotkey)
            y_was_down = y_is_down

            self.update_controller_movement(gamepad, diablo_active)
            time.sleep(CONTROLLER_POLL_SECONDS)

    def update_controller_movement(self, gamepad, diablo_active):
        """Convert the left stick into relative cursor motion without clicking."""
        dialog_control = self.exit_confirmation_open
        now = time.perf_counter()
        dt = min(0.025, max(0.001, now - self.controller_last_move_at))
        self.controller_last_move_at = now
        if self.running or (not diablo_active and not dialog_control):
            self.reset_controller_cursor_motion()
            return

        lx = int(gamepad.sThumbLX)
        ly = int(gamepad.sThumbLY)
        magnitude = ((lx * lx) + (ly * ly)) ** 0.5
        if magnitude < CONTROLLER_LEFT_STICK_DEADZONE:
            self.reset_controller_cursor_motion()
            return

        current = wintypes.POINT()
        if not user32.GetCursorPos(ctypes.byref(current)):
            self.reset_controller_cursor_motion()
            return

        normalized_x = lx / magnitude
        normalized_y = ly / magnitude
        intensity = min(1.0, (magnitude - CONTROLLER_LEFT_STICK_DEADZONE) / (32767 - CONTROLLER_LEFT_STICK_DEADZONE))
        intensity = intensity ** self.controller_response_curve
        pixels = self.controller_cursor_speed * dt * intensity
        raw_dx = (normalized_x * pixels) + self.controller_cursor_remainder_x
        raw_dy = (-normalized_y * pixels) + self.controller_cursor_remainder_y
        dx = round(raw_dx)
        dy = round(raw_dy)
        self.controller_cursor_remainder_x = raw_dx - dx
        self.controller_cursor_remainder_y = raw_dy - dy
        if dx == 0 and dy == 0:
            return
        x = current.x + dx
        y = current.y + dy

        if diablo_active:
            rect = diablo_window_rect()
            if not rect:
                self.reset_controller_cursor_motion()
                return
            if self.combat_running:
                region = self.controller_combat_cursor_region(rect)
                x = max(region["left"], min(x, region["right"] - 1))
                y = max(region["top"], min(y, region["bottom"] - 1))
            else:
                x = max(rect.left, min(x, rect.right - 1))
                y = max(rect.top, min(y, rect.bottom - 1))
        else:
            bounds = virtual_screen_bounds()
            x = max(bounds["left"], min(x, bounds["left"] + bounds["width"] - 1))
            y = max(bounds["top"], min(y, bounds["top"] + bounds["height"] - 1))

        user32.SetCursorPos(x, y)

    def controller_combat_cursor_region(self, rect):
        """Return the combat-only controller cursor region inside Diablo."""
        width = rect.right - rect.left
        height = rect.bottom - rect.top
        return {
            "left": rect.left + round(width * CONTROLLER_COMBAT_CURSOR_REGION_RATIO["left"]),
            "top": rect.top + round(height * CONTROLLER_COMBAT_CURSOR_REGION_RATIO["top"]),
            "right": rect.left + round(width * (
                CONTROLLER_COMBAT_CURSOR_REGION_RATIO["left"] + CONTROLLER_COMBAT_CURSOR_REGION_RATIO["width"]
            )),
            "bottom": rect.top + round(height * (
                CONTROLLER_COMBAT_CURSOR_REGION_RATIO["top"] + CONTROLLER_COMBAT_CURSOR_REGION_RATIO["height"]
            )),
        }

    def reset_controller_cursor_motion(self):
        """Clear stick movement carry-over."""
        self.controller_cursor_remainder_x = 0.0
        self.controller_cursor_remainder_y = 0.0
        self.controller_last_move_at = time.perf_counter()

    def press_controller_a_mouse(self):
        """Hold left mouse while the controller A button is held."""
        if self.controller_a_mouse_down:
            return
        user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, None)
        self.controller_a_mouse_down = True

    def release_controller_a_mouse(self):
        """Release the controller A-held left mouse button."""
        if not self.controller_a_mouse_down:
            return
        user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, None)
        self.controller_a_mouse_down = False

    def press_controller_x_mouse(self):
        """Hold right mouse while the controller X button is held."""
        if self.controller_x_mouse_down:
            return
        user32.mouse_event(MOUSEEVENTF_RIGHTDOWN, 0, 0, 0, None)
        self.controller_x_mouse_down = True

    def release_controller_x_mouse(self):
        """Release the controller X-held right mouse button."""
        if not self.controller_x_mouse_down:
            return
        user32.mouse_event(MOUSEEVENTF_RIGHTUP, 0, 0, 0, None)
        self.controller_x_mouse_down = False

    def controller_confirm_exit_game(self):
        """Handle the controller B button by stopping combat, then using the normal Exit Game confirmation."""
        if self.running:
            return
        if self.combat_running:
            self.stop_monk_combat("controller B exit game")
        self.controller_exit_confirmation_requested = True
        try:
            self.confirm_exit_game_hotkey()
        finally:
            self.controller_exit_confirmation_requested = False

    def controller_toggle_game_menu(self):
        """Open or close Diablo's game menu with the controller Menu button."""
        if self.running:
            return
        if self.combat_running:
            self.stop_monk_combat("controller menu button")
        self.release_controller_a_mouse()
        self.release_controller_x_mouse()
        self.press_escape_for_game()

    def toggle_combat(self):
        """Start or stop combat automation depending on the current combat state/class."""
        if self.combat_running:
            self.stop_monk_combat("combat hotkey toggle")
            return
        if self.running or not diablo_is_active():
            return
        if self.is_witch_doctor_active():
            self.start_witch_doctor_combat()
            return
        if self.is_demon_hunter_active():
            self.start_demon_hunter_combat()
            return
        self.start_monk_combat()

    def character_template_active(self, template_name, threshold):
        """Check whether a class-identifying template is visible."""
        path = self.matcher.template_path(COMBAT_TEMPLATE_DIR, template_name)
        match = self.matcher.find(path, self.character_scan_region)
        confidence = match["confidence"] if match else 0.0
        is_match = bool(match and confidence >= threshold)
        LOGGER.info("%s character scan confidence=%.3f matched=%s", template_name, confidence, is_match)
        return is_match

    def is_witch_doctor_active(self):
        """Detect whether the Witch Doctor template is active."""
        return self.character_template_active("Witch Doctor", WITCH_DOCTOR_CHARACTER_THRESHOLD)

    def is_demon_hunter_active(self):
        """Detect whether the Demon Hunter template is active."""
        return self.character_template_active("Demon Hunter", DEMON_HUNTER_CHARACTER_THRESHOLD)

    def witch_doctor_hex_ready(self):
        """Detect whether the Witch Doctor Hex skill appears ready."""
        path = self.matcher.template_path(COMBAT_TEMPLATE_DIR, "Hex Skill")
        match = self.matcher.find(path, self.witch_doctor_hex_region)
        return bool(match and match["confidence"] >= WITCH_DOCTOR_HEX_THRESHOLD)

    def start_monk_combat(self):
        """Start the Monk combat key loop and cursor-click loop."""
        if self.combat_running:
            return
        self.combat_running = True
        self.combat_class = "monk"
        self.combat_started_at = time.time()
        self.monk_key_index = 1
        self.monk_last_cursor_click_at = 0.0
        self.monk_original_cursor_handle = current_cursor_handle()
        self.update_status("combat")
        lock_cursor_to_diablo_window()
        self.monk_key_thread = threading.Thread(target=self.monk_key_loop, daemon=True)
        self.monk_cursor_thread = threading.Thread(target=self.monk_cursor_loop, daemon=True)
        self.monk_key_thread.start()
        self.monk_cursor_thread.start()
        self.start_combat_menu_watcher()
        LOGGER.info("Monk combat started")

    def start_witch_doctor_combat(self):
        """Start Witch Doctor combat, scroll, and cursor-click loops."""
        if self.combat_running:
            return
        self.witch_doctor_original_hex_ready = self.witch_doctor_hex_ready()
        LOGGER.info("Witch Doctor original Hex ready state=%s", self.witch_doctor_original_hex_ready)
        self.combat_running = True
        self.combat_class = "witch_doctor"
        self.combat_started_at = time.time()
        self.update_status("combat")
        lock_cursor_to_diablo_window()
        self.monk_last_cursor_click_at = 0.0
        self.monk_original_cursor_handle = current_cursor_handle()
        self.witch_doctor_thread = threading.Thread(target=self.witch_doctor_combat_loop, daemon=True)
        self.witch_doctor_scroll_thread = threading.Thread(target=self.witch_doctor_mouse_wheel_loop, daemon=True)
        self.monk_cursor_thread = threading.Thread(target=self.monk_cursor_loop, daemon=True)
        self.witch_doctor_thread.start()
        self.witch_doctor_scroll_thread.start()
        self.monk_cursor_thread.start()
        self.start_combat_menu_watcher()
        LOGGER.info("Witch Doctor combat started")

    def start_demon_hunter_combat(self):
        """Start Demon Hunter momentum setup and combat loops."""
        if self.combat_running:
            return
        self.combat_running = True
        self.combat_class = "demon_hunter"
        self.combat_started_at = time.time()
        self.monk_key_index = 1
        self.monk_last_cursor_click_at = 0.0
        self.monk_original_cursor_handle = current_cursor_handle()
        self.update_status("combat")
        lock_cursor_to_diablo_window()
        thread = threading.Thread(target=self.demon_hunter_combat_startup_loop, daemon=True)
        thread.start()
        self.start_combat_menu_watcher()
        LOGGER.info("Demon Hunter combat starting")

    def stop_monk_combat(self, reason="unspecified"):
        """Stop any active combat automation and restore held inputs/cursor state."""
        if not self.combat_running:
            return False
        combat_names = {
            "demon_hunter": "Demon Hunter",
            "witch_doctor": "Witch Doctor",
        }
        combat_class = self.combat_class
        combat_name = combat_names.get(combat_class, "Monk")
        # Flip the shared flag first so all combat loops, especially Witch
        # Doctor's mouse-wheel movement loop, stop before cleanup or teleporting.
        self.combat_running = False
        if combat_class == "witch_doctor":
            self.wait_for_witch_doctor_scroll_stop()
            self.restore_witch_doctor_hex_before_stop()
        self.combat_class = ""
        release_cursor_lock()
        release_inputs()
        self.update_status("idle")
        LOGGER.info("%s combat stopped: %s", combat_name, reason)
        return True

    def wait_for_witch_doctor_scroll_stop(self):
        """Give the Witch Doctor mouse-wheel loop a moment to exit before cleanup."""
        thread = self.witch_doctor_scroll_thread
        if thread and thread.is_alive() and thread is not threading.current_thread():
            LOGGER.info("Waiting for Witch Doctor mouse-wheel loop to stop")
            thread.join(WITCH_DOCTOR_SCROLL_STOP_TIMEOUT)

    def start_combat_menu_watcher(self):
        """Start the shared watcher that closes accidental combat-blocking menus for every class."""
        if not self.combat_menu_watch_targets:
            LOGGER.info("Combat menu watcher disabled: no combat menu templates could be loaded")
            return
        if self.combat_menu_thread and self.combat_menu_thread.is_alive():
            return
        self.combat_menu_thread = threading.Thread(target=self.combat_menu_watcher_loop, daemon=True)
        self.combat_menu_thread.start()

    def combat_menu_watcher_loop(self):
        """Close watched menus such as bounty/objective and follower panels during any combat loop."""
        next_escape_at = 0.0
        while self.combat_running:
            if not diablo_is_active():
                time.sleep(BOUNTY_MENU_POLL_SECONDS)
                continue
            for target in self.combat_menu_watch_targets:
                match = self.matcher.find_loaded(target["template"], target["region"])
                if not match or match["confidence"] < target["threshold"]:
                    continue
                now = time.time()
                if now < next_escape_at:
                    break
                LOGGER.info(
                    "%s detected during combat confidence=%.3f; pressing Esc",
                    target["name"],
                    match["confidence"],
                )
                self.press_escape_for_game()
                next_escape_at = now + BOUNTY_MENU_ESCAPE_COOLDOWN_SECONDS
                break
            time.sleep(BOUNTY_MENU_POLL_SECONDS)

    def restore_witch_doctor_hex_before_stop(self):
        """Leave Witch Doctor combat with Hex back on its default ready still."""
        original_ready = self.witch_doctor_original_hex_ready
        current_ready = self.witch_doctor_hex_ready()
        self.witch_doctor_original_hex_ready = None
        if current_ready:
            LOGGER.info(
                "Witch Doctor Hex already ready on stop: original=%s current=%s",
                original_ready,
                current_ready,
            )
            return
        for attempt in range(1, WITCH_DOCTOR_HEX_STOP_PRESS_ATTEMPTS + 1):
            if not diablo_is_active() and not activate_diablo():
                LOGGER.info("Witch Doctor Hex stop restore skipped: Diablo is not active")
                return
            release_inputs(release_mouse=False)
            LOGGER.info(
                "Witch Doctor Hex not ready on stop: original=%s current=%s; pressing 1 attempt %s/%s",
                original_ready,
                current_ready,
                attempt,
                WITCH_DOCTOR_HEX_STOP_PRESS_ATTEMPTS,
            )
            press_vk(VK_1)
            time.sleep(WITCH_DOCTOR_HEX_STOP_PRESS_SETTLE_SECONDS)
            current_ready = self.witch_doctor_hex_ready()
            if current_ready:
                LOGGER.info("Witch Doctor Hex ready after stop restore attempt %s", attempt)
                return
        LOGGER.info("Witch Doctor Hex still not ready after stop restore attempts")

    def monk_key_loop(self):
        """Cycle Monk combat keys while combat automation is active."""
        while self.combat_running and self.combat_class == "monk":
            if not diablo_is_active():
                self.stop_monk_combat("Diablo inactive in monk key loop")
                return
            vk = 0x30 + self.monk_key_index
            user32.keybd_event(vk, 0, 0, None)
            time.sleep(0.01)
            user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, None)
            self.monk_key_index = 1 if self.monk_key_index >= 3 else self.monk_key_index + 1
            time.sleep(MONK_COMBAT_KEY_DELAY_SECONDS)

    def monk_cursor_loop(self):
        """Click at the cursor for Monk/Witch Doctor when the cursor is safe."""
        while self.combat_running:
            if not diablo_is_active():
                self.stop_monk_combat("Diablo inactive in combat cursor loop")
                return
            if not self.combat_mouse_click_is_safe():
                time.sleep(MONK_CURSOR_POLL_SECONDS)
                continue
            cursor_handle = current_cursor_handle()
            now = time.time()
            if (
                self.monk_original_cursor_handle
                and cursor_handle
                and cursor_handle != self.monk_original_cursor_handle
                and now - self.monk_last_cursor_click_at >= MONK_CURSOR_CLICK_GAP_SECONDS
            ):
                left_click_current_position()
                self.monk_last_cursor_click_at = now
            time.sleep(MONK_CURSOR_POLL_SECONDS)

    def witch_doctor_combat_loop(self):
        """Maintain Witch Doctor Hex and primary skill behavior."""
        sequence = (VK_2, VK_3, VK_1)
        while self.combat_running and self.combat_class == "witch_doctor":
            if not diablo_is_active():
                self.stop_monk_combat("Diablo inactive in Witch Doctor combat loop")
                return
            for vk in sequence:
                if not self.combat_running or self.combat_class != "witch_doctor":
                    return
                if not diablo_is_active():
                    self.stop_monk_combat("Diablo inactive during Witch Doctor key sequence")
                    return
                press_vk(vk)
                time.sleep(MONK_COMBAT_KEY_DELAY_SECONDS)
            self.wait_for_witch_doctor_hex_ready()

    def witch_doctor_mouse_wheel_loop(self):
        """Drive Witch Doctor mouse-wheel input during combat."""
        while self.combat_running and self.combat_class == "witch_doctor":
            if not diablo_is_running():
                self.stop_monk_combat("Diablo not running in Witch Doctor mouse wheel loop")
                return
            scroll_diablo_mouse_wheel(WITCH_DOCTOR_MOUSE_WHEEL_DELTA)
            time.sleep(WITCH_DOCTOR_MOUSE_WHEEL_SECONDS)

    def demon_hunter_combat_startup_loop(self):
        """Build Demon Hunter momentum before starting sustained combat threads."""
        try:
            if not self.build_demon_hunter_momentum_stacks():
                return
            if not self.combat_running or self.combat_class != "demon_hunter":
                return
            time.sleep(0.05)
            self.monk_key_index = 1
            self.demon_hunter_key_thread = threading.Thread(target=self.demon_hunter_key_loop, daemon=True)
            self.demon_hunter_shift_click_thread = threading.Thread(target=self.demon_hunter_shift_click_loop, daemon=True)
            self.demon_hunter_right_mouse_thread = threading.Thread(target=self.demon_hunter_right_mouse_loop, daemon=True)
            self.monk_cursor_thread = threading.Thread(target=self.monk_cursor_loop, daemon=True)
            self.demon_hunter_key_thread.start()
            self.demon_hunter_shift_click_thread.start()
            self.demon_hunter_right_mouse_thread.start()
            self.monk_cursor_thread.start()
            LOGGER.info("Demon Hunter combat started")
        finally:
            if self.combat_running and self.combat_class == "demon_hunter":
                return
            release_inputs()

    def build_demon_hunter_momentum_stacks(self):
        """Press the Demon Hunter setup key until momentum stacks are ready or timeout."""
        wait_for_count_template = self.demon_hunter_momentum_count_template_available()
        deadline = None
        holding_click = False
        try:
            while self.combat_running and self.combat_class == "demon_hunter":
                if not diablo_is_active():
                    self.stop_monk_combat("Diablo inactive during Demon Hunter momentum build")
                    return False
                if not self.combat_mouse_click_is_safe():
                    if holding_click:
                        user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, None)
                        user32.keybd_event(VK_SHIFT, 0, KEYEVENTF_KEYUP, None)
                        holding_click = False
                    time.sleep(DEMON_HUNTER_MOMENTUM_SCAN_SECONDS)
                    continue
                if not holding_click:
                    user32.keybd_event(VK_SHIFT, 0, 0, None)
                    user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, None)
                    holding_click = True
                    if not wait_for_count_template and deadline is None:
                        deadline = time.time() + DEMON_HUNTER_MOMENTUM_BUILD_TIMEOUT
                if self.demon_hunter_momentum_stacks_ready():
                    return True
                if deadline is not None and time.time() >= deadline:
                    LOGGER.info("Demon Hunter momentum count template missing; continuing after timed startup build")
                    return True
                time.sleep(DEMON_HUNTER_MOMENTUM_SCAN_SECONDS)
        finally:
            if holding_click:
                user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, None)
                user32.keybd_event(VK_SHIFT, 0, KEYEVENTF_KEYUP, None)
        return False

    def wait_for_combat_mouse_click_safe(self, timeout=2.0):
        """Wait until the cursor leaves protected UI regions before clicking."""
        deadline = time.time() + timeout
        while self.combat_running and time.time() <= deadline:
            if not diablo_is_active():
                self.stop_monk_combat("Diablo inactive while waiting for safe combat click")
                return False
            if self.combat_mouse_click_is_safe():
                return True
            time.sleep(MONK_CURSOR_POLL_SECONDS)
        LOGGER.info("Combat mouse click skipped: cursor was over protected UI")
        return False

    def demon_hunter_momentum_count_template_available(self):
        """Check whether the momentum-count template file exists."""
        return self.matcher.template_path(COMBAT_TEMPLATE_DIR, "Momentum Count 20").exists()

    def demon_hunter_momentum_stacks_ready(self, log_ready=True):
        """Detect whether Demon Hunter momentum stacks are at the configured target."""
        path = self.matcher.template_path(COMBAT_TEMPLATE_DIR, "Momentum Count 20")
        if not path.exists():
            return False
        match = self.matcher.find(path, self.demon_hunter_momentum_region)
        if match and match["confidence"] >= DEMON_HUNTER_MOMENTUM_THRESHOLD:
            if log_ready:
                LOGGER.info("Demon Hunter momentum ready via Momentum Count 20 confidence=%.3f", match["confidence"])
            return True
        return False

    def demon_hunter_key_loop(self):
        """Send Demon Hunter ability keys during combat."""
        sequence = (VK_1, VK_2, VK_3, VK_4)
        while self.combat_running and self.combat_class == "demon_hunter":
            if not diablo_is_active():
                self.stop_monk_combat("Diablo inactive in Demon Hunter key loop")
                return
            press_vk(sequence[(self.monk_key_index - 1) % len(sequence)])
            self.monk_key_index = 1 if self.monk_key_index >= len(sequence) else self.monk_key_index + 1
            time.sleep(DEMON_HUNTER_KEY_SECONDS)

    def demon_hunter_right_mouse_loop(self):
        """Hold/repeat right mouse behavior for Demon Hunter combat."""
        right_is_down = False
        try:
            if not self.wait_for_combat_mouse_click_safe():
                self.stop_monk_combat("Demon Hunter right mouse could not start in safe region")
                return
            user32.mouse_event(MOUSEEVENTF_RIGHTDOWN, 0, 0, 0, None)
            right_is_down = True
            while self.combat_running and self.combat_class == "demon_hunter":
                if not diablo_is_active():
                    self.stop_monk_combat("Diablo inactive in Demon Hunter right mouse loop")
                    return
                time.sleep(MONK_CURSOR_POLL_SECONDS)
        finally:
            if right_is_down:
                user32.mouse_event(MOUSEEVENTF_RIGHTUP, 0, 0, 0, None)

    def demon_hunter_shift_click_loop(self):
        """Repeatedly perform shift-left-click attacks for Demon Hunter combat."""
        while self.combat_running and self.combat_class == "demon_hunter":
            if not diablo_is_active():
                self.stop_monk_combat("Diablo inactive in Demon Hunter momentum maintenance loop")
                return
            if self.demon_hunter_momentum_stacks_ready(log_ready=False):
                time.sleep(DEMON_HUNTER_MOMENTUM_SCAN_SECONDS)
                continue
            LOGGER.info("Demon Hunter momentum dropped below 20; sending one Shift+Left Click")
            if not self.demon_hunter_shift_left_click_once():
                return
            time.sleep(DEMON_HUNTER_MOMENTUM_RECOVERY_SETTLE_SECONDS)

    def demon_hunter_shift_left_click_once(self):
        """Perform one safe shift-left-click attack."""
        if not self.wait_for_combat_mouse_click_safe():
            self.stop_monk_combat("Demon Hunter Shift+Left Click could not find safe region")
            return False
        user32.keybd_event(VK_SHIFT, 0, 0, None)
        try:
            time.sleep(0.03)
            left_click_current_position()
            time.sleep(0.03)
        finally:
            user32.keybd_event(VK_SHIFT, 0, KEYEVENTF_KEYUP, None)
        return True

    def wait_for_witch_doctor_hex_ready(self):
        """Wait for the Witch Doctor Hex template to appear ready."""
        time.sleep(WITCH_DOCTOR_HEX_CAST_SETTLE_SECONDS)
        disappear_deadline = time.time() + WITCH_DOCTOR_HEX_DISAPPEAR_TIMEOUT
        saw_cooldown = False

        while self.combat_running and self.combat_class == "witch_doctor" and time.time() < disappear_deadline:
            if not diablo_is_active():
                self.stop_monk_combat("Diablo inactive while waiting for Witch Doctor Hex cooldown")
                return False
            if not self.witch_doctor_hex_ready():
                saw_cooldown = True
                break
            time.sleep(WITCH_DOCTOR_HEX_SCAN_SECONDS)

        while self.combat_running and self.combat_class == "witch_doctor":
            if not diablo_is_active():
                self.stop_monk_combat("Diablo inactive while waiting for Witch Doctor Hex ready")
                return False
            if self.witch_doctor_hex_ready():
                return True
            if not saw_cooldown:
                return True
            time.sleep(WITCH_DOCTOR_HEX_SCAN_SECONDS)
        return False

    def press_escape_for_game(self):
        """Press Escape while temporarily telling the stop watcher to ignore that automated Escape."""
        self.ignore_esc_until = time.time() + 0.35
        self.allow_game_escape_until = time.time() + AUTOMATION_ESCAPE_ALLOW_SECONDS
        press_vk(VK_ESCAPE)
        self.ignore_esc_until = time.time() + AUTOMATION_ESCAPE_ALLOW_SECONDS

    def normalize_detected_location(self, location_name):
        """Normalize current-location template names and remember the last successful detection."""
        previous_location = self.last_detected_location
        if previous_location and location_name != previous_location:
            broad_locations = {
                "City Of Caldeum": {"Sewers of Caldeum"},
                "Northern Highlands": {"Highlands Cave", "Leoric's Hunting Grounds"},
                "The Weeping Hollow": {"Cave Of The Moon Clan Level 1", "Cave Of The Moon Clan Level 2"},
                "Stinging Winds": {"Black Canyon Mines"},
            }
            if location_name in broad_locations.get(previous_location, set()):
                location_name = previous_location
        self.last_detected_location = location_name
        return location_name

    def toggle_lock(self):
        """Toggle overlay lock/click-through state."""
        self.overlay_locked = not self.overlay_locked
        self.update_status()

    def restore_overlay(self):
        """Move the overlay back to its startup/home position."""
        self.overlay_x = self.overlay_home_x
        self.overlay_y = self.overlay_home_y
        self.root.geometry(f"{OVERLAY_WIDTH}x{OVERLAY_HEIGHT}+{self.overlay_x}+{self.overlay_y}")

    def save_overlay_position(self):
        """Persist the current overlay position as the default startup position."""
        try:
            OVERLAY_POSITION_STATE.write_text(f"{int(self.overlay_x)},{int(self.overlay_y)}", encoding="utf-8")
        except OSError:
            LOGGER.exception("Failed to save overlay position")
            self.show_splash("Overlay position save failed", duration=2.0)
            return
        self.overlay_home_x = self.overlay_x
        self.overlay_home_y = self.overlay_y
        LOGGER.info("Saved overlay position: x=%s y=%s", self.overlay_x, self.overlay_y)
        self.show_splash("Overlay position saved", duration=2.0)

    def run_on_ui_thread(self, callback, wait=False, timeout=1.0):
        """Run a callback on Tk thread, optionally blocking the caller until it finishes."""
        done = threading.Event()

        def wrapped():
            """Run the requested UI callback and always release the waiting worker thread."""
            try:
                callback()
            finally:
                done.set()

        try:
            self.root.after(0, wrapped)
        except tk.TclError:
            return False

        if wait:
            return done.wait(timeout)
        return True

    def reload_app(self):
        """Launch a clean replacement process and close the current overlay."""
        LOGGER.info("Reload requested")
        self.launch_clean_reload()
        self.stop_escape_watcher()
        self.hotkey_watcher_active = False
        self.controller_watcher_active = False
        self.release_controller_a_mouse()
        self.release_controller_x_mouse()
        self.stop_monk_combat("reload requested")
        cleanup_logging()
        self.root.destroy()

    def launch_clean_reload(self):
        """Spawn a PowerShell helper that starts a new clean GoblinFarming process."""
        script_names = ("GoblinFarming.py",)
        command_filter = " -or ".join([f"$_.CommandLine -like '*{name}*'" for name in script_names])
        command = (
            f"$currentPid = {os.getpid()}; "
            f"$python = {powershell_quote(sys.executable)}; "
            f"$script = {powershell_quote(Path(__file__).resolve())}; "
            f"$workdir = {powershell_quote(SCRIPT_DIR)}; "
            "try { Wait-Process -Id $currentPid -Timeout 10 -ErrorAction SilentlyContinue } catch { }; "
            "Get-CimInstance Win32_Process -Filter \"name = 'python.exe'\" | "
            f"Where-Object {{ $_.CommandLine -and ({command_filter}) }} | "
            "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }; "
            "Start-Sleep -Milliseconds 250; "
            "Start-Process -FilePath $python -ArgumentList @($script) -WorkingDirectory $workdir -WindowStyle Hidden"
        )
        try:
            subprocess.Popen(
                ["powershell.exe", "-NoProfile", "-Command", command],
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        except OSError:
            LOGGER.exception("Failed to launch clean reload helper")

    def exit_app(self):
        """Shut down watchers/combat/cursor lock and destroy the overlay."""
        LOGGER.info("App exit requested")
        self.stop_escape_watcher()
        self.hotkey_watcher_active = False
        self.controller_watcher_active = False
        self.release_controller_a_mouse()
        self.release_controller_x_mouse()
        self.stop_monk_combat("app exit requested")
        release_cursor_lock()
        cleanup_logging()
        self.root.destroy()

    def confirm_kill_all_goblin_processes(self):
        """Ask before killing all GoblinFarming Python processes."""
        try:
            confirmed = messagebox.askyesno(
                "Close Goblin Farming",
                "Close all Goblin Farming Python processes?",
                parent=self.root,
            )
        except tk.TclError:
            confirmed = False

        if not confirmed:
            return

        LOGGER.info("Confirmed close all Goblin Farming processes")
        self.kill_all_goblin_processes()

    def kill_all_goblin_processes(self):
        """Terminate every detected GoblinFarming.py process."""
        try:
            APP_INSTANCE_LOCK.unlink(missing_ok=True)
        except OSError:
            LOGGER.exception("Failed to remove GoblinFarming instance lock before close-all")

        script_names = ("GoblinFarming.py",)
        command_filter = " -or ".join([f"$_.CommandLine -like '*{name}*'" for name in script_names])
        command = [
            "powershell.exe",
            "-NoProfile",
            "-Command",
            (
                "Get-CimInstance Win32_Process | "
                "Where-Object { $_.CommandLine -and ("
                + command_filter
                + ") } | ForEach-Object { "
                "Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue "
                "}"
            ),
        ]
        try:
            subprocess.Popen(command, creationflags=subprocess.CREATE_NO_WINDOW)
        except OSError:
            LOGGER.exception("Failed to launch Goblin process terminator")
            self.exit_app()

    def exit_game_and_close(self):
        """Start the Exit Game worker unless another automation flow is active."""
        if self.running or self.combat_running:
            return
        thread = threading.Thread(target=self.exit_game_and_close_worker, daemon=True)
        thread.start()

    def confirm_exit_game_hotkey(self):
        """Confirm the number-2 hotkey before starting the Exit Game flow."""
        if self.running or self.combat_running:
            return
        confirmed = self.ask_exit_game_confirmation()

        if confirmed:
            LOGGER.info("Exit Game hotkey confirmed")
            self.exit_game_and_close()
        else:
            LOGGER.info("Exit Game hotkey cancelled")
            activate_diablo()

    def ask_exit_game_confirmation(self):
        """Show an Exit Game confirmation dialog centered over Diablo."""
        return self.ask_controller_flow_confirmation(
            title="Exit Game",
            heading="Run Exit Game now?",
            message="This will run town cleanup, close Diablo, and close this overlay if successful.",
            confirm_text="Exit Game",
            confirm_bg=OVERLAY_CLOSE_BG,
            confirm_active_bg=OVERLAY_CLOSE_HOVER_BG,
        )

    def confirm_make_new_game_hotkey(self):
        """Confirm the controller Y button before starting Make New Game."""
        if self.running or self.combat_running:
            return
        confirmed = self.ask_make_new_game_confirmation()

        if confirmed:
            LOGGER.info("Make New Game controller hotkey confirmed")
            self.make_new_game()
        else:
            LOGGER.info("Make New Game controller hotkey cancelled")
            activate_diablo()

    def ask_make_new_game_confirmation(self):
        """Show a Make New Game confirmation dialog centered over Diablo."""
        return self.ask_controller_flow_confirmation(
            title="Make New Game",
            heading="Run Make New Game now?",
            message="This will leave the current game if needed, start a new one, and begin the route flow.",
            confirm_text="Make New Game",
            confirm_bg=OVERLAY_BUTTON_ACTIVE_BG,
            confirm_active_bg="#6F9140",
        )

    def ask_controller_flow_confirmation(
        self,
        title,
        heading,
        message,
        confirm_text,
        confirm_bg,
        confirm_active_bg,
    ):
        """Show a centered confirmation dialog for controller-triggered flows."""
        result = {"confirmed": False}
        self.exit_confirmation_open = True
        # Controller layout overlay disabled.
        # self.refresh_controller_layout_overlay(reschedule=False)
        try:
            dialog = tk.Toplevel(self.root)
            dialog.title(title)
            dialog.configure(bg=OVERLAY_BG, highlightthickness=1, highlightbackground="#5A5143")
            dialog.resizable(False, False)
            dialog.attributes("-topmost", True)
            dialog.transient(self.root)

            width = 430
            height = 190
            self.center_window_over_diablo(dialog, width, height)

            tk.Label(
                dialog,
                text=heading,
                bg=OVERLAY_BG,
                fg=OVERLAY_HEADER,
                font=("Segoe UI", 13, "bold"),
                anchor="center",
            ).place(x=18, y=18, width=width - 36, height=26)
            tk.Label(
                dialog,
                text=message,
                bg=OVERLAY_BG,
                fg=OVERLAY_TEXT,
                font=("Segoe UI", 10),
                wraplength=width - 48,
                justify="center",
            ).place(x=24, y=56, width=width - 48, height=54)

            def choose(value):
                result["confirmed"] = value
                dialog.destroy()

            yes_button = tk.Button(
                dialog,
                text=confirm_text,
                command=lambda: choose(True),
                bg=confirm_bg,
                fg=OVERLAY_CLOSE_TEXT,
                activebackground=confirm_active_bg,
                activeforeground=OVERLAY_CLOSE_TEXT,
                relief="flat",
                font=("Segoe UI", 10, "bold"),
            )
            no_button = tk.Button(
                dialog,
                text="Cancel",
                command=lambda: choose(False),
                bg=OVERLAY_BUTTON_BG,
                fg=OVERLAY_BUTTON_TEXT,
                activebackground="#3A3228",
                activeforeground=OVERLAY_BUTTON_TEXT,
                relief="flat",
                font=("Segoe UI", 10),
            )
            yes_button.place(x=92, y=128, width=116, height=34)
            no_button.place(x=222, y=128, width=116, height=34)

            dialog.protocol("WM_DELETE_WINDOW", lambda: choose(False))
            dialog.bind("<Return>", lambda _event: choose(True))
            dialog.bind("<Escape>", lambda _event: choose(False))
            dialog.update_idletasks()
            self.center_window_over_diablo(dialog, width, height)
            dialog.update_idletasks()
            if self.controller_exit_confirmation_requested:
                user32.SetCursorPos(
                    dialog.winfo_rootx() + 92 + 58,
                    dialog.winfo_rooty() + 128 + 17,
                )
            dialog.grab_set()
            no_button.focus_set()
            self.root.wait_window(dialog)
        except tk.TclError:
            return False
        finally:
            self.exit_confirmation_open = False
            # Controller layout overlay disabled.
            # self.refresh_controller_layout_overlay(reschedule=False)
        return result["confirmed"]

    def exit_game_and_close_worker(self):
        """Run Exit Game, then close the overlay if Diablo was closed successfully."""
        game_closed = False
        self.running = True
        self.stop_requested = False
        self.reset_last_pressed_button()
        self.update_status("running")
        LOGGER.info("Exit Game button clicked")
        self.teleport_failsafe_bypass = True
        try:
            game_closed = self.exit_game_flow()
        except Exception:
            LOGGER.exception("Exit Game worker crashed")
        finally:
            self.teleport_failsafe_bypass = False
            if not game_closed:
                release_inputs()
            self.running = False
            self.stop_requested = False
            self.update_status("idle")
        if game_closed:
            try:
                self.root.after(0, self.exit_app)
            except tk.TclError:
                pass

    def exit_game_flow(self):
        """Prepare the character, then close Diablo through WM_CLOSE with Alt+F4 fallback."""
        if self.stop_requested:
            LOGGER.info("Exit game cancelled before start")
            return False
        if not self.ensure_diablo_running():
            LOGGER.info("Exit game failed: Diablo not available")
            return False
        if self.is_in_game_by_current_location() or not self.start_game_button_visible():
            # Exit Game shares the same prep chain as Make New Game: get to
            # New Tristram, salvage non-GG items, repair, and stash GG if found.
            LOGGER.info("Exit game preparing New Tristram before close")
            if not self.prepare_new_tristram_for_exit():
                LOGGER.info("Exit game failed: could not prepare New Tristram")
                return False
        release_inputs()
        hwnd = find_diablo_window()
        if not hwnd:
            LOGGER.info("Exit game failed: Diablo window not found")
            return False
        # WM_CLOSE is the clean close path; Alt+F4 is kept as a fallback when the
        # window does not respond to the close message quickly.
        user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
        if self.wait_for_diablo_window_closed(5):
            LOGGER.info("Exit game succeeded via WM_CLOSE")
            return True
        if self.stop_requested or not activate_diablo():
            LOGGER.info("Exit game cancelled before Alt+F4")
            return False
        user32.keybd_event(VK_MENU, 0, 0, None)
        press_vk(0x73)
        user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, None)
        result = self.wait_for_diablo_window_closed(5)
        LOGGER.info("Exit game Alt+F4 result=%s", result)
        return result

    def wait_for_diablo_window_closed(self, timeout):
        """Wait until Diablo is no longer detected as running."""
        deadline = time.time() + timeout
        while time.time() <= deadline:
            if self.stop_requested:
                return False
            if not diablo_is_running():
                return True
            time.sleep(POLL_SECONDS)
        return False

    def make_new_game_if_diablo_closed(self):
        """Auto-trigger Make New Game on startup when Diablo is closed."""
        if not self.running and not self.combat_running and not diablo_is_running():
            LOGGER.info("Auto Make New Game triggered because Diablo is closed")
            self.make_new_game()

    def make_new_game(self):
        """Start the Make New Game worker unless another automation flow is active."""
        if self.running or self.combat_running:
            return
        LOGGER.info("Make New Game button clicked")
        thread = threading.Thread(target=self.make_new_game_worker, daemon=True)
        thread.start()

    def make_new_game_worker(self):
        """Hide overlay, run the Make New Game flow, and restore idle state afterward."""
        self.running = True
        self.stop_requested = False
        self.reset_last_pressed_button()
        self.update_status("running")
        self.teleport_failsafe_bypass = True
        try:
            self.make_new_game_flow()
        except Exception:
            LOGGER.exception("Make New Game worker crashed")
        finally:
            self.teleport_failsafe_bypass = False
            release_inputs()
            self.running = False
            self.stop_requested = False
            self.update_status("idle")

    def make_new_game_flow(self):
        """Launch/activate Diablo, leave current game if needed, start a new game, and begin farming."""
        if self.stop_requested:
            return False

        diablo_was_running = diablo_is_running()
        LOGGER.info("Make New Game flow started; diablo_was_running=%s", diablo_was_running)

        if not self.ensure_diablo_running():
            LOGGER.info("Make New Game failed: Diablo could not be started/activated")
            return False

        if self.stop_requested:
            LOGGER.info("Make New Game stopped after ensuring Diablo")
            return False

        if diablo_was_running:
            if self.is_in_game_by_current_location():
                LOGGER.info("Make New Game detected in-game by current location")
                # In-game Make New Game must clean up inventory/gear before
                # leaving, otherwise the next run can inherit full bags or broken gear.
                if not self.prepare_new_tristram_for_exit():
                    LOGGER.info("Make New Game failed: New Tristram prep failed")
                    return False
                if not self.leave_current_game_to_menu():
                    LOGGER.info("Make New Game failed: leave game failed")
                    return False
                if not self.wait_for_start_game_button_and_click():
                    LOGGER.info("Make New Game failed: Start Game wait/click failed")
                    return False
                if not self.wait_for_game_start_and_open_map():
                    LOGGER.info("Make New Game failed: map did not open after start")
                    return False
                self.repair_already_executed = False
                return self.start_farming_flow(map_already_open=True)

            if self.start_game_button_visible():
                LOGGER.info("Make New Game detected character menu")
                if not self.wait_for_start_game_button_and_click():
                    LOGGER.info("Make New Game failed: Start Game click failed from menu")
                    return False
                if not self.wait_for_game_start_and_open_map():
                    LOGGER.info("Make New Game failed: map did not open after menu start")
                    return False
                self.repair_already_executed = False
                return self.start_farming_flow(map_already_open=True)

            LOGGER.info("Make New Game did not see menu; assuming already in-game")
            # If image state is ambiguous, prefer the safer in-game path:
            # prep first, then try to leave to the menu.
            if not self.prepare_new_tristram_for_exit():
                LOGGER.info("Make New Game failed: New Tristram prep failed after assumption")
                return False
            if not self.leave_current_game_to_menu():
                LOGGER.info("Make New Game failed: leave game failed after assumption")
                return False

        if not self.wait_for_start_game_button_and_click():
            LOGGER.info("Make New Game failed: fresh launch Start Game wait/click failed")
            return False
        if not self.wait_for_game_start_and_open_map():
            LOGGER.info("Make New Game failed: fresh launch map did not open")
            return False
        self.repair_already_executed = False
        return self.start_farming_flow(map_already_open=True)

    def ensure_diablo_running(self):
        """Activate Diablo or launch it through Battle.net if needed."""
        if activate_diablo():
            LOGGER.debug("Diablo activated")
            return True
        if not DIABLO_LAUNCH_PATH.exists():
            LOGGER.info("Diablo launch path does not exist: %s", DIABLO_LAUNCH_PATH)
            return False
        try:
            LOGGER.info("Launching Diablo from %s", DIABLO_LAUNCH_PATH)
            subprocess.Popen([str(DIABLO_LAUNCH_PATH)], cwd=DIABLO_LAUNCH_PATH.parent)
        except OSError:
            LOGGER.exception("Failed to launch Diablo")
            return False
        return self.wait_for_diablo_ready_from_battle_net()

    def wait_for_battle_net_ready(self):
        """Wait for Battle.net to become available during launch."""
        deadline = time.time() + BATTLE_NET_LAUNCH_WAIT_SECONDS
        while time.time() <= deadline:
            if self.stop_requested:
                LOGGER.info("Battle.net wait stopped")
                return False
            if activate_battle_net():
                LOGGER.debug("Battle.net activated")
                return True
            time.sleep(1)
        LOGGER.info("Battle.net did not activate before timeout")
        return False

    def wait_for_diablo_ready_from_battle_net(self):
        """Click Battle.net Play until Diablo activates or launch timeout expires."""
        if not self.wait_for_battle_net_ready():
            return activate_diablo()

        deadline = time.time() + DIABLO_LAUNCH_WAIT_SECONDS
        last_play_click = 0.0
        fallback_allowed_at = time.time() + BATTLE_NET_PLAY_RETRY_SECONDS
        while time.time() <= deadline:
            if self.stop_requested:
                return False
            if activate_diablo():
                close_battle_net()
                LOGGER.info("Diablo activated after Battle.net launch")
                return True
            now = time.time()
            if activate_battle_net():
                match = self.battle_net_play_button_match()
                if match and match["confidence"] >= BATTLE_NET_PLAY_BUTTON_THRESHOLD and now - last_play_click >= 1.0:
                    x = match["x"] + match["width"] // 2
                    y = match["y"] + match["height"] // 2
                    release_inputs(release_mouse=False)
                    LOGGER.info("Battle.net Play button detected at %s,%s confidence=%.3f; clicking", x, y, match["confidence"])
                    left_click_clean(x, y)
                    last_play_click = now
                elif now >= fallback_allowed_at and now - last_play_click >= BATTLE_NET_PLAY_RETRY_SECONDS:
                    release_inputs(release_mouse=False)
                    LOGGER.info(
                        "Battle.net Play image not detected; clicking coordinate fallback at %s,%s",
                        self.battle_net_play_button_point["x"],
                        self.battle_net_play_button_point["y"],
                    )
                    left_click_clean(self.battle_net_play_button_point["x"], self.battle_net_play_button_point["y"])
                    last_play_click = now
            time.sleep(0.25)
        LOGGER.info("Diablo did not activate before launch timeout")
        return False

    def battle_net_play_button_match(self):
        """Return a confident Battle.net Play button template match if visible."""
        path = self.matcher.template_path(START_GAME_TEMPLATE_DIR, "Battle Net Play Button")
        match = self.matcher.find(path, self.battle_net_play_button_region)
        return match if match and match["confidence"] >= BATTLE_NET_PLAY_BUTTON_THRESHOLD else None

    def start_farming_flow(self, map_already_open=False):
        """Teleport to Southern Highlands and queue Northern Highlands as the next route step."""
        LOGGER.debug("Start farming flow; map_already_open=%s", map_already_open)
        self.run_on_ui_thread(self.clear_route_button_state, wait=True)
        if self.teleport_to_location(
            "Southern Highlands",
            verify_arrival=True,
            map_already_open=map_already_open,
            record_progress=False,
        ):
            self.run_on_ui_thread(lambda: self.set_last_teleport_location("Southern Highlands"), wait=True)
            self.run_on_ui_thread(lambda: self.set_only_queued_button("Northern Highlands"), wait=True)
            LOGGER.info("Start farming succeeded")
            return True
        LOGGER.info("Start farming flow failed")
        return False

    def run_teleport(self, location_name, verify=False, bypass_failsafe=False):
        """Start a background teleport worker for a selected location."""
        if self.running or self.combat_running:
            return
        if not bypass_failsafe and not self.teleport_failsafe_allows():
            blocked_location = self.teleport_failsafe_blocked_location()
            self.show_splash(f"Teleport blocked: {blocked_location or 'failsafe'}")
            return
        self.stop_requested = False
        self.prepare_teleport_button_state(location_name)
        thread = threading.Thread(
            target=self.teleport_worker,
            args=(location_name, verify, bypass_failsafe),
            daemon=True,
        )
        thread.start()

    def teleport_worker(self, location_name, verify, bypass_failsafe):
        """Run one teleport operation and reset running state afterward."""
        self.running = True
        self.update_status("running")
        try:
            arrived = self.teleport_to_location(
                location_name,
                verify_arrival=verify,
                bypass_failsafe=bypass_failsafe,
            )
            if verify:
                if arrived:
                    pending = self.pending_hotkey_teleport
                    if pending and pending.get("target") == location_name:
                        self.pending_hotkey_teleport = None
                elif not self.stop_requested:
                    self.run_on_ui_thread(lambda loc=location_name: self.set_only_queued_button(loc))
        finally:
            self.running = False
            self.stop_requested = False
            self.update_status("idle")

    def safe_sleep(self, seconds):
        """Sleep in short increments so stop requests can interrupt long waits."""
        deadline = time.time() + seconds
        while time.time() < deadline:
            if self.stop_requested:
                return False
            time.sleep(min(POLL_SECONDS, max(0, deadline - time.time())))
        return True

    def template_visible_in_dir(self, directory, template_name, region, threshold):
        """Check one named template in a directory against a screen region."""
        path = self.matcher.template_path(directory, template_name)
        return self.matcher.visible(path, region, threshold)

    def current_location_match(self, location_name):
        """Return a current-location template match for one location."""
        path = self.matcher.template_path(CURRENT_LOCATION_TEMPLATE_DIR, location_name)
        match = self.matcher.find(path, self.current_location_region)
        return match if match and match["confidence"] >= CURRENT_LOCATION_THRESHOLD else None

    def current_location_visible(self, location_name):
        """Return True when a location title is visible."""
        return self.current_location_match(location_name) is not None

    def best_current_location_match(self):
        """Return the strongest current-location template match."""
        return self.matcher.find_best_in_dir(CURRENT_LOCATION_TEMPLATE_DIR, self.current_location_region)

    def detect_current_location_by_image(self):
        """Identify the current area from the location-title template directory."""
        match = self.best_current_location_match()
        if match and match["confidence"] >= CURRENT_LOCATION_THRESHOLD:
            best_name = match["name"]
            LOGGER.debug("Current location detected as %s confidence=%.3f", best_name, match["confidence"])
            return self.normalize_detected_location(best_name)
        if match:
            LOGGER.debug("Ignoring weak current location match %s confidence=%.3f", match["name"], match["confidence"])
        return ""

    def is_in_game_by_current_location(self):
        """Infer in-game state from the presence of any current-location title."""
        return bool(self.detect_current_location_by_image())

    def is_current_location_new_tristram(self, log_miss=False):
        """Check whether current-location detection says New Tristram."""
        match = self.best_current_location_match()
        if match and match["name"] == "New Tristram" and match["confidence"] >= NEW_TRISTRAM_THRESHOLD:
            return True
        if log_miss and match:
            LOGGER.info("New Tristram check saw %s confidence=%.3f", match["name"], match["confidence"])
        elif log_miss:
            LOGGER.info("New Tristram check found no current location match")
        return False

    def wait_for_current_location_new_tristram(self, timeout):
        """Wait for New Tristram to be detected as the current location."""
        deadline = time.time() + timeout
        best_miss = None
        while time.time() <= deadline:
            if self.stop_requested:
                return False
            match = self.best_current_location_match()
            if match and match["name"] == "New Tristram" and match["confidence"] >= NEW_TRISTRAM_THRESHOLD:
                return True
            if match and (best_miss is None or match["confidence"] > best_miss["confidence"]):
                best_miss = match
            time.sleep(CURRENT_LOCATION_POLL_SECONDS)
        if best_miss:
            LOGGER.info("New Tristram check saw %s confidence=%.3f", best_miss["name"], best_miss["confidence"])
        else:
            LOGGER.info("New Tristram check found no current location match")
        return False

    def classify_new_tristram_location(self, timeout):
        """Classify current state as New Tristram, not New Tristram, or unknown."""
        deadline = time.time() + timeout
        best_miss = None
        while time.time() <= deadline:
            if self.stop_requested:
                return ""
            match = self.best_current_location_match()
            if match and match["confidence"] >= NEW_TRISTRAM_THRESHOLD and match["name"] == "New Tristram":
                return "yes"
            if match and match["confidence"] >= CURRENT_LOCATION_THRESHOLD:
                LOGGER.info("New Tristram check saw current location %s confidence=%.3f", match["name"], match["confidence"])
                return "no"
            if match and (best_miss is None or match["confidence"] > best_miss["confidence"]):
                best_miss = match
            time.sleep(CURRENT_LOCATION_POLL_SECONDS)
        if best_miss:
            LOGGER.info("New Tristram check inconclusive; best was %s confidence=%.3f", best_miss["name"], best_miss["confidence"])
        else:
            LOGGER.info("New Tristram check inconclusive; no current location match")
        return "unknown"

    def teleport_failsafe_blocked_location(self):
        """Return a location where manual teleporting should be blocked until cleared."""
        current_location = self.detect_current_location_by_image()
        if not current_location:
            return ""
        if current_location in TELEPORT_FAILSAFE_BLOCKED_LOCATIONS:
            return current_location
        return ""

    def teleport_failsafe_allows(self):
        """Enforce the teleport failsafe unless the current flow has bypassed it."""
        if self.teleport_failsafe_bypass:
            return True
        blocked_location = self.teleport_failsafe_blocked_location()
        if not blocked_location:
            return True
        LOGGER.info("Teleport blocked by failsafe at %s", blocked_location)
        return False

    def wait_for_current_location(self, location_name, timeout):
        """Wait for a specific current-location title after teleporting."""
        deadline = time.time() + timeout
        while time.time() <= deadline:
            if self.stop_requested:
                return False
            if location_key(location_name) == location_key("New Tristram"):
                if self.is_current_location_new_tristram():
                    return True
                time.sleep(CURRENT_LOCATION_POLL_SECONDS)
                continue
            current_location = self.detect_current_location_by_image()
            if location_matches_route_target(current_location, location_name):
                return True
            time.sleep(CURRENT_LOCATION_POLL_SECONDS)
        return False

    def prepare_new_tristram_for_exit(self):
        """Prepare to leave/close game by running salvage, repair, and GG handling once per cycle."""
        if self.repair_already_executed:
            # A second prep in the same cycle should not salvage/repair twice;
            # it only makes sure the character is back in the expected town.
            return self.teleport_to_new_tristram_if_needed()
        return self.salvage_repair_stash_for_new_game()

    def salvage_repair_stash_for_new_game(self):
        """Run the exit/new-game prep chain: salvage non-GG items, repair, stash GG if detected, close panels."""
        if self.stop_requested or not activate_diablo():
            return False
        LOGGER.info("Make New Game prep: salvaging non-GG items before repair")
        if not self.salvage_flow():
            return False
        if self.stop_requested:
            return False
        # Salvage leaves the inventory/menu context available, which is the
        # cheapest moment to scan for a Gibbering Gemstone before repair closes panels.
        gibbering_gemstone_matches = self.gibbering_gemstone_matches(self.salvage_inventory_grid)
        if gibbering_gemstone_matches:
            LOGGER.info("Make New Game prep: Gibbering Gemstone detected during open-inventory salvage scan")
        else:
            LOGGER.info("Make New Game prep: no Gibbering Gemstone detected during open-inventory salvage scan")
        LOGGER.info("Make New Game prep: repairing after salvage")
        if not self.perform_repair_from_blacksmith_menu():
            return False
        if self.stop_requested:
            return False
        if gibbering_gemstone_matches:
            # GG stash failures are logged but non-fatal so the main farming loop
            # can continue rather than getting stuck in town.
            LOGGER.info("Make New Game prep: storing detected Gibbering Gemstone before leaving game")
            if not self.store_gibbering_gemstones_in_stash(gibbering_gemstone_matches):
                LOGGER.info("Make New Game prep: GG stash step did not complete; continuing to new game")
        else:
            LOGGER.info("Make New Game prep: skipping GG stash; none detected during salvage")
        if not self.close_panels_before_salvage_positioning():
            LOGGER.info("Make New Game prep: could not close panels before leaving game")
            return False
        return True

    def teleport_to_new_tristram_if_needed(self):
        """Return to New Tristram, using the bounce path when already there."""
        if self.stop_requested or not activate_diablo():
            return False
        if self.wait_for_current_location_new_tristram(NEW_TRISTRAM_PREP_DETECT_TIMEOUT):
            return self.bounce_through_hidden_camp_to_new_tristram("Make New Game prep")
        return self.teleport_to_new_tristram()

    def teleport_to_new_tristram(self):
        """Teleport to New Tristram and recover through Hidden Camp if arrival is ambiguous."""
        if not self.teleport_to_location("New Tristram", record_progress=False):
            return False
        arrival_started_at = time.time()
        if self.wait_for_current_location("New Tristram", TELEPORT_ARRIVAL_TIMEOUT):
            if time.time() - arrival_started_at <= NEW_TRISTRAM_ALREADY_THERE_CONFIRM_SECONDS:
                LOGGER.info("New Tristram confirmed immediately after waypoint click; bouncing through Hidden Camp")
                return self.bounce_through_hidden_camp_to_new_tristram("New Tristram already-there recovery")
            return True
        LOGGER.info("New Tristram arrival not confirmed; bouncing through Hidden Camp")
        return self.bounce_through_hidden_camp_to_new_tristram("New Tristram recovery")

    def bounce_through_hidden_camp_to_new_tristram(self, context):
        """Teleport away to Hidden Camp and back to force a clean New Tristram state."""
        LOGGER.info("%s: teleporting to Hidden Camp, then back to New Tristram", context)
        if not self.teleport_to_location("Hidden Camp", verify_arrival=True, record_progress=False):
            LOGGER.info("%s failed: could not teleport to Hidden Camp", context)
            return False
        if not self.teleport_to_location("New Tristram", record_progress=False):
            LOGGER.info("%s failed: could not click New Tristram after Hidden Camp", context)
            return False
        if not self.wait_for_current_location("New Tristram", TELEPORT_ARRIVAL_TIMEOUT):
            LOGGER.info("%s failed: could not confirm return to New Tristram", context)
            return False
        return True

    def perform_repair_from_blacksmith_menu(self):
        """Click Repair tab/button from an already-open blacksmith menu and close it."""
        if self.stop_requested:
            return False
        repair_tab = self.repair_coords.get("Repair Tab")
        LOGGER.debug("Repair: clicking repair tab at %s,%s", repair_tab["x"], repair_tab["y"])
        click(repair_tab["x"], repair_tab["y"])
        if not self.wait_for_repair_template("Repair Menu", self.repair_menu_region, REPAIR_TIMEOUT):
            LOGGER.info("Repair failed: repair menu not detected")
            return False
        if self.stop_requested:
            return False
        repair_button = self.repair_coords.get("Repair Button")
        LOGGER.debug("Repair: clicking repair button at %s,%s", repair_button["x"], repair_button["y"])
        click(repair_button["x"], repair_button["y"])
        if not self.wait_for_repair_template("Repair Menu", self.repair_menu_region, REPAIR_TIMEOUT):
            LOGGER.info("Repair failed: repair menu not still visible after repair click")
            return False
        LOGGER.debug(
            "Repair: closing repair window at %s,%s",
            self.left_panel_close_point["x"],
            self.left_panel_close_point["y"],
        )
        click(self.left_panel_close_point["x"], self.left_panel_close_point["y"])
        if not self.wait_for_repair_window_closed(REPAIR_TIMEOUT):
            LOGGER.info("Repair failed: repair window did not close after Esc")
            return False
        if not self.close_blacksmith_panel_before_gg_stash():
            LOGGER.info("Repair failed: blacksmith panel did not close after repair")
            return False
        self.repair_already_executed = True
        LOGGER.info("Repair succeeded")
        return True

    def wait_for_repair_station_and_click(self, timeout):
        """Click/path to the repair station until the blacksmith menu opens."""
        return self.click_repair_station_by_coordinates(timeout)

    def click_repair_station_by_coordinates(self, timeout):
        """Cycle configured repair-station coordinate candidates until the menu appears."""
        deadline = time.time() + timeout
        last_click_at = 0.0
        point_index = 0
        while time.time() <= deadline:
            if self.stop_requested:
                return False
            if self.blacksmith_menu_visible():
                LOGGER.info("Repair station opened blacksmith menu")
                return True

            now = time.time()
            if now - last_click_at < REPAIR_STATION_PATH_RECLICK_SECONDS:
                time.sleep(POLL_SECONDS)
                continue

            point = self.repair_station_points[point_index % len(self.repair_station_points)]
            LOGGER.info(
                "Clicking repair station coordinate candidate %s at %s,%s",
                (point_index % len(self.repair_station_points)) + 1,
                point["x"],
                point["y"],
            )
            click(point["x"], point["y"])
            last_click_at = now
            point_index += 1

        LOGGER.info("Repair station coordinate pathing timed out")
        return False

    def wait_for_repair_template(self, template_name, region, timeout):
        """Wait for a repair/blacksmith template to become visible."""
        LOGGER.debug("Waiting for repair template: %s", template_name)
        deadline = time.time() + timeout
        while time.time() <= deadline:
            if self.stop_requested:
                return False
            if self.template_visible_in_dir(REPAIR_TEMPLATE_DIR, template_name, region, REPAIR_THRESHOLD):
                LOGGER.debug("Repair template visible: %s", template_name)
                return True
            time.sleep(POLL_SECONDS)
        LOGGER.info("Timed out waiting for repair template: %s", template_name)
        return False

    def wait_for_repair_window_closed(self, timeout):
        """Wait until the repair menu template disappears after Escape."""
        LOGGER.debug("Waiting for repair window to close")
        deadline = time.time() + timeout
        while time.time() <= deadline:
            if self.stop_requested:
                return False
            if not self.template_visible_in_dir(REPAIR_TEMPLATE_DIR, "Repair Menu", self.repair_menu_region, REPAIR_THRESHOLD):
                LOGGER.debug("Repair window closed")
                return True
            time.sleep(POLL_SECONDS)
        LOGGER.info("Timed out waiting for repair window to close")
        return False

    def close_blacksmith_panel_before_gg_stash(self):
        """Close any lingering blacksmith/salvage panel before interacting with the stash chest."""
        for _attempt in range(3):
            if self.stop_requested:
                return False
            if not (self.salvage_menu_visible() or self.blacksmith_menu_visible()):
                return True
            LOGGER.info(
                "Closing blacksmith panel before GG stash at %s,%s",
                self.left_panel_close_point["x"],
                self.left_panel_close_point["y"],
            )
            click(self.left_panel_close_point["x"], self.left_panel_close_point["y"])
            if self.safe_sleep(0.35) is False:
                return False
        return not (self.salvage_menu_visible() or self.blacksmith_menu_visible())

    def salvage_inventory_visible(self):
        """Infer whether inventory is open from visible blank inventory tiles."""
        return self.blank_inventory_tile_count() >= SALVAGE_INVENTORY_OPEN_MIN_BLANK_TILES

    def blank_inventory_tile_count(self):
        """Count blank inventory-slot template matches in the inventory grid."""
        path = self.matcher.template_path(SALVAGE_TEMPLATE_DIR, "Blank Inventory Tile")
        return len(self.matcher.find_all(path, self.salvage_inventory_grid, SALVAGE_BLANK_TILE_THRESHOLD, min_distance=20))

    def salvage_flow(self):
        """Position at blacksmith, open salvage menu, and salvage filled non-GG inventory slots."""
        if self.stop_requested or not activate_diablo():
            return False
        # The blacksmith flow assumes a clean panel state before pathing, because
        # open inventory/menus can intercept clicks meant for the repair station.
        if not self.close_panels_before_salvage_positioning():
            return False
        if not self.position_for_salvage():
            return False
        if not self.open_salvage_menu():
            return False
        # The first-slot check prevents activating the salvage cursor when there
        # is nothing worth clicking and also protects GG slots from normal salvage.
        first_salvageable_slot = self.first_filled_inventory_slot(ignore_gibbering_gemstone=True)
        if not first_salvageable_slot:
            LOGGER.info("Salvage skipped: no salvageable inventory items detected")
            return True
        if not self.activate_salvage_cursor():
            return False
        salvaged_count = self.salvage_inventory_items(first_slot=first_salvageable_slot)
        LOGGER.info("Salvage finished; salvaged %s item click(s)", salvaged_count)
        return True

    def close_panels_before_salvage_positioning(self):
        """Close inventory/blacksmith panels before moving or leaving the salvage flow."""
        if self.salvage_menu_visible() or self.blacksmith_menu_visible():
            LOGGER.info("Closing blacksmith panel before salvage positioning")
            self.press_escape_for_game()
            if self.safe_sleep(0.35) is False:
                return False
        if self.salvage_inventory_visible():
            LOGGER.info("Closing inventory before salvage positioning")
            press_vk(VK_I)
            if not self.wait_for_salvage_inventory_closed(1.5):
                LOGGER.info("Inventory still appears visible after pressing I")
                return False
        if self.salvage_menu_visible() or self.blacksmith_menu_visible():
            LOGGER.info("Closing blacksmith panel before salvage positioning")
            self.press_escape_for_game()
            if self.safe_sleep(0.35) is False:
                return False
        return True

    def wait_for_salvage_inventory_closed(self, timeout):
        """Wait until the inventory grid is no longer visible."""
        deadline = time.time() + timeout
        while time.time() <= deadline:
            if self.stop_requested:
                return False
            if not self.salvage_inventory_visible():
                return True
            time.sleep(POLL_SECONDS)
        return False

    def position_for_salvage(self):
        """Ensure the character is in a reliable New Tristram blacksmith position."""
        if self.salvage_menu_visible() or self.blacksmith_menu_visible():
            return True

        new_tristram_state = self.classify_new_tristram_location(NEW_TRISTRAM_PREP_DETECT_TIMEOUT)
        if new_tristram_state == "yes":
            LOGGER.info("Salvage position: in New Tristram")
            return self.bounce_through_hidden_camp_to_new_tristram("Salvage position")
        if new_tristram_state == "unknown":
            LOGGER.info("Salvage position: current location inconclusive; using normal New Tristram teleport")

        LOGGER.info("Salvage position: not in New Tristram; teleporting to New Tristram")
        if not self.teleport_to_new_tristram():
            LOGGER.info("Salvage failed: could not teleport to New Tristram")
            return False

        return True

    def open_salvage_menu(self):
        """Open the blacksmith menu and switch to the Salvage tab."""
        if self.salvage_menu_visible():
            return True
        if not self.blacksmith_menu_visible():
            if not self.wait_for_repair_station_and_click(REPAIR_TIMEOUT):
                LOGGER.info("Salvage failed: repair station not detected")
                return False
            if not self.wait_for_blacksmith_menu(REPAIR_TIMEOUT):
                LOGGER.info("Salvage failed: blacksmith menu not detected")
                return False
        if self.stop_requested:
            return False
        if not self.click_salvage_tab():
            return False
        return self.wait_for_salvage_menu(SALVAGE_TIMEOUT)

    def blacksmith_menu_visible(self):
        """Return True when the blacksmith menu template is visible."""
        return self.template_visible_in_dir(REPAIR_TEMPLATE_DIR, "Blacksmith Menu", self.blacksmith_shop_region, REPAIR_THRESHOLD)

    def wait_for_blacksmith_menu(self, timeout):
        """Wait for the blacksmith menu to appear."""
        if self.blacksmith_menu_visible():
            LOGGER.info("Blacksmith menu visible")
            return True
        return self.wait_for_repair_template("Blacksmith Menu", self.blacksmith_shop_region, timeout)

    def salvage_menu_visible(self):
        """Return True when the Salvage tab/button templates are visible."""
        return (
            self.template_visible_in_dir(SALVAGE_TEMPLATE_DIR, "Salvage Tab", self.salvage_tab_region, SALVAGE_THRESHOLD)
            or self.template_visible_in_dir(SALVAGE_TEMPLATE_DIR, "Salvage Button", self.salvage_button_region, SALVAGE_THRESHOLD)
        )

    def click_salvage_tab(self):
        """Click the Salvage tab using image detection with coordinate fallback."""
        path = self.matcher.template_path(SALVAGE_TEMPLATE_DIR, "Salvage Tab")
        deadline = time.time() + SALVAGE_TIMEOUT
        fallback_deadline = time.time() + 1.0
        best_confidence = 0.0
        best_match = None
        while time.time() <= deadline:
            if self.stop_requested:
                return False
            match = self.matcher.find(path, self.salvage_tab_region)
            if match:
                if match["confidence"] > best_confidence:
                    best_confidence = match["confidence"]
                    best_match = match
                if match["confidence"] >= SALVAGE_THRESHOLD:
                    x = match["x"] + match["width"] // 2
                    y = match["y"] + match["height"] // 2
                    LOGGER.info("Salvage tab detected at %s,%s confidence=%.3f; clicking", x, y, match["confidence"])
                    click(x, y)
                    return True
            if best_match and best_confidence >= SALVAGE_TAB_FALLBACK_THRESHOLD and time.time() >= fallback_deadline:
                x = best_match["x"] + best_match["width"] // 2
                y = best_match["y"] + best_match["height"] // 2
                LOGGER.info("Salvage tab weak match at %s,%s confidence=%.3f; clicking fallback", x, y, best_confidence)
                click(x, y)
                return True
            time.sleep(POLL_SECONDS)
        LOGGER.info(
            "Timed out waiting for salvage tab; clicking coordinate fallback at %s,%s best confidence=%.3f",
            self.salvage_tab_point["x"],
            self.salvage_tab_point["y"],
            best_confidence,
        )
        click(self.salvage_tab_point["x"], self.salvage_tab_point["y"])
        return True

    def wait_for_salvage_menu(self, timeout):
        """Wait for the salvage menu UI to become visible."""
        deadline = time.time() + timeout
        while time.time() <= deadline:
            if self.stop_requested:
                return False
            if self.salvage_menu_visible():
                return True
            time.sleep(POLL_SECONDS)
        LOGGER.info("Timed out waiting for salvage menu")
        return False

    def activate_salvage_cursor(self):
        """Click the Salvage button so inventory clicks salvage items."""
        path = self.matcher.template_path(SALVAGE_TEMPLATE_DIR, "Salvage Button")
        match = self.matcher.find(path, self.salvage_button_region)
        if match and match["confidence"] >= SALVAGE_THRESHOLD:
            x = match["x"] + match["width"] // 2
            y = match["y"] + match["height"] // 2
            LOGGER.info("Salvage button detected at %s,%s confidence=%.3f; clicking", x, y, match["confidence"])
            click(x, y)
        else:
            confidence = match["confidence"] if match else 0.0
            LOGGER.info(
                "Salvage button template not detected; clicking fallback at %s,%s best confidence=%.3f",
                self.salvage_button_point["x"],
                self.salvage_button_point["y"],
                confidence,
            )
            click(self.salvage_button_point["x"], self.salvage_button_point["y"])
        return self.safe_sleep(0.08) is not False

    def salvage_inventory_items(self, first_slot=None):
        """Click filled inventory slots until no more non-GG salvageable items are detected."""
        salvaged_count = 0
        for _attempt in range(SALVAGE_MAX_ITEMS):
            if self.stop_requested:
                return salvaged_count
            slot = first_slot or self.first_filled_inventory_slot(ignore_gibbering_gemstone=True)
            first_slot = None
            if not slot:
                return salvaged_count
            LOGGER.info(
                "Salvaging inventory slot at %s,%s mean=%.1f std=%.1f blank=%.3f",
                slot["x"],
                slot["y"],
                slot["mean"],
                slot["stddev"],
                slot["blank_confidence"],
            )
            click(slot["x"], slot["y"])
            result = self.wait_for_salvage_click_result(slot, SALVAGE_CLICK_RESULT_TIMEOUT)
            if result == "confirmation":
                press_vk(VK_RETURN)
            elif result == "removed":
                LOGGER.info("Salvage slot at %s,%s cleared without confirmation", slot["x"], slot["y"])
            else:
                LOGGER.info("Salvage skipped slot at %s,%s: no confirmation or slot clear detected", slot["x"], slot["y"])
                if self.safe_sleep(0.15) is False:
                    return salvaged_count
                continue
            salvaged_count += 1
            if self.safe_sleep(0.05) is False:
                return salvaged_count
        LOGGER.info("Salvage stopped after max item attempts: %s", SALVAGE_MAX_ITEMS)
        return salvaged_count

    def wait_for_salvage_click_result(self, slot, timeout):
        """Wait for salvage confirmation or for the clicked slot to become empty."""
        deadline = time.time() + timeout
        while time.time() <= deadline:
            if self.stop_requested:
                return ""
            if self.template_visible_in_dir(
                SALVAGE_TEMPLATE_DIR,
                "Salvage Confirmation Button",
                self.salvage_confirmation_region,
                SALVAGE_THRESHOLD,
            ):
                return "confirmation"
            if not self.inventory_slot_still_filled(slot):
                return "removed"
            time.sleep(SALVAGE_CLICK_RESULT_POLL_SECONDS)
        return ""

    def first_filled_inventory_slot(self, ignore_gibbering_gemstone=False):
        """Find the next non-empty inventory slot, optionally skipping GG matches."""
        grid = self.salvage_inventory_grid
        blank_path = self.matcher.template_path(SALVAGE_TEMPLATE_DIR, "Blank Inventory Tile")
        blank_template = self.matcher.load(blank_path) if blank_path.exists() else None
        gemstone_matches = self.gibbering_gemstone_matches(grid) if ignore_gibbering_gemstone else []
        region = {
            "left": grid["left"],
            "top": grid["top"],
            "width": grid["width"],
            "height": grid["height"],
        }
        with mss.mss() as sct:
            screenshot = np.array(sct.grab(region))
        gray = cv2.cvtColor(screenshot, cv2.COLOR_BGRA2GRAY)
        step_x = grid["width"] / SALVAGE_GRID_COLUMNS
        step_y = grid["height"] / SALVAGE_GRID_ROWS
        slot_size = round(min(step_x, step_y) * 0.78)
        inset = max(3, round(slot_size * 0.08))

        for row in range(SALVAGE_GRID_ROWS):
            for col in range(SALVAGE_GRID_COLUMNS):
                x = round(col * step_x + (step_x - slot_size) / 2)
                y = round(row * step_y + (step_y - slot_size) / 2)
                crop = gray[
                    y + inset : y + slot_size - inset,
                    x + inset : x + slot_size - inset,
                ]
                if crop.size == 0:
                    continue
                blank_confidence = 0.0
                if blank_template is not None:
                    resized_blank = cv2.resize(
                        blank_template,
                        (crop.shape[1], crop.shape[0]),
                        interpolation=cv2.INTER_AREA,
                    )
                    result = cv2.matchTemplate(crop, resized_blank, cv2.TM_CCOEFF_NORMED)
                    _, blank_confidence, _, _ = cv2.minMaxLoc(result)
                    if blank_confidence >= SALVAGE_EMPTY_SLOT_MATCH_THRESHOLD:
                        continue

                mean = float(crop.mean())
                stddev = float(crop.std())
                if mean >= SALVAGE_SLOT_FILLED_MEAN_THRESHOLD or stddev >= SALVAGE_SLOT_FILLED_STD_THRESHOLD:
                    slot = {
                        "x": grid["left"] + x + slot_size // 2,
                        "y": grid["top"] + y + slot_size // 2,
                        "crop_region": {
                            "left": grid["left"] + x + inset,
                            "top": grid["top"] + y + inset,
                            "width": slot_size - (inset * 2),
                            "height": slot_size - (inset * 2),
                        },
                        "mean": mean,
                        "stddev": stddev,
                        "blank_confidence": blank_confidence,
                    }
                    if ignore_gibbering_gemstone and self.point_near_any_match(slot, gemstone_matches):
                        LOGGER.info("Skipping Gibbering Gemstone inventory slot at %s,%s during salvage", slot["x"], slot["y"])
                        continue
                    return slot
        return None

    def gibbering_gemstone_matches(self, region):
        """Find all Gibbering Gemstone template matches in a region."""
        path = self.matcher.template_path(SALVAGE_TEMPLATE_DIR, "Gibbering Gemstone")
        matches = self.matcher.find_all(path, region, GG_TEMPLATE_THRESHOLD, GG_TEMPLATE_NMS_DISTANCE)
        return sorted(matches, key=lambda item: (item["y"], item["x"]))

    def point_near_any_match(self, point, matches, padding=34):
        """Return True when a point lies inside or near any template match rectangle."""
        x = point["x"]
        y = point["y"]
        for match in matches:
            left = match["x"] - padding
            top = match["y"] - padding
            right = match["x"] + match["width"] + padding
            bottom = match["y"] + match["height"] + padding
            if left <= x <= right and top <= y <= bottom:
                return True
        return False

    def store_gibbering_gemstones_in_stash(self, initial_inventory_matches=None):
        """Move detected Gibbering Gemstones from inventory into the configured stash tab."""
        if self.stop_requested or not activate_diablo():
            return False
        if not self.close_blacksmith_panel_before_gg_stash():
            return False
        pending_inventory_matches = list(initial_inventory_matches or [])
        if not pending_inventory_matches and not self.ensure_inventory_visible_for_gg_stash():
            return False
        if not pending_inventory_matches:
            pending_inventory_matches = self.gibbering_gemstone_matches(self.salvage_inventory_grid)
        if not pending_inventory_matches:
            LOGGER.info("GG stash skipped: no Gibbering Gemstone detected in inventory")
            self.press_escape_for_game()
            self.safe_sleep(0.20)
            return True
        if not self.open_stash_menu():
            return False
        # Opening the stash automatically exposes inventory. The blank-slot
        # inventory detector is tuned for salvage and can miss the darker
        # inventory panel while stash is open, so do not block the tab click on it.
        if self.safe_sleep(0.25) is False:
            return False
        if not self.open_gg_stash_tab():
            return False
        # Once the stash tab is selected, rescan the inventory instead of using
        # a pre-repair match. The inventory panel is the same place visually,
        # but a fresh match avoids carrying stale coordinates into the move.
        pending_inventory_matches = []

        stored_count = 0
        for _attempt in range(SALVAGE_MAX_ITEMS):
            if self.stop_requested:
                return False
            inventory_matches = pending_inventory_matches or self.gibbering_gemstone_matches(self.salvage_inventory_grid)
            pending_inventory_matches = []
            if not inventory_matches:
                LOGGER.info("GG stash: stored %s Gibbering Gemstone(s)", stored_count)
                self.press_escape_for_game()
                return self.safe_sleep(0.25) is not False

            placement_point = self.next_available_gg_stash_point()
            if not placement_point:
                # If the reserved GG stash columns are full, the overflow policy
                # is to salvage the remaining stones instead of blocking the run.
                LOGGER.info("GG stash columns full; salvaging remaining Gibbering Gemstones")
                self.press_escape_for_game()
                if self.safe_sleep(0.25) is False:
                    return False
                return self.salvage_remaining_gibbering_gemstones()

            source = inventory_matches[0]
            source_x = source["x"] + source["width"] // 2
            source_y = source["y"] + source["height"] // 2
            LOGGER.info(
                "GG stash: moving Gibbering Gemstone from %s,%s to %s,%s",
                source_x,
                source_y,
                placement_point["x"],
                placement_point["y"],
            )
            left_click_clean(source_x, source_y)
            if self.safe_sleep(0.25) is False:
                return False
            left_click_clean(placement_point["x"], placement_point["y"])
            stored_count += 1
            if self.safe_sleep(0.35) is False:
                return False

        LOGGER.info("GG stash stopped after max item attempts: %s", SALVAGE_MAX_ITEMS)
        return False

    def ensure_inventory_visible_for_gg_stash(self):
        """Open inventory before scanning/moving Gibbering Gemstones."""
        if self.salvage_inventory_visible():
            return True
        LOGGER.info("GG stash: opening inventory before gemstone scan")
        if self.stop_requested or not activate_diablo():
            return False
        press_vk(VK_I)
        deadline = time.time() + 2.0
        while time.time() <= deadline:
            if self.stop_requested:
                return False
            if self.salvage_inventory_visible():
                return True
            time.sleep(POLL_SECONDS)
        LOGGER.info("GG stash failed: inventory did not open for gemstone scan")
        return False

    def open_stash_menu(self):
        """Click the stash point until the stash menu appears."""
        deadline = time.time() + GG_STASH_TIMEOUT
        point_index = 0
        while time.time() <= deadline:
            if self.stop_requested:
                return False
            if self.stash_menu_visible():
                return True
            point = self.stash_points[point_index % len(self.stash_points)]
            LOGGER.info("Clicking stash coordinate at %s,%s", point["x"], point["y"])
            click(point["x"], point["y"])
            point_index += 1
            if self.wait_for_stash_menu_after_click(min(GG_STASH_CLICK_MENU_TIMEOUT, max(0.0, deadline - time.time()))):
                return True
            LOGGER.info("Stash menu did not appear within %.1f seconds; retrying stash click", GG_STASH_CLICK_MENU_TIMEOUT)
        LOGGER.info("Timed out waiting for stash menu")
        return False

    def stash_menu_visible(self):
        """Return True when the stash menu template is visible."""
        return self.template_visible_in_dir(SALVAGE_TEMPLATE_DIR, "Stash", self.stash_menu_region, SALVAGE_THRESHOLD)

    def wait_for_stash_menu_after_click(self, timeout):
        """Wait briefly after a stash click for the stash menu to appear."""
        deadline = time.time() + timeout
        while time.time() <= deadline:
            if self.stop_requested:
                return False
            if self.stash_menu_visible():
                return True
            time.sleep(POLL_SECONDS)
        return False

    def open_gg_stash_tab(self):
        """Click the configured GG stash tab and wait for it to focus."""
        if self.stop_requested:
            return False
        clicked_tab = False
        non_focused_match = self.matcher.find(
            self.matcher.template_path(SALVAGE_TEMPLATE_DIR, "GG Tab Non Focused"),
            self.gg_tab_region,
        )
        if non_focused_match and non_focused_match["confidence"] >= SALVAGE_THRESHOLD:
            x = non_focused_match["x"] + non_focused_match["width"] // 2
            y = non_focused_match["y"] + non_focused_match["height"] // 2
            LOGGER.info("Clicking detected GG stash tab at %s,%s confidence=%.3f", x, y, non_focused_match["confidence"])
            left_click_clean(x, y)
            clicked_tab = True
        else:
            confidence = non_focused_match["confidence"] if non_focused_match else 0.0
            LOGGER.info(
                "GG stash tab template not detected; clicking coordinate fallback at %s,%s best confidence=%.3f",
                self.gg_stash_tab_point["x"],
                self.gg_stash_tab_point["y"],
                confidence,
            )
            left_click_clean(self.gg_stash_tab_point["x"], self.gg_stash_tab_point["y"])
            clicked_tab = True
        return self.wait_for_gg_stash_tab(GG_STASH_TIMEOUT, clicked_tab=clicked_tab)

    def wait_for_gg_stash_tab(self, timeout, clicked_tab=False):
        """Wait for the GG tab to focus, clicking a non-focused match as fallback."""
        deadline = time.time() + timeout
        last_fallback_click_at = 0.0
        while time.time() <= deadline:
            if self.stop_requested:
                return False
            if self.template_visible_in_dir(SALVAGE_TEMPLATE_DIR, "GG Tab Focused", self.gg_tab_region, SALVAGE_THRESHOLD):
                return True
            non_focused_match = self.matcher.find(
                self.matcher.template_path(SALVAGE_TEMPLATE_DIR, "GG Tab Non Focused"),
                self.gg_tab_region,
            )
            if clicked_tab and self.stash_menu_visible() and (not non_focused_match or non_focused_match["confidence"] < SALVAGE_TAB_FALLBACK_THRESHOLD):
                LOGGER.info("GG tab appears focused after click; non-focused tab no longer visible")
                return True
            now = time.time()
            if non_focused_match and non_focused_match["confidence"] >= SALVAGE_THRESHOLD and now - last_fallback_click_at >= 0.5:
                x = non_focused_match["x"] + non_focused_match["width"] // 2
                y = non_focused_match["y"] + non_focused_match["height"] // 2
                LOGGER.info("GG tab visible but not focused at %s,%s confidence=%.3f; clicking fallback", x, y, non_focused_match["confidence"])
                left_click_clean(x, y)
                clicked_tab = True
                if self.safe_sleep(0.08) is False:
                    return False
                left_click_clean(self.gg_stash_tab_point["x"], self.gg_stash_tab_point["y"])
                clicked_tab = True
                last_fallback_click_at = now
            time.sleep(POLL_SECONDS)
        LOGGER.info("Timed out waiting for GG stash tab")
        return False

    def next_available_gg_stash_point(self):
        """Choose the first configured stash placement point that does not already contain a GG."""
        occupied_matches = self.gibbering_gemstone_matches(self.gg_stash_placement_region)
        for point in self.gg_stash_placement_points:
            if not self.point_near_any_match(point, occupied_matches, padding=28):
                return point
        return None

    def salvage_remaining_gibbering_gemstones(self):
        """If the GG stash columns are full, salvage remaining GG items as overflow handling."""
        if self.stop_requested or not activate_diablo():
            return False
        if not self.open_salvage_menu():
            return False
        if not self.activate_salvage_cursor():
            return False

        salvaged_count = 0
        for _attempt in range(SALVAGE_MAX_ITEMS):
            if self.stop_requested:
                return False
            matches = self.gibbering_gemstone_matches(self.salvage_inventory_grid)
            if not matches:
                LOGGER.info("GG overflow salvage finished; salvaged %s Gibbering Gemstone(s)", salvaged_count)
                self.press_escape_for_game()
                return self.safe_sleep(0.25) is not False
            match = matches[0]
            x = match["x"] + match["width"] // 2
            y = match["y"] + match["height"] // 2
            LOGGER.info("Salvaging overflow Gibbering Gemstone at %s,%s", x, y)
            click(x, y)
            result = self.wait_for_salvage_click_result(
                {"x": x, "y": y, "crop_region": {"left": match["x"], "top": match["y"], "width": match["width"], "height": match["height"]}},
                SALVAGE_CLICK_RESULT_TIMEOUT,
            )
            if result == "confirmation":
                press_vk(VK_RETURN)
            salvaged_count += 1
            if self.safe_sleep(0.08) is False:
                return False

        LOGGER.info("GG overflow salvage stopped after max item attempts: %s", SALVAGE_MAX_ITEMS)
        return False

    def inventory_slot_still_filled(self, slot):
        """Determine whether a previously clicked inventory slot still appears occupied."""
        region = slot.get("crop_region")
        if not region or region["width"] <= 0 or region["height"] <= 0:
            return True

        with mss.mss() as sct:
            screenshot = np.array(sct.grab(region))
        gray = cv2.cvtColor(screenshot, cv2.COLOR_BGRA2GRAY)

        blank_path = self.matcher.template_path(SALVAGE_TEMPLATE_DIR, "Blank Inventory Tile")
        if blank_path.exists():
            blank_template = self.matcher.load(blank_path)
            resized_blank = cv2.resize(
                blank_template,
                (gray.shape[1], gray.shape[0]),
                interpolation=cv2.INTER_AREA,
            )
            result = cv2.matchTemplate(gray, resized_blank, cv2.TM_CCOEFF_NORMED)
            _, blank_confidence, _, _ = cv2.minMaxLoc(result)
            if blank_confidence >= SALVAGE_EMPTY_SLOT_MATCH_THRESHOLD:
                return False

        return (
            float(gray.mean()) >= SALVAGE_SLOT_FILLED_MEAN_THRESHOLD
            or float(gray.std()) >= SALVAGE_SLOT_FILLED_STD_THRESHOLD
        )

    def leave_current_game_to_menu(self):
        """Open the game menu, click Leave Game, and wait for menu/start state."""
        if self.stop_requested or not activate_diablo():
            return False
        clicked_leave = False
        for attempt in range(1, LEAVE_GAME_MENU_ATTEMPTS + 1):
            LOGGER.info("Leave game: opening game menu with Esc, attempt %s", attempt)
            self.press_escape_for_game()
            if self.safe_sleep(LEAVE_GAME_MENU_OPEN_DELAY) is False:
                return False
            if self.start_game_button_visible():
                LOGGER.info("Leave game already at character menu")
                if self.safe_sleep(START_GAME_MENU_SETTLE_SECONDS) is False:
                    return False
                return True
            if self.wait_for_leave_game_button_and_click(timeout=LEAVE_GAME_BUTTON_IMAGE_ATTEMPT_TIMEOUT):
                clicked_leave = True
                break
            LOGGER.info("Leave game: menu attempt %s did not click Leave Game", attempt)

        if not clicked_leave:
            LOGGER.info("Leave game failed: leave button not clicked")
            return False

        deadline = time.time() + LEAVE_GAME_TIMEOUT
        last_in_game_seen = time.time()
        while time.time() <= deadline:
            if self.stop_requested:
                return False
            if not activate_diablo():
                time.sleep(0.5)
                continue
            if self.start_game_button_visible():
                LOGGER.info("Leave game succeeded: Start Game visible")
                if self.safe_sleep(START_GAME_MENU_SETTLE_SECONDS) is False:
                    return False
                return True
            if self.is_in_game_by_current_location():
                last_in_game_seen = time.time()
            elif time.time() - last_in_game_seen >= 2.0:
                LOGGER.info("Leave game likely succeeded: current location disappeared")
                return True
            time.sleep(POLL_SECONDS)
        LOGGER.info("Leave game failed: Start Game did not appear")
        return False

    def wait_for_leave_game_button_and_click(self, timeout=LEAVE_GAME_BUTTON_TIMEOUT):
        """Wait for Leave Game button template and click it, with fallback coordinates."""
        LOGGER.info("Waiting for Leave Game button for up to %s seconds", timeout)
        deadline = time.time() + timeout
        best_seen = None
        next_activate_at = 0
        while time.time() <= deadline:
            if self.stop_requested:
                return False
            now = time.time()
            if now >= next_activate_at:
                activate_diablo()
                next_activate_at = now + 1.0
            if self.start_game_button_visible():
                LOGGER.info("Leave Game wait found Start Game already visible")
                return True
            match = self.leave_game_button_match()
            if match and (best_seen is None or match["confidence"] > best_seen["confidence"]):
                best_seen = match
            if match and match["confidence"] >= LEAVE_GAME_THRESHOLD:
                x = match["x"] + (match["width"] // 2)
                y = match["y"] + (match["height"] // 2)
                LOGGER.info(
                    "Leave Game button detected via %s at %s,%s confidence=%.3f; clicking",
                    match.get("template", "unknown"),
                    x,
                    y,
                    match["confidence"],
                )
                click(x, y)
                return True
            time.sleep(LEAVE_GAME_BUTTON_POLL_SECONDS)
        if self.stop_requested or not activate_diablo():
            return False
        if best_seen:
            LOGGER.info(
                "Leave Game image timed out; best was %s confidence=%.3f",
                best_seen.get("template", "unknown"),
                best_seen["confidence"],
            )
        LOGGER.info("Leave Game button not detected; clicking configured fallback point")
        click(self.leave_game_button_point["x"], self.leave_game_button_point["y"])
        return True

    def leave_game_button_match(self):
        """Return the best Leave Game button image match."""
        best_match = None
        for path in sorted(LEAVE_GAME_TEMPLATE_DIR.glob("*.png")):
            if path.name.lower().endswith("coordinates.txt"):
                continue
            match = self.matcher.find(path, self.leave_game_button_region)
            if match and (best_match is None or match["confidence"] > best_match["confidence"]):
                best_match = match | {"template": path.name}
        return best_match

    def start_game_button_visible(self):
        """Return True when the Start Game button is visible."""
        return self.start_game_button_match() is not None

    def start_game_button_match(self):
        """Return a confident ready-color Start Game button template match if visible."""
        path = self.matcher.template_path(START_GAME_TEMPLATE_DIR, "Start Game Button")
        # Start Game keeps the same shape while its color changes as it becomes
        # clickable. Use the actual PNG colors so the disabled/wrong-color state
        # does not pass the same way it could with grayscale shape matching.
        match = self.matcher.find_color(path, self.start_game_button_region)
        return match if match and match["confidence"] >= START_GAME_COLOR_THRESHOLD else None

    def wait_for_stable_start_game_button(self, first_match, timeout=START_GAME_STABLE_SECONDS):
        """Require the Start Game button to remain visible briefly before clicking."""
        deadline = time.time() + timeout
        stable_match = first_match
        while time.time() <= deadline:
            if self.stop_requested:
                return None
            if self.is_in_game_by_current_location():
                return None
            park_cursor_away_from_menu_buttons()
            match = self.start_game_button_match()
            if not match:
                return None
            stable_match = match
            time.sleep(POLL_SECONDS)
        return stable_match

    def wait_for_start_game_button_and_click(self):
        """Wait for the character-menu Start Game button and click it."""
        LOGGER.info("Waiting for Start Game button for up to %s seconds", START_GAME_TIMEOUT)
        deadline = time.time() + START_GAME_TIMEOUT
        while time.time() <= deadline:
            if self.stop_requested:
                return False
            if not activate_diablo():
                time.sleep(0.5)
                continue
            if self.is_in_game_by_current_location():
                LOGGER.info("Already in-game while waiting for Start Game button")
                return True
            park_cursor_away_from_menu_buttons()
            match = self.start_game_button_match()
            if match:
                stable_match = self.wait_for_stable_start_game_button(match)
                if not stable_match:
                    time.sleep(POLL_SECONDS)
                    continue
                if self.safe_sleep(START_GAME_CLICK_SETTLE_SECONDS) is False:
                    return False
                match = self.start_game_button_match()
                if not match:
                    LOGGER.info("Start Game button disappeared during click settle; retrying")
                    time.sleep(POLL_SECONDS)
                    continue
                x = match["x"] + (match["width"] // 2)
                y = match["y"] + (match["height"] // 2)
                LOGGER.info(
                    "Start Game button stable at %s,%s confidence=%.3f; clicking",
                    x,
                    y,
                    match["confidence"],
                )
                left_click_clean(x, y)
                park_cursor_away_from_menu_buttons()
                if self.wait_for_start_game_button_to_disappear(3):
                    LOGGER.info("Start Game button disappeared after click")
                    return True
                LOGGER.info(
                    "Start Game button still visible after click; waiting %.2fs before retry",
                    START_GAME_RETRY_COOLDOWN_SECONDS,
                )
                if self.safe_sleep(START_GAME_RETRY_COOLDOWN_SECONDS) is False:
                    return False
            time.sleep(POLL_SECONDS)
        LOGGER.info("Timed out waiting for Start Game button")
        return False

    def wait_for_start_game_button_to_disappear(self, timeout):
        """Wait for Start Game to disappear after clicking it."""
        deadline = time.time() + timeout
        while time.time() <= deadline:
            if self.stop_requested:
                return False
            if not activate_diablo():
                time.sleep(0.5)
                continue
            if self.is_in_game_by_current_location():
                LOGGER.info("Current location visible after Start Game click")
                return True
            park_cursor_away_from_menu_buttons()
            if not self.start_game_button_visible():
                return True
            time.sleep(0.25)
        return False

    def game_loaded_location_title_visible(self):
        """Detect the post-load location title as a signal that the game has loaded."""
        with mss.mss() as sct:
            screenshot = np.array(sct.grab(self.game_loaded_location_title_region))
        bgr = cv2.cvtColor(screenshot, cv2.COLOR_BGRA2BGR)
        lower = np.array([185, 185, 185], dtype=np.uint8)
        mask = cv2.inRange(bgr, lower, np.array([255, 255, 255], dtype=np.uint8))
        return cv2.countNonZero(mask) > 8

    def open_map_and_wait(self):
        """Open the world map and wait until map templates confirm readiness."""
        if self.stop_requested or not activate_diablo():
            return False
        if self.wait_for_map_ready(0.1):
            return True
        deadline = time.time() + 8
        while time.time() <= deadline:
            if self.stop_requested or not activate_diablo():
                return False
            LOGGER.debug("Opening map with M")
            press_vk(VK_M)
            if self.safe_sleep(0.35) is False:
                return False
            if self.wait_for_map_ready(1.5):
                LOGGER.info("Map opened")
                return True
        LOGGER.info("Map did not open after retries")
        return False

    def wait_for_game_start_and_open_map(self):
        """After Start Game, wait for load signals and open the map."""
        LOGGER.info("Waiting for game load after Start Game")
        if self.safe_sleep(GAME_START_MAP_OPEN_DELAY) is False:
            return False
        deadline = time.time() + GAME_START_MAP_OPEN_TIMEOUT
        last_start_retry = 0
        while time.time() <= deadline:
            if self.stop_requested:
                LOGGER.info("Game load wait stopped")
                return False
            if not activate_diablo():
                time.sleep(0.5)
                continue
            if self.start_game_button_visible() and time.time() - last_start_retry >= 3:
                LOGGER.info("Start Game button still visible during load wait; clicking again")
                last_start_retry = time.time()
                if not self.wait_for_start_game_button_and_click():
                    return False
            if self.game_loaded_location_title_visible():
                LOGGER.info("Game loaded location title visible; opening map")
                return self.open_map_and_wait()
            if self.is_in_game_by_current_location():
                LOGGER.info("Current location visible after Start Game; opening map")
                return self.open_map_and_wait()
            time.sleep(0.5)
        LOGGER.info("Timed out waiting for game load after Start Game")
        return False

    def teleport_to_location(
        self,
        display_name,
        verify_arrival=False,
        map_already_open=False,
        record_progress=True,
        bypass_failsafe=False,
    ):
        """Use the world map to select an act/location and optionally verify arrival."""
        if self.stop_requested:
            return False
        target_key = location_key(display_name)
        if target_key not in self.location_coords:
            return False
        target = self.location_coords[target_key]
        if not activate_diablo():
            return False
        if not bypass_failsafe and not self.teleport_failsafe_allows():
            LOGGER.info("Teleport to %s blocked by failsafe", display_name)
            return False
        if not map_already_open:
            # Normal teleports open the map themselves; Make New Game can pass
            # map_already_open=True because it just opened the map after loading.
            LOGGER.info("Opening map for teleport to %s", display_name)
            press_vk(VK_M)
            if self.safe_sleep(0.2) is False:
                return False
            if not self.wait_for_map_ready(5):
                LOGGER.info("Teleport to %s failed: map did not become ready", display_name)
                return False

        current_act = self.detect_map_act_header()
        if current_act != target["act"]:
            act = self.act_coords.get(target["act"])
            if not act:
                LOGGER.info("Teleport to %s failed: missing world map coordinate for %s", display_name, target["act"])
                return False
            LOGGER.info("Teleport to %s: switching from %s to %s", display_name, current_act or "unknown act", target["act"])
            click(self.map_right_click_point["x"], self.map_right_click_point["y"], "right")
            if self.safe_sleep(0.2) is False:
                return False
            if not self.wait_for_world_map_ready(5):
                LOGGER.info("Teleport to %s failed: world map did not become ready", display_name)
                return False
            click(act["x"], act["y"])
            if self.safe_sleep(0.2) is False:
                return False
            if not self.wait_for_map_act_header(target["act"], 2.5):
                LOGGER.info("Teleport to %s failed: act header did not change to %s", display_name, target["act"])
                return False

        LOGGER.info("Teleport to %s: clicking destination at %s,%s", display_name, target["x"], target["y"])
        click(target["x"], target["y"])
        if verify_arrival:
            arrived = self.wait_for_current_location(display_name, TELEPORT_ARRIVAL_TIMEOUT)
            if arrived and record_progress:
                self.record_last_teleport(display_name)
            return arrived
        if record_progress:
            self.record_last_teleport(display_name)
        return True

    def teleport_template_visible(self, template_name, region, threshold=TELEPORT_THRESHOLD):
        """Check a teleport/map template by name against a region."""
        path = self.matcher.template_path(TELEPORT_TEMPLATE_DIR, template_name)
        return self.matcher.visible(path, region, threshold)

    def teleport_template_match(self, template_name, region):
        """Return a teleport/map template match by name."""
        path = self.matcher.template_path(TELEPORT_TEMPLATE_DIR, template_name)
        return self.matcher.find(path, region)

    def wait_for_map_ready(self, timeout):
        """Wait for both world map title and act header to be ready."""
        deadline = time.time() + timeout
        while time.time() <= deadline:
            if self.stop_requested:
                return False
            if self.detect_map_act_header():
                return True
            time.sleep(POLL_SECONDS)
        return False

    def wait_for_world_map_ready(self, timeout):
        """Wait for the World Map title template."""
        deadline = time.time() + timeout
        while time.time() <= deadline:
            if self.stop_requested:
                return False
            if self.teleport_template_visible("World Map", self.world_map_title_region, WORLD_MAP_THRESHOLD):
                return True
            time.sleep(POLL_SECONDS)
        return False

    def wait_for_map_act_header(self, act_name, timeout):
        """Wait for a specific act header template on the world map."""
        deadline = time.time() + timeout
        while time.time() <= deadline:
            if self.stop_requested:
                return False
            if self.detect_map_act_header() == act_name:
                return True
            time.sleep(POLL_SECONDS)
        return False

    def detect_map_act_header(self):
        """Identify which act header is currently selected on the map."""
        template_by_act = {
            "Act 1": "Act 1 Map Header",
            "Act 2": "Act 2",
            "Act 3": "Act 3",
            "Act 4": "Act 4",
            "Act 5": "Act 5",
        }
        best_act = ""
        best_confidence = 0.0
        for act_name, template in template_by_act.items():
            match = self.teleport_template_match(template, self.map_scan_region)
            if match and match["confidence"] > best_confidence:
                best_act = act_name
                best_confidence = match["confidence"]

        return best_act if best_confidence >= ACT_HEADER_THRESHOLD else ""

    def run(self):
        """Enter the Tk main loop."""
        self.root.mainloop()


def main():
    """Claim singleton ownership, minimize console, build the app, and start the UI loop."""
    if not claim_single_instance():
        return 0
    minimize_console_window()
    try:
        GoblinFarmingApp().run()
    except Exception:
        LOGGER.exception("GoblinFarming crashed")
        raise
    finally:
        try:
            if APP_INSTANCE_LOCK.read_text(encoding="utf-8", errors="ignore").strip() == str(os.getpid()):
                APP_INSTANCE_LOCK.unlink(missing_ok=True)
        except OSError:
            pass
        cleanup_logging()


if __name__ == "__main__":
    main()
