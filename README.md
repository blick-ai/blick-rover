# Blick Rover

Código do rover autônomo do projeto Blick (TCC CMD04, IMT Mauá): monitoramento de plantação de milho. O rover anda pela lavoura, a Jetson fotografa as plantas com uma câmera ZED, grava a posição GPS de cada foto e, quando há internet, envia tudo para a API do Blick, que guarda a imagem no S3 e os metadados no DynamoDB.

## Estrutura

| Pasta | Conteúdo |
|---|---|
| `Jetson/` | Scripts Python e services systemd que rodam na Jetson Nano: captura, análise e envio. |
| `Rover/` | Código e documentação da parte de locomoção: Arduino, RoboClaws e motores. |

## Pipeline da Jetson

O fluxo é dividido em três services independentes que conversam só por pastas. Se um quebra, os outros continuam e nenhuma foto se perde.

```
ZED + GPS -> [captura] -> capturas/ -> [analise] -> aprovadas/ -> [envio] -> API (S3 + DynamoDB)
```

| Etapa | Script | Service | O que faz |
|---|---|---|---|
| Captura | `capturar_zed.py` | `blick-captura` | Tira uma foto a cada 2 s (câmera esquerda da ZED) e grava um `.json` com data e GPS ao lado. |
| Análise | `analisar_imagem.py` | `blick-analise` | Descarta fotos sem planta e move as aprovadas para `aprovadas/`. |
| Envio | `enviar_pendentes.py` | `blick-envio` | Autentica na API, envia cada foto aprovada e apaga do cartão SD após o sucesso. |

Cada foto tem um `.json` com o mesmo nome:

```json
{"dia_mes_ano": "18/09/2026", "latitude": -23.5, "longitude": -46.6}
```

Sem fix de GPS, latitude e longitude ficam em `0.0`. Vale conferir antes de confiar na localização de uma foto.

### API

| Rota | Corpo | Resposta |
|---|---|---|
| `POST /auth/login` | `{"email", "senha"}` | token JWT do Cognito |
| `POST /capturas` | `{"dia_mes_ano", "latitude", "longitude", "imagem_base64"}` | 201 em caso de sucesso, com `Authorization: Bearer <token>` |

A URL base fica na variável `API_BASE` do `enviar_pendentes.py`.

### Atenção com credenciais

Este repositório é público. Email, senha e URL da API não devem ser commitados. Leia de variáveis de ambiente (`AUTH_EMAIL`, `AUTH_SENHA`, `API_BASE`) e defina no service com `Environment=`, ou em um arquivo `/etc/blick.env` fora do repositório e referenciado com `EnvironmentFile=`. Se alguma senha já foi commitada, troque no Cognito: apagar do histórico não basta.

## Hardware

| Item | Detalhe |
|---|---|
| Computador | Jetson Nano Yahboom 4B, JetPack 4.6.3 (L4T 32.7.3), Python 3.6.9 |
| Câmera | ZED estéreo, ZED SDK 3.8.2, usa só a vista esquerda em 1280x720 |
| GPS | u-blox / VK-162 USB, aparece como `/dev/ttyACM0` ou `/dev/ttyACM1`, 9600 bps |
| Wi-Fi | Adaptador USB com chip ID `0bda:c811` (o `0bda:1a2b` não é suportado) |
| Alimentação da Jetson | Bateria 12 V, conversor XL4015 ajustado para 5,1 V |

A Jetson usa uma bateria própria, separada da bateria do rover.

## Instalação na Jetson

Todos os comandos abaixo rodam na Jetson, via SSH. O `scp` roda no PC, fora do SSH.

Dependências Python:

```bash
pip3 install --user numpy==1.17.4
pip3 install pyserial
wget https://download.stereolabs.com/verify/pyzed/3.8/pyzed-3.8-cp36-cp36m-linux_aarch64.whl
pip3 install --no-deps pyzed-3.8-cp36-cp36m-linux_aarch64.whl
```

O `numpy` do `apt` (1.13.3) conflita com o `pyzed`. Use a versão do `pip`.

Copie os arquivos do PC para a Jetson (PowerShell do PC):

```powershell
scp *.py *.service jetson@IP_DA_JETSON:/home/jetson/
```

Instale os services (na Jetson):

```bash
sudo cp blick-*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable blick-captura
```

Não rode `apt upgrade` na Jetson. Já quebrou dependências do JetPack.

## Uso

### Em campo

Só a captura fica ativa. Menos processamento, menos consumo de bateria, menos risco de corromper o cartão SD.

```bash
sudo systemctl enable --now blick-captura
sudo systemctl disable --now blick-analise blick-envio
```

