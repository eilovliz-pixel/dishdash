import network
import socket
import json
import time
import machine
import gc
import os

# === OTA UPDATE ===
OTA_VERSION = "4.5.3"

# === PINS ===
FRONT_BTN = machine.Pin(2, machine.Pin.IN, machine.Pin.PULL_UP)
SIDE_BTN = machine.Pin(15, machine.Pin.IN, machine.Pin.PULL_UP)
PIR = machine.Pin(27, machine.Pin.IN)
AMP = machine.Pin(14, machine.Pin.OUT)
AMP.value(0)

# === SPI LED MATRIX ===
spi = machine.SPI(1, baudrate=10000000, polarity=0, phase=0,
                  sck=machine.Pin(18), mosi=machine.Pin(23))
LED_CS = machine.Pin(5, machine.Pin.OUT)
LED_NUM = 4
LED_W = LED_NUM * 8

def led_send(d):
    LED_CS.value(0)
    spi.write(d)
    LED_CS.value(1)

def led_init():
    for reg, val in [(0x0C,1),(0x0B,7),(0x09,0),(0x0A,3),(0x0F,0)]:
        led_send(bytes([reg, val] * LED_NUM))
    led_clear()

def led_clear():
    for r in range(1, 9):
        led_send(bytes([r, 0] * LED_NUM))

def led_brightness(val):
    v = max(0, min(15, val))
    led_send(bytes([0x0A, v] * LED_NUM))

FONT = {
 'A':[0x7E,0x11,0x11,0x7E],'B':[0x7F,0x49,0x49,0x36],'C':[0x3E,0x41,0x41,0x22],
 'D':[0x7F,0x41,0x41,0x3E],'E':[0x7F,0x49,0x49,0x41],'F':[0x7F,0x09,0x09,0x01],
 'G':[0x3E,0x41,0x49,0x3A],'H':[0x7F,0x08,0x08,0x7F],'I':[0x41,0x7F,0x41],
 'J':[0x20,0x40,0x40,0x3F],'K':[0x7F,0x08,0x14,0x63],'L':[0x7F,0x40,0x40,0x40],
 'M':[0x7F,0x02,0x04,0x02,0x7F],'N':[0x7F,0x06,0x18,0x7F],'O':[0x3E,0x41,0x41,0x3E],
 'P':[0x7F,0x09,0x09,0x06],'Q':[0x3E,0x41,0x51,0x21,0x5E],'R':[0x7F,0x09,0x19,0x66],
 'S':[0x26,0x49,0x49,0x32],'T':[0x01,0x01,0x7F,0x01,0x01],
 'U':[0x3F,0x40,0x40,0x3F],'V':[0x1F,0x20,0x40,0x20,0x1F],
 'W':[0x3F,0x40,0x30,0x40,0x3F],'X':[0x63,0x14,0x08,0x14,0x63],
 'Y':[0x03,0x04,0x78,0x04,0x03],'Z':[0x61,0x51,0x49,0x45,0x43],
 'Ä':[0xFE,0x11,0x11,0xFE],'Ö':[0xBE,0x41,0x41,0xBE],'Ü':[0xBF,0x40,0x40,0xBF],
 '0':[0x3E,0x51,0x49,0x45,0x3E],'1':[0x42,0x7F,0x40],'2':[0x62,0x51,0x49,0x46],
 '3':[0x22,0x49,0x49,0x36],'4':[0x0F,0x08,0x08,0x7F],'5':[0x27,0x45,0x45,0x39],
 '6':[0x3E,0x49,0x49,0x32],'7':[0x01,0x71,0x09,0x07],'8':[0x36,0x49,0x49,0x36],
 '9':[0x26,0x49,0x49,0x3E],'!':[0x5F],'?':[0x02,0x01,0x59,0x09,0x06],
 ' ':[0,0,0],'+':[0x08,0x1C,0x08],'-':[0x08,0x08,0x08],
 '.':[0x40],':':[0x24],'/':[0x60,0x18,0x06,0x01],
}

def text_to_cols(text):
    cols = []
    for ch in text:
        cols += FONT.get(ch, FONT.get(ch.upper(), [0x55,0x2A,0x55,0x2A]))
        cols.append(0)
    return cols

_frame_buf = bytearray(LED_NUM * 2)
wdt = None

def wdt_feed():
    if wdt:
        wdt.feed()

def led_display_frame(cols, offset):
    for row in range(8):
        d = _frame_buf
        for m in range(LED_NUM):
            byte = 0
            for bit in range(8):
                c = offset + m * 8 + bit
                if row == 0:
                    if 0 <= c < len(cols) and cols[c] & 0x80:
                        byte |= 0x80 >> bit
                elif 0 <= c < len(cols) and cols[c] & (1 << (row - 1)):
                    byte |= 0x80 >> bit
            d[m * 2] = row + 1
            d[m * 2 + 1] = byte
        led_send(d)

# Non-blocking scroll state
scroll = {
    "cols": None, "offset": 0, "last": 0, "speed": 35,
    "count": 0, "max_count": 2, "done": True,
    "callback": None, "text": "", "blink": 0, "blink_last": 0,
    "static": False
}

def scroll_start(text, count=2, speed=None, callback=None):
    if speed is None:
        speed = state["display"].get("scrollSpeed", 30)
    scroll["text"] = text
    cols = [0] * LED_W + text_to_cols(text) + [0] * LED_W
    scroll["cols"] = cols
    scroll["offset"] = 0
    scroll["last"] = time.ticks_ms()
    scroll["speed"] = speed
    scroll["count"] = 0
    scroll["max_count"] = count
    scroll["done"] = False
    scroll["callback"] = callback
    scroll["static"] = False
    scroll["blink"] = 0

