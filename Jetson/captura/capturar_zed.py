# -*- coding: utf-8 -*-
"""
capturar_zed.py
---------------
Captura imagens da camera ZED (camera esquerda) e salva todas as imagens
com seus metadados em disco. A analise YOLO e o envio para a API sao etapas
separadas, executadas por analisar_imagem.py e enviar_pendentes.py.

Exemplos:
    # captura indefinidamente
    python3 capturar_zed.py --minutos 0

    # teste rapido sem ZED e sem GPS (gera imagem verde simulada)
    python3 capturar_zed.py --minutos 1 --espera 5 --simular

Requisitos:
    - ZED SDK + pyzed
    - OpenCV, numpy, pyserial
"""

import os
import time
import json
import argparse
import threading
import serial
from datetime import datetime

import numpy as np
import cv2

# ---------------------------------------------------------------------------
# Configuracoes
# ---------------------------------------------------------------------------
# Portas GPS para testar automaticamente
GPS_PORTAS  = ["/dev/ttyUSB0", "/dev/ttyUSB1", "/dev/ttyACM0", "/dev/ttyACM1"]
GPS_BAUD    = 9600

META_EXT = ".json"


# ---------------------------------------------------------------------------
# Leitor GPS com deteccao automatica de porta
# ---------------------------------------------------------------------------
class LeitorGPS:
    def __init__(self, porta=None, baud=GPS_BAUD, simular=False):
        self.baud     = baud
        self.simular  = simular
        self.porta    = porta
        self.lat      = 0.0
        self.lon      = 0.0
        self._lock    = threading.Lock()
        self._rodando = False

    def _detectar_porta(self):
        """Testa as portas conhecidas e retorna a primeira que abrir."""
        for p in GPS_PORTAS:
            try:
                s = serial.Serial(p, self.baud, timeout=1)
                s.close()
                print(f"[GPS] Porta detectada automaticamente: {p}")
                return p
            except Exception:
                continue
        return None

    def iniciar(self):
        if self.simular:
            print("[GPS] Modo simulacao — coordenadas fixas (0.0, 0.0).")
            return

        if self.porta is None:
            self.porta = self._detectar_porta()

        if self.porta is None:
            print("[GPS] Nenhuma porta GPS encontrada — continuando sem GPS.")
            return

        self._rodando = True
        t = threading.Thread(target=self._loop, daemon=True)
        t.start()
        print(f"[GPS] Lendo em {self.porta} a {self.baud} bps.")

    def parar(self):
        self._rodando = False

    def coordenadas(self):
        with self._lock:
            return self.lat, self.lon

    def _loop(self):
        try:
            gps = serial.Serial(self.porta, self.baud, timeout=2)
        except serial.SerialException as e:
            print(f"[GPS] Erro ao abrir {self.porta}: {e}")
            return

        sem_fix_count = 0
        while self._rodando:
            try:
                linha = gps.readline().decode("ascii", errors="ignore").strip()
                if linha.startswith(("$GPRMC", "$GNRMC")):
                    lat, lon = self._parsear_rmc(linha)
                    if lat is not None:
                        with self._lock:
                            self.lat, self.lon = lat, lon
                        sem_fix_count = 0
                    else:
                        sem_fix_count += 1
                        if sem_fix_count % 20 == 0:
                            print(f"[GPS] Aguardando fix satelite...")
            except Exception:
                pass
        gps.close()

    @staticmethod
    def _parsear_rmc(s):
        try:
            p = s.split(",")
            if p[2] != "A":   # A = fix valido, V = sem sinal
                return None, None
            lat = float(p[3][:2]) + float(p[3][2:]) / 60.0
            if p[4] == "S": lat = -lat
            lon = float(p[5][:3]) + float(p[5][3:]) / 60.0
            if p[6] == "W": lon = -lon
            return lat, lon
        except Exception:
            return None, None


