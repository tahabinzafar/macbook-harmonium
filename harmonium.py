"""
HARMONIUM — MacBook Lid-Angle Bellows Controller

Keys A-L play notes (Sa Re Ga Ma Pa Dha Ni Sa')
Z/X for flats. Up/Down arrows shift octave.
Mouse Y or MacBook lid tilt controls bellows (volume).

NOTE on lid sensor: macOS may prompt for Motion & Fitness
permission. If sensor isn't detected, mouse Y controls bellows.
"""

import sys
import math
import threading
import time
import os

# Suppress pygame welcome message
os.environ["PYGAME_HIDE_SUPPORT_PROMPT"] = "1"

try:
    import numpy as np
    import sounddevice as sd
except ImportError:
    print("\n  Missing audio deps. Run:\n")
    print("    pip3 install sounddevice numpy\n")
    sys.exit(1)

try:
    import pygame
except ImportError:
    print("\n  Missing display dep. Run:\n")
    print("    pip3 install pygame\n")
    sys.exit(1)

# Audio Config
SAMPLE_RATE = 44100
BLOCK_SIZE = 512

# Note Definitions
NOTES = [
    ("a", "Sa",    261.63, "white"),
    ("s", "Re",    293.66, "white"),
    ("d", "Ga",    329.63, "white"),
    ("f", "Ma",    349.23, "white"),
    ("g", "Pa",    392.00, "white"),
    ("h", "Dha",   440.00, "white"),
    ("j", "Ni",    493.88, "white"),
    ("k", "Sa'",   523.25, "white"),
    ("l", "Re'",   587.33, "white"),
    ("z", "Ma♭",   277.18, "black"),
    ("x", "Dha♭",  415.30, "black"),
]

KEY_TO_NOTE = {n[0]: n for n in NOTES}

# Pygame key mapping
PG_KEY_MAP = {
    pygame.K_a: "a", pygame.K_s: "s", pygame.K_d: "d",
    pygame.K_f: "f", pygame.K_g: "g", pygame.K_h: "h",
    pygame.K_j: "j", pygame.K_k: "k", pygame.K_l: "l",
    pygame.K_z: "z", pygame.K_x: "x",
}


# macOS Lid Angle Sensor via pybooklid
class LidSensor:
    """
    Reads MacBook lid angle via the HID lid angle sensor.
    Uses pybooklid (pip3 install pybooklid) which talks directly
    to the IOKit HID device — no entitlements or signing needed.

    Lid angle maps to bellows: movement = air pressure.
    Faster lid movement = louder sound, like a real harmonium.
    """

    def __init__(self):
        self.available = False
        self.bellows = 0.7
        self._lock = threading.Lock()
        self.sensor = None
        self.prev_angle = None
        self.air_pressure = 0.0

        try:
            from pybooklid import LidSensor as PBLidSensor

            self.sensor = PBLidSensor()
            angle = self.sensor.read_angle()
            if angle is not None:
                self.prev_angle = angle
                self.available = True
                print(f"[Sensor] Lid angle sensor active! Current angle: {angle:.1f}°")
                print("[Sensor] Move your lid to pump the bellows!")
            else:
                print("[Sensor] pybooklid installed but sensor returned no data")
                print("[Sensor] Your MacBook may not have a lid angle sensor (need 2019+)")
                print("[Sensor] Using mouse Y for bellows control")
                self.sensor = None

        except ImportError:
            print("[Sensor] pybooklid not installed")
            print("[Sensor]   pip3 install pybooklid")
            print("[Sensor] Using mouse Y for bellows control")
        except Exception as e:
            print(f"[Sensor] Init error: {e}")
            print("[Sensor] Using mouse Y for bellows control")

    def update_bellows(self):
        """
        Read lid angle and simulate bellows air pressure.
        
        The bellows model works like a real harmonium:
        - Moving the lid (any direction) pumps air into a reservoir
        - Air pressure naturally decays over time
        - More movement = more pressure = louder sound
        """
        if not self.available or not self.sensor:
            return self.bellows

        try:
            angle = self.sensor.read_angle()
            if angle is None:
                return self.bellows

            if self.prev_angle is not None:
                # Lid velocity = air being pumped in
                delta = abs(angle - self.prev_angle)

                # Pump air proportional to movement speed
                # delta of ~2-3 degrees per poll (~30Hz) = moderate pumping
                pump = delta * 0.15

                # Add to reservoir, decay naturally
                self.air_pressure += pump
                self.air_pressure *= 0.92  # natural air leak

                # Clamp
                self.air_pressure = max(0.0, min(1.0, self.air_pressure))

            self.prev_angle = angle

            # Map air pressure to bellows (keep a floor so it doesn't go silent)
            bellows_val = 0.03 + self.air_pressure * 0.97

            with self._lock:
                self.bellows = bellows_val

        except Exception:
            pass

        return self.bellows

    def get_bellows(self):
        with self._lock:
            return self.bellows

    def set_bellows(self, val):
        with self._lock:
            self.bellows = max(0.05, min(1.0, val))

    def recalibrate(self):
        self.prev_angle = None
        self.air_pressure = 0.0
        print("[Sensor] Recalibrated")

    def cleanup(self):
        if self.sensor:
            try:
                self.sensor.disconnect()
            except Exception:
                pass


