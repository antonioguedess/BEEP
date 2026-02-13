import os
import time
import matplotlib
import sys
import serial
import serial.tools.list_ports
import threading
import socket
from datetime import datetime
from collections import Counter
import numpy as np

# --- CONFIGURAÇÕES DE COMUNICAÇÃO ---
BAUD_RATE = 921600
UDP_PORT = 12345  # Porta para o Wi-Fi
COLUMNS = ['timestamp_s', 'pos1', 'pos2', 'vel1_rpm', 'vel2_rpm', 'erro_graus', 'erro_us']

# --- CONFIGURAÇÕES DE PASTA E FICHEIRO ---
DATA_FOLDER = 'BEEP_data'
if not os.path.exists(DATA_FOLDER):
    os.makedirs(DATA_FOLDER) # Cria a pasta se não existir

timestamp_inicio = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
# O caminho agora aponta para dentro da pasta
FILE_PATH = os.path.join(DATA_FOLDER, f'BEEP_data_{timestamp_inicio}.csv')

# Listas globais para partilha entre Threads e Gráfico
t_zero = None
buffer_t = []
buffer_v1 = []
buffer_v2 = []
buffer_e = []
data_lock = threading.Lock() # Garante que não há conflito de escrita/leitura

# Força o motor gráfico PyQt5 para alta performance
try:
    matplotlib.use('Qt5Agg')
    import matplotlib.pyplot as plt
except ImportError:
    print("Erro: PyQt5 não encontrado. Instale com 'pip install PyQt5'")
    sys.exit(1)

# --- FUNÇÃO DE AUTO-DETEÇÃO DE PORTA USB ---
def find_esp32_port():
    ports = list(serial.tools.list_ports.comports())
    for p in ports:
        desc = p.description.upper()
        if any(x in desc for x in ["CP210", "CH340", "USB SERIAL"]):
            return p.device
    return None

# --- LOGGER VIA USB (SERIAL) ---
def serial_logger(port):
    global buffer_t, buffer_v1, buffer_v2, buffer_e
    try:
        ser = serial.Serial(port, BAUD_RATE, timeout=0.01)
        ser.set_buffer_size(rx_size=128000, tx_size=128000)
        ser.reset_input_buffer()
        
        last_valid_t = 0
        error_samples = []
        stable_error_us = None
        calibration_limit = 500 

        with open(FILE_PATH, 'a', encoding='utf-16') as f:
            while True:
                if ser.in_waiting > 500:
                    raw = ser.read(ser.in_waiting).decode('utf-8', errors='ignore')
                    chunks = raw.split('$')[1:] 
                    
                    for chunk in chunks:
                        try:
                            line = chunk.split('\n')[0].strip()
                            parts = line.split(',')
                            if len(parts) < 7: continue
                            
                            val = [float(x) for x in parts]
                            current_err_us = val[6]

                            # 1. Calibração do erro estável
                            if stable_error_us is None:
                                error_samples.append(current_err_us)
                                if len(error_samples) >= calibration_limit:
                                    stable_error_us = Counter(error_samples).most_common(1)[0][0]
                                    print(f"[*] Estabilidade em: {stable_error_us} us")
                                continue 

                            # 2. Filtro de Jitter (Tolerância de 2us)
                            if abs(current_err_us - stable_error_us) > 2:
                                continue

                            # 3. CÁLCULO DO OFFSET DINÂMICO
                            # Aplicando a tua fórmula: (current_err - 1) / 4
                            offset_us = (current_err_us - 1.0) / 4.0
                            dt_s = offset_us / 1000000.0
                            
                            # 4. COMPENSAÇÃO DA POSIÇÃO 2
                            # v2_deg_s = RPM * 6
                            v2_deg_s = val[4] * 6.0
                            pos2_corrigida = val[2] - (v2_deg_s * dt_s)
                            
                            # Erro angular real (Pos1 - Pos2_corrigida)
                            erro_angular_real = val[1] - pos2_corrigida

                            t_s = val[0] / 1000000.0
                            if t_s <= last_valid_t or t_s > last_valid_t + 2.0:
                                continue
                            
                            last_valid_t = t_s
                            
                            # Gravação com Pos2 e Erro corrigidos
                            f.write(f"{t_s:.6f},{val[1]:.2f},{pos2_corrigida:.4f},{val[3]:.2f},{val[4]:.2f},{erro_angular_real:.4f},{val[6]}\n")
                            
                            with data_lock:
                                buffer_t.append(t_s)
                                buffer_v1.append(val[3])
                                buffer_v2.append(val[4])
                                buffer_e.append(erro_angular_real)
                                
                                if len(buffer_t) > 2000:
                                    buffer_t.pop(0)
                                    buffer_v1.pop(0)
                                    buffer_v2.pop(0)
                                    buffer_e.pop(0)
                        except:
                            continue
                    f.flush()
                else:
                    time.sleep(0.001)
    except Exception as e: print(f"Erro: {e}")

