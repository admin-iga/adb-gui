import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog
import subprocess
import os
import urllib.request
import zipfile
import shutil
import webbrowser
import threading
import time
from PIL import Image, ImageTk
import io

ADB_DIR = "adb"
ADB_EXE = os.path.join(ADB_DIR, "platform-tools", "adb.exe")
ADB_URL = "https://dl.google.com/android/repository/platform-tools-latest-windows.zip"
# ---- Полу авто рут ----
def show_result_window(title, text):
    win = tk.Toplevel(root)
    win.title(title)
    win.geometry("460x340")
    win.resizable(False, False)

    t = tk.Text(win, wrap=tk.WORD)
    t.pack(expand=True, fill=tk.BOTH, padx=10, pady=10)
    t.insert(tk.END, text)
    t.config(state=tk.DISABLED)

def check_oem_unlock_gui():
    out = adb("shell getprop ro.product.manufacturer").lower()
    if "samsung" not in out:
        messagebox.showinfo(
            "OEM Unlock",
            "Это не Samsung устройство.\nOEM Unlock не требуется."
        )
        return

    model = adb("shell getprop ro.product.model").strip()
    kg = adb("shell getprop ro.boot.kg.state").strip().lower()
    flash = adb("shell getprop ro.boot.flash.locked").strip()
    frp = adb("shell getprop ro.boot.frp.pst").strip()

    result = f"📱 Модель: {model}\n\n"

    # 1️⃣ Операторская модель
    if model.endswith("U") or model.endswith("U1"):
        result += (
            "🚫 Операторская версия (USA)\n\n"
            "Bootloader заблокирован НАВСЕГДА\n"
            "OEM Unlock не появится\n"
            "Root невозможен ❌"
        )
        show_result_window("OEM Unlock Check", result)
        return

    # 2️⃣ KG State
    if kg == "prenormal":
        result += (
            "⏳ KG State: Prenormal\n\n"
            "Это новый или сброшенный телефон\n"
            "Нужно подождать 7 дней\n\n"
            "Требования:\n"
            "✔ Интернет\n"
            "✔ SIM или Wi-Fi\n"
            "✔ Google + Samsung аккаунт\n"
        )
        show_result_window("OEM Unlock Check", result)
        return

    # 3️⃣ FRP Lock
    if frp:
        result += (
            "🔒 FRP Lock активен\n\n"
            "Выйди из Google аккаунта\n"
            "и повтори проверку"
        )
        show_result_window("OEM Unlock Check", result)
        return

    # 4️⃣ Разблокировка возможна
    if flash == "1":
        result += (
            "✅ OEM Unlock должен быть доступен\n\n"
            "Путь:\n"
            "Настройки → Для разработчиков → OEM Unlock\n\n"
            "После включения можно unlock bootloader"
        )
    else:
        result += "🔓 Bootloader уже разблокирован"

    show_result_window("OEM Unlock Check", result)

def is_samsung():
    return "samsung" in adb("shell getprop ro.product.manufacturer").lower()

def check_oem_unlock():
    out = adb("shell getprop ro.boot.flash.locked")
    if "0" in out:
        return True
    return False

def check_frp():
    out = adb("shell getprop ro.boot.frp.pst")
    return out.strip() != ""

def reboot_download():
    adb("reboot download")
    messagebox.showinfo(
        "Download Mode",
        "Телефон переведён в Download Mode\n\n"
        "Подключи кабель и следуй инструкциям"
    )

def check_magisk():
    out = adb("shell pm list packages")
    return "com.topjohnwu.magisk" in out

def samsung_auto_root():
    if not is_samsung():
        messagebox.showerror("Ошибка", "Это не Samsung устройство")
        return

    if not check_oem_unlock():
        messagebox.showwarning(
            "OEM Unlock",
            "❌ OEM Unlock выключен\n\n"
            "Включи:\n"
            "Настройки → Для разработчиков → OEM Unlock\n"
            "После включения подожди 7 дней!"
        )
        return

    if check_frp():
        messagebox.showerror(
            "FRP Lock",
            "❌ FRP Lock активен\n\n"
            "Выйди из Google-аккаунта\n"
            "и повтори попытку"
        )
        return

    msg = (
        "✅ Устройство готово к разблокировке\n\n"
        "Следующие шаги:\n"
        "1. Телефон перейдёт в Download Mode\n"
        "2. Зажми ГРОМКОСТЬ ВВЕРХ\n"
        "3. Подтверди UNLOCK\n\n"
        "⚠️ Данные будут удалены"
    )

    if messagebox.askyesno("Samsung Root Unlock", msg):
        reboot_download()

