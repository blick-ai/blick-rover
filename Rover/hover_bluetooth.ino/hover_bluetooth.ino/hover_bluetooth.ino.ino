// =============================================================================
// CONTROLE BLUETOOTH DO HOVER - Arduino Mega 2560 + HC-05
// =============================================================================
// Serial1 (pinos 18/19) -> RoboClaws (38400 baud)
// Serial2 (pinos 16/17) -> HC-05 Bluetooth (9600 baud)
// =============================================================================

#include "RoboClaw.h"

RoboClaw roboclaw(&Serial1, 1000);

// ---- Endereços das RoboClaws ----
#define TRACAO_1   128   // Tração - RoboClaw 1 (M1 + M2)
#define TRACAO_2   129   // Tração - RoboClaw 2 (M1 + M2)
#define TRACAO_3   130   // Tração - RoboClaw 3 (M1 + M2)
#define DIRECAO_F  131   // Direção frontal (M1 + M2)
#define DIRECAO_T  132   // Direção traseira (M1 + M2)

// ---- Parâmetros ajustáveis ----
#define VEL_TRACAO     80    // Velocidade de tração (0-127)
#define VEL_DIRECAO    30    // Velocidade dos motores de direção (0-127)
#define TEMPO_DIRECAO  300   // Tempo em ms que o motor de direção gira para posicionar

// ---- Estado atual ----
char comandoAtual = 'S';  // S = parado

void setup() {
  Serial.begin(9600);      // Monitor serial (debug via USB)
  roboclaw.begin(38400);   // RoboClaws na Serial1
  Serial2.begin(9600);     // HC-05 na Serial2 (pinos 16=TX2, 17=RX2)

  pararTudo();
  Serial.println("Hover pronto. Aguardando comandos Bluetooth...");
  Serial2.println("Hover conectado!");
}

void loop() {
  // Lê comando do Bluetooth
  if (Serial2.available()) {
    char cmd = Serial2.read();

    // Ignora caracteres de controle (newline, etc)
    if (cmd == '\n' || cmd == '\r') return;

    comandoAtual = cmd;
    executarComando(cmd);
  }
}

// =============================================================================
// EXECUÇÃO DOS COMANDOS
// =============================================================================
void executarComando(char cmd) {
  switch (cmd) {
    case 'F': case 'f':   // FRENTE
      Serial.println(">> FRENTE");
      moverFrente();
      break;

    case 'B': case 'b':   // TRÁS (RÉ)
      Serial.println(">> RÉ");
      moverTras();
      break;

    case 'L': case 'l':   // ESQUERDA
      Serial.println(">> ESQUERDA");
      virarEsquerda();
      break;

    case 'R': case 'r':   // DIREITA
      Serial.println(">> DIREITA");
      virarDireita();
      break;

    case 'G': case 'g':   // FRENTE + ESQUERDA
      Serial.println(">> FRENTE-ESQUERDA");
      virarEsquerda();
      moverFrente();
      break;

    case 'I': case 'i':   // FRENTE + DIREITA
      Serial.println(">> FRENTE-DIREITA");
      virarDireita();
      moverFrente();
      break;

    case 'H': case 'h':   // RÉ + ESQUERDA
      Serial.println(">> RÉ-ESQUERDA");
      virarEsquerda();
      moverTras();
      break;

    case 'J': case 'j':   // RÉ + DIREITA
      Serial.println(">> RÉ-DIREITA");
      virarDireita();
      moverTras();
      break;

    case 'S': case 's':   // PARAR
      Serial.println(">> PARAR");
      pararTudo();
      break;

    case '1':              // Velocidade baixa
      Serial.println(">> Velocidade BAIXA");
      // Você pode implementar níveis de velocidade aqui
      break;

    case '2':              // Velocidade média
      Serial.println(">> Velocidade MEDIA");
      break;

    case '3':              // Velocidade alta
      Serial.println(">> Velocidade ALTA");
      break;

    default:
      break;
  }
}

// =============================================================================
// FUNÇÕES DE TRAÇÃO (motores nas RoboClaws 128, 129, 130)
// =============================================================================

void moverFrente() {
  // Aciona os 6 motores de tração para frente
  for (int addr = TRACAO_1; addr <= TRACAO_3; addr++) {
    roboclaw.ForwardM1(addr, VEL_TRACAO);
    roboclaw.ForwardM2(addr, VEL_TRACAO);
  }
}

void moverTras() {
  // Aciona os 6 motores de tração para trás
  for (int addr = TRACAO_1; addr <= TRACAO_3; addr++) {
    roboclaw.BackwardM1(addr, VEL_TRACAO);
    roboclaw.BackwardM2(addr, VEL_TRACAO);
  }
}

void pararTracao() {
  for (int addr = TRACAO_1; addr <= TRACAO_3; addr++) {
    roboclaw.ForwardM1(addr, 0);
    roboclaw.ForwardM2(addr, 0);
  }
}

// =============================================================================
// FUNÇÕES DE DIREÇÃO (motores nas RoboClaws 131 e 132)
// =============================================================================
// Os motores "direcionadores" giram para posicionar o mecanismo de steering.
// Funcionam como servos de posição: giram por um tempo curto e param.
// =============================================================================

void virarEsquerda() {
  // Direção frontal e traseira giram para a esquerda
  roboclaw.ForwardM1(DIRECAO_F, VEL_DIRECAO);
  roboclaw.ForwardM2(DIRECAO_F, VEL_DIRECAO);
  roboclaw.ForwardM1(DIRECAO_T, VEL_DIRECAO);
  roboclaw.ForwardM2(DIRECAO_T, VEL_DIRECAO);

  delay(TEMPO_DIRECAO);  // Tempo para o mecanismo se posicionar

  // Para os motores de direção (já posicionados)
  pararDirecao();
}

void virarDireita() {
  // Direção frontal e traseira giram para a direita
  roboclaw.BackwardM1(DIRECAO_F, VEL_DIRECAO);
  roboclaw.BackwardM2(DIRECAO_F, VEL_DIRECAO);
  roboclaw.BackwardM1(DIRECAO_T, VEL_DIRECAO);
  roboclaw.BackwardM2(DIRECAO_T, VEL_DIRECAO);

  delay(TEMPO_DIRECAO);

  pararDirecao();
}

void centralizarDirecao() {
  // Aqui você pode implementar lógica para voltar ao centro
  // usando encoders ou fim-de-curso, se disponível
  pararDirecao();
}

void pararDirecao() {
  roboclaw.ForwardM1(DIRECAO_F, 0);
  roboclaw.ForwardM2(DIRECAO_F, 0);
  roboclaw.ForwardM1(DIRECAO_T, 0);
  roboclaw.ForwardM2(DIRECAO_T, 0);
}

// =============================================================================
// PARAR TUDO
// =============================================================================

void pararTudo() {
  pararTracao();
  pararDirecao();
}
