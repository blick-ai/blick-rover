# -*- coding: utf-8 -*-
"""
capturar_zed.py
---------------
Aciona a camera ZED na Jetson Nano e tira fotos em intervalo fixo (padrao: a
cada 5 segundos), durante X minutos. Usa apenas a camera ESQUERDA da ZED.

OFFLINE-FIRST:
  - A captura (ZED + GPS + filtro TFLite) NUNCA depende de internet.
  - Toda imagem classificada como PLANTA e salva no SD imediatamente.
  - Um worker em background (thread separada) tenta autenticar e enviar
    as imagens pendentes sempre que houver conexao, sem bloquear a captura.
  - Se nao houver internet, as imagens ficam acumuladas no SD ate a proxima
    janela de conectividade (Wi-Fi da base).

Exemplos:
    # producao: roda ate Ctrl+C, captura nunca para por falta de internet
    python3 capturar_zed.py --minutos 0

    # teste sem ZED e sem GPS
    python3 capturar_zed.py --minutos 1 --espera 5 --simular

Requisitos na Jetson:
    - ZED SDK + pyzed
    - OpenCV, numpy, requests, pyserial
    - tflite-runtime + modelo em /home/jetson/modelo/plants_v1.tflite
"""

import os
import time
import json
import base64
import argparse
import threading
import serial
import requests
from datetime import datetime, timedelta

import numpy as np
import cv2

# ---------------------------------------------------------------------------
# Configuracoes
# ---------------------------------------------------------------------------
API_BASE    = "https://1uzo5w52jk.execute-api.us-east-1.amazonaws.com"
API_URL     = f"{API_BASE}/capturas"
AUTH_URL    = f"{API_BASE}/auth/login"
API_TIMEOUT = 10

AUTH_EMAIL  = "lucascrapino@gmail.com"
AUTH_SENHA  = "BlickMaua2026!"
TOKEN_DURACAO_MINUTOS = 55

GPS_PORTA   = "/dev/ttyUSB0"
GPS_BAUD    = 9600

MODEL_PATH    = os.path.join(os.path.dirname(os.path.abspath(__file__)), "modelo", "plants_v1.tflite")
BACKGROUND_ID = 2101
THRESHOLD     = 0.5

ENVIO_INTERVALO_SEGUNDOS = 15   # de quanto em quanto tempo o worker tenta reenviar pendentes
META_EXT = ".json"              # extensao do arquivo de metadata ao lado de cada .jpg


# ---------------------------------------------------------------------------
# Detector de plantas via TFLite
# ---------------------------------------------------------------------------
class DetectorPlanta:
    def __init__(self, simular=False):
        self.simular      = simular
        self._interpreter = None
        self._input_idx   = None
        self._output_idx  = None

    def carregar(self):
        if not os.path.exists(MODEL_PATH):
            print(f"[DETECTOR] Modelo nao encontrado em {MODEL_PATH}.")
            print("[DETECTOR] Continuando SEM filtro — todas as imagens serao aceitas.")
            return
        try:
            from tflite_runtime.interpreter import Interpreter
            self._interpreter = Interpreter(model_path=MODEL_PATH)
            self._interpreter.allocate_tensors()
            self._input_idx  = self._interpreter.get_input_details()[0]["index"]
            self._output_idx = self._interpreter.get_output_details()[0]["index"]
            print(f"[DETECTOR] Modelo TFLite carregado: {MODEL_PATH}")
        except Exception as e:
            print(f"[DETECTOR] Erro ao carregar modelo: {e}")
            print("[DETECTOR] Continuando SEM filtro.")

    def eh_planta(self, frame_bgr):
        if self._interpreter is None or self.simular:
            return True, 1.0
        try:
            img_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
            img_res = cv2.resize(img_rgb, (224, 224)).astype(np.float32) / 255.0
            tensor  = np.expand_dims(img_res, axis=0)
            self._interpreter.set_tensor(self._input_idx, tensor)
            self._interpreter.invoke()
            probs = self._interpreter.get_tensor(self._output_idx)[0]
            score = 1.0 - float(probs[BACKGROUND_ID])
            return score >= THRESHOLD, score
        except Exception as e:
            print(f"  [DETECTOR] Erro na inferencia: {e}")
            return True, 1.0