def scroll_static(text):
    """Show text centered without scrolling"""
    cols = text_to_cols(text)
    pad = max(0, (LED_W - len(cols)) // 2)
    buf = [0] * pad + cols + [0] * LED_W
    scroll["cols"] = buf
    scroll["offset"] = 0
    scroll["static"] = True
    scroll["done"] = True
    led_display_frame(buf, 0)

def scroll_tick():
    wdt_feed()
    if scroll["done"] or scroll["static"] or scroll["cols"] is None:
        return
    now = time.ticks_ms()
    if time.ticks_diff(now, scroll["last"]) < scroll["speed"]:
        return
    scroll["last"] = now
    scroll["offset"] += 1
    total = len(scroll["cols"]) - LED_W
    if scroll["offset"] >= total:
        scroll["count"] += 1
        if scroll["count"] >= scroll["max_count"]:
            scroll["done"] = True
            if scroll["callback"]:
                scroll["callback"]()
            return
        scroll["offset"] = 0
    led_display_frame(scroll["cols"], scroll["offset"])

# === UART FINGERPRINT ===
fp_uart = machine.UART(2, baudrate=57600, rx=32, tx=33)

def fp_send(data):
    wdt_feed()
    pkt = bytearray([0xEF,0x01,0xFF,0xFF,0xFF,0xFF,0x01])
    ln = len(data) + 2
    pkt.append(ln >> 8)
    pkt.append(ln & 0xFF)
    pkt.extend(data)
    s = sum(pkt[6:])
    pkt.append(s >> 8)
    pkt.append(s & 0xFF)
    fp_uart.write(pkt)
    time.sleep_ms(500)
    return fp_uart.read()

def fp_code(resp):
    return resp[9] if resp and len(resp) > 9 else -1

def fp_scan():
    """Try to scan and identify a finger. Returns slot number or -1"""
    r = fp_send(bytearray([0x01]))  # GenImg
    if fp_code(r) != 0:
        return -1
    r = fp_send(bytearray([0x02, 0x01]))  # Img2Tz
    if fp_code(r) != 0:
        return -1
    r = fp_send(bytearray([0x04, 0x01, 0x00, 0x00, 0x00, 0xA3]))  # Search
    if fp_code(r) == 0 and r and len(r) > 11:
        return r[10] * 256 + r[11]
    return -1

def fp_enroll(slot):
    """Enroll finger at slot. Returns True/False. Blocking with LED feedback!"""
    global fp_enrolling
    fp_enrolling = True
    name = state["names"][slot] if slot < len(state["names"]) else "?"
    fp_uart.read()  # Clear buffer
    try:
        # Step 1: Wait for first finger
        scroll_start("FINGER AUFLEGEN", count=99, speed=35)
        print("FP enroll slot", slot, "- waiting for finger 1...")
        ok = False
        for _ in range(30):
            scroll_tick()
            r = fp_send(bytearray([0x01]))
            if fp_code(r) == 0:
                ok = True
                break
            for _ in range(10):
                scroll_tick()
                time.sleep_ms(50)
        if not ok:
            print("FP: timeout finger 1")
            return False
        r = fp_send(bytearray([0x02, 0x01]))
        if fp_code(r) != 0:
            print("FP: Img2Tz 1 failed")
            return False
        print("FP: finger 1 OK")
        scroll_start("OK! FINGER WEG!", count=99, speed=35)
        # Step 2: Wait for finger removal
        for _ in range(20):
            scroll_tick()
            r = fp_send(bytearray([0x01]))
            if fp_code(r) != 0:
                break
            for _ in range(10):
                scroll_tick()
                time.sleep_ms(50)
        time.sleep_ms(500)
        # Step 3: Wait for second finger
        scroll_start("NOCHMAL AUFLEGEN", count=99, speed=35)
        print("FP: waiting for finger 2...")
        ok = False
        for _ in range(30):
            scroll_tick()
            r = fp_send(bytearray([0x01]))
            if fp_code(r) == 0:
                ok = True
                break
            for _ in range(10):
                scroll_tick()
                time.sleep_ms(50)
        if not ok:
            print("FP: timeout finger 2")
            return False
        r = fp_send(bytearray([0x02, 0x02]))
        if fp_code(r) != 0:
            print("FP: Img2Tz 2 failed")
            return False
        print("FP: finger 2 OK")
        r = fp_send(bytearray([0x05]))
        if fp_code(r) != 0:
            print("FP: RegModel failed")
            scroll_start("FEHLER!", count=2, speed=35)
            for _ in range(20):
                scroll_tick()
                time.sleep_ms(50)
            return False
        r = fp_send(bytearray([0x06, 0x01, slot >> 8, slot & 0xFF]))
        if fp_code(r) != 0:
            print("FP: Store failed")
            scroll_start("FEHLER!", count=2, speed=35)
            for _ in range(20):
                scroll_tick()
                time.sleep_ms(50)
            return False
        print("FP: enrolled slot", slot, "OK!")
        scroll_start(name + " GESPEICHERT!", count=2, speed=35)
        sound_score()
        for _ in range(30):
            scroll_tick()
            time.sleep_ms(50)
        return True
    finally:
        fp_enrolling = False

def fp_delete(slot):
    """Delete fingerprint at slot"""
    r = fp_send(bytearray([0x0C, slot >> 8, slot & 0xFF, 0x00, 0x01]))
    return fp_code(r) == 0

def fp_count():
    """Get number of stored templates"""
    r = fp_send(bytearray([0x1D]))
    if r and len(r) > 11:
        return r[10] * 256 + r[11]
    return 0

# === SOUND ===
def play_tone(freq, dur_ms, duty=50):
    AMP.value(1)
    time.sleep_ms(50)
    spk = machine.PWM(machine.Pin(25))
    spk.freq(freq)
    spk.duty(duty)
    time.sleep_ms(dur_ms)
    spk.duty(0)
    spk.deinit()
    machine.Pin(25, machine.Pin.OUT).value(0)
    AMP.value(0)

def _play(notes):
    """Play note sequence: [(freq, duration_ms), ...]"""
    wdt_feed()
    d = [40, 80, 130, 200, 300][max(0, min(4, state["sound"]["volume"] - 1))]
    AMP.value(1)
    time.sleep_ms(80)
    spk = machine.PWM(machine.Pin(25))
    spk.duty(d)
    for freq, ms in notes:
        if freq == 0:
            spk.duty(0)
            time.sleep_ms(ms)
            spk.duty(d)
        else:
            spk.freq(freq)
            time.sleep_ms(ms)
    spk.duty(0)
    spk.deinit()
    machine.Pin(25, machine.Pin.OUT).value(0)
    AMP.value(0)

def sound_score():
    if not state["sound"]["enabled"] or not state["sound"]["onScore"]:
        return
    # Fröhlich aufsteigend mit Pausen
    _play([(523,200),(0,40),(659,200),(0,40),(784,200),(0,40),(1047,400)])

def sound_start():
    if not state["sound"]["enabled"] or not state["sound"]["onStart"]:
        return
    # Mario Theme
    _play([(660,150),(660,150),(0,150),(660,150),(0,150),(523,150),(660,200),(784,400)])

def sound_milestone():
    if not state["sound"]["enabled"] or not state["sound"]["onMilestone"]:
        return
    # Triumphale Fanfare
    _play([(523,150),(523,150),(0,80),(523,150),(0,80),(392,150),(523,200),(659,200),(784,400)])

def sound_error():
    if not state["sound"]["enabled"]:
        return
    # Traurig absteigend
    _play([(392,300),(349,300),(330,300),(262,450)])

def sound_highscore():
    if not state["sound"]["enabled"]:
        return
    # Kurze Fanfare
    _play([(784,150),(0,40),(784,150),(0,40),(1047,350)])

# === BUTTONS ===
btn_state = {
    "front_down": 0, "front_clicks": 0, "front_last_up": 0, "front_was_down": False,
    "side_down": 0, "side_clicks": 0, "side_last_up": 0, "side_was_down": False,
    "both_start": 0, "both_triggered": False
}

def check_buttons():
    """Non-blocking button handler. Returns action string or None"""
    now = time.ticks_ms()
    f = FRONT_BTN.value() == 0
    s = SIDE_BTN.value() == 0

    # Both buttons held = WiFi reset
    if f and s:
        if btn_state["both_start"] == 0:
            btn_state["both_start"] = now
        elif not btn_state["both_triggered"] and time.ticks_diff(now, btn_state["both_start"]) > 3000:
            btn_state["both_triggered"] = True
            return "wifi_reset"
        return None
    else:
        btn_state["both_start"] = 0
        btn_state["both_triggered"] = False

    # FRONT BUTTON
    if f:
        if not btn_state["front_was_down"]:
            btn_state["front_down"] = now
            btn_state["front_was_down"] = True
    else:
        if btn_state["front_was_down"]:
            btn_state["front_was_down"] = False
            held = time.ticks_diff(now, btn_state["front_down"])
            if held > 1500:
                btn_state["front_clicks"] = 0
                return "front_long"
            else:
                btn_state["front_clicks"] += 1
                btn_state["front_last_up"] = now
        # Check for click timeout (300ms after last release)
        if btn_state["front_clicks"] > 0 and time.ticks_diff(now, btn_state["front_last_up"]) > 400:
            clicks = btn_state["front_clicks"]
            btn_state["front_clicks"] = 0
            if clicks == 1:
                return "front_1"
            elif clicks >= 2:
                return "front_2"

    # SIDE BUTTON
    if s:
        if not btn_state["side_was_down"]:
            btn_state["side_down"] = now
            btn_state["side_was_down"] = True
    else:
        if btn_state["side_was_down"]:
            btn_state["side_was_down"] = False
            held = time.ticks_diff(now, btn_state["side_down"])
            if held > 1500:
                btn_state["side_clicks"] = 0
                return "side_long"
            else:
                btn_state["side_clicks"] += 1
                btn_state["side_last_up"] = now
        if btn_state["side_clicks"] > 0 and time.ticks_diff(now, btn_state["side_last_up"]) > 400:
            clicks = btn_state["side_clicks"]
            btn_state["side_clicks"] = 0
            if clicks == 1:
                return "side_1"
            elif clicks >= 2:
                return "side_2"

    return None

# === PIR / MOTION ===
motion_last = 0
display_active = True

def check_motion():
    global motion_last, display_active
    if scroll.get("_ota"):
        return False
    if not state.get("pirEnabled", True):
        if not display_active:
            display_active = True
            led_init()
            show_current_state()
        return False
    if PIR.value() == 1:
        motion_last = time.ticks_ms()
        if not display_active:
            display_active = True
            led_init()
            show_current_state()
            return True
    elif display_active:
        timeout = state.get("motionTimeout", 15) * 1000
        if time.ticks_diff(time.ticks_ms(), motion_last) > timeout:
            display_active = False
            led_clear()
            led_send(bytes([0x0C, 0] * LED_NUM))  # Shutdown mode
    return False

# === FINGERPRINT CHECK ===
FP_WAKE = machine.Pin(4, machine.Pin.IN, machine.Pin.PULL_UP)
fp_last_check = 0
FP_CHECK_INTERVAL = 500
fp_enrolling = False
fp_cooldown = 0

def check_fingerprint():
    global fp_last_check, fp_cooldown
    if not display_active or fp_enrolling:
        return
    if scroll.get("_ota"):
        return
    now = time.ticks_ms()
    if time.ticks_diff(now, fp_cooldown) < 3000:
        return
    if FP_WAKE.value() == 1:
        return
    if time.ticks_diff(now, fp_last_check) < FP_CHECK_INTERVAL:
        return
    fp_last_check = now
    try:
        fp_uart.read()
        r = fp_send(bytearray([0x01]))
        if fp_code(r) != 0:
            return
        r = fp_send(bytearray([0x02, 0x01]))
        if fp_code(r) != 0:
            return
        r = fp_send(bytearray([0x04, 0x01, 0x00, 0x00, 0x00, 0xA3]))
        if fp_code(r) != 0 or not r or len(r) < 12:
            txt = state["texts"].get("unknown", "UNBEKANNT!")
            scroll_start(txt, count=1, callback=lambda: show_current_state())
            sound_error()
            fp_cooldown = now
            return
        slot = r[10] * 256 + r[11]
        fp_cooldown = now
        do_score(slot)
    except Exception as e:
        print("FP err:", e)

# === MDNS ===
MDNS_HOST = "dishdash"
mdns_sock = None

def start_mdns(ip_str):
    global mdns_sock
    try:
        mdns_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        mdns_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        mdns_sock.bind(("", 5353))
        mdns_sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP,
            bytes([224, 0, 0, 251, 0, 0, 0, 0]))
        mdns_sock.settimeout(0)
        print("mDNS: " + MDNS_HOST + ".local")
    except Exception as e:
        print("mDNS err: " + str(e))
        mdns_sock = None

