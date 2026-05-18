import os
import re

CONFIG_FILE = "config.py"

def inject_hpa():
    print("Mencari file config.py di folder saat ini...")
    
    if not os.path.exists(CONFIG_FILE):
        print(f"❌ ERROR: File '{CONFIG_FILE}' tidak ditemukan.")
        print("Pastikan Anda menempatkan file 'inject_hpa.py' ini di dalam folder project (satu tempat dengan config.py).")
        input("\nTekan Enter untuk keluar...")
        return

    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            content = f.read()

        # Daftar parameter yang akan diinjeksi beserta komentarnya
        patterns = [
            (r"^COUNTDOWN_BUY_START\s*=.*$", "COUNTDOWN_BUY_START = 150  # ⚡ HPA: Eksekusi mulai sisa 2 menit 30 detik"),
            (r"^COUNTDOWN_BUY_END\s*=.*$", "COUNTDOWN_BUY_END = 0      # ⚡ HPA: Eksekusi hingga detik 0"),
            (r"^PRICE_DISQUALIFY_EARLY\s*=.*$", "PRICE_DISQUALIFY_EARLY = 1.10  # ⚡ HPA: Safety Lock OFF (Abaikan volatilitas)"),
            (r"^POLL_INTERVAL\s*=.*$", "POLL_INTERVAL = 3              # ⚡ HPA: API Polling 3 detik"),
            # MAX_DAILY_LOSS sekarang diambil dari .env — JANGAN di-override di sini
        ]

        new_content = content
        for pattern, replacement in patterns:
            # Gunakan re.MULTILINE agar ^ dan $ membaca tiap baris, bukan keseluruhan teks
            new_content, count = re.subn(pattern, replacement, new_content, flags=re.MULTILINE)
            if count == 0:
                print(f"⚠️ PERINGATAN: Variabel target untuk '{pattern[:25]}...' tidak ditemukan di {CONFIG_FILE}.")

        # Simpan kembali ke file config.py
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            f.write(new_content)

        print("\n=====================================================")
        print("✅ PROFIL HPA (High-Performance Aggressive) BERHASIL DIINJEKSI!")
        print("=====================================================")
        print("Bot sekarang di-setting menjadi sangat agresif dan tidak terbatas:")
        print(" 🎯 COUNTDOWN_BUY_START    : 150")
        print(" 🎯 COUNTDOWN_BUY_END      : 0")
        print(" 🎯 PRICE_DISQUALIFY_EARLY : 1.10")
        print(" 🎯 POLL_INTERVAL          : 3")
        print(" 🎯 MAX_DAILY_LOSS         : dari .env (tidak di-override)")
        print("=====================================================")
        print("Silakan restart bot (bot.py) untuk mengaplikasikan konfigurasi baru.")
        
    except Exception as e:
        print(f"\n❌ TERJADI KESALAHAN: {e}")
        
    input("\nTekan Enter untuk menutup jendela ini...")

if __name__ == "__main__":
    inject_hpa()