def live_screen():
    win = tk.Toplevel(root)
    win.title("📸 Live Screen")
    # increase height to make room for control buttons
    win.geometry("360x820")
    win.resizable(False, False)

    label = tk.Label(win)
    label.pack()

    # display size used for preview (fixed)
    display_w, display_h = 360, 720

    # device and last image sizes
    device_w = device_h = None
    img_w = img_h = None

    # try to read device physical size (optional)
    try:
        out = adb("shell wm size").strip()
        if out:
            if "Physical size:" in out:
                size = out.split("Physical size:")[-1].strip()
            else:
                size = out
            parts = size.split()[-1] if ' ' in size else size
            if 'x' in parts:
                dw, dh = parts.split('x')
                device_w, device_h = int(dw), int(dh)
    except:
        device_w = device_h = None

    def on_click(event):
        nonlocal img_w, img_h, device_w, device_h
        if img_w is None or img_h is None:
            return
        try:
            # map preview coords to device coords
            if device_w and device_h:
                x = int(event.x * device_w / display_w)
                y = int(event.y * device_h / display_h)
            else:
                x = int(event.x * img_w / display_w)
                y = int(event.y * img_h / display_h)
            adb(f"shell input tap {x} {y}")
        except Exception:
            pass

    label.bind("<Button-1>", on_click)
    label.config(cursor="hand2")

    # Controls for screen management
    controls_frame = tk.Frame(win)
    controls_frame.pack(fill=tk.X, pady=6)

    def send_key(code):
        adb(f"shell input keyevent {code}")

    def do_tap():
        coords = simpledialog.askstring("Tap", "Enter coordinates x y (e.g. 100 200):", parent=win)
        if coords:
            try:
                x, y = coords.split()
                adb(f"shell input tap {x} {y}")
            except:
                messagebox.showerror("Ошибка", "Неверные координаты")

    def do_swipe():
        coords = simpledialog.askstring("Swipe", "Enter x1 y1 x2 y2 [duration_ms] (e.g. 100 500 300 500 300):", parent=win)
        if coords:
            parts = coords.split()
            if len(parts) in (4,5):
                adb(f"shell input swipe {' '.join(parts)}")
            else:
                messagebox.showerror("Ошибка", "Неверный формат для swipe")

    def input_text():
        txt = simpledialog.askstring("Text Input", "Enter text to input:", parent=win)
        if txt is not None:
            safe = txt.replace(' ', '%s')
            adb(f'shell input text "{safe}"')

    # Row 1
    tk.Button(controls_frame, text="Power", width=8, command=lambda: send_key(26)).grid(row=0, column=0, padx=3)
    tk.Button(controls_frame, text="Home", width=8, command=lambda: send_key(3)).grid(row=0, column=1, padx=3)
    tk.Button(controls_frame, text="Back", width=8, command=lambda: send_key(4)).grid(row=0, column=2, padx=3)
    tk.Button(controls_frame, text="Recent", width=8, command=lambda: send_key(187)).grid(row=0, column=3, padx=3)

    # Row 2
    tk.Button(controls_frame, text="Wake", width=8, command=lambda: send_key(224)).grid(row=1, column=0, padx=3, pady=3)
    tk.Button(controls_frame, text="Sleep", width=8, command=lambda: send_key(223)).grid(row=1, column=1, padx=3, pady=3)
    tk.Button(controls_frame, text="Tap", width=8, command=do_tap).grid(row=1, column=2, padx=3, pady=3)
    tk.Button(controls_frame, text="Swipe", width=8, command=do_swipe).grid(row=1, column=3, padx=3, pady=3)

    # Row 3
    tk.Button(controls_frame, text="Text", width=36, command=input_text).grid(row=2, column=0, columnspan=4, pady=3)

    running = True

    def update():
        nonlocal running, img_w, img_h, device_w
        while running:
            try:
                raw = subprocess.check_output(
                    f'"{ADB_EXE if os.path.exists(ADB_EXE) else "adb"}" exec-out screencap -p',
                    shell=True
                )
                img = Image.open(io.BytesIO(raw))
                # update original sizes
                try:
                    img_w_local, img_h_local = img.size
                except:
                    img_w_local = img_h_local = None
                if img_w_local:
                    img_w = img_w_local
                    img_h = img_h_local

                img = img.resize((display_w, display_h))
                photo = ImageTk.PhotoImage(img)
                label.config(image=photo)
                label.image = photo
            except:
                pass
            time.sleep(0.7)

    def on_close():
        nonlocal running
        running = False
        win.destroy()

    win.protocol("WM_DELETE_WINDOW", on_close)
    threading.Thread(target=update, daemon=True).start()