def check_mdns(ip_str):
    if not mdns_sock:
        return
    try:
        data, addr = mdns_sock.recvfrom(256)
        label = bytes([len(MDNS_HOST)]) + MDNS_HOST.encode() + b"\x05local\x00"
        if label not in data:
            return
        resp = data[0:2] + b"\x84\x00\x00\x00\x00\x01\x00\x00\x00\x00"
        resp += label + b"\x00\x01\x80\x01\x00\x00\x00\x78\x00\x04"
        resp += bytes(int(x) for x in ip_str.split("."))
        mdns_sock.sendto(resp, ("224.0.0.251", 5353))
    except:
        pass

# === DNS CAPTIVE PORTAL ===
dns_sock = None

def start_dns():
    global dns_sock
    try:
        dns_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        dns_sock.bind(("", 53))
        dns_sock.settimeout(0)
    except:
        pass

def check_dns(ip_str):
    if not dns_sock:
        return
    try:
        data, addr = dns_sock.recvfrom(256)
        if len(data) < 12:
            return
        resp = data[0:2] + b"\x81\x80"
        resp += data[4:6] + data[4:6] + b"\x00\x00\x00\x00"
        pos = 12
        while pos < len(data) and data[pos] != 0:
            pos += data[pos] + 1
        pos += 5
        resp += data[12:pos]
        resp += b"\xc0\x0c\x00\x01\x00\x01\x00\x00\x00\x3c\x00\x04"
        resp += bytes(int(x) for x in ip_str.split("."))
        dns_sock.sendto(resp, addr)
    except:
        pass

# === STATE ===
state = {
    "names": ["DAVID", "AMELIE", "JAMIE", "BABSI"],
    "avatars": ["\U0001f534", "\U0001f7e2", "\U0001f535", "\U0001f7e1"],
    "scores": [0, 0, 0, 0], "turn": 0, "running": False,
    "texts": {
        "dirty": "EINRÄUMEN!", "yourTurn": "{NAME} DU BIST DRAN!",
        "point": "{NAME} +1 PUNKT!", "pointOther": "DANKE {NAME} +1 PUNKT!",
        "skipped": "{NAME} WURDE ÜBERSPRUNGEN!", "unknown": "UNBEKANNT!",
        "milestone": "GRATULIERE {NAME}!", "milestoneScore": "{SCORE} PUNKTE!",
        "reset": "RESET!"
    },
    "display": {"brightness": 5, "scrollSpeed": 30, "scrollCount": 2, "blinkCount": 3},
    "motionTimeout": 15,
    "pirEnabled": True,
    "sound": {"enabled": True, "volume": 3, "onStart": True, "onScore": True, "onMilestone": True},
    "log": [],
    "streaks": [0, 0, 0, 0],
    "lastScorer": -1,
    "fp": [False, False, False, False],
    "rewards": [
        {"10": "Eis essen! 🍦", "20": "Extra Fernsehen 📺", "50": "Kleines Geschenk 🎁", "100": "Großer Ausflug! 🎢"},
        {"10": "Lieblingskuchen backen 🧁", "20": "Film aussuchen 🎬", "50": "Freundin einladen 👯", "100": "Shopping Tour! 🛍"},
        {"10": "Länger wach bleiben ⏰", "20": "Lieblingsessen 🍕", "50": "Neues Spielzeug 🎮", "100": "Freizeitpark! 🎡"},
        {"10": "Extra Süßigkeiten 🍭", "20": "Mama-Papa Zeit 👨‍👩‍👧‍👦", "50": "Kleiner Wunsch 💫", "100": "Großer Wunsch! ⭐"}
    ]
}

