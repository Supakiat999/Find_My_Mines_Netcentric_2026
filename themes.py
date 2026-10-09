"""Colour themes for the game window.

Seven palettes: the original dark one, a light one for bright rooms, a
colour-blind-safe one built on the Okabe-Ito set (whose hues stay distinct
under the common forms of colour blindness), and four mood themes - Ocean,
Neon, Sunset and a High-contrast one for low-vision play.  Every text pairing
is held to the WCAG contrast guidance - tests/check_features.py measures it.

Besides the original keys, each palette now carries three optional bomb keys
the client reads when it draws a bomb:

    bomb_body  the sphere itself
    bomb_glow  the pulse of light around a revealed bomb and the blast colour
    spark      the lit end of the fuse

The client falls back to sensible defaults if a key is missing, so an older
palette still works.  Depth shading (tile edges, shadows, highlights) is
derived from the palette's own colours in client.py, so a new theme only has
to supply the base colours.
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
    "bomb_body": (20, 14, 18), "bomb_glow": (248, 90, 80),
    "spark": (253, 224, 71),
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
    "bomb_body": (30, 27, 38), "bomb_glow": (220, 38, 38),
    "spark": (234, 138, 0),
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
    "bomb_body": (18, 16, 14), "bomb_glow": (255, 140, 60),
    "spark": (240, 228, 66),
    "digits": {0: (120, 134, 156), 1: (86, 180, 233), 2: (240, 228, 66),
               3: (230, 159, 0), 4: (204, 121, 167), 5: (0, 190, 140),
               6: (255, 140, 60), 7: (236, 240, 246), 8: (160, 174, 194)},
}

OCEAN = {
    "label": "Ocean",
    "bg": (8, 24, 38), "panel": (13, 37, 56), "panel2": (19, 49, 72),
    "line": (42, 82, 110),
    "text": (224, 242, 250), "muted": (140, 178, 200),
    "accent": (56, 189, 248), "good": (45, 212, 191),
    "warn": (251, 191, 36), "bad": (251, 113, 133),
    "covered": (35, 78, 107), "covered_hover": (52, 104, 140),
    "opened": (14, 32, 48), "bomb_cell": (110, 32, 48),
    "btn": (3, 105, 161), "btn_hot": (2, 118, 180), "btn_text": (255, 255, 255),
    "veil": (4, 14, 24), "veil_alpha": 232,
    "p1": (56, 189, 248), "p2": (251, 146, 100),
    "bomb_body": (10, 14, 24), "bomb_glow": (251, 90, 110),
    "spark": (253, 224, 71),
    "digits": {0: (110, 150, 175), 1: (96, 190, 255), 2: (74, 222, 170),
               3: (251, 113, 133), 4: (196, 160, 255), 5: (251, 170, 80),
               6: (45, 212, 191), 7: (224, 242, 250), 8: (150, 180, 200)},
}

NEON = {
    "label": "Neon",
    "bg": (10, 8, 22), "panel": (19, 15, 40), "panel2": (28, 22, 58),
    "line": (74, 52, 130),
    "text": (240, 236, 255), "muted": (166, 154, 205),
    "accent": (0, 229, 255), "good": (57, 255, 150),
    "warn": (255, 234, 0), "bad": (255, 90, 140),
    "covered": (44, 34, 86), "covered_hover": (66, 52, 122),
    "opened": (16, 12, 34), "bomb_cell": (96, 10, 56),
    "btn": (126, 34, 206), "btn_hot": (147, 51, 234), "btn_text": (255, 255, 255),
    "veil": (6, 4, 14), "veil_alpha": 234,
    "p1": (0, 229, 255), "p2": (255, 90, 214),
    "bomb_body": (8, 6, 16), "bomb_glow": (255, 40, 110),
    "spark": (255, 234, 0),
    "digits": {0: (140, 128, 190), 1: (80, 200, 255), 2: (57, 255, 150),
               3: (255, 90, 140), 4: (190, 150, 255), 5: (255, 170, 50),
               6: (0, 229, 255), 7: (240, 236, 255), 8: (170, 160, 210)},
}

SUNSET = {
    "label": "Sunset",
    "bg": (28, 16, 24), "panel": (42, 24, 34), "panel2": (56, 32, 44),
    "line": (104, 60, 72),
    "text": (255, 240, 230), "muted": (212, 172, 162),
    "accent": (251, 146, 60), "good": (163, 230, 53),
    "warn": (253, 224, 71), "bad": (251, 113, 113),
    "covered": (92, 52, 62), "covered_hover": (122, 70, 80),
    "opened": (36, 20, 30), "bomb_cell": (136, 28, 28),
    "btn": (180, 60, 10), "btn_hot": (194, 65, 12), "btn_text": (255, 255, 255),
    "veil": (16, 8, 14), "veil_alpha": 232,
    "p1": (253, 186, 116), "p2": (196, 160, 255),
    "bomb_body": (20, 10, 14), "bomb_glow": (251, 100, 60),
    "spark": (253, 224, 71),
    "digits": {0: (176, 144, 144), 1: (125, 211, 252), 2: (163, 230, 53),
               3: (248, 113, 113), 4: (216, 180, 254), 5: (251, 146, 60),
               6: (45, 212, 191), 7: (255, 240, 230), 8: (204, 174, 174)},
}

CONTRAST = {
    "label": "High contrast",
    "bg": (0, 0, 0), "panel": (10, 10, 12), "panel2": (22, 22, 26),
    "line": (200, 200, 210),
    "text": (255, 255, 255), "muted": (214, 214, 222),
    "accent": (255, 230, 0), "good": (0, 255, 120),
    "warn": (255, 200, 0), "bad": (255, 100, 100),
    "covered": (64, 64, 76), "covered_hover": (98, 98, 116),
    "opened": (8, 8, 10), "bomb_cell": (150, 0, 0),
    "btn": (0, 64, 210), "btn_hot": (0, 80, 230), "btn_text": (255, 255, 255),
    "veil": (0, 0, 0), "veil_alpha": 238,
    "p1": (0, 210, 255), "p2": (255, 160, 0),
    "bomb_body": (30, 30, 34), "bomb_glow": (255, 60, 60),
    "spark": (255, 255, 0),
    "digits": {0: (190, 190, 200), 1: (110, 180, 255), 2: (0, 255, 120),
               3: (255, 100, 100), 4: (210, 160, 255), 5: (255, 170, 40),
               6: (0, 240, 220), 7: (255, 255, 255), 8: (225, 225, 235)},
}

THEMES = {"dark": DARK, "light": LIGHT, "colorblind": COLORBLIND,
          "ocean": OCEAN, "neon": NEON, "sunset": SUNSET,
          "contrast": CONTRAST}
ORDER = ["dark", "light", "colorblind", "ocean", "neon", "sunset", "contrast"]


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