# --- LOGGER VIA WI-FI (UDP) ---
def udp_logger():
    global buffer_t, buffer_v1, buffer_v2, buffer_e
    print(f"[WI-FI] Ouvindo na porta UDP {UDP_PORT}...")
    
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(("0.0.0.0", UDP_PORT))
        
        # --- Variáveis para Calibração e Correção ---
        last_valid_t = 0
        error_samples = []
        stable_error_us = None
        calibration_limit = 500  # Amostras para detetar o valor estável (moda)
        
        with open(FILE_PATH, 'a', encoding='utf-16') as f:
            while True:
                # Receção dos dados via UDP
                data, addr = sock.recvfrom(4096)
                raw_data = data.decode('utf-8', errors='ignore')
                
                # Sincronização pelo marcador '$' (ignora o primeiro elemento se estiver incompleto)
                chunks = raw_data.split('$')[1:]
                
                for chunk in chunks:
                    try:
                        # Limpeza da linha e separação dos valores
                        line = chunk.split('\n')[0].strip()
                        parts = line.split(',')
                        if len(parts) < 7: continue
                        
                        val = [float(x.strip()) for x in parts]
                        # Estrutura: [0]t_us, [1]pos1, [2]pos2, [3]v1, [4]v2, [5]err_g, [6]err_us
                        current_err_us = val[6]

                        # 1. FASE DE CALIBRAÇÃO: Identifica o erro_us mais frequente
                        if stable_error_us is None:
                            error_samples.append(current_err_us)
                            if len(error_samples) >= calibration_limit:
                                stable_error_us = Counter(error_samples).most_common(1)[0][0]
                                print(f"[*] UDP: Estabilidade detetada em {stable_error_us} us")
                            continue

                        # 2. FILTRAGEM DE JITTER: Aceita apenas amostras estáveis (+/- 2us)
                        if abs(current_err_us - stable_error_us) > 2:
                            continue

                        # 3. CÁLCULO DO OFFSET DINÂMICO (Tua fórmula)
                        # O desfasamento entre Encoder 1 e 2 é (erro_total - 1) / 4
                        offset_us = (current_err_us - 1.0) / 4.0
                        dt_s = offset_us / 1000000.0
                        
                        # Correção da posição 2 (v2_rpm * 6 = graus por segundo)
                        v2_deg_s = val[4] * 6.0
                        pos2_corrigida = val[2] - (v2_deg_s * dt_s)
                        
                        # Recálculo do erro angular real sincronizado
                        erro_angular_real = val[1] - pos2_corrigida

                        tempo_decimal = val[0] / 1000000.0

                        # 4. FILTRO DE SEGURANÇA TEMPORAL
                        if tempo_decimal <= last_valid_t or tempo_decimal > last_valid_t + 2.0:
                            continue
                        last_valid_t = tempo_decimal

                        # Gravação dos dados corrigidos no CSV
                        nova_linha = f"{tempo_decimal:.6f},{val[1]:.2f},{pos2_corrigida:.4f},{val[3]:.2f},{val[4]:.2f},{erro_angular_real:.4f},{val[6]}"
                        f.write(nova_linha + "\n")
                        
                        # Atualização dos buffers para o gráfico (respeitando o data_lock)
                        with data_lock:
                            buffer_t.append(tempo_decimal)
                            buffer_v1.append(val[3])
                            buffer_v2.append(val[4])
                            buffer_e.append(erro_angular_real)
                            
                            # Limitação do tamanho do buffer para performance
                            if len(buffer_t) > 2000:
                                buffer_t.pop(0)
                                buffer_v1.pop(0)
                                buffer_v2.pop(0)
                                buffer_e.pop(0)

                    except Exception:
                        continue
                
                # Garante escrita no disco sem esperar pelo fecho do ficheiro
                f.flush()
                
    except Exception as e: 
        print(f"Erro UDP: {e}")

# Parâmetros globais de escala para o gráfico
REF_RPM, REF_ERRO = 200, 5
max_rpm_h, max_erro_h = REF_RPM, REF_ERRO