wifi_config = None
network_config = {"dhcp": True, "ip": "", "gateway": "", "dns": ""}
current_ip = ""
ap_mode = False
cached_nets = "[]"
last_connect_attempt = 0
wifi_failures = 0

def save_state():
    try:
        with open("state.tmp", "w") as f:
            json.dump(state, f)
        try:
            os.remove("state.json")
        except:
            pass
        os.rename("state.tmp", "state.json")
    except Exception as e:
        print("Save err: " + str(e))

def load_state():
    global state
    for fn in ["state.json", "state.tmp"]:
        try:
            with open(fn, "r") as f:
                data = json.load(f)
            state.update(data)
            n = len(state["names"])
            if "sound" not in state:
                state["sound"] = {"enabled": True, "volume": 3, "onStart": True, "onScore": True, "onMilestone": True}
            if "log" not in state:
                state["log"] = []
            if "streaks" not in state:
                state["streaks"] = [0] * n
            if "lastScorer" not in state:
                state["lastScorer"] = -1
            if "fp" not in state:
                state["fp"] = [False] * n
            if "rewards" not in state:
                state["rewards"] = [{"10": "Belohnung 🎁", "20": "Größere Belohnung 🌟", "50": "Super Belohnung! 🎉", "100": "Mega Belohnung!! 🏆"} for _ in range(n)]
            for arr, dv in [("scores", 0), ("streaks", 0)]:
                while len(state[arr]) < n:
                    state[arr].append(dv)
                state[arr] = state[arr][:n]
            while len(state["fp"]) < n:
                state["fp"].append(False)
            state["fp"] = state["fp"][:n]
            while len(state["rewards"]) < n:
                state["rewards"].append({"10": "Belohnung 🎁", "20": "Größere Belohnung 🌟", "50": "Super Belohnung! 🎉", "100": "Mega Belohnung!! 🏆"})
            state["rewards"] = state["rewards"][:n]
            print("State: " + fn)
            return
        except:
            continue
    print("Kein State -> Defaults")
    save_state()

def load_wifi():
    global wifi_config
    try:
        with open("wifi.json", "r") as f:
            wifi_config = json.load(f)
            return True
    except:
        return False

def save_wifi(ssid, password):
    global wifi_config
    wifi_config = {"ssid": ssid, "password": password}
    with open("wifi.json", "w") as f:
        json.dump(wifi_config, f)

def load_network():
    global network_config
    try:
        with open("network.json", "r") as f:
            network_config.update(json.load(f))
    except:
        pass

def save_network():
    with open("network.json", "w") as f:
        json.dump(network_config, f)

def factory_reset():
    for fn in ["state.json", "state.tmp", "wifi.json", "network.json", "boots.txt"]:
        try:
            os.remove(fn)
            print("Removed: " + fn)
        except:
            pass

# === GAME LOGIC ===
def show_current_state():
    """Show the current game state on LED"""
    if not display_active:
        return
    if scroll.get("_ota"):
        return
    led_brightness(state["display"].get("brightness", 5))
    spd = state["display"].get("scrollSpeed", 30)
    cnt = state["display"].get("scrollCount", 2)
    if state["running"]:
        # Dishwasher running, show "EINRÄUMEN!"
        txt = state["texts"].get("dirty", "EINRÄUMEN!")
        scroll_start(txt, count=cnt, speed=spd, callback=lambda: show_current_state())
    else:
        # Someone's turn
        turn = state["turn"]
        name = state["names"][turn] if turn < len(state["names"]) else "?"
        txt = state["texts"].get("yourTurn", "{NAME} DU BIST DRAN!").replace("{NAME}", name)
        scroll_start(txt, count=cnt, speed=spd, callback=lambda: show_current_state())

def do_score(player_idx):
    """Score a point for player. Called by fingerprint or API."""
    n = len(state["names"])
    if player_idx < 0 or player_idx >= n:
        txt = state["texts"].get("unknown", "UNBEKANNT!")
        scroll_start(txt, count=1, callback=lambda: show_current_state())
        sound_error()
        return None

    current_turn = state["turn"]
    state["scores"][player_idx] += 1
    
    # Turn logic: only advance if the correct player scored
    if player_idx == current_turn:
        # Correct player scored - advance to next
        state["turn"] = (current_turn + 1) % n
    else:
        # Someone else helped out - turn stays (original player still needs to go)
        pass
    
    state["running"] = True
    is_turn_player = (player_idx == current_turn)

    # Streak tracking
    if state["lastScorer"] == player_idx:
        state["streaks"][player_idx] += 1
    else:
        for j in range(n):
            if j != player_idx:
                state["streaks"][j] = 0
        state["streaks"][player_idx] = max(state["streaks"][player_idx], 1) if state["streaks"][player_idx] > 0 else 1
    state["lastScorer"] = player_idx

    # Log
    state["log"].append({"p": player_idx, "t": int(time.time())})
    if len(state["log"]) > 30:
        state["log"] = state["log"][-30:]

    save_state()

    # LED + Sound
    name = state["names"][player_idx]

    if is_turn_player:
        txt = state["texts"].get("point", "{NAME} +1 PUNKT!").replace("{NAME}", name)
    else:
        txt = state["texts"].get("pointOther", "DANKE {NAME} +1 PUNKT!").replace("{NAME}", name)

    score = state["scores"][player_idx]
    reward = None
    if str(score) in state["rewards"][player_idx]:
        reward = {"player": player_idx, "score": score, "text": state["rewards"][player_idx][str(score)]}

    if reward:
        sound_milestone()
    else:
        sound_score()

    scroll_start(txt, count=1, callback=lambda: show_current_state())
    return reward

def do_start():
    """Dishwasher started - show next player's turn"""
    state["running"] = False
    save_state()
    sound_start()
    show_current_state()

def do_skip():
    """Skip current player"""
    turn = state["turn"]
    name = state["names"][turn] if turn < len(state["names"]) else "?"
    state["turn"] = (state["turn"] + 1) % len(state["names"])
    save_state()
    txt = state["texts"].get("skipped", "{NAME} ÜBERSPRUNGEN!").replace("{NAME}", name)
    scroll_start(txt, count=1, callback=lambda: show_current_state())

def do_reset():
    """Reset all scores"""
    n = len(state["names"])
    state["scores"] = [0] * n
    state["turn"] = 0
    state["running"] = False
    state["log"] = []
    state["streaks"] = [0] * n
    state["lastScorer"] = -1
    save_state()
    scroll_start(state["texts"].get("reset", "RESET!"), count=1, callback=lambda: show_current_state())

def show_highscores():
    """Show highscores on LED"""
    sound_highscore()
    pairs = [(state["names"][i], state["scores"][i]) for i in range(len(state["names"]))]
    pairs.sort(key=lambda x: x[1], reverse=True)
    txt = "  ".join([p[0] + ":" + str(p[1]) for p in pairs])
    scroll_start(txt, count=1, callback=lambda: show_current_state())

