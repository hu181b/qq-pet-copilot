"""Free money bags: semantic home control and image-verified friend row badges."""
from functools import lru_cache
import time

import cv2
import numpy as np

from .config import resource_path
from .progress import log

VISITS = '//*[@content-desc="访问"]'
HOME_BAG = '//*[@content-desc="钱袋"]'
SUMMARY = '//*[contains(@content-desc,"福袋") and contains(@content-desc,"金币")]'
INTERVAL = 300
MAX_SECONDS = 25


def recommendation_boundary(screen):
    """Return boundary y; uncertain separator text conservatively stops the scan."""
    from .ocr import ocr_texts
    w = screen.shape[1]
    readings = ocr_texts(screen[:, round(w*.32):round(w*.68)])
    boundaries = []
    for text, x, y, score in readings:
        text = ''.join(text.split())
        if '他们都在玩' in text and score >= .85:
            boundaries.append(y - round(w*.025))
        elif '他们' in text or '都在玩' in text:
            return 0
    return min(boundaries) if boundaries else None


def confirmed_friend_rows(screen, visits):
    """Only rows entirely above a visible recommendation separator."""
    cutoff = recommendation_boundary(screen)
    if cutoff is None:
        return []
    return [e for e in visits if 0 <= e.bounds[1] < e.bounds[3] < cutoff]


@lru_cache(maxsize=1)
def _template():
    data = np.fromfile(resource_path('resources/moneybag-friend.png'), dtype=np.uint8)
    return cv2.imdecode(data, cv2.IMREAD_GRAYSCALE)


def friend_bag_points(screen, visit_bounds):
    """Match only the badge area left of a visible visit button, at normalized width."""
    h, w = screen.shape[:2]
    scale = 1080 / w
    gray = cv2.cvtColor(screen, cv2.COLOR_RGB2GRAY)
    gray = cv2.resize(gray, (1080, round(h * scale)))
    color = cv2.resize(screen, (1080, round(h * scale)))
    template = _template()
    hits = []
    for left, top, right, bottom in visit_bounds:
        cy = round((top + bottom) * .5 * scale)
        x1, x2 = 760, round(left * scale) - 12
        y1, y2 = max(0, cy - 48), min(gray.shape[0], cy + 48)
        if x2 <= x1 or y2 - y1 < 66:
            continue
        roi = gray[y1:y2, x1:x2]
        best = None
        for size in (60, 66, 72):
            if roi.shape[1] < size or roi.shape[0] < size:
                continue
            tile = cv2.resize(template, (size, size))
            _, score, _, point = cv2.minMaxLoc(cv2.matchTemplate(roi, tile, cv2.TM_CCOEFF_NORMED))
            patch = color[y1+point[1]:y1+point[1]+size, x1+point[0]:x1+point[0]+size]
            hsv = cv2.cvtColor(patch, cv2.COLOR_RGB2HSV)
            golden = np.mean((hsv[:, :, 0] >= 10) & (hsv[:, :, 0] <= 40)
                             & (hsv[:, :, 1] >= 35) & (hsv[:, :, 2] >= 140))
            if score >= .94 and golden >= .55 and (best is None or score > best[2]):
                best = (round((x1 + point[0] + size / 2) / scale),
                        round((y1 + point[1] + size / 2) / scale), score)
        if best:
            hits.append(best)
    return hits