# ---------------------------------------------------------------------------
# Gerenciador de token JWT — NUNCA bloqueia a captura.
# So tenta autenticar quando o worker de envio chamar, e com timeout curto.
# ---------------------------------------------------------------------------
class GerenciadorToken:
    def __init__(self):
        self._token     = None
        self._expira_em = None

    def obter_token_nao_bloqueante(self):
        """
        Retorna o token em cache se ainda for valido. Caso contrario tenta
        renovar com timeout curto; se falhar, retorna None sem travar.
        """
        if self._token is not None and datetime.now() < self._expira_em:
            return self._token

        try:
            resp = requests.post(
                AUTH_URL,
                json={"email": AUTH_EMAIL, "senha": AUTH_SENHA},
                timeout=5
            )
            if resp.status_code == 200:
                dados           = resp.json()
                self._token     = dados.get("access_token") or dados.get("id_token")
                self._expira_em = datetime.now() + timedelta(minutes=TOKEN_DURACAO_MINUTOS)
                return self._token
        except Exception:
            pass

        return None


# ---------------------------------------------------------------------------
# Leitor GPS
# ---------------------------------------------------------------------------
class LeitorGPS:
    def __init__(self, porta=GPS_PORTA, baud=GPS_BAUD, simular=False):
        self.porta    = porta
        self.baud     = baud
        self.simular  = simular
        self.lat      = 0.0
        self.lon      = 0.0
        self._lock    = threading.Lock()
        self._rodando = False

    def iniciar(self):
        if self.simular:
            print("[GPS] Modo simulacao — coordenadas fixas (0.0, 0.0).")
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
            print(f"[GPS] Erro: {e} — continuando sem GPS.")
            return
        while self._rodando:
            try:
                linha = gps.readline().decode("ascii", errors="ignore").strip()
                if linha.startswith(("$GPRMC", "$GNRMC")):
                    lat, lon = self._parsear_rmc(linha)
                    if lat is not None:
                        with self._lock:
                            self.lat, self.lon = lat, lon
            except Exception:
                pass
        gps.close()

    @staticmethod
    def _parsear_rmc(s):
        try:
            p = s.split(",")
            if p[2] != "A":
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
        self.simular  = simular
        self.zed      = None
        self._image   = None
        self._runtime = None

    def abrir(self):
        if self.simular:
            print("[ZED] Modo simulacao — imagens de teste.")
            return
        import pyzed.sl as sl
        self.zed = sl.Camera()
        init     = sl.InitParameters()
        init.camera_resolution = sl.RESOLUTION.HD720
        init.camera_fps        = 30
        init.depth_mode        = sl.DEPTH_MODE.NONE
        err = self.zed.open(init)
        if err != sl.ERROR_CODE.SUCCESS:
            raise RuntimeError(f"Falha ao abrir a ZED: {err}")
        self._image   = sl.Mat()
        self._runtime = sl.RuntimeParameters()
        self._sl      = sl
        self._view    = sl.VIEW.LEFT
        print("[ZED] Aberta (vista=LEFT).")

    def capturar(self):
        if self.simular:
            img = np.zeros((720, 1280, 3), dtype=np.uint8)
            txt = datetime.now().strftime("SIMULACAO %Y-%m-%d %H:%M:%S")
            cv2.putText(img, txt, (40, 360), cv2.FONT_HERSHEY_SIMPLEX,
                        1.5, (0, 255, 0), 3, cv2.LINE_AA)
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
# Worker de envio — roda em thread separada, le a fila do SD e tenta enviar.
# A captura NUNCA espera por ele.
# ---------------------------------------------------------------------------
class WorkerEnvio:
    def __init__(self, pasta, auth, intervalo=ENVIO_INTERVALO_SEGUNDOS):
        self.pasta     = pasta
        self.auth      = auth
        self.intervalo = intervalo
        self._rodando  = False
        self._thread   = None
        self.stats     = {"enviadas": 0, "falhas": 0}
        self._lock     = threading.Lock()

    def iniciar(self):
        self._rodando = True
        self._thread  = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        print(f"[ENVIO] Worker iniciado (verifica fila a cada {self.intervalo}s).")

    def parar(self):
        self._rodando = False

    def _loop(self):
        while self._rodando:
            self._processar_fila()
            time.sleep(self.intervalo)
        # Uma ultima tentativa ao encerrar
        self._processar_fila()

    def _processar_fila(self):
        pendentes = sorted(
            f for f in os.listdir(self.pasta)
            if f.lower().endswith(".jpg")
        )
        if not pendentes:
            return

        print(f"  [ENVIO] {len(pendentes)} pendente(s) na fila, autenticando...", flush=True)
        token = self.auth.obter_token_nao_bloqueante()
        if token is None:
            print("  [ENVIO] Sem conexao/token — mantendo tudo no SD.", flush=True)
            return

        for nome_jpg in pendentes:
            caminho_jpg  = os.path.join(self.pasta, nome_jpg)
            caminho_meta = os.path.join(self.pasta, nome_jpg.replace(".jpg", META_EXT))

            if not os.path.exists(caminho_meta):
                continue  # metadata ainda nao foi escrita (captura em andamento)

            try:
                with open(caminho_meta, "r") as f:
                    meta = json.load(f)
            except Exception:
                continue

            sucesso = self._enviar(caminho_jpg, meta, token)
            with self._lock:
                if sucesso:
                    self.stats["enviadas"] += 1
                    os.remove(caminho_jpg)
                    os.remove(caminho_meta)
                    print(f"  [ENVIO] {nome_jpg} enviada e removida do SD.", flush=True)
                else:
                    self.stats["falhas"] += 1
                    print(f"  [ENVIO] {nome_jpg} FALHOU, mantida no SD.", flush=True)
                    # token pode ter expirado no meio da fila — tenta renovar na proxima imagem
                    token = self.auth.obter_token_nao_bloqueante()
                    if token is None:
                        break  # perdeu conexao no meio do envio, para e tenta depois

    @staticmethod
    def _enviar(caminho_imagem, meta, token):
        try:
            with open(caminho_imagem, "rb") as f:
                imagem_b64 = base64.b64encode(f.read()).decode("utf-8")
            payload = {
                "dia_mes_ano":   meta["dia_mes_ano"],
                "latitude":      meta["latitude"],
                "longitude":     meta["longitude"],
                "imagem_base64": imagem_b64,
            }
            headers = {"Authorization": f"Bearer {token}"}
            resp = requests.post(API_URL, json=payload, headers=headers, timeout=API_TIMEOUT)
            return resp.status_code in (200, 201)
        except Exception:
            return False