def show_ip():
    """Show IP on LED"""
    scroll_start(current_ip, count=1, callback=lambda: show_current_state())

# === BUTTON ACTIONS ===
def handle_button(action):
    if scroll.get("_ota"):
        return
    if action == "front_1":
        # Dishwasher started
        do_start()
    elif action == "front_2":
        # Show highscores
        show_highscores()
    elif action == "front_long":
        # Skip current player
        do_skip()
    elif action == "side_1":
        # Show IP
        show_ip()
    elif action == "side_long":
        # Score reset
        do_reset()
    elif action == "wifi_reset":
        scroll_start("WIFI RESET!", count=1)
        time.sleep(2)
        try:
            os.remove("wifi.json")
        except:
            pass
        machine.reset()

# === WLAN ===
def do_scan():
    global cached_nets
    gc.collect()
    print("Scanne WLANs... Free:", gc.mem_free())
    sta = network.WLAN(network.STA_IF)
    sta.active(True)
    time.sleep(3)
    try:
        raw = sta.scan()
        seen = set()
        result = []
        for n in raw:
            try:
                ssid = n[0].decode("utf-8").strip()
            except:
                continue
            if not ssid or ssid in seen:
                continue
            seen.add(ssid)
            rssi = n[3]
            if rssi > -50: sig = "████"
            elif rssi > -65: sig = "███░"
            elif rssi > -75: sig = "██░░"
            else: sig = "█░░░"
            result.append({"s": ssid, "r": sig})
        result.sort(key=lambda x: x["s"].lower())
        cached_nets = json.dumps(result)
        print("  " + str(len(result)) + " Netzwerke")
    except Exception as e:
        print("  Scan err: " + str(e))
        cached_nets = "[]"
    sta.active(False)
    time.sleep(1)
    gc.collect()

def connect_wifi():
    global current_ip, wifi_failures
    ap = network.WLAN(network.AP_IF)
    ap.active(False)
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    try:
        network.hostname(MDNS_HOST)
    except:
        pass
    print("Verbinde: " + wifi_config["ssid"])
    wlan.connect(wifi_config["ssid"], wifi_config["password"])
    for i in range(20):
        if wlan.isconnected():
            break
        for _ in range(20):
            scroll_tick()
            time.sleep_ms(50)
        print(".", end="")
    print()
    if wlan.isconnected():
        if not network_config["dhcp"]:
            try:
                wlan.ifconfig((network_config["ip"], "255.255.255.0", network_config["gateway"], network_config["dns"]))
                current_ip = network_config["ip"]
                print("Static IP: " + current_ip)
            except Exception as e:
                print("Static IP failed: " + str(e))
                current_ip = wlan.ifconfig()[0]
        else:
            current_ip = wlan.ifconfig()[0]
            print("DHCP IP: " + current_ip)
        try:
            with open("lastip.txt", "w") as f:
                f.write(current_ip)
        except:
            pass
        start_mdns(current_ip)
        wifi_failures = 0
        return True
    else:
        print("VERBINDUNG FEHLGESCHLAGEN")
        wifi_failures += 1
        wlan.active(False)
        return False

def check_wifi_reconnect():
    global last_connect_attempt, wifi_failures
    if ap_mode:
        return
    wlan = network.WLAN(network.STA_IF)
    if not wlan.isconnected():
        now = time.ticks_ms()
        delay = min(30000 + (wifi_failures * 10000), 120000)
        if time.ticks_diff(now, last_connect_attempt) > delay:
            last_connect_attempt = now
            print("WiFi reconnect attempt " + str(wifi_failures + 1))
            connect_wifi()

def quick_connect(ssid, pwd):
    print("Quick-connect: " + ssid)
    sta = network.WLAN(network.STA_IF)
    sta.active(True)
    time.sleep(1)
    try:
        sta.connect(ssid, pwd)
        ip = ""
        for i in range(12):
            if sta.isconnected():
                if not network_config["dhcp"]:
                    try:
                        sta.ifconfig((network_config["ip"], "255.255.255.0", network_config["gateway"], network_config["dns"]))
                        ip = network_config["ip"]
                    except:
                        ip = sta.ifconfig()[0]
                else:
                    ip = sta.ifconfig()[0]
                print("Quick-connect OK: " + ip)
                break
            time.sleep(1)
            print(".", end="")
        print()
        try:
            sta.disconnect()
        except:
            pass
        sta.active(False)
        return ip
    except Exception as e:
        print("Quick-connect err: " + str(e))
        try:
            sta.active(False)
        except:
            pass
        return ""

def start_ap():
    global current_ip, ap_mode
    ap_mode = True
    gc.collect()
    do_scan()
    sta = network.WLAN(network.STA_IF)
    sta.active(False)
    time.sleep(1)
    ap = network.WLAN(network.AP_IF)
    ap.active(True)
    time.sleep(1)
    ap.config(essid="DISH-DASH-Setup")
    time.sleep(2)
    current_ip = ap.ifconfig()[0]
    start_dns()
    print("=== SETUP MODUS ===")
    print("WLAN: DISH-DASH-Setup")
    print("URL:  http://" + current_ip)

# === SETUP HTML ===
def get_setup_html():
    nl = ""
    try:
        nets = json.loads(cached_nets)
        for i, n in enumerate(nets):
            s = n["s"].replace("&", "&amp;").replace("<", "&lt;").replace('"', "&quot;")
            nl += '<div class="n" data-s="' + s + '"><span>' + s + '</span><span class="r">' + n["r"] + '</span></div>'
    except:
        pass
    if not nl:
        nl = '<div style="text-align:center;padding:12px;color:#666;font-size:12px">Keine Netzwerke gefunden.<br>Nutze die manuelle Eingabe.</div>'
    h = '<!DOCTYPE html><html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Dish Dash Setup</title><style>'
    h += '*{box-sizing:border-box;margin:0;padding:0}body{background:#0a0a0f;color:#e8e8ef;font-family:system-ui,sans-serif;min-height:100vh;display:flex;align-items:center;justify-content:center;padding:20px}'
    h += '.c{background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.1);border-radius:16px;padding:28px;max-width:380px;width:100%}'
    h += 'h1{font-size:22px;text-align:center}.sub{font-size:10px;color:#555;text-align:center;letter-spacing:3px;margin:4px 0 24px}'
    h += 'label{font-size:12px;color:#888;display:block;margin:14px 0 6px}'
    h += 'input{width:100%;padding:11px;background:rgba(255,255,255,.04);border:1px solid rgba(255,255,255,.08);border-radius:8px;color:#fff;font-size:14px;outline:none}input:focus{border-color:#00d68f}'
    h += '.btn{width:100%;padding:13px;border:none;border-radius:10px;color:#fff;font-size:14px;font-weight:600;cursor:pointer;margin-top:18px;background:linear-gradient(135deg,#00d68f,#00b377)}'
    h += '.st{text-align:center;margin-top:14px;font-size:13px;color:#888;line-height:1.7}'
    h += '.nl{max-height:200px;overflow-y:auto;margin-top:8px}'
    h += '.n{padding:10px 12px;background:rgba(255,255,255,.03);border:1px solid rgba(255,255,255,.05);border-radius:8px;margin-bottom:4px;cursor:pointer;font-size:13px;display:flex;justify-content:space-between}'
    h += '.n:hover,.n.a{background:rgba(0,214,143,.12);border-color:rgba(0,214,143,.25)}.n .r{font-size:11px;color:#555;font-family:monospace}'
    h += '.e{color:#ff4d6a}.ic{text-align:center;font-size:36px;margin-bottom:12px}'
    h += '.or{font-size:12px;color:#555;text-align:center;margin:12px 0;display:flex;align-items:center;gap:8px}.or::before,.or::after{content:"";flex:1;height:1px;background:rgba(255,255,255,.06)}'
    h += '.ok{background:rgba(0,214,143,.08);border:1px solid rgba(0,214,143,.2);border-radius:12px;padding:24px;text-align:center;margin-top:16px}'
    h += '.lnk{display:block;padding:14px;background:linear-gradient(135deg,#00d68f,#00b377);color:#fff;font-size:15px;font-weight:700;border-radius:10px;text-decoration:none;margin:12px 0}'
    h += '.ip{font-size:22px;font-weight:700;color:#00d68f;margin:8px 0;font-family:monospace}'
    h += '.wait{padding:12px;background:rgba(255,255,255,.04);border-radius:8px;margin:16px 0}.wait .num{font-size:24px;font-weight:700;color:#ffb740}'
    h += '.dim{font-size:11px;color:#666;line-height:1.8}'
    h += '.spinner{display:inline-block;width:14px;height:14px;border:2px solid rgba(255,255,255,.1);border-top:2px solid #00d68f;border-radius:50%;animation:spin 1s linear infinite}@keyframes spin{to{transform:rotate(360deg)}}'
    h += '</style></head><body><div class="c"><div class="ic">&#127869;</div><h1>Dish Dash</h1><div class="sub">WLAN EINRICHTEN</div><div id="f">'
    h += '<label>&#128246; Netzwerk waehlen</label><div id="nl" class="nl">'
    h += nl
    h += '</div><div class="or">oder manuell eingeben</div>'
    h += '<input id="ms" placeholder="SSID manuell eingeben" style="font-size:12px">'
    h += '<label>&#128274; Passwort</label>'
    h += '<div style="position:relative"><input id="pw" type="password" placeholder="WLAN Passwort"><span id="eye" style="position:absolute;right:10px;top:50%;transform:translateY(-50%);cursor:pointer">&#128065;</span></div>'
    h += '<button class="btn" id="goBtn">Verbinden</button></div><div id="st" class="st"></div></div>'
    h += '<script>'
    h += 'var pick="";'
    h += 'document.getElementById("nl").addEventListener("click",function(e){var n=e.target.closest(".n");if(!n)return;pick=n.getAttribute("data-s");document.getElementById("ms").value="";var a=document.querySelectorAll(".n");for(var i=0;i<a.length;i++)a[i].className="n";n.className="n a";});'
    h += 'document.getElementById("eye").addEventListener("click",function(){var i=document.getElementById("pw");i.type=i.type==="password"?"text":"password";});'
    h += 'document.getElementById("goBtn").addEventListener("click",function(){'
    h += 'var ssid=pick||document.getElementById("ms").value.trim();'
    h += 'if(!ssid){document.getElementById("st").innerHTML="<span class=e>Bitte Netzwerk waehlen</span>";return;}'
    h += 'document.getElementById("st").innerHTML="<div class=spinner></div> Verbinde... (ca. 15 Sek.)";'
    h += 'document.getElementById("f").style.opacity="0.3";document.getElementById("f").style.pointerEvents="none";'
    h += 'var x=new XMLHttpRequest();x.timeout=25000;x.open("POST","/api/setup");x.setRequestHeader("Content-Type","application/json");'
    h += 'x.onload=function(){try{var d=JSON.parse(x.responseText)}catch(e){showDone(ssid,"");return}showDone(ssid,d.ip||"")};'
    h += 'x.onerror=function(){showDone(ssid,"")};x.ontimeout=function(){showDone(ssid,"")};'
    h += 'x.send(JSON.stringify({ssid:ssid,password:document.getElementById("pw").value}));});'
    h += 'function showDone(ssid,ip){document.getElementById("f").style.display="none";'
    h += 'var h="<div class=ok><div style=font-size:28px;margin-bottom:8px>&#9989;</div>";'
    h += 'if(ip){h+="<div style=font-weight:600>Verbunden!</div>";h+="<div class=ip>"+ip+"</div>";'
    h += 'h+="<div class=wait><div style=font-size:12px;color:#888;margin-bottom:6px>Verbinde dein Handy jetzt mit <b style=color:#fff>"+ssid+"</b></div>";'
    h += 'h+="<div class=num id=ct>10</div><div style=font-size:11px;color:#555>Sekunden</div></div>";'
    h += 'h+="<div id=lw style=display:none><a class=lnk href=http://"+ip+">&#127869; Dashboard oeffnen</a>";'
    h += 'h+="<div class=dim>Speichere http://"+ip+" als Lesezeichen!</div></div>";'
    h += 'var sec=10;var ci=setInterval(function(){sec--;document.getElementById(\"ct\").textContent=sec;if(sec<=0){clearInterval(ci);document.querySelector(\".wait\").style.display=\"none\";document.getElementById(\"lw\").style.display=\"block\";}},1000);'
    h += '}else{h+="<div style=font-weight:600>Gespeichert!</div>";'
    h += 'h+="<div style=margin:10px_0;font-size:13px;color:#888>Geraet startet neu...<br>Verbinde dein Handy mit <b style=color:#fff>"+ssid+"</b></div>";'
    h += 'h+="<div class=wait><div class=num id=ct>15</div><div style=font-size:11px;color:#555>Sekunden</div></div>";'
    h += 'h+="<div id=lw style=display:none><a class=lnk href=http://dishdash.local>&#127869; dishdash.local</a>";'
    h += 'h+="<div class=dim>Falls nicht erreichbar: IP im Router nachschauen</div></div>";'
    h += 'var sec=15;var ci=setInterval(function(){sec--;document.getElementById(\"ct\").textContent=sec;if(sec<=0){clearInterval(ci);document.querySelector(\".wait\").style.display=\"none\";document.getElementById(\"lw\").style.display=\"block\";}},1000);'
    h += '}h+="</div>";document.getElementById("st").innerHTML=h;}'
    h += '</script></body></html>'
    return h

# === HTTP ===
def send_resp(cl, body, ct="text/html"):
    b = body.encode("utf-8") if isinstance(body, str) else body
    hdr = "HTTP/1.1 200 OK\r\nContent-Type: " + ct + "; charset=utf-8\r\nAccess-Control-Allow-Origin: *\r\nContent-Length: " + str(len(b)) + "\r\nConnection: close\r\n\r\n"
    cl.send(hdr)
    for i in range(0, len(b), 256):
        cl.send(b[i:i+256])
        time.sleep_ms(5)

def send_redirect(cl, url):
    cl.send("HTTP/1.1 302 Found\r\nLocation: " + url + "\r\nConnection: close\r\n\r\n")

