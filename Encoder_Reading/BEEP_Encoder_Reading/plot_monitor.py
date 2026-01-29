import os
import shutil
import time
import pandas as pd
import matplotlib
import sys
import serial
import serial.tools.list_ports
import threading
import socket
from datetime import datetime
#import pyqtgraph as pg
#from PyQt5 import QtWidgets, QtCore

# --- CONFIGURAÇÕES DE COMUNICAÇÃO ---
BAUD_RATE = 115200
UDP_PORT = 12345  # Porta para o Wi-Fi
timestamp_inicio = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
FILE_PATH = f'BEEP_data_{timestamp_inicio}.csv'
TEMP_PATH = 'temp_plot_debug.csv'
COLUMNS = ['timestamp_ms', 'pos1', 'pos2', 'vel1_rpm', 'vel2_rpm', 'erro_graus']

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
    print(f"[SERIAL] Conectado em {port}...")
    try:
        ser = serial.Serial(port, BAUD_RATE, timeout=1)
        with open(FILE_PATH, 'a', encoding='utf-16') as f:
            while True:
                line = ser.readline().decode('utf-8', errors='ignore').strip()
                if line and "," in line:
                    # Se a linha começar com "I (" ou "timestamp", ignoramos
                    if line.startswith("I (") or "timestamp" in line:
                        continue
                        
                    f.write(line + "\n")
                    f.flush()
                    try:
                        valores = [float(x.strip()) for x in line.split(',')]
                        # Só adicionamos se tivermos os 6 campos e o primeiro for o timestamp
                        if len(valores) == 6:
                            with data_lock:
                                buffer_t.append(valores[0])
                                buffer_v1.append(valores[3])
                                buffer_v2.append(valores[4])
                                buffer_e.append(valores[5])
                    except ValueError:
                        continue
    except Exception as e: print(f"[SERIAL] Erro: {e}")

# --- LOGGER VIA WI-FI (UDP) ---
def udp_logger():
    global buffer_t, buffer_v1, buffer_v2, buffer_e
    print(f"[WI-FI] Ouvindo na porta UDP {UDP_PORT}...")
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(("0.0.0.0", UDP_PORT))
        # O ficheiro continua a ser gravado para a dissertação
        with open(FILE_PATH, 'a', encoding='utf-16') as f:
            while True:
                data, addr = sock.recvfrom(1024)
                line = data.decode('utf-8').strip()
                if line and "," in line:
                    # Se a linha começar com "I (" ou "timestamp", ignoramos
                    if line.startswith("I (") or "timestamp" in line:
                        continue
                        
                    f.write(line + "\n")
                    f.flush()
                    try:
                        valores = [float(x.strip()) for x in line.split(',')]
                        # Só adicionamos se tivermos os 6 campos e o primeiro for o timestamp
                        if len(valores) == 6:
                            with data_lock:
                                buffer_t.append(valores[0])
                                buffer_v1.append(valores[3])
                                buffer_v2.append(valores[4])
                                buffer_e.append(valores[5])
                    except ValueError:
                        continue
    except Exception as e: print(f"Erro UDP: {e}")

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
                t_rel = (buffer_t[-1] - t_zero) / 1000

            # 2. ATUALIZAÇÃO DO TEXTO (Definir na condição abaixo a periodicidade de atualização)
            if now - last_text_update >= 0.1:
                txt_v.set_text(f"M1: {v1_atual:.2f} | M2: {v2_atual:.2f} RPM")
                txt_e.set_text(f"ERRO: {e_atual:.3f}º | T: {t_rel:.1f}s")
                last_text_update = now

            # 3. ATUALIZAÇÃO DO GRÁFICO (Definir na condição abaixo a periodicidade de atualização)
            if now - last_graph_update >= 0.5:
                with data_lock:
                    idx = -500 # Últimos 500 pontos
                    # Criamos cópias locais para o plot não travar a receção
                    t_plot = [(x - t_zero)/1000 for x in buffer_t[idx:]]
                    v1_plot = buffer_v1[idx:]
                    v2_plot = buffer_v2[idx:]
                    e_plot = buffer_e[idx:]

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

            plt.pause(0.001)

        except Exception as e:
            print(f"Erro no Loop: {e}")
            plt.pause(0.1)

    print(f"\nSessão terminada. Dados guardados em: {FILE_PATH}")

if __name__ == "__main__":
    main()