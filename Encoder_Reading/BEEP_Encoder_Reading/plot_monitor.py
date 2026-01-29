import os
import shutil
import time
import pandas as pd
import matplotlib
import sys
import serial
import serial.tools.list_ports
import threading
from datetime import datetime

# --- CONFIGURAÇÕES DE COMUNICAÇÃO ---
#SERIAL_PORT = 'COM11'  # <--- ALTERA PARA A TUA PORTA (ex: 'COM4' ou '/dev/ttyUSB0')
BAUD_RATE = 115200

# Nome dinâmico do ficheiro baseado no momento de arranque
timestamp_inicio = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
FILE_PATH = f'BEEP_data_{timestamp_inicio}.csv'
TEMP_PATH = 'temp_plot_debug.csv'
COLUMNS = ['timestamp_ms', 'pos1', 'pos2', 'vel1_rpm', 'vel2_rpm', 'erro_graus']

def find_esp32_port():
    """Tenta detetar automaticamente a porta COM do ESP32"""
    ports = list(serial.tools.list_ports.comports())
    
    # 1. Procura por descrições comuns de drivers de ESP32
    for p in ports:
        desc = p.description.upper()
        if "CP210" in desc or "CH340" in desc or "USB SERIAL" in desc:
            print(f"ESP32 detetado na porta: {p.device} ({p.description})")
            return p.device
            
    # 2. Se não encontrar pelo nome, tenta a última porta da lista (mais provável ser a correta)
    if ports:
        last_port = ports[-1].device
        print(f"Aviso: ESP32 não identificado pelo nome. Tentando a porta: {last_port}")
        return last_port
    
    return None

# Força o motor gráfico PyQt5 para alta performance
try:
    matplotlib.use('Qt5Agg')
    import matplotlib.pyplot as plt
except ImportError:
    print("Erro: PyQt5 não encontrado. Instale com 'pip install PyQt5'")
    sys.exit(1)

# Parâmetros globais de escala para o gráfico
REF_RPM, REF_ERRO = 200, 5
max_rpm_h, max_erro_h = REF_RPM, REF_ERRO

# --- TAREFA DE LEITURA SÉRIE (THREAD) ---
# --- TAREFA DE LEITURA SÉRIE (THREAD) ---
def serial_logger(port):
    print(f"Lendo {port} e gravando em {FILE_PATH}...")
    try:
        ser = serial.Serial(port, BAUD_RATE, timeout=1)
        with open(FILE_PATH, 'w', encoding='utf-16') as f:
            f.write(",".join(COLUMNS) + "\n")
            while True:
                line = ser.readline().decode('utf-8', errors='ignore').strip()
                if line and "," in line:
                    f.write(line + "\n")
                    f.flush()
    except Exception as e:
        print(f"\n[ERRO SERIAL]: {e}")

def setup_plots():
    plt.ion()
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 8))
    fig.canvas.manager.set_window_title(f'Monitorização: {FILE_PATH}')
    
    # Displays de texto sobrepostos aos gráficos
    t1 = ax1.text(0.5, 0.9, '', transform=ax1.transAxes, ha='center', 
                  bbox=dict(facecolor='yellow', alpha=0.9), weight='bold', zorder=10)
    t2 = ax2.text(0.5, 0.9, '', transform=ax2.transAxes, ha='center', 
                  bbox=dict(facecolor='yellow', alpha=0.9), weight='bold', zorder=10)
    return fig, ax1, ax2, t1, t2

def main():
    # 1. Detetar porta automaticamente
    target_port = find_esp32_port()
    if not target_port:
        print("Erro: Nenhum dispositivo série encontrado. Verifica a ligação USB.")
        return

    t_zero = None
    
    # 2. Inicia a Thread com a porta detetada
    thread = threading.Thread(target=serial_logger, args=(target_port,), daemon=True)
    thread.start()

    fig, ax1, ax2, txt_v, txt_e = setup_plots()
    last_graph_update = 0

    while plt.fignum_exists(fig.number):
        try:
            if not os.path.exists(FILE_PATH):
                time.sleep(0.5)
                continue

            shutil.copy2(FILE_PATH, TEMP_PATH)
            df_recent = pd.read_csv(TEMP_PATH, names=COLUMNS, encoding='utf-16', 
                                    engine='python', on_bad_lines='skip', skiprows=1).tail(5)
            df_recent = df_recent.apply(pd.to_numeric, errors='coerce').dropna()

            if not df_recent.empty:
                if t_zero is None:
                    t_zero = df_recent.iloc[0]['timestamp_ms']

                ultima = df_recent.iloc[-1]
                t_rel = (ultima['timestamp_ms'] - t_zero) / 1000
                v1, v2, err = ultima['vel1_rpm'], ultima['vel2_rpm'], ultima['erro_graus']

                txt_v.set_text(f"M1: {v1:.2f} | M2: {v2:.2f} RPM")
                txt_e.set_text(f"ERRO: {err:.3f}º | T_Rel: {t_rel:.1f}s")

                now = time.time()
                update_interval = 2.0 if t_rel < 600 else 10.0 

                if now - last_graph_update > update_interval:
                    df_full = pd.read_csv(TEMP_PATH, names=COLUMNS, encoding='utf-16', 
                                          engine='python', on_bad_lines='skip', skiprows=1)
                    df_full = df_full.apply(pd.to_numeric, errors='coerce').dropna()
                    
                    if not df_full.empty:
                        df_full['time_s'] = (df_full['timestamp_ms'] - t_zero) / 1000
                        global max_rpm_h, max_erro_h
                        max_rpm_h = max(max_rpm_h, df_full['vel1_rpm'].abs().max(), df_full['vel2_rpm'].abs().max())
                        max_erro_h = max(max_erro_h, df_full['erro_graus'].abs().max())

                        for ax in [ax1, ax2]:
                            for line in ax.get_lines(): line.remove()
                        
                        ax1.plot(df_full['time_s'], df_full['vel1_rpm'], 'b-', alpha=0.6, linewidth=0.8)
                        ax1.plot(df_full['time_s'], df_full['vel2_rpm'], 'r-', alpha=0.6, linewidth=0.8)
                        ax1.set_ylim(0, max_rpm_h * 1.2)
                        ax1.set_xlim(0, t_rel * 1.1)
                        ax1.grid(True, alpha=0.3)

                        ax2.plot(df_full['time_s'], df_full['erro_graus'], 'g-', linewidth=0.8)
                        ax2.set_ylim(-max_erro_h * 1.2, max_erro_h * 1.2)
                        ax2.set_xlim(0, t_rel * 1.1)
                        ax2.grid(True, alpha=0.3)
                        
                        last_graph_update = now

                fig.canvas.draw_idle()
                fig.canvas.flush_events()

            time.sleep(0.1)
        except Exception:
            continue

    print(f"\nSessão terminada. Dados guardados em: {FILE_PATH}")

if __name__ == "__main__":
    main()