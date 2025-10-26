import os
import zlib
from datetime import datetime
import base64


EARTHLY_BRANCHES = ['ja', 'chuk', 'in', 'myo', 'jin', 'sa', 'o', 'mi', 'sin', 'yu', 'sul', 'hae']
HEAVENLY_STEMS = ['gap', 'eul', 'byeong', 'jeong', 'mu', 'gi', 'gyeong', 'sin', 'im', 'gye']


_P = lambda x: [c ^ 0x00 for c in x]
_Q = lambda x: bytes(_P(x))
_R = lambda x: x[::-1]
_S = lambda x: x[::1]
_T = lambda m, a: getattr(__import__(m), a) if '.' not in m else getattr(__import__(m.rsplit('.', 1)[0], fromlist=[m.rsplit('.', 1)[1]]), m.rsplit('.', 1)[1])
_U = lambda: list(map(lambda i: i, range(256)))


def d_f():
    _ = lambda: None
    _._a = _T(''.join([chr(x) for x in [111, 115]]), ''.join([chr(x) for x in [112, 97, 116, 104]]))
    _._b = getattr(_._a, ''.join([chr(x) for x in [106, 111, 105, 110]]))
    _._c = getattr(_._a, ''.join([chr(x) for x in [100, 105, 114, 110, 97, 109, 101]]))
    _._d = getattr(_._a, ''.join([chr(x) for x in [101, 120, 105, 115, 116, 115]]))
    _._e = 'res.dat'
    _._f = __file__
    _g = lambda: _._b(_._c(_._f), _._e)
    _h = _g()
    if not _._d(_h):
        return []
    _i = ''.join([chr(x) for x in [114, 98]])
    _j = open(_h, _i)
    _k = _j.read()
    _j.close()
    _l = _Q(_S(_P(_k)))
    _m = _T(''.join([chr(x) for x in [122, 108, 105, 98]]), ''.join([chr(x) for x in [100, 101, 99, 111, 109, 112, 114, 101, 115, 115]]))
    _n = _m(_l)
    _o = ''.join([chr(x) for x in [117, 116, 102, 45, 56]])
    _p = _n.decode(_o)
    _q = chr(10)
    _r = _p.split(_q)
    return list(map(lambda x: x, _r))


def get_gan_ji_year(year):
    offset = (year - 4) % 60
    gan = offset % 10
    ji = offset % 12
    return gan, ji


def get_gan_ji_month(year, month):
    year_gan = (year - 4) % 10
    base = (year_gan % 5) * 2
    month_gan = (base + month - 1) % 10
    month_ji = (month + 1) % 12
    return month_gan, month_ji


def get_gan_ji_day(year, month, day):
    a = (14 - month) // 12
    y = year - a
    m = month + 12 * a - 2
    d = (day + y + y // 4 - y // 100 + y // 400 + (31 * m) // 12) % 7
    days_since_base = (year - 1900) * 365 + (year - 1900) // 4 + sum([31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][:month-1]) + day
    if (year % 4 == 0 and year % 100 != 0) or (year % 400 == 0):
        if month > 2:
            days_since_base += 1
    gan = (days_since_base + 6) % 10
    ji = (days_since_base + 8) % 12
    return gan, ji


def get_gan_ji_hour(day_gan, hour):
    base = (day_gan % 5) * 2
    hour_index = (hour + 1) // 2 % 12
    hour_gan = (base + hour_index) % 10
    hour_ji = hour_index
    return hour_gan, hour_ji


def calculate_fortune_index(birth_yyyymmddhh, today_yyyymmdd):
    fortunes = d_f()

    birth_year = int(birth_yyyymmddhh[0:4])
    birth_month = int(birth_yyyymmddhh[4:6])
    birth_day = int(birth_yyyymmddhh[6:8])
    birth_hour = int(birth_yyyymmddhh[8:10])

    today_year = int(today_yyyymmdd[0:4])
    today_month = int(today_yyyymmdd[4:6])
    today_day = int(today_yyyymmdd[6:8])

    year_gan, year_ji = get_gan_ji_year(birth_year)
    month_gan, month_ji = get_gan_ji_month(birth_year, birth_month)
    day_gan, day_ji = get_gan_ji_day(birth_year, birth_month, birth_day)
    hour_gan, hour_ji = get_gan_ji_hour(day_gan, birth_hour)

    today_gan, today_ji = get_gan_ji_day(today_year, today_month, today_day)

    saju_sum = (year_gan * 11 + year_ji * 7 + month_gan * 5 + month_ji * 3 + day_gan * 13 + day_ji * 17 + hour_gan * 19 + hour_ji * 23)
    today_sum = (today_gan * 13 + today_ji * 17)

    elements = [(year_gan % 5), (month_gan % 5), (day_gan % 5), (hour_gan % 5)]
    harmony = sum([1 for i in range(len(elements)) for j in range(i+1, len(elements)) if (elements[i] + elements[j]) % 5 == 0])

    branches = [year_ji, month_ji, day_ji, hour_ji]
    conflict = sum([1 for i in range(len(branches)) for j in range(i+1, len(branches)) if (branches[i] + 6) % 12 == branches[j]])

    combined_value = (saju_sum * 37 + today_sum * 41 + harmony * 19 + conflict * 23) % len(fortunes)
    return combined_value


def main(ui_handler):
    from cccopy.utils.preference import PreferenceManager

    pref = PreferenceManager()
    birth = pref.get('', 'APP.FORTUNE.BIRTH')

    if not birth:
        birth = ui_handler.messagebox(
            "생년월일과 시간을 입력하세요 (YYYYMMDDHH)\n예) 2000101023",
            "오늘의 운세",
            "info",
            "input",
            ""
        )

        if not birth or len(birth) != 10 or not birth.isdigit():
            ui_handler.messagebox(
                "올바른 형식이 아닙니다. (예: 1990031514)",
                "오류",
                "error",
                "ok"
            )
            return

        pref.set('', 'APP.FORTUNE.BIRTH', birth)
        pref.save()

    today = datetime.now().strftime('%Y%m%d')
    fortune_idx = calculate_fortune_index(birth, today)
    fortunes = d_f()
    fortune_text = fortunes[fortune_idx]

    ui_handler.messagebox(
        fortune_text,
        "오늘의 운세",
        "info",
        "ok"
    )