def setup_plots():
    plt.ion()
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 8))
    fig.canvas.manager.set_window_title(f'BEEP Monitor Híbrido - {FILE_PATH}')
    
    # --- Configuração do Gráfico 1 (Velocidade) ---
    ax1.set_ylabel("RPM")
    ax1.grid(True, which='both', linestyle='--', alpha=0.5) # Adiciona a grelha
    ax1.set_axisbelow(True) # Grelha para trás das linhas
    ax1.set_ylim(-10, max_rpm_h * 1.2)
    ax1.set_xlim(0, 10.0)
    
    # --- Configuração do Gráfico 2 (Erro) ---
    ax2.set_ylabel("Erro (ticks)")
    ax2.set_xlabel("Tempo (s)")
    ax2.grid(True, which='both', linestyle=':', alpha=0.5) # Adiciona a grelha
    ax2.set_axisbelow(True) # Grelha para trás das linhas
    ax2.set_ylim(-max_erro_h * 1.2, max_erro_h * 1.2)
    ax2.set_xlim(0, 10.0)

    # Mantendo os nomes t1 e t2 para os teus textos
    t1 = ax1.text(0.5, 0.9, '', transform=ax1.transAxes, ha='center', 
                  bbox=dict(facecolor='yellow', alpha=0.9), weight='bold', zorder=10)
    t2 = ax2.text(0.5, 0.9, '', transform=ax2.transAxes, ha='center', 
                  bbox=dict(facecolor='yellow', alpha=0.9), weight='bold', zorder=10)
    
    return fig, ax1, ax2, t1, t2

def main():

    global t_zero, buffer_t, buffer_v1, buffer_v2, buffer_e

    # Reset total de dados ao iniciar

    with data_lock:
        buffer_t.clear()
        buffer_v1.clear()
        buffer_v2.clear()
        buffer_e.clear()
        t_zero = None     # Força o reset do tempo para esta sessão

    # Cria o ficheiro inicial

    with open(FILE_PATH, 'w', encoding='utf-16') as f:
        f.write(",".join(COLUMNS) + "\n")

    # Inicia as duas vias de comunicação
    threading.Thread(target=udp_logger, daemon=True).start()

    usb_port = find_esp32_port()

    if usb_port:
        threading.Thread(target=serial_logger, args=(usb_port,), daemon=True).start()
    else:
        print("[AVISO] Cabo USB não detetado. A aguardar apenas por Wi-Fi.")

    fig, ax1, ax2, txt_v, txt_e = setup_plots()
    last_graph_update = 0
    last_text_update = 0
    max_rpm_h, max_erro_h = 200, 3

    buffer_t.clear()   # Limpa dados de execuções anteriores
    buffer_v1.clear()
    buffer_v2.clear()
    buffer_e.clear()
    t_zero = None      # Força o reset do tempo para esta sessão

    while plt.fignum_exists(fig.number):
        try:
            now = time.time()
            # 1. Verifica se temos dados
            with data_lock:
                if not buffer_t:
                    plt.pause(0.1)
                    continue

                # Sincroniza o tempo inicial
                if t_zero is None: t_zero = buffer_t[0]

                # Dados mais recentes para o texto
                v1_atual = buffer_v1[-1]
                v2_atual = buffer_v2[-1]
                e_atual = buffer_e[-1]
                t_rel = (buffer_t[-1] - t_zero)

            # 2. ATUALIZAÇÃO DO TEXTO (Definir na condição abaixo a periodicidade de atualização)
            if now - last_text_update >= 0.1:
                txt_v.set_text(f"M1: {v1_atual:.2f} | M2: {v2_atual:.2f} RPM")
                txt_e.set_text(f"ERRO: {e_atual:.3f}º | T: {t_rel:.3f}s")
                last_text_update = now

            # 3. ATUALIZAÇÃO DO GRÁFICO (Definir na condição abaixo a periodicidade de atualização)
            if now - last_graph_update >= 1:
                with data_lock:
                    idx = -200 # Últimos 500 pontos
                    # Criamos cópias locais para o plot não travar a receção
                    t_slice = buffer_t[idx:]
                    v1_plot = buffer_v1[idx:]
                    v2_plot = buffer_v2[idx:]
                    e_plot = buffer_e[idx:]

                t_plot = [(x - t_zero) for x in t_slice]

                for ax in [ax1, ax2]:
                    for line in ax.get_lines(): line.remove()

                ax1.plot(t_plot, v1_plot, 'b-', alpha=0.7, label='M1')
                ax1.plot(t_plot, v2_plot, 'r-', alpha=0.7, label='M2')
                ax2.plot(t_plot, e_plot, 'g-', linewidth=1)

                # Escala dinâmica do Eixo X
                t_max = t_plot[-1]
                ax1.set_xlim(max(0, t_max - 15), t_max + 1)
                ax2.set_xlim(max(0, t_max - 15), t_max + 1)

                fig.canvas.draw_idle()
                last_graph_update = now

            plt.pause(0.01)


        except Exception as e:
            print(f"Erro no Loop: {e}")
            plt.pause(0.1)

    print(f"\nSessão terminada. Dados guardados em: {FILE_PATH}")

if __name__ == "__main__":
    main()