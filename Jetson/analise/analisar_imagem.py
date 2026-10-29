# -*- coding: utf-8 -*-
"""
analisar_imagem.py
------------------
Analisa fotos em loop continuo usando detector HSV de verde.
Sem dependencia de ONNX ou TensorFlow — so OpenCV.

Fotos com verde suficiente sao movidas para aprovadas/.
Fotos sem verde sao descartadas.
"""

import argparse
import os
import shutil
import time

import cv2
import numpy as np

# Faixa HSV de verde calibrada para milho (Maua)
HSV_VERDE_MIN = np.array([35,  60,  25], dtype=np.uint8)
HSV_VERDE_MAX = np.array([90, 255, 255], dtype=np.uint8)


def eh_planta_hsv(frame_bgr, threshold_pct):
    hsv  = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, HSV_VERDE_MIN, HSV_VERDE_MAX)
    pct  = float((mask > 0).mean() * 100)
    return pct >= threshold_pct, pct


def listar_fotos(entrada):
    return sorted(f for f in os.listdir(entrada) if f.lower().endswith(".jpg"))


def mover_aprovada(nome_jpg, entrada, saida):
    caminho_jpg  = os.path.join(entrada, nome_jpg)
    caminho_json = os.path.splitext(caminho_jpg)[0] + ".json"
    if not os.path.exists(caminho_json):
        return False
    shutil.move(caminho_jpg, os.path.join(saida, nome_jpg))
    shutil.move(caminho_json, os.path.join(saida, os.path.basename(caminho_json)))
    return True


def descartar(nome_jpg, entrada):
    caminho_jpg  = os.path.join(entrada, nome_jpg)
    caminho_json = os.path.splitext(caminho_jpg)[0] + ".json"
    if os.path.exists(caminho_jpg):
        os.remove(caminho_jpg)
    if os.path.exists(caminho_json):
        os.remove(caminho_json)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--entrada",   default="/home/jetson/capturas")
    parser.add_argument("--saida",     default="/home/jetson/aprovadas")
    parser.add_argument("--threshold", type=float, default=5.0,
                        help="Percentual minimo de pixels verdes (padrao: 5%%)")
    parser.add_argument("--espera",    type=float, default=5.0)
    args = parser.parse_args()

    os.makedirs(args.saida, exist_ok=True)

    print(f"[DETECTOR] HSV verde | threshold={args.threshold}%")
    print("[LOOP] Iniciando processamento continuo...")

    aprovadas_total = descartadas_total = 0

    while True:
        fotos = listar_fotos(args.entrada)

        if not fotos:
            time.sleep(args.espera)
            continue

        for nome in fotos:
            img = cv2.imread(os.path.join(args.entrada, nome))
            if img is None:
                descartar(nome, args.entrada)
                continue

            eh_planta, pct = eh_planta_hsv(img, args.threshold)

            if eh_planta:
                mover_aprovada(nome, args.entrada, args.saida)
                aprovadas_total += 1
                print(f"  [APROVADA]   {nome} (verde={pct:.1f}%) | aprovadas={aprovadas_total}", flush=True)
            else:
                descartar(nome, args.entrada)
                descartadas_total += 1
                print(f"  [DESCARTADA] {nome} (verde={pct:.1f}%) | descartadas={descartadas_total}", flush=True)


if __name__ == "__main__":
    main()