# ---------------------------------------------------------------------------
# Espera inicial
# ---------------------------------------------------------------------------
def esperar_inicio(segundos):
    if segundos <= 0:
        return
    print(f"Aguardando {int(segundos)}s para posicionar o carrinho...")
    restante = int(segundos)
    while restante > 0:
        if restante % 5 == 0 or restante <= 5:
            print(f"  iniciando em {restante}s...")
        time.sleep(1)
        restante -= 1
    print("Iniciando captura!\n")


# ---------------------------------------------------------------------------
# Principal
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="Captura ZED + GPS + filtro TFLite (offline-first) -> API Blick."
    )
    parser.add_argument("--minutos",   type=float, required=True)
    parser.add_argument("--intervalo", type=float, default=5.0)
    parser.add_argument("--espera",    type=float, default=30.0)
    parser.add_argument("--saida",     default="/home/jetson/capturas")
    parser.add_argument("--gps-porta", default=GPS_PORTA)
    parser.add_argument("--threshold", type=float, default=THRESHOLD)
    parser.add_argument("--simular",   action="store_true")
    args = parser.parse_args()

    os.makedirs(args.saida, exist_ok=True)

    dur_txt = "ate Ctrl+C" if args.minutos == 0 else f"{args.minutos} min"
    print("=" * 60)
    print("Blick — Captura ZED + GPS + Filtro TFLite (OFFLINE-FIRST)")
    print(f"  Duracao        : {dur_txt}")
    print(f"  Intervalo      : {args.intervalo}s")
    print(f"  Espera inicial : {args.espera}s")
    print(f"  Pasta temp     : {os.path.abspath(args.saida)}")
    print(f"  GPS porta      : {args.gps_porta}")
    print(f"  Threshold      : {args.threshold}")
    print(f"  Modelo TFLite  : {MODEL_PATH}")
    print(f"  API            : {API_URL}")
    print("=" * 60)

    detector = DetectorPlanta(simular=args.simular)
    detector.carregar()

    auth = GerenciadorToken()

    # Worker de envio roda em paralelo — NUNCA bloqueia a captura
    worker = WorkerEnvio(pasta=args.saida, auth=auth)
    worker.iniciar()

    gps = LeitorGPS(porta=args.gps_porta, simular=args.simular)
    gps.iniciar()

    zed = FonteZED(simular=args.simular)
    zed.abrir()

    esperar_inicio(args.espera)

    t_fim = None if args.minutos == 0 else time.monotonic() + args.minutos * 60.0

    n_total = n_plantas = n_descartadas = 0

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
            eh_planta, score = detector.eh_planta(frame)

            if not eh_planta:
                n_descartadas += 1
                print(f"  [{n_total}] NAO-PLANTA ({score:.2f}) — descartada")
            else:
                n_plantas += 1
                lat, lon = gps.coordenadas()

                base    = datetime.now().strftime("cap_%Y%m%d_%H%M%S")
                caminho_jpg  = os.path.join(args.saida, base + ".jpg")
                caminho_meta = os.path.join(args.saida, base + META_EXT)

                # Salva a imagem no SD imediatamente — sem depender de rede
                cv2.imwrite(caminho_jpg, frame)
                with open(caminho_meta, "w") as f:
                    json.dump({
                        "dia_mes_ano": datetime.now().strftime("%d/%m/%Y"),
                        "latitude":    lat,
                        "longitude":   lon,
                    }, f)

                print(f"  [{n_total}] {base}.jpg | PLANTA ({score:.2f}) | "
                      f"GPS ({lat:.6f}, {lon:.6f}) — salva no SD")

            # Aguarda o restante do intervalo (a captura NUNCA espera a rede)
            elapsed  = time.monotonic() - t_ciclo
            restante = args.intervalo - elapsed
            if restante > 0:
                time.sleep(restante)

    except KeyboardInterrupt:
        print("\nInterrompido (Ctrl+C).")
    finally:
        gps.parar()
        zed.fechar()
        worker.parar()
        time.sleep(1)  # da um tempo para a ultima tentativa de envio do worker

        with worker._lock:
            enviadas = worker.stats["enviadas"]
            falhas   = worker.stats["falhas"]

        pendentes_no_sd = len([
            f for f in os.listdir(args.saida) if f.lower().endswith(".jpg")
        ])

        print("=" * 60)
        print(f"Total capturado    : {n_total}")
        print(f"  Plantas          : {n_plantas}")
        print(f"  Descartadas      : {n_descartadas}")
        print(f"Enviadas (sessao)  : {enviadas}")
        print(f"Tentativas falhas  : {falhas}")
        print(f"Pendentes no SD    : {pendentes_no_sd}")
        if pendentes_no_sd > 0:
            print(f"  -> O worker continua tentando enviar na proxima execucao do servico.")
            print(f"  -> {os.path.abspath(args.saida)}")
        print("=" * 60)


if __name__ == "__main__":
    main()