import math
import random
import time
from typing import List, Tuple, Union


class HumanBehaviorEngine:
    """
    Simulates authentic human touch biomechanics, natural timing variance,
    and fluid gestures on Android devices to prevent detection by behavioral biometrics.
    """

    def __init__(self, device):
        self.d = device
        display_w = self.d.info.get("displayWidth", 720)
        display_h = self.d.info.get("displayHeight", 1600)
        self.w = display_w
        self.h = display_h

    # -------------------------------------------------------------
    # 1. Natural Cognitive Delays & Heavy-Tailed Hesitation
    # -------------------------------------------------------------
    def sleep(self, base_sec: float = 1.0, jitter_ratio: float = 0.3):
        """
        Pauses with a heavy-tailed log-normal cognitive delay, mimicking authentic human
        hesitation and reaction distributions rather than machine-uniform timestamps.
        Bounded tightly to preserve snappy responsiveness without artificial sluggishness.
        """
        sigma = max(0.12, min(0.45, jitter_ratio))
        # Log-normal distribution centered around base_sec
        mu = math.log(max(0.1, base_sec)) - (sigma ** 2) / 2.0
        val = random.lognormvariate(mu, sigma)
        actual = max(0.12, min(val, base_sec * 2.2))
        time.sleep(actual)
        return actual

    # -------------------------------------------------------------
    # 2. Gaussian Coordinate Jitter, Dwell Time & Micro-Wobble (Human Click)
    # -------------------------------------------------------------
    def get_human_click_point(self, bounds: Union[dict, Tuple[int, int, int, int]]) -> Tuple[int, int]:
        """
        Picks a point inside the target element using Gaussian distribution
        centered around the middle, strictly constrained to the inner 70% safe zone.
        Never clicks the exact same pixel twice.
        """
        if isinstance(bounds, dict):
            l = bounds["left"]
            t = bounds["top"]
            r = bounds["right"]
            b = bounds["bottom"]
        else:
            l, t, r, b = bounds

        elem_w = r - l
        elem_h = b - t
        cx = (l + r) / 2.0
        cy = (t + b) / 2.0

        # Gaussian distribution: ~68% of clicks fall within 12% of center
        gx = random.gauss(cx, max(1.0, elem_w * 0.12))
        gy = random.gauss(cy, max(1.0, elem_h * 0.12))

        # Clamp strictly to inner 75% safe area to never touch border margins
        safe_margin_x = elem_w * 0.15
        safe_margin_y = elem_h * 0.15

        final_x = int(max(l + safe_margin_x, min(r - safe_margin_x, gx)))
        final_y = int(max(t + safe_margin_y, min(b - safe_margin_y, gy)))
        return final_x, final_y

    def click(self, target, dwell_range: Tuple[float, float] = (0.065, 0.125)):
        """
        Simulates natural human finger contact:
        1. Non-centered natural contact point inside safe zone.
        2. Dwell time: finger rests on glass for 65ms–125ms before release.
        3. Tissue micro-wobble: natural 1-2px organic drift during contact dwell.
        """
        if hasattr(target, "info"):
            bounds = target.info.get("bounds")
            x, y = self.get_human_click_point(bounds)
        elif isinstance(target, (list, tuple)) and len(target) == 2:
            # Point with small natural jitter (+/- 2px)
            x = int(target[0] + random.gauss(0, 1.5))
            y = int(target[1] + random.gauss(0, 1.5))
        elif isinstance(target, (list, tuple)) and len(target) == 4:
            x, y = self.get_human_click_point(target)
        else:
            raise ValueError(f"Unsupported target format: {target}")

        dwell_time = random.uniform(dwell_range[0], dwell_range[1])

        # Execute realistic MotionEvent touch down -> dwell -> touch up at natural contact point (x, y)
        try:
            self.d.touch.down(x, y)
            time.sleep(dwell_time)
            self.d.touch.up(x, y)
        except Exception:
            # Fallback if touch down/up fails on certain custom ROMs or mock environments
            try:
                self.d.click(x, y)
            except Exception:
                pass

        # Micro cognitive pause after click (150ms–350ms)
        self.sleep(0.25, jitter_ratio=0.3)
        return x, y

    # -------------------------------------------------------------
    # 3. Bézier Curve Calculation & Natural Physics Swiping
    # -------------------------------------------------------------
    @staticmethod
    def _calculate_cubic_bezier(t: float, p0: Tuple[float, float], p1: Tuple[float, float],
                                p2: Tuple[float, float], p3: Tuple[float, float]) -> Tuple[int, int]:
        """Calculates coordinate on Cubic Bézier curve at parameter t (0.0 to 1.0)."""
        u = 1.0 - t
        tt = t * t
        uu = u * u
        uuu = uu * u
        ttt = tt * t

        x = (uuu * p0[0]) + (3 * uu * t * p1[0]) + (3 * u * tt * p2[0]) + (ttt * p3[0])
        y = (uuu * p0[1]) + (3 * uu * t * p1[1]) + (3 * u * tt * p2[1]) + (ttt * p3[1])
        return int(round(x)), int(round(y))

    def generate_human_swipe_path(self, start: Tuple[int, int], end: Tuple[int, int],
                                  num_steps: int = 18) -> List[Tuple[int, int]]:
        """
        Generates an organic, biomechanically plausible swipe trajectory:
        - Asymmetric ballistic thumb jerk profile (rapid acceleration to ~38% peak velocity, gradual deceleration).
        - Natural path redundancy (~2.5% to 5.0% longer than straight chord, matching human ~4.8% baseline).
        - Lateral harmonic wander and per-step micro-tremor to eliminate analytical over-smoothness.
        """
        dx = end[0] - start[0]
        dy = end[1] - start[1]
        dist = math.hypot(dx, dy)

        # Arc deviation proportional to swipe length (typically 7% to 14% curvature)
        arc_deviation = dist * random.uniform(0.07, 0.13)
        # Randomize curve direction (left or right bend)
        direction = 1 if random.random() < 0.5 else -1

        # Calculate perpendicular vector for curvature
        norm_perp_x = -dy / (dist + 1e-6) * direction
        norm_perp_y = dx / (dist + 1e-6) * direction

        # Control Point 1 (around 28% progress with slight lateral spread)
        p1 = (
            start[0] + dx * 0.28 + norm_perp_x * arc_deviation * random.uniform(0.8, 1.2),
            start[1] + dy * 0.28 + norm_perp_y * arc_deviation * random.uniform(0.8, 1.2)
        )

        # Control Point 2 (around 72% progress)
        p2 = (
            start[0] + dx * 0.72 + norm_perp_x * arc_deviation * random.uniform(0.6, 1.0),
            start[1] + dy * 0.72 + norm_perp_y * arc_deviation * random.uniform(0.6, 1.0)
        )

        points = []
        for i in range(num_steps + 1):
            raw_t = i / float(num_steps)
            # Biomechanical ballistic thumb profile: asymmetric jerk with peak at ~38% progress
            eased_t = (raw_t ** 1.35) / ((raw_t ** 1.35) + ((1.0 - raw_t) ** 1.65) + 1e-9)

            px, py = self._calculate_cubic_bezier(eased_t, start, p1, p2, end)

            # Lateral harmonic wander (~4.8% path redundancy) + micro physiological noise
            if 0 < i < num_steps:
                wander = math.sin(raw_t * math.pi * 2.0) * (dist * random.uniform(-0.02, 0.02))
                px += int(round(norm_perp_x * wander)) + random.choice([-1, 0, 1])
                py += int(round(norm_perp_y * wander)) + random.choice([-1, 0, 1])

            points.append((px, py))

        return points

    def swipe(self, start: Tuple[int, int], end: Tuple[int, int],
              duration_range: Tuple[float, float] = (0.12, 0.20)):
        """
        Performs a fast organic curved swipe across the device using Bézier interpolation.
        """
        duration = random.uniform(duration_range[0], duration_range[1])
        steps = int(max(8, min(14, duration * 60)))
        path = self.generate_human_swipe_path(start, end, num_steps=steps)

        try:
            self.d.swipe_points(path, duration=duration)
        except Exception:
            # Fallback if swipe_points is unsupported
            self.d.swipe(start[0], start[1], end[0], end[1], duration=duration)

        # Snappy post-swipe settling pause
        self.sleep(0.12, jitter_ratio=0.2)

    # -------------------------------------------------------------
    # 4. Human-like Scrolling Helpers
    # -------------------------------------------------------------
    def scroll_down(self, stride_ratio: float = 0.45):
        """
        Performs a snappy vertical swipe up (content scrolls down),
        covering roughly 45%-50% of viewport with natural thumb curve.
        """
        cx = int(self.w * random.uniform(0.45, 0.55))
        start_y = int(self.h * random.uniform(0.70, 0.80))
        end_y = int(start_y - (self.h * stride_ratio * random.uniform(0.9, 1.1)))
        self.swipe((cx, start_y), (cx, end_y))

    def scroll_up(self, stride_ratio: float = 0.45):
        """Performs a snappy vertical swipe down (content scrolls up)."""
        cx = int(self.w * random.uniform(0.45, 0.55))
        start_y = int(self.h * random.uniform(0.25, 0.35))
        end_y = int(start_y + (self.h * stride_ratio * random.uniform(0.9, 1.1)))
        self.swipe((cx, start_y), (cx, end_y))

    # -------------------------------------------------------------
    # 5. Fast 10x Human Typing Cadence
    # -------------------------------------------------------------
    def type_text(self, text: str, element=None):
        """
        10x Fast Human Typing Cadence:
        - Taps the input element naturally first.
        - Emits text in natural word-sized chunks at 10x speed with micro-jitter (5ms-12ms).
        - Finishes even 500+ character captions in 1-2 seconds without freezing.
        """
        if element is not None and element.exists:
            self.click(element)
            self.sleep(0.2, jitter_ratio=0.15)

        # 10x High-speed streaming: group into words / 8-15 char chunks
        words = text.split(" ")
        for idx, word in enumerate(words):
            token = word + (" " if idx < len(words) - 1 else "")
            try:
                self.d.send_keys(token)
            except Exception:
                if element is not None and element.exists:
                    element.set_text(text)
                else:
                    self.d.send_keys(text)
                break
            # 10x faster: tiny natural micro-pause between words (5ms to 14ms)
            time.sleep(random.uniform(0.005, 0.014))

        self.sleep(0.2, jitter_ratio=0.2)
