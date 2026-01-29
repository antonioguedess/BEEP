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

# --- CONFIGURAÇÕES DE COMUNICAÇÃO ---
BAUD_RATE = 115200
UDP_PORT = 12345  # Porta para o Wi-Fi
timestamp_inicio = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
FILE_PATH = f'BEEP_data_{timestamp_inicio}.csv'
TEMP_PATH = 'temp_plot_debug.csv'
COLUMNS = ['timestamp_ms', 'pos1', 'pos2', 'vel1_rpm', 'vel2_rpm', 'erro_graus']

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
    print(f"[SERIAL] Tentando conectar em {port}...")
    try:
        ser = serial.Serial(port, BAUD_RATE, timeout=1)
        with open(FILE_PATH, 'a', encoding='utf-16') as f: # 'a' para não apagar dados do Wi-Fi
            while True:
                line = ser.readline().decode('utf-8', errors='ignore').strip()
                if line and "," in line:
                    f.write(line + "\n")
                    f.flush()
    except Exception as e:
        print(f"[SERIAL] Desconectado ou Erro: {e}")

# --- LOGGER VIA WI-FI (UDP) ---
def udp_logger():
    print(f"[WI-FI] Ouvindo na porta UDP {UDP_PORT}...")
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(("0.0.0.0", UDP_PORT))
        with open(FILE_PATH, 'a', encoding='utf-16') as f:
            while True:
                data, addr = sock.recvfrom(1024)
                line = data.decode('utf-8').strip()
                if line and "," in line:
                    f.write(line + "\n")
                    f.flush()
    except Exception as e:
        print(f"[WI-FI] Erro: {e}")

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
    t_zero = None
    last_graph_update = 0
    max_rpm_h, max_erro_h = 200, 3

    while plt.fignum_exists(fig.number):
        try:
            if not os.path.exists(FILE_PATH):
                time.sleep(0.5)
                continue

            shutil.copy2(FILE_PATH, TEMP_PATH)
            
            # Leitura rápida para os displays
            df = pd.read_csv(TEMP_PATH, names=COLUMNS, encoding='utf-16', 
                             engine='python', on_bad_lines='skip', skiprows=1).tail(5)
            df = df.apply(pd.to_numeric, errors='coerce').dropna()

            if not df.empty:
                if t_zero is None: t_zero = df.iloc[0]['timestamp_ms']
                
                ultima = df.iloc[-1]
                t_rel = (ultima['timestamp_ms'] - t_zero) / 1000
                
                txt_v.set_text(f"M1: {ultima['vel1_rpm']:.2f} | M2: {ultima['vel2_rpm']:.2f} RPM")
                txt_e.set_text(f"ERRO: {ultima['erro_graus']:.3f}º | T: {t_rel:.1f}s")

                # Atualização do gráfico histórico
                now = time.time()
                if now - last_graph_update > 2.0:
                    df_full = pd.read_csv(TEMP_PATH, names=COLUMNS, encoding='utf-16', 
                                         engine='python', on_bad_lines='skip', skiprows=1).dropna()
                    df_full = df_full.apply(pd.to_numeric, errors='coerce')
                    df_full['time_s'] = (df_full['timestamp_ms'] - t_zero) / 1000

                    max_rpm_h = max(max_rpm_h, df_full['vel1_rpm'].abs().max(), df_full['vel2_rpm'].abs().max())
                    max_erro_h = max(max_erro_h, df_full['erro_graus'].abs().max())

                    for ax in [ax1, ax2]:
                        for line in ax.get_lines(): line.remove()
                    
                    ax1.plot(df_full['time_s'], df_full['vel1_rpm'], 'b-', alpha=0.6, linewidth=0.8)
                    ax1.plot(df_full['time_s'], df_full['vel2_rpm'], 'r-', alpha=0.6, linewidth=0.8)
                    ax1.set_ylim(-10, max_rpm_h * 1.2)
                    ax1.set_xlim(0, max(1.0, t_rel * 1.1))
                    
                    ax2.plot(df_full['time_s'], df_full['erro_graus'], 'g-', linewidth=0.8)
                    ax2.set_ylim(-max_erro_h * 1.2, max_erro_h * 1.2)
                    ax2.set_xlim(0, max(1.0, t_rel * 1.1))
                    
                    last_graph_update = now

            fig.canvas.draw_idle()
            fig.canvas.flush_events()
            time.sleep(0.1)

        except Exception:
            continue

    print(f"\nSessão terminada. Dados guardados em: {FILE_PATH}")

if __name__ == "__main__":
    main()