def check_bootloader_unlock():
    try:
        out = subprocess.check_output(
            "fastboot oem device-info",
            shell=True,
            stderr=subprocess.STDOUT
        ).decode()

        if "Device unlocked: true" in out or "unlocked: yes" in out.lower():
            messagebox.showinfo("Bootloader", "🔓 Bootloader разблокирован")
        else:
            messagebox.showwarning(
                "Bootloader",
                "🔒 Bootloader ЗАБЛОКИРОВАН\n\n"
                "Для root потребуется разблокировка"
            )
    except:
        messagebox.showerror(
            "Fastboot",
            "❌ Fastboot не найден или устройство не в fastboot режиме"
        )

def get_device_brand():
    out = adb("shell getprop ro.product.manufacturer").lower()

    if "xiaomi" in out or "redmi" in out or "mi" in out:
        return "Xiaomi"
    if "samsung" in out:
        return "Samsung"
    if "huawei" in out or "honor" in out:
        return "Huawei"
    if "google" in out:
        return "Google Pixel"

    return "Unknown"

def show_device_help(reason="not_found"):
    help_text = (
        "📱 Устройство не найдено\n\n"
        "Что нужно сделать:\n"
        "1. Подключи телефон по USB\n"
        "2. Открой:\n"
        "   Настройки → О телефоне\n"
        "3. Нажми 7 раз на «Номер сборки»\n"
        "4. Зайди в:\n"
        "   Настройки → Для разработчиков\n"
        "5. Включи:\n"
        "   ✔ USB-отладка\n"
        "6. Подтверди доступ на телефоне\n\n"
    )

    if reason == "unauthorized":
        help_text += (
            "⚠ Устройство найдено, но не авторизовано!\n"
            "Посмотри на экран телефона\n"
            "и нажми «Разрешить USB-отладку»"
        )

    win = tk.Toplevel(root)
    win.title("Устройство не подключено")
    win.geometry("480x360")
    win.resizable(False, False)

    txt = tk.Text(win, wrap=tk.WORD)
    txt.pack(expand=True, fill=tk.BOTH, padx=10, pady=10)
    txt.insert(tk.END, help_text)
    txt.config(state=tk.DISABLED)

    tk.Button(
        win,
        text="🌐 Открыть официальную инструкцию",
        command=lambda: webbrowser.open(
            "https://developer.android.com/studio/debug/dev-options"
        )
    ).pack(pady=5)

def reboot_fastboot():
    adb("reboot bootloader")
    messagebox.showinfo("Fastboot", "Телефон переведён в fastboot режим")

def adb_exists():
    if os.path.exists(ADB_EXE):
        return True
    try:
        subprocess.check_output("adb version", shell=True)
        return True
    except:
        return False


def install_adb():
    try:
        os.makedirs(ADB_DIR, exist_ok=True)
        zip_path = os.path.join(ADB_DIR, "adb.zip")

        messagebox.showinfo("ADB", "Скачиваю ADB (platform-tools)...")
        urllib.request.urlretrieve(ADB_URL, zip_path)

        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(ADB_DIR)

        os.remove(zip_path)

        messagebox.showinfo("ADB", "ADB успешно установлен!")
    except Exception as e:
        messagebox.showerror("Ошибка", str(e))


def adb(cmd):
    exe = ADB_EXE if os.path.exists(ADB_EXE) else "adb"
    try:
        result = subprocess.check_output(
            f'"{exe}" {cmd}',
            shell=True,
            stderr=subprocess.STDOUT
        )
        return result.decode("utf-8")
    except subprocess.CalledProcessError as e:
        return e.output.decode("utf-8")


def ensure_adb():
    if not adb_exists():
        if messagebox.askyesno(
            "ADB не найден",
            "ADB не установлен.\nУстановить автоматически?"
        ):
            install_adb()
        else:
            root.destroy()