# ---------------------------------------------------------------------------
# Camera ZED
# ---------------------------------------------------------------------------
class FonteZED:
    def __init__(self, simular=False):
        self.simular = simular
        self.zed     = None

    def abrir(self, tentativas=10, espera_entre=5):
        if self.simular:
            print("[ZED] Modo simulacao — imagens verdes de teste.")
            return
        import pyzed.sl as sl
        import time as _time

        for i in range(1, tentativas + 1):
            cam  = sl.Camera()
            init = sl.InitParameters()
            init.camera_resolution = sl.RESOLUTION.HD720
            init.camera_fps        = 30
            init.depth_mode        = sl.DEPTH_MODE.NONE
            err = cam.open(init)
            if err == sl.ERROR_CODE.SUCCESS:
                self.zed      = cam
                self._image   = sl.Mat()
                self._runtime = sl.RuntimeParameters()
                self._sl      = sl
                self._view    = sl.VIEW.LEFT
                print("[ZED] Aberta (vista=LEFT).")
                return
            print(f"[ZED] Tentativa {i}/{tentativas}: {err} — aguardando {espera_entre}s...")
            cam.close()
            _time.sleep(espera_entre)

        raise RuntimeError(f"ZED nao detectada apos {tentativas} tentativas.")

    def capturar(self):
        if self.simular:
            # Imagem com tom de verde de planta para testar o filtro HSV
            img = np.zeros((720, 1280, 3), dtype=np.uint8)
            # Verde em BGR: (34, 139, 34) = forest green
            img[:480, :, :] = [34, 139, 34]
            txt = datetime.now().strftime("SIMULACAO %Y-%m-%d %H:%M:%S")
            cv2.putText(img, txt, (40, 360), cv2.FONT_HERSHEY_SIMPLEX,
                        1.5, (255, 255, 255), 3, cv2.LINE_AA)
            return img
        sl = self._sl
        if self.zed.grab(self._runtime) != sl.ERROR_CODE.SUCCESS:
            return None
        self.zed.retrieve_image(self._image, self._view)
        return cv2.cvtColor(self._image.get_data(), cv2.COLOR_BGRA2BGR)

    def fechar(self):
        if self.zed is not None:
            self.zed.close()
            print("[ZED] Fechada.")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def esperar_inicio(segundos):
    if segundos <= 0:
        return
    print(f"Aguardando {int(segundos)}s para posicionar o carrinho...")
    for restante in range(int(segundos), 0, -1):
        if restante % 5 == 0 or restante <= 5:
            print(f"  iniciando em {restante}s...")
        time.sleep(1)
    print("Iniciando captura!\n")


# ---------------------------------------------------------------------------
# Principal
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Captura ZED + GPS para processamento posterior.")
    parser.add_argument("--minutos",   type=float, default=0,
                        help="Duracao em minutos (0 = infinito, padrao).")
    parser.add_argument("--intervalo", type=float, default=2.0)
    parser.add_argument("--espera",    type=float, default=30.0)
    parser.add_argument("--saida",     default="/home/jetson/capturas")
    parser.add_argument("--gps-porta", default=None,
                        help="Porta serial do GPS. Se omitido, detecta automaticamente.")
    parser.add_argument("--simular",   action="store_true",
                        help="Sem ZED e GPS reais — gera imagem verde de teste.")
    args = parser.parse_args()

    os.makedirs(args.saida, exist_ok=True)

    dur_txt = "infinito" if args.minutos == 0 else f"{args.minutos} min"
    print("=" * 60)
    print("Blick — Captura ZED + GPS")
    print(f"  Duracao        : {dur_txt}")
    print(f"  Intervalo      : {args.intervalo}s")
    print(f"  Espera inicial : {args.espera}s")
    print(f"  Pasta temp     : {os.path.abspath(args.saida)}")
    print("=" * 60)

    gps = LeitorGPS(porta=args.gps_porta, simular=args.simular)
    gps.iniciar()

    zed = FonteZED(simular=args.simular)
    zed.abrir()

    esperar_inicio(args.espera)

    t_fim = None if args.minutos == 0 else time.monotonic() + args.minutos * 60.0
    n_total = 0

    try:
        while True:
            if t_fim is not None and time.monotonic() >= t_fim:
                break

            t_ciclo = time.monotonic()

            frame = zed.capturar()
            if frame is None:
                print("  [ZED] falha ao capturar, tentando de novo...")
                time.sleep(0.5)
                continue

            n_total += 1
            lat, lon = gps.coordenadas()
            base         = datetime.now().strftime("cap_%Y%m%d_%H%M%S_%f")
            caminho_jpg  = os.path.join(args.saida, base + ".jpg")
            caminho_meta = os.path.join(args.saida, base + META_EXT)

            if not cv2.imwrite(caminho_jpg, frame):
                print(f"  [{n_total}] falha ao salvar {caminho_jpg}")
                continue
            with open(caminho_meta, "w") as f:
                json.dump({
                    "dia_mes_ano": datetime.now().strftime("%d/%m/%Y"),
                    "latitude":    lat,
                    "longitude":   lon,
                }, f)

            print(f"  [{n_total}] {base}.jpg | GPS ({lat:.6f}, {lon:.6f}) — salva em capturas")

            elapsed  = time.monotonic() - t_ciclo
            restante = args.intervalo - elapsed
            if restante > 0:
                time.sleep(restante)

    except KeyboardInterrupt:
        print("\nInterrompido (Ctrl+C).")
    finally:
        gps.parar()
        zed.fechar()
        print("=" * 60)
        print(f"Total capturado    : {n_total}")
        print(f"Fotos salvas       : {n_total}")
        print(f"  Analise: python3 analisar_imagem.py --entrada {args.saida}")
        print("=" * 60)


if __name__ == "__main__":
    main()