def send_file(cl, fn, ct="text/html", cache=0, gz=False):
    try:
        sz = os.stat(fn)[6]
        hdr = "HTTP/1.1 200 OK\r\nContent-Type: " + ct + "; charset=utf-8\r\nAccess-Control-Allow-Origin: *\r\nContent-Length: " + str(sz) + "\r\nConnection: close\r\n"
        if gz:
            hdr += "Content-Encoding: gzip\r\n"
        if cache:
            hdr += "Cache-Control: public,max-age=" + str(cache) + "\r\n"
        hdr += "\r\n"
        cl.send(hdr)
        with open(fn, "rb") as f:
            while True:
                chunk = f.read(2048)
                if not chunk:
                    break
                cl.send(chunk)
                scroll_tick()
    except:
        pass

def send_cors(cl):
    cl.send("HTTP/1.1 204 No Content\r\nAccess-Control-Allow-Origin: *\r\nAccess-Control-Allow-Methods: *\r\nAccess-Control-Allow-Headers: *\r\nConnection: close\r\n\r\n")

# === API ===
def handle_api(method, path, body):
    # === SETUP ===
    if path == "/api/setup" and method == "POST":
        data = json.loads(body)
        ssid = data["ssid"]
        pwd = data.get("password", "")
        save_wifi(ssid, pwd)
        ip = quick_connect(ssid, pwd)
        if ip:
            def reboot(t):
                machine.reset()
            machine.Timer(0).init(period=3000, mode=machine.Timer.ONE_SHOT, callback=reboot)
            return '{"ok":true,"ip":"' + ip + '"}'
        else:
            def reboot(t):
                machine.reset()
            machine.Timer(0).init(period=1500, mode=machine.Timer.ONE_SHOT, callback=reboot)
            return '{"ok":true,"ip":""}'

    if path == "/api/scan":
        return '{"networks":' + cached_nets + '}'

    # === STATE ===
    if path == "/api/state":
        s = dict(state)
        s["ip"] = current_ip
        s["wifi"] = {"ssid": wifi_config["ssid"] if wifi_config else ""}
        s["mdns"] = MDNS_HOST + ".local"
        s["network"] = network_config
        wlan = network.WLAN(network.STA_IF)
        s["wifi_connected"] = wlan.isconnected() if not ap_mode else False
        s["wifi_failures"] = wifi_failures
        s["boot_count"] = boot_count
        s["uptime"] = time.ticks_diff(time.ticks_ms(), boot_time) // 1000
        return json.dumps(s)

    if path == "/api/ip":
        return '{"ip":"' + current_ip + '","local":"' + MDNS_HOST + '.local"}'

    # === GAME ===
    if method == "POST" and path == "/api/score":
        data = json.loads(body)
        idx = data.get("player", 0)
        reward = do_score(idx)
        if reward:
            return '{"ok":true,"reward":' + json.dumps(reward) + '}'
        return '{"ok":true}'

    if method == "POST" and path == "/api/start":
        do_start()
        return '{"ok":true}'

    if method == "POST" and path == "/api/skip":
        do_skip()
        return '{"ok":true}'

    if method == "POST" and path == "/api/reset":
        do_reset()
        return '{"ok":true}'

    # === CONFIG ===
    if method == "PUT" and path == "/api/names":
        data = json.loads(body)
        names = [nm.upper()[:10] for nm in data.get("names", state["names"])]
        avatars = data.get("avatars", state["avatars"])
        old_n = len(state["names"])
        n = len(names)
        # If players were removed, clean up orphaned fingerprint slots
        if n < old_n:
            for slot in range(n, old_n):
                try:
                    fp_delete(slot)
                except:
                    pass
        state["names"] = names
        while len(avatars) < n:
            avatars.append("\U0001f534")
        state["avatars"] = avatars[:n]
        for arr, dv in [("scores", 0), ("streaks", 0)]:
            while len(state[arr]) < n:
                state[arr].append(dv)
            state[arr] = state[arr][:n]
        while len(state["fp"]) < n:
            state["fp"].append(False)
        state["fp"] = state["fp"][:n]
        while len(state["rewards"]) < n:
            state["rewards"].append({"10": "Belohnung 🎁", "20": "Größere Belohnung 🌟", "50": "Super Belohnung! 🎉", "100": "Mega Belohnung!! 🏆"})
        state["rewards"] = state["rewards"][:n]
        if state["turn"] >= n:
            state["turn"] = 0
        save_state()
        show_current_state()
        return '{"ok":true}'

    if method == "PUT" and path == "/api/texts":
        data = json.loads(body)
        state["texts"].update(data)
        save_state()
        show_current_state()
        return '{"ok":true}'

    if method == "PUT" and path == "/api/display":
        data = json.loads(body)
        if "motionTimeout" in data:
            state["motionTimeout"] = data.pop("motionTimeout")
        if "pirEnabled" in data:
            state["pirEnabled"] = data.pop("pirEnabled")
        state["display"].update(data)
        led_brightness(state["display"].get("brightness", 5))
        scroll["speed"] = state["display"].get("scrollSpeed", 30)
        save_state()
        return '{"ok":true}'

    if method == "POST" and path == "/api/sound/test":
        sound_score()
        return '{"ok":true}'

    if method == "PUT" and path == "/api/sound":
        data = json.loads(body)
        state["sound"].update(data)
        save_state()
        return '{"ok":true}'

    if method == "PUT" and path == "/api/rewards":
        data = json.loads(body)
        idx = data.get("player", 0)
        rewards = data.get("rewards", {})
        if 0 <= idx < len(state["names"]):
            state["rewards"][idx] = rewards
            save_state()
        return '{"ok":true}'

    # === FINGERPRINT API ===
    if method == "POST" and path == "/api/fp/enroll":
        data = json.loads(body)
        slot = data.get("slot", 0)
        if 0 <= slot < len(state["names"]):
            print("FP enroll request for slot", slot)
            ok = fp_enroll(slot)
            if ok:
                state["fp"][slot] = True
                save_state()
                show_current_state()
                return '{"ok":true,"message":"enrolled"}'
            else:
                show_current_state()
                return '{"ok":false,"error":"Registrierung fehlgeschlagen"}'
        return '{"ok":false,"error":"invalid slot"}'

    if method == "POST" and path == "/api/fp/delete":
        data = json.loads(body)
        slot = data.get("slot", 0)
        if 0 <= slot < len(state["names"]):
            fp_delete(slot)
            state["fp"][slot] = False
            save_state()
            return '{"ok":true}'
        return '{"ok":false}'

    if method == "PUT" and path == "/api/fp":
        data = json.loads(body)
        idx = data.get("slot", 0)
        reg = data.get("registered", False)
        if 0 <= idx < len(state["names"]):
            state["fp"][idx] = reg
            save_state()
        return '{"ok":true}'

    if method == "PUT" and path == "/api/wifi":
        data = json.loads(body)
        save_wifi(data["ssid"], data.get("password", ""))
        return '{"ok":true}'

    if method == "PUT" and path == "/api/network":
        data = json.loads(body)
        network_config.update(data)
        save_network()
        return '{"ok":true}'

    if method == "POST" and path == "/api/factory-reset":
        factory_reset()
        def reboot(t):
            machine.reset()
        machine.Timer(0).init(period=1000, mode=machine.Timer.ONE_SHOT, callback=reboot)
        return '{"ok":true}'

    if method == "POST" and path == "/api/restore":
        try:
            data = json.loads(body)
            for k in ["names", "avatars", "scores", "streaks", "fp", "rewards", "turn", "running", "texts", "display"]:
                if k in data:
                    state[k] = data[k]
            save_state()
            show_current_state()
            return '{"ok":true}'
        except Exception as e:
            return '{"ok":false,"error":"' + str(e) + '"}'

    # === OTA UPDATE API ===
    if method == "GET" and path == "/api/ota/version":
        return '{"version":"' + OTA_VERSION + '"}'

    if method == "POST" and path == "/api/ota/start":
        gc.collect()
        data = json.loads(body)
        fn = data["filename"]
        if not scroll.get("_ota"):
            scroll["_ota"] = True
            scroll_static("UPDATE")
        try:
            os.remove(fn + ".new")
        except:
            pass
        with open(fn + ".new", "wb") as f:
            pass  # Create empty file
        print("OTA: start", fn)
        return '{"ok":true}'

    if method == "POST" and path == "/api/ota/chunk":
        wdt_feed()
        gc.collect()
        data = json.loads(body)
        fn = data["filename"]
        import ubinascii
        chunk = ubinascii.a2b_base64(data["data"])
        with open(fn + ".new", "ab") as f:
            f.write(chunk)
        del chunk
        gc.collect()
        return '{"ok":true}'

    if method == "POST" and path == "/api/ota/finish":
        wdt_feed()
        gc.collect()
        data = json.loads(body)
        fn = data["filename"]
        try:
            os.remove(fn + ".bak")
        except:
            pass
        try:
            os.rename(fn, fn + ".bak")
        except:
            pass
        os.rename(fn + ".new", fn)
        print("OTA: finished", fn)
        gc.collect()
        return '{"ok":true}'

    if method == "POST" and path == "/api/reboot":
        def rb(t):
            machine.reset()
        machine.Timer(0).init(period=500, mode=machine.Timer.ONE_SHOT, callback=rb)
        return '{"ok":true}'

    return '{"error":"not found"}'

# === SERVER ===
def start_server():
    addr = socket.getaddrinfo("0.0.0.0", 80)[0][-1]
    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(addr)
    s.listen(3)
    s.settimeout(0)
    print("Server: http://" + current_ip)
    gc.collect()
    print("Free:", gc.mem_free())

    # Init hardware
    led_init()
    motion_last_global = time.ticks_ms()
    show_current_state()

    gc_counter = 0
    global wdt
    wdt = machine.WDT(timeout=30000)  # 30s watchdog - auto-reboot on hang
    while True:
        wdt.feed()
        # === Network ===
        if ap_mode:
            check_dns(current_ip)
        else:
            check_mdns(current_ip)
            check_wifi_reconnect()

        # === Hardware ===
        scroll_tick()
        check_motion()
        check_fingerprint()

        action = check_buttons()
        if action:
            handle_button(action)

        # === Memory ===
        gc_counter += 1
        if gc_counter >= 100:
            gc.collect()
            gc_counter = 0

        # === HTTP ===
        try:
            cl, ca = s.accept()
            cl.settimeout(3)
        except OSError:
            continue
        except:
            continue

        try:
            req = cl.recv(4096).decode("utf-8")
            if not req:
                cl.close()
                continue

            parts = req.split(" ")
            method = parts[0]
            path = parts[1] if len(parts) > 1 else "/"
            body = ""
            if "\r\n\r\n" in req:
                hdr, body = req.split("\r\n\r\n", 1)
                # Read remaining body if Content-Length says there's more
                cl_val = 0
                for line in hdr.split("\r\n"):
                    if line.lower().startswith("content-length:"):
                        cl_val = int(line.split(":")[1].strip())
                while len(body) < cl_val:
                    try:
                        body += cl.recv(2048).decode("utf-8")
                    except:
                        break

            if method == "OPTIONS":
                send_cors(cl)
            elif path.startswith("/api/"):
                r = handle_api(method, path, body)
                send_resp(cl, r, ct="application/json")
            elif ap_mode:
                if "generate_204" in path or "gen_204" in path:
                    send_redirect(cl, "http://192.168.4.1/setup")
                elif "hotspot-detect" in path or "captive" in path:
                    send_redirect(cl, "http://192.168.4.1/setup")
                elif "connecttest" in path or "ncsi" in path:
                    send_redirect(cl, "http://192.168.4.1/setup")
                else:
                    try:
                        setup_html = get_setup_html()
                        with open("_setup.htm", "w") as sf:
                            sf.write(setup_html)
                        send_file(cl, "_setup.htm")
                    except Exception as e:
                        print("Setup err: " + str(e))
                        cl.send("HTTP/1.1 500\r\n\r\nError")
            elif path in ["/", "/index.html"]:
                try:
                    os.stat("dashboard.gz")
                    send_file(cl, "dashboard.gz", cache=300, gz=True)
                except:
                    send_file(cl, "dashboard.html", cache=300)
            elif path == "/manifest.json":
                mf = '{"name":"Dish Dash","short_name":"DishDash","start_url":"/","display":"standalone","background_color":"#0a0a0f","theme_color":"#0a0a0f","icons":[{"src":"/icon.svg","sizes":"any","type":"image/svg+xml"}]}'
                send_resp(cl, mf, ct="application/json")
            elif path == "/icon.svg" or path == "/favicon.svg":
                svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><rect width="100" height="100" rx="20" fill="#0a0a0f"/><text x="50" y="62" text-anchor="middle" font-size="50">🍽</text></svg>'
                cl.send("HTTP/1.1 200 OK\r\nContent-Type: image/svg+xml\r\nCache-Control: public,max-age=86400\r\nConnection: close\r\n\r\n")
                cl.send(svg)
            else:
                cl.send("HTTP/1.1 404\r\n\r\n")

            cl.close()
            gc.collect()
        except Exception as e:
            if str(e):
                print("E:", e)
            try:
                cl.close()
            except:
                pass

# === MAIN ===
print()
print("  DISH DASH v" + OTA_VERSION)
print()

# Boot counter
boot_count = 0
try:
    with open("boots.txt", "r") as f:
        boot_count = int(f.read().strip())
except:
    pass
boot_count += 1
with open("boots.txt", "w") as f:
    f.write(str(boot_count))
boot_time = time.ticks_ms()
print("  Boot #" + str(boot_count))

load_state()
load_network()

# Quick LED test
led_init()
scroll_start("DISH DASH v" + OTA_VERSION, count=1, speed=35)
while not scroll["done"]:
    scroll_tick()
gc.collect()
print("Free:", gc.mem_free())
time.sleep(1)

if load_wifi():
    scroll_start("VERBINDE WIFI...", count=99, speed=12)
    gc.collect()
    if connect_wifi():
        # Blinking LOADING for 5 seconds
        cols = text_to_cols("BOOT")
        pad = max(0, (LED_W - len(cols)) // 2)
        load_buf = [0] * pad + cols + [0] * LED_W
        for i in range(10):
            led_display_frame(load_buf, 0)
            time.sleep_ms(300)
            led_clear()
            time.sleep_ms(200)
        gc.collect()
        start_server()
    else:
        print("-> AP Modus (Fallback)")
        start_ap()
        scroll_static("SETUP")
        start_server()
else:
    print("-> AP Modus (Ersteinrichtung)")
    start_ap()
    scroll_static("SETUP")
    start_server()
