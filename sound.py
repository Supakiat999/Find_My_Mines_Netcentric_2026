"""Sound effects, synthesised from maths so there are no audio files to ship.

Every effect is a short burst of sine tones or filtered noise built as raw
16-bit samples.  If the machine has no audio device, or the mixer will not
start, the bank quietly reports itself unavailable and play() does nothing -
the game never fails because it cannot make noise.
"""

import array
import math
import random

import pygame

RATE = 22050


def _envelope(i, n, attack=0.006, decay_power=1.6):
    """Quick fade-in to avoid a click, then a smooth fade-out."""
    t = i / float(n)
    fade_in = min(1.0, (i / float(RATE)) / attack)
    return fade_in * (1.0 - t) ** decay_power


def tone(freq, ms, volume=0.5, rate=RATE, slide=0.0):
    """A decaying sine.  `slide` bends the pitch by that many Hz over its life."""
    n = max(1, int(rate * ms / 1000.0))
    out = array.array("h")
    phase = 0.0
    for i in range(n):
        f = freq + slide * (i / float(n))
        phase += 2 * math.pi * f / rate
        out.append(int(32767 * volume * _envelope(i, n) * math.sin(phase)))
    return out


def noise(ms, volume=0.5, rate=RATE, smooth=0.86, seed=7):
    """Low-passed noise: a dull thump rather than a hiss."""
    rng = random.Random(seed)
    n = max(1, int(rate * ms / 1000.0))
    out = array.array("h")
    level = 0.0
    for i in range(n):
        level = smooth * level + (1 - smooth) * rng.uniform(-1, 1)
        out.append(int(32767 * volume * 3.2 * _envelope(i, n, decay_power=2.2) * level))
    return out


def silence(ms, rate=RATE):
    return array.array("h", [0] * max(1, int(rate * ms / 1000.0)))


def join(*parts):
    out = array.array("h")
    for part in parts:
        out.extend(part)
    return out


def mix(a, b):
    """Overlay two clips (the result is as long as the longer one)."""
    n = max(len(a), len(b))
    out = array.array("h")
    for i in range(n):
        va = a[i] if i < len(a) else 0
        vb = b[i] if i < len(b) else 0
        out.append(max(-32767, min(32767, va + vb)))
    return out


def build_effects(rate=RATE):
    """name -> samples.  Pure functions of the rate, so easy to test."""
    t = lambda f, ms, v=0.5, s=0.0: tone(f, ms, v, rate, s)
    return {
        # a small wooden tick when a slot opens
        "click": t(880, 45, 0.35, -300),
        # found a bomb and that is good: a bright two-note chime
        "bomb": join(t(660, 90, 0.5), t(990, 170, 0.5)),
        # hit a bomb and that is bad: a low thump
        "boom": mix(noise(260, 0.55, rate), t(90, 260, 0.6, -40)),
        # your turn
        "turn": join(t(784, 70, 0.4), t(1046, 130, 0.4)),
        # last seconds of the clock
        "tick": t(1300, 40, 0.3),
        # somebody spoke
        "chat": t(1175, 60, 0.25),
        # coach answered
        "hint": join(t(523, 70, 0.35), t(659, 70, 0.35), t(784, 110, 0.35)),
        # refused
        "error": t(200, 130, 0.4, -60),
        "win": join(t(523, 110, 0.5), t(659, 110, 0.5), t(784, 110, 0.5),
                    t(1046, 320, 0.55)),
        "lose": join(t(392, 160, 0.5), t(330, 160, 0.5), t(262, 380, 0.5, -30)),
        "draw": join(t(440, 150, 0.45), t(440, 220, 0.45)),
    }


class SoundBank:
    def __init__(self, muted=False):
        self.available = False
        self.muted = muted
        self.sounds = {}
        self.played = []            # recent names, so tests can see what fired
        try:
            if not pygame.mixer.get_init():
                pygame.mixer.init(frequency=RATE, size=-16, channels=1,
                                  buffer=512)
            rate, size, channels = pygame.mixer.get_init()
            if size != -16:
                return
            for name, samples in build_effects(rate).items():
                if channels == 2:                    # duplicate for both ears
                    stereo = array.array("h")
                    for value in samples:
                        stereo.append(value)
                        stereo.append(value)
                    samples = stereo
                self.sounds[name] = pygame.mixer.Sound(buffer=samples.tobytes())
                self.sounds[name].set_volume(0.55)
            self.available = True
        except Exception:
            self.available = False
            self.sounds = {}

    def play(self, name):
        """Play an effect.  Silent when muted or when there is no audio."""
        if self.muted:
            return False
        self.played.append(name)
        del self.played[:-20]
        if not self.available or name not in self.sounds:
            return False
        try:
            self.sounds[name].play()
            return True
        except Exception:
            return False

    def toggle(self):
        self.muted = not self.muted
        return self.muted