# Harmonium Synth
class HarmoniumSynth:
    def __init__(self, sensor: LidSensor):
        self.sensor = sensor
        self.active_notes = {}
        self._lock = threading.Lock()
        self.octave_shift = 0
        self.stream = None

        self.harmonics = [
            (1.0,   0.35, "saw"),
            (2.0,   0.15, "square"),
            (3.0,   0.06, "saw"),
            (0.5,   0.18, "sine"),
        ]

        self.lfo_freq = 5.5
        self.lfo_depth = 0.07
        self.lfo_phase = 0.0
        self.env_attack = 0.04
        self.env_release = 0.10

    def start(self):
        self.stream = sd.OutputStream(
            samplerate=SAMPLE_RATE,
            blocksize=BLOCK_SIZE,
            channels=1,
            dtype="float32",
            callback=self._audio_callback,
        )
        self.stream.start()

    def stop(self):
        if self.stream:
            self.stream.stop()
            self.stream.close()

    def note_on(self, key):
        note = KEY_TO_NOTE.get(key)
        if not note:
            return
        freq = note[2] * (2 ** self.octave_shift)
        with self._lock:
            if key not in self.active_notes:
                self.active_notes[key] = {
                    "freq": freq,
                    "phases": [0.0] * len(self.harmonics),
                    "env": 0.0,
                    "state": "attack",
                }

    def note_off(self, key):
        with self._lock:
            if key in self.active_notes:
                self.active_notes[key]["state"] = "release"

    def set_octave(self, shift):
        self.octave_shift = max(-2, min(2, shift))

    def _generate_sample(self, note_data, dt):
        freq = note_data["freq"]
        sample = 0.0

        for i, (rel_freq, amp, wave) in enumerate(self.harmonics):
            f = freq * rel_freq
            phase = note_data["phases"][i]

            if wave == "saw":
                val = 2.0 * (phase * f - math.floor(phase * f + 0.5))
            elif wave == "square":
                val = 1.0 if math.sin(2 * math.pi * phase * f) > 0 else -1.0
            else:
                val = math.sin(2 * math.pi * phase * f)

            sample += val * amp
            note_data["phases"][i] = phase + dt
            if note_data["phases"][i] > 1.0:
                note_data["phases"][i] -= 1.0

        return sample

    def _audio_callback(self, outdata, frames, time_info, status):
        bellows = self.sensor.get_bellows()
        dt = 1.0 / SAMPLE_RATE
        output = np.zeros(frames, dtype=np.float32)

        with self._lock:
            dead_keys = []

            for key, note in self.active_notes.items():
                for i in range(frames):
                    if note["state"] == "attack":
                        note["env"] += dt / self.env_attack
                        if note["env"] >= 1.0:
                            note["env"] = 1.0
                            note["state"] = "sustain"
                    elif note["state"] == "release":
                        note["env"] -= dt / self.env_release
                        if note["env"] <= 0.0:
                            note["env"] = 0.0
                            dead_keys.append(key)
                            break

                    self.lfo_phase += dt
                    lfo_val = 1.0 + self.lfo_depth * math.sin(
                        2 * math.pi * self.lfo_freq * self.lfo_phase
                    )
                    sample = self._generate_sample(note, dt)
                    output[i] += sample * note["env"] * bellows * lfo_val * 0.4

            for k in dead_keys:
                del self.active_notes[k]

        output = np.tanh(output)
        outdata[:, 0] = output