Acompanhe os logs com `journalctl -fu blick-captura`. Se o GPS aparecer como `0.000000, 0.000000`, ele ainda não conseguiu fix. Espere alguns minutos a céu aberto antes de andar com o rover.

### Em casa

Com energia estável e internet:

```bash
sudo systemctl disable --now blick-captura
sudo systemctl enable --now blick-analise blick-envio
journalctl -fu blick-analise
journalctl -fu blick-envio
```

### Sempre desligue antes de cortar a bateria

```bash
sudo shutdown -h now
```

Cortar a energia com a Jetson ligada já corrompeu o sistema de arquivos uma vez e exigiu regravar o JetPack.

## Filtro de planta

O filtro decide se a foto tem milho suficiente para valer o envio.

### HSV (em produção)

Conta a porcentagem de pixels verdes (H 35 a 90, S 60 a 255, V 25 a 255). Se passar do limite (padrão 5%), a foto é aprovada. É rápido, roda em tempo real na Jetson e não precisa de GPU. A limitação: qualquer coisa verde passa, inclusive grama e mato.

```bash
python3 analisar_imagem.py --entrada /home/jetson/capturas --saida /home/jetson/aprovadas --threshold 5
```

### YOLO (em teste)

Modelos de segmentação treinados no Roboflow, exportados para ONNX.

| Modelo | mAP50 | Precisão | Recall | F1 |
|---|---|---|---|---|
| YOLO26-seg | 77,8 | 86,5 | 69,3 | 77,0 |
| YOLOv11-seg | 75,2 | 87,4 | 71,0 | 78,4 |

Formato de saída para decodificar a confiança de cada detecção:

| Modelo | Shape | Onde está a confiança |
|---|---|---|
| YOLO26-seg (end2end) | `(1, 300, 38)` | coluna 4 de cada linha (já entre 0 e 1). A coluna 5 é o índice da classe. |
| YOLOv11-seg | `(1, 37, 8400)` | linha 4 (score da classe). As linhas 0 a 3 são caixa e as 5 a 36 são coeficientes de máscara. |

Use a confiança como ela vem. Não aplique sigmoid nem multiplique pelo índice da classe, isso distorce o valor e faz o filtro aprovar tudo.

Na CPU da Jetson cada foto leva cerca de 3 s e processar em lote não acelera. O `onnxruntime-gpu` 1.11.0 instalado só funciona em CPU porque faltam `libcublas.so.10` e `libcudart.so.10.2`. O pacote `ultralytics` não instala em Python 3.6.

## Problemas conhecidos

| Sintoma | Causa provável | O que fazer |
|---|---|---|
| Coordenadas `0.0, 0.0` nas fotos | GPS sem fix (sentença `$GPRMC` com `V`) | Esperar fix a céu aberto. Conferir com `cat /dev/ttyACM0 \| grep GPRMC` e ver se o segundo campo é `A`. |
| `std::out_of_range ... substr __pos (which is 140)` e `blick-analise` reiniciando | Crash do ONNX Runtime ao rodar YOLO na Jetson, causa não confirmada | Voltar para o filtro HSV enquanto não for resolvido. |
| Envio com `status 503` | API fora do ar ou sem internet | O `blick-envio` tenta de novo a cada 15 s. As fotos continuam seguras no cartão. |
| Envio com `status 401` | Email ou senha errados | Conferir `AUTH_EMAIL` e `AUTH_SENHA`. |
| Muitas fotos sem planta enviadas | Threshold HSV baixo ou YOLO com parsing errado | Subir o `--threshold` ou corrigir a leitura da confiança conforme a tabela acima. |
| ZED não abre | USB sem energia ou cabo ruim | O script tenta 10 vezes com 5 s entre elas. Se falhar, reconectar a câmera e reiniciar o service. |
| Wi-Fi USB não aparece | Chip não suportado | Usar adaptador com ID `0bda:c811`. |

## Rover

Parte de locomoção, independente da Jetson.

| Componente | Detalhe |
|---|---|
| Bateria | LiFe 4S, 13,2 V, separada da bateria da Jetson |
| Controlador | Arduino, comandado por Bluetooth (app Serial Bluetooth Terminal) |
| Drivers | 5 RoboClaw 2x7A |
| Motores | 10 motores DC: 6 de tração e 4 de direção |
| Chassi | Suspensão rocker-bogie |

## Autores

- [Felipe Kenzo Ohara Sakae](https://github.com/Sakaef03)
- [Igor Improta Martinez da Silva](https://github.com/igor-ims)
- [Lucas Gozze Crapino](https://github.com/LucasCrapino)
- [Murillo Penha Strina](https://github.com/Murillo-Strina)
- [Vinicius Garcia Imendes Dechechi](https://github.com/vdechechi)