class MoneyBagCollector:
    def __init__(self, dev):
        self.dev = dev

    @staticmethod
    def elements(source, xpath):
        if source is None:
            raise RuntimeError('成长福袋巡检无法读取控件树')
        return source.find_elements(xpath)

    def collect_at(self, x, y, friend=False):
        """Only close a recognized money-bag result, never pay/share/visit buttons."""
        self.dev.click(x, y)
        for _ in range(4):
            time.sleep(.5)
            source = self.dev.hierarchy()
            summaries = self.elements(source, SUMMARY)
            if not summaries:
                continue
            closes = []
            close_elements = self.elements(source, '//*[@content-desc="关闭"]')
            max_right = max((e.bounds[2] for e in close_elements), default=1080)
            for element in close_elements:
                l, t, r, b = element.bounds
                # Reject the full-screen close backdrop; use the small close icon.
                if 0 < r-l <= max_right*.16 and 0 < b-t <= max_right*.16:
                    closes.append((l, t, r, b))
            if len(closes) != 1:
                raise RuntimeError('成长福袋结果页关闭按钮不唯一，暂停本轮领取')
            l, t, r, b = closes[0]
            text = summaries[0].attrib.get('content-desc', '')
            self.dev.click((l+r)//2, (t+b)//2)
            time.sleep(.4)
            if self.elements(self.dev.hierarchy(), SUMMARY):
                raise RuntimeError('成长福袋结果页尚未关闭，暂停本轮领取')
            log(f'成长福袋：已处理结果（{text}）')
            return text
        if friend:
            source = self.dev.hierarchy()
            if self.elements(source, '//*[@content-desc="回家"]') and not self.elements(source, '//*[@content-desc="关闭"]'):
                log('好友成长福袋：已点击（无金额弹窗，不推算金币数量）')
                return '好友成长福袋已点击'
        raise RuntimeError('点击成长福袋后未确认结果，暂停本轮，不连续盲点')

    def _click_unique(self, xpath):
        elements = self.elements(self.dev.hierarchy(), xpath)
        if len(elements) != 1:
            raise RuntimeError('成长福袋导航目标不唯一，停止本轮')
        l, t, r, b = elements[0].bounds
        if r <= l or b <= t:
            raise RuntimeError('成长福袋导航目标不可见')
        self.dev.click((l+r)//2, (t+b)//2)
        time.sleep(.6)

    def _visit_bag(self, badge_y, visits):
        rows = [e for e in visits if e.bounds[1] <= badge_y <= e.bounds[3]]
        if len(rows) != 1:
            raise RuntimeError('无法将成长福袋标记匹配到唯一好友，停止本轮')
        l, t, r, b = rows[0].bounds
        self.dev.click((l+r)//2, (t+b)//2)
        for _ in range(4):
            time.sleep(.5)
            source = self.dev.hierarchy()
            if self.elements(source, '//*[@content-desc="回家"]') and not self.elements(source, VISITS):
                break
        else:
            raise RuntimeError('访问好友后未确认好友主页，停止本轮')
        bags = self.elements(source, HOME_BAG)
        try:
            if len(bags) == 1:
                l, t, r, b = bags[0].bounds
                self.collect_at((l+r)//2, (t+b)//2, friend=True)
                return True
            log('好友成长福袋标记已过期，未找到成长福袋')
            return False
        finally:
            source = self.dev.hierarchy()
            if not self.elements(source, '//*[@content-desc="关闭"]'):
                self._click_unique('//*[@content-desc="回家"]')
                self._click_unique('//*[@content-desc="friend"]')

    def run(self, max_pages=32, max_clicks=12):
        """A bounded sweep; caller keeps activity settlement ahead of this work."""
        deadline = time.monotonic() + MAX_SECONDS
        source = self.dev.hierarchy()
        visits = self.elements(source, VISITS)
        home = self.elements(source, '//*[@content-desc="金币胶囊"]')
        if not home:
            return 0
        if self.elements(source, '//*[@content-desc="关闭"]'):
            return 0  # Don't click controls behind an existing modal.
        count = 0
        if visits:
            # An already open sheet may be scrolled inside recommendations.
            self.dev.d.press('back')
            time.sleep(.6)
            if self.elements(self.dev.hierarchy(), VISITS):
                raise RuntimeError('好友列表尚未关闭，不能从顶部开始巡检')
            self._click_unique('//*[@content-desc="friend"]')
        if not visits:
            bags = self.elements(source, HOME_BAG)
            if len(bags) == 1:
                l, t, r, b = bags[0].bounds
                if r > l and b > t:
                    self.collect_at((l+r)//2, (t+b)//2)
                    count += 1
            source = self.dev.hierarchy()
            friends = self.elements(source, '//*[@content-desc="friend"]')
            if len(friends) != 1:
                return count
            l, t, r, b = friends[0].bounds
            self.dev.click((l+r)//2, (t+b)//2)
            time.sleep(.6)
        previous = None
        attempted = set()
        try:
            for _ in range(max_pages):
                if time.monotonic() >= deadline or count >= max_clicks:
                    break
                source = self.dev.hierarchy()
                visits = self.elements(source, VISITS)
                if not visits or self.elements(source, '//*[@content-desc="关闭"]'):
                    break
                screen = self.dev.screenshot()
                h, w = screen.shape[:2]
                cutoff = recommendation_boundary(screen)
                allowed = visits if cutoff is None else [e for e in visits
                            if 0 <= e.bounds[1] < e.bounds[3] < cutoff]
                points = friend_bag_points(screen, [e.bounds for e in allowed])
                target = None
                for x, y, score in points:
                    # Avatar/name row signature remains stable after returning to the top.
                    top, bottom = max(0, y-round(w*.055)), min(h, y+round(w*.055))
                    stamp = cv2.resize(screen[top:bottom, :round(w*.58)], (48, 12))
                    signature = (stamp//16).tobytes()
                    if signature not in attempted:
                        target = (y, score, signature)
                        break
                if target:
                    y, score, signature = target
                    attempted.add(signature)
                    log(f'好友成长福袋：图标匹配 {score:.3f}')
                    if self._visit_bag(y, allowed):
                        count += 1
                    # Visiting reopens the sheet at its top. Re-scan rather than
                    # applying stale row coordinates or a previous page boundary.
                    previous = None
                    continue
                if cutoff is not None:
                    log('成长福袋：已到“他们都在玩”分界，停止向下翻页')
                    break
                start_y = max(0, min(e.bounds[1] for e in visits)-round(w*.12))
                fingerprint = cv2.resize(screen[start_y:, :round(w*.6)], (32,64)).astype(float)
                if previous is not None and np.mean(np.abs(fingerprint-previous)) < 1:
                    log('成长福袋：列表未移动，停止本轮翻页')
                    break
                previous = fingerprint
                # Less than half the visible list height; retain overlap so the
                # separator cannot be skipped between settled screenshots.
                span = max(e.bounds[3] for e in visits)-min(e.bounds[1] for e in visits)
                bottom = max(e.bounds[3] for e in visits)-round(w*.04)
                distance = max(1, round(span*.4))
                self.dev.swipe(round(w*.48), bottom, round(w*.48), bottom-distance)
                time.sleep(.6)
        finally:
            # Only dismiss the friend sheet if it is still present and no reward modal remains.
            source = self.dev.hierarchy()
            if self.elements(source, VISITS) and not self.elements(source, '//*[@content-desc="关闭"]'):
                self.dev.d.press('back')
                time.sleep(.6)
                if self.elements(self.dev.hierarchy(), VISITS):
                    raise RuntimeError('好友列表尚未关闭，停止本轮巡检')
        log(f'成长福袋巡检完成：处理 {count} 个（上限 {max_pages} 屏，时间预算 {MAX_SECONDS} 秒）')
        return count
