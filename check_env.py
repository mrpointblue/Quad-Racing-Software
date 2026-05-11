import sys

def check_environment():
    print("--- Quad Parallel Race Environment Check ---")
    
    major = sys.version_info.major
    minor = sys.version_info.minor
    
    if major < 3 or (major == 3 and minor < 10):
        print(f"FEHLER: Python 3.10 oder höher wird benötigt.")
        print(f"Gefunden: Python {major}.{minor}")
        print("\nHinweis: Raspberry Pi OS Bullseye (Python 3.9) wird nicht unterstützt.")
        print("Bitte nutzen Sie Raspberry Pi OS Bookworm.")
        sys.exit(1)
    
    if major == 3 and minor == 11:
        print(f"Python {major}.{minor} erkannt (Empfohlene Version).")
    else:
        print(f"Python {major}.{minor} erkannt (Kompatibel).")
        
    print("Umgebung ist bereit für die Installation.")

if __name__ == "__main__":
    check_environment()