# Colors
class C:
    BG          = (26, 15, 7)
    BG_LIGHTER  = (45, 26, 14)
    GOLD        = (193, 154, 91)
    GOLD_DIM    = (139, 115, 85)
    TEXT_DIM    = (90, 74, 53)
    WHITE_KEY   = (245, 230, 208)
    WHITE_PRESS = (190, 170, 140)
    BLACK_KEY   = (45, 26, 14)
    BLACK_PRESS = (90, 58, 31)
    KEY_TEXT    = (139, 90, 43)
    KEY_HINT   = (184, 160, 128)
    GREEN       = (106, 191, 105)
    BAR_BG      = (35, 20, 10)


# Pygame GUI
class HarmoniumApp:
    def __init__(self):
        self.sensor = LidSensor()
        self.synth = HarmoniumSynth(self.sensor)
        self.pressed_keys = set()
        self.running = True

        if self.sensor.available:
            t = threading.Thread(target=self._poll_sensor, daemon=True)
            t.start()

        pygame.init()
        info = pygame.display.Info()
        self.W = 920
        self.H = 540
        self.screen = pygame.display.set_mode((self.W, self.H), pygame.RESIZABLE)
        pygame.display.set_caption("Harmonium")
        self.clock = pygame.time.Clock()

        # Fonts
        try:
            self.f_title = pygame.font.SysFont("Georgia", 22, bold=True)
            self.f_key   = pygame.font.SysFont("Georgia", 15, bold=True)
            self.f_note  = pygame.font.SysFont("Georgia", 13)
            self.f_small = pygame.font.SysFont("Menlo", 10)
        except Exception:
            self.f_title = pygame.font.Font(None, 28)
            self.f_key   = pygame.font.Font(None, 20)
            self.f_note  = pygame.font.Font(None, 16)
            self.f_small = pygame.font.Font(None, 14)

        pygame.key.set_repeat(0)
        self.synth.start()
        self._loop()

    def _poll_sensor(self):
        while self.running:
            self.sensor.update_bellows()
            time.sleep(0.03)

    def _loop(self):
        while self.running:
            for ev in pygame.event.get():
                if ev.type == pygame.QUIT:
                    self.running = False
                elif ev.type == pygame.KEYDOWN:
                    self._on_key_down(ev)
                elif ev.type == pygame.KEYUP:
                    self._on_key_up(ev)
                elif ev.type == pygame.VIDEORESIZE:
                    self.W, self.H = ev.w, ev.h

            # Mouse Y = bellows when no sensor
            if not self.sensor.available:
                _, my = pygame.mouse.get_pos()
                self.sensor.set_bellows(1.0 - my / max(self.H, 1))

            self._draw()
            self.clock.tick(60)

        self.sensor.cleanup()
        self.synth.stop()
        pygame.quit()

    def _on_key_down(self, ev):
        if ev.key == pygame.K_UP:
            self.synth.set_octave(self.synth.octave_shift + 1)
            return
        if ev.key == pygame.K_DOWN:
            self.synth.set_octave(self.synth.octave_shift - 1)
            return
        if ev.key == pygame.K_SPACE:
            self.sensor.recalibrate()
            return

        key = PG_KEY_MAP.get(ev.key)
        if key and key not in self.pressed_keys:
            self.pressed_keys.add(key)
            self.synth.note_on(key)

    def _on_key_up(self, ev):
        key = PG_KEY_MAP.get(ev.key)
        if key and key in self.pressed_keys:
            self.pressed_keys.discard(key)
            self.synth.note_off(key)

    def _draw(self):
        W, H = self.W, self.H
        self.screen.fill(C.BG)

        # Header
        self.screen.blit(self.f_title.render("HARMONIUM", True, C.GOLD), (24, 14))

        dot_col = C.GREEN if self.sensor.available else C.KEY_TEXT
        status = "LID SENSOR" if self.sensor.available else "MOUSE Y = BELLOWS"
        s_surf = self.f_small.render(status, True, C.GOLD_DIM)
        sx = W - s_surf.get_width() - 24
        pygame.draw.circle(self.screen, dot_col, (sx - 12, 26), 5)
        self.screen.blit(s_surf, (sx, 20))

        # Bellows bar
        by = 50
        bx = 90
        bw = W - bx - 60
        bh = 8
        self.screen.blit(self.f_small.render("BELLOWS", True, C.GOLD_DIM), (24, by - 1))
        pygame.draw.rect(self.screen, C.BAR_BG, (bx, by, bw, bh))
        level = self.sensor.get_bellows()
        fw = int(bw * level)
        if fw > 0:
            pygame.draw.rect(self.screen, C.GOLD, (bx, by, fw, bh))
        self.screen.blit(
            self.f_small.render(f"{int(level * 100)}%", True, C.GOLD),
            (bx + bw + 8, by - 1)
        )

        # Octave + controls
        sh = self.synth.octave_shift
        sign = f"+{sh}" if sh > 0 else str(sh)
        oct = f"OCTAVE {sign}  ·  ↑↓ SHIFT  ·  SPACE RECALIBRATE"
        o_surf = self.f_small.render(oct, True, C.GOLD_DIM)
        self.screen.blit(o_surf, ((W - o_surf.get_width()) // 2, 70))

        # Keys area
        kt = 95       # top
        kb = H - 45   # bottom
        kl = 24       # left
        kr = W - 24   # right
        kh = kb - kt
        kw = kr - kl

        if kh < 30 or kw < 100:
            pygame.display.flip()
            return

        # Border
        pygame.draw.rect(self.screen, C.BG_LIGHTER, (kl - 3, kt - 3, kw + 6, kh + 6), border_radius=4)

        # White keys
        whites = [n for n in NOTES if n[3] == "white"]
        blacks = [n for n in NOTES if n[3] == "black"]
        nw = len(whites)
        gap = 3
        ww = (kw - gap * (nw + 1)) / nw

        wrects = {}
        for i, (kc, name, freq, _) in enumerate(whites):
            x = kl + gap + i * (ww + gap)
            pressed = kc in self.pressed_keys
            fill = C.WHITE_PRESS if pressed else C.WHITE_KEY
            r = pygame.Rect(x, kt, ww, kh)
            pygame.draw.rect(self.screen, fill, r, border_radius=3)
            pygame.draw.rect(self.screen, (200, 185, 155), r, 1, border_radius=3)
            wrects[i] = x

            # Labels
            ns = self.f_key.render(name, True, C.KEY_TEXT)
            self.screen.blit(ns, (x + (ww - ns.get_width()) // 2, kt + kh - ns.get_height() - 10))
            hs = self.f_small.render(kc.upper(), True, C.KEY_HINT)
            self.screen.blit(hs, (x + (ww - hs.get_width()) // 2, kt + kh - ns.get_height() - hs.get_height() - 14))

        # Black keys
        bpos = {"Ma♭": (0, 1), "Dha♭": (4, 5)}
        bkw = ww * 0.6
        bkh = kh * 0.5

        for kc, name, freq, _ in blacks:
            pos = bpos.get(name)
            if not pos or pos[0] not in wrects or pos[1] not in wrects:
                continue

            cx = wrects[pos[0]] + ww + gap / 2
            x = cx - bkw / 2

            pressed = kc in self.pressed_keys
            fill = C.BLACK_PRESS if pressed else C.BLACK_KEY
            r = pygame.Rect(x, kt, bkw, bkh)
            pygame.draw.rect(self.screen, fill, r, border_radius=3)
            pygame.draw.rect(self.screen, (30, 18, 8), r, 1, border_radius=3)

            ns = self.f_note.render(name, True, C.GOLD_DIM)
            self.screen.blit(ns, (x + (bkw - ns.get_width()) // 2, kt + bkh - ns.get_height() - 22))
            hs = self.f_small.render(kc.upper(), True, C.GOLD_DIM)
            self.screen.blit(hs, (x + (bkw - hs.get_width()) // 2, kt + bkh - hs.get_height() - 6))

        # Footer
        info = "A S D F G H J K L = Sa Re Ga Ma Pa Dha Ni Sa' Re'  ·  Z X = flats  ·  ↑↓ = octave"
        self.screen.blit(self.f_small.render(info, True, C.TEXT_DIM), ((W - self.f_small.size(info)[0]) // 2, H - 30))

        pygame.display.flip()


if __name__ == "__main__":
    print("\n Harmonium starting...\n")
    app = HarmoniumApp()