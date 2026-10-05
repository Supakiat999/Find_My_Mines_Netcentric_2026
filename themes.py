"""Colour themes for the game window.

Three palettes: the original dark one, a light one for bright rooms, and a
colour-blind-safe one built on the Okabe-Ito set, whose hues stay distinct
under the common forms of colour blindness.  Every text pairing is held to
the WCAG contrast guidance - tests/check_features.py measures it.
"""

DARK = {
    "label": "Dark",
    "bg": (17, 22, 32), "panel": (26, 33, 46), "panel2": (32, 41, 57),
    "line": (49, 61, 82),
    "text": (226, 232, 240), "muted": (138, 152, 175),
    "accent": (96, 165, 250), "good": (52, 211, 153),
    "warn": (251, 191, 36), "bad": (248, 113, 113),
    "covered": (55, 68, 92), "covered_hover": (74, 91, 122),
    "opened": (36, 45, 62), "bomb_cell": (128, 36, 40),
    "btn": (29, 78, 216), "btn_hot": (37, 99, 235), "btn_text": (235, 244, 255),
    "veil": (10, 14, 20), "veil_alpha": 232,
    "p1": (96, 165, 250), "p2": (251, 146, 60),
    "digits": {0: (110, 124, 148), 1: (110, 168, 255), 2: (82, 209, 143),
               3: (248, 113, 113), 4: (167, 139, 250), 5: (251, 146, 60),
               6: (45, 212, 191), 7: (226, 232, 240), 8: (148, 163, 184)},
}

LIGHT = {
    "label": "Light",
    "bg": (243, 246, 250), "panel": (255, 255, 255), "panel2": (232, 238, 246),
    "line": (196, 207, 222),
    "text": (24, 32, 46), "muted": (84, 96, 116),
    "accent": (29, 78, 216), "good": (4, 120, 87),
    "warn": (146, 84, 0), "bad": (185, 28, 28),
    "covered": (190, 203, 224), "covered_hover": (170, 187, 214),
    "opened": (236, 242, 250), "bomb_cell": (250, 190, 190),
    "btn": (29, 78, 216), "btn_hot": (30, 64, 175), "btn_text": (255, 255, 255),
    "veil": (232, 238, 246), "veil_alpha": 236,
    "p1": (29, 78, 216), "p2": (180, 60, 8),
    "digits": {0: (100, 112, 132), 1: (29, 78, 216), 2: (4, 120, 87),
               3: (185, 28, 28), 4: (109, 40, 217), 5: (180, 60, 8),
               6: (15, 118, 110), 7: (24, 32, 46), 8: (71, 85, 105)},
}

COLORBLIND = {
    "label": "Colour-blind",
    "bg": (15, 20, 28), "panel": (24, 31, 42), "panel2": (31, 40, 54),
    "line": (58, 72, 94),
    "text": (236, 240, 246), "muted": (150, 164, 184),
    "accent": (86, 180, 233), "good": (86, 180, 233),
    "warn": (240, 228, 66), "bad": (255, 140, 60),
    "covered": (55, 68, 92), "covered_hover": (78, 96, 128),
    "opened": (34, 43, 58), "bomb_cell": (120, 45, 0),
    "btn": (0, 114, 178), "btn_hot": (0, 98, 152), "btn_text": (255, 255, 255),
    "veil": (8, 12, 18), "veil_alpha": 232,
    "p1": (86, 180, 233), "p2": (230, 159, 0),
    "digits": {0: (120, 134, 156), 1: (86, 180, 233), 2: (240, 228, 66),
               3: (230, 159, 0), 4: (204, 121, 167), 5: (0, 190, 140),
               6: (255, 140, 60), 7: (236, 240, 246), 8: (160, 174, 194)},
}

THEMES = {"dark": DARK, "light": LIGHT, "colorblind": COLORBLIND}
ORDER = ["dark", "light", "colorblind"]


def next_theme(name):
    """The theme after this one, wrapping round."""
    if name not in ORDER:
        return ORDER[0]
    return ORDER[(ORDER.index(name) + 1) % len(ORDER)]


def _linear(channel):
    c = channel / 255.0
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def luminance(rgb):
    r, g, b = rgb
    return 0.2126 * _linear(r) + 0.7152 * _linear(g) + 0.0722 * _linear(b)


def contrast(a, b):
    """WCAG contrast ratio between two colours: 1 (none) to 21 (black/white)."""
    la, lb = luminance(a), luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)
