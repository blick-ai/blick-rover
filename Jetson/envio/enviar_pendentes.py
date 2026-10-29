# -*- coding: utf-8 -*-
"""
enviar_pendentes.py
-------------------
Envia todas as fotos pendentes no SD para a API Blick e remove apos sucesso.
Encerra automaticamente quando a fila estiver vazia.

Uso:
    python3 enviar_pendentes.py
    python3 enviar_pendentes.py --saida /home/jetson/aprovadas
"""

import os
import json
import base64
import argparse
import requests
from datetime import datetime, timedelta

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

META_EXT = ".json"


# ---------------------------------------------------------------------------
# Token JWT
# ---------------------------------------------------------------------------
class GerenciadorToken:
    def __init__(self):
        self._token     = None
        self._expira_em = None

    def obter(self):
        if self._token and datetime.now() < self._expira_em:
            return self._token
        print("[AUTH] Autenticando...", end=" ", flush=True)
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
                print("OK")
                return self._token
            print(f"FALHOU (status {resp.status_code})")
        except Exception as e:
            print(f"ERRO: {e}")
        return None


# ---------------------------------------------------------------------------
# Envio
# ---------------------------------------------------------------------------
def enviar(caminho_jpg, meta, token):
    try:
        with open(caminho_jpg, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        payload = {
            "dia_mes_ano":   meta["dia_mes_ano"],
            "latitude":      meta["latitude"],
            "longitude":     meta["longitude"],
            "imagem_base64": b64,
        }
        r = requests.post(API_URL, json=payload,
                          headers={"Authorization": f"Bearer {token}"},
                          timeout=API_TIMEOUT)
        return r.status_code in (200, 201)
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Principal
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="Envia fotos pendentes do SD para a API Blick."
    )
    parser.add_argument("--saida", default="/home/jetson/aprovadas",
                        help="Pasta com as fotos aprovadas pelo YOLO.")
    args = parser.parse_args()

    pendentes = sorted(
        f for f in os.listdir(args.saida)
        if f.lower().endswith(".jpg")
    )

    if not pendentes:
        print("Nenhuma foto pendente encontrada.")
        return

    print("=" * 60)
    print(f"Blick — Envio de pendentes")
    print(f"  Pasta   : {os.path.abspath(args.saida)}")
    print(f"  Total   : {len(pendentes)} foto(s)")
    print("=" * 60)

    auth = GerenciadorToken()
    token = auth.obter()
    if token is None:
        print("[ERRO] Sem conexao com a API. Verifique a internet e tente novamente.")
        return

    enviadas = 0
    falhas   = 0

    for i, nome_jpg in enumerate(pendentes, 1):
        caminho_jpg  = os.path.join(args.saida, nome_jpg)
        caminho_meta = caminho_jpg.replace(".jpg", META_EXT)

        if not os.path.exists(caminho_meta):
            print(f"  [{i}/{len(pendentes)}] {nome_jpg} — sem metadata, ignorada")
            continue

        try:
            with open(caminho_meta) as f:
                meta = json.load(f)
        except Exception:
            print(f"  [{i}/{len(pendentes)}] {nome_jpg} — metadata corrompida, ignorada")
            continue

        # Renova token se necessario
        token = auth.obter()
        if token is None:
            print("  Sem conexao — interrompendo.")
            break

        print(f"  [{i}/{len(pendentes)}] {nome_jpg} — enviando...", end=" ", flush=True)
        ok = enviar(caminho_jpg, meta, token)

        if ok:
            os.remove(caminho_jpg)
            os.remove(caminho_meta)
            enviadas += 1
            print("OK")
        else:
            falhas += 1
            print("FALHOU")

    print("=" * 60)
    print(f"Enviadas : {enviadas}")
    print(f"Falhas   : {falhas}")
    restantes = len([f for f in os.listdir(args.saida) if f.lower().endswith(".jpg")])
    print(f"Restantes: {restantes}")
    print("=" * 60)


if __name__ == "__main__":
    main()