def check_root():
    out = adb("shell su -c id")
    if "uid=0" in out:
        messagebox.showinfo("Root", "✅ Root доступ получен!")
    else:
        messagebox.showwarning(
            "Root",
            "❌ Root не обнаружен\n\n"
            "Либо root отсутствует,\n"
            "либо не разрешён для ADB"
        )

def request_root():
    out = adb("root")
    if "cannot run as root" in out.lower():
        messagebox.showerror(
            "Root",
            "❌ ADB root невозможен\n\n"
            "Причины:\n"
            "- Нет root\n"
            "- User build\n"
            "- Заблокирован bootloader"
        )
    else:
        messagebox.showinfo("Root", out)


# --- GUI actions ---
def show_brand_info():
    brand = get_device_brand()

    instructions = {
        "Xiaomi": (
            "🔧 Xiaomi\n\n"
            "1. Включи USB-отладку\n"
            "2. Включи «OEM Unlock»\n"
            "3. Разблокируй загрузчик через Mi Unlock\n"
            "4. Установи Magisk\n"
        ),
        "Samsung": (
            "🔧 Samsung\n\n"
            "1. Включи USB-отладку\n"
            "2. Включи «OEM Unlock»\n"
            "3. Используй Odin\n"
            "4. Установи Magisk (AP файл)\n"
        ),
        "Huawei": (
            "🔧 Huawei\n\n"
            "⚠ Большинство моделей НЕ разблокируются\n"
            "Без официального OEM unlock root невозможен\n"
        ),
        "Google Pixel": (
            "🔧 Google Pixel\n\n"
            "1. OEM Unlock\n"
            "2. fastboot flashing unlock\n"
            "3. Magisk\n"
        ),
        "Unknown": "Производитель не определён"
    }

    win = tk.Toplevel(root)
    win.title(f"Устройство: {brand}")
    win.geometry("420x300")

    t = tk.Text(win, wrap=tk.WORD)
    t.pack(expand=True, fill=tk.BOTH, padx=10, pady=10)
    t.insert(tk.END, instructions.get(brand, "Нет информации"))
    t.config(state=tk.DISABLED)

def check_device():
    output = adb("devices")
    text.delete(1.0, tk.END)
    text.insert(tk.END, output)

    lines = output.strip().splitlines()

    if len(lines) <= 1:
        show_device_help("not_found")
        return

    if "unauthorized" in output:
        show_device_help("unauthorized")



def install_apk():
    file = filedialog.askopenfilename(filetypes=[("APK files", "*.apk")])
    if file:
        text.delete(1.0, tk.END)
        text.insert(tk.END, adb(f'install "{file}"'))


def reboot(mode=""):
    adb(f"reboot {mode}")


# --- GUI ---

root = tk.Tk()
root.title("Mini ADB GUI")
root.geometry("520x420")
root.resizable(False, False)

ensure_adb()

frame = tk.Frame(root)
frame.pack(pady=10)

tk.Button(frame, text="📱 Проверить устройство", width=22, command=check_device).grid(row=0, column=0, padx=5)
tk.Button(frame, text="📦 Установить APK", width=22, command=install_apk).grid(row=0, column=1, padx=5)

tk.Button(frame, text="🔁 Reboot", width=22, command=lambda: reboot()).grid(row=1, column=0, padx=5)
tk.Button(frame, text="⚠ Recovery", width=22, command=lambda: reboot("recovery")).grid(row=1, column=1)

tk.Button(frame, text="🧠 Определить бренд", width=22, command=show_brand_info).grid(row=3, column=0, padx=5)
tk.Button(frame, text="🔐 Проверить Root", width=22, command=check_root).grid(row=3, column=1, padx=5)

tk.Button(frame, text="⚡ Запросить ADB Root", width=46, command=request_root).grid(row=4, column=0, columnspan=2, pady=6)

tk.Button(frame, text="📸 Live экран", width=22, command=live_screen).grid(row=5, column=0, padx=5)
tk.Button(frame, text="⚠ Fastboot", width=22, command=reboot_fastboot).grid(row=5, column=1, padx=5)

tk.Button(frame, text="🔓 Unlock checker", width=46, command=check_bootloader_unlock).grid(
    row=6, column=0, columnspan=2, pady=6
)



text = tk.Text(root, height=14, width=65)
text.pack(pady=10)

root.mainloop()
