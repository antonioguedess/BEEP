import os
import shutil
import time
import pandas as pd
import matplotlib
import sys

# Força o motor gráfico PyQt5
try:
    matplotlib.use('Qt5Agg')
    import matplotlib.pyplot as plt
except ImportError:
    print("Erro: PyQt5 não encontrado. Instale com 'pip install PyQt5'")
    sys.exit(1)

# --- CONFIGURAÇÕES ---
FILE_PATH = 'teste_final_1h.csv'
TEMP_PATH = 'temp_plot_debug.csv'
COLUMNS = ['timestamp_ms', 'pos1', 'pos2', 'vel1_rpm', 'vel2_rpm', 'erro_graus']

# Parâmetros de Escala
REF_RPM, REF_ERRO = 200, 5
max_rpm_h, max_erro_h = REF_RPM, REF_ERRO

def setup_plots():
    plt.ion()
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 8))
    fig.canvas.manager.set_window_title('BEEP Monitor - Debug Mode')
    
    # Inicializa os textos
    t1 = ax1.text(0.5, 0.9, '', transform=ax1.transAxes, ha='center', 
                  bbox=dict(facecolor='yellow', alpha=0.9), weight='bold', zorder=10)
    t2 = ax2.text(0.5, 0.9, '', transform=ax2.transAxes, ha='center', 
                  bbox=dict(facecolor='yellow', alpha=0.9), weight='bold', zorder=10)
    return fig, ax1, ax2, t1, t2

def main():
    fig, ax1, ax2, txt_v, txt_e = setup_plots()
    last_graph_update = 0
    
    print(f"Monitorização preparada para: {FILE_PATH}")
    print("A aguardar criação do ficheiro pelo ESP32...")

    while plt.fignum_exists(fig.number):
        try:
            if not os.path.exists(FILE_PATH):
                time.sleep(1)
                continue

            # 1. CÓPIA SEGURA (Tenta várias vezes se o ficheiro estiver bloqueado)
            try:
                shutil.copy2(FILE_PATH, TEMP_PATH)
            except IOError:
                time.sleep(0.05)
                continue

            # 2. LEITURA OTIMIZADA (Lê apenas o essencial para o Live Data)
            # Usamos low_memory=False para performance
            df_recent = pd.read_csv(TEMP_PATH, names=COLUMNS, encoding='utf16', 
                                    engine='python', on_bad_lines='skip').tail(5)
            df_recent = df_recent.apply(pd.to_numeric, errors='coerce').dropna()

            if not df_recent.empty:
                ultima = df_recent.iloc[-1]
                t_s, v1, v2, err = ultima['timestamp_ms']/1000, ultima['vel1_rpm'], ultima['vel2_rpm'], ultima['erro_graus']

                # Atualiza Texto Instantâneo
                txt_v.set_text(f"M1: {v1:.2f} | M2: {v2:.2f} RPM")
                txt_e.set_text(f"ERRO: {err:.3f}º | T: {t_s:.1f}s")

                # 3. ATUALIZAÇÃO DO GRÁFICO HISTÓRICO (Regra de cadência)
                # Aumentamos o intervalo se o ficheiro ficar muito grande para evitar lag
                now = time.time()
                update_interval = 3.0 if t_s < 600 else 10.0 # Se >10min, atualiza menos vezes

                if now - last_graph_update > update_interval:
                    df_full = pd.read_csv(TEMP_PATH, names=COLUMNS, encoding='utf16', 
                                          engine='python', on_bad_lines='skip')
                    df_full = df_full.apply(pd.to_numeric, errors='coerce').dropna()
                    
                    if not df_full.empty:
                        # Atualiza escalas
                        global max_rpm_h, max_erro_h
                        max_rpm_h = max(max_rpm_h, df_full['vel1_rpm'].abs().max(), df_full['vel2_rpm'].abs().max())
                        max_erro_h = max(max_erro_h, df_full['erro_graus'].abs().max())

                        # Redesenho Otimizado
                        for ax in [ax1, ax2]:
                            for line in ax.get_lines(): line.remove()
                        
                        # Gráfico 1
                        ax1.plot(df_full['timestamp_ms']/1000, df_full['vel1_rpm'], 'b-', alpha=0.5, linewidth=0.8)
                        ax1.plot(df_full['timestamp_ms']/1000, df_full['vel2_rpm'], 'r-', alpha=0.5, linewidth=0.8)
                        ax1.set_ylim(0, max_rpm_h * 1.2)
                        ax1.set_xlim(0, t_s * 1.1)
                        ax1.minorticks_on()
                        ax1.grid(True, which='major', alpha=0.7)
                        ax1.grid(True, which='minor', alpha=0.2, linestyle=':')

                        # Gráfico 2
                        ax2.plot(df_full['timestamp_ms']/1000, df_full['erro_graus'], 'g-', linewidth=0.8)
                        ax2.set_ylim(-max_erro_h * 1.2, max_erro_h * 1.2)
                        ax2.set_xlim(0, t_s * 1.1)
                        ax2.minorticks_on()
                        ax2.grid(True, which='major', alpha=0.7)
                        ax2.grid(True, which='minor', alpha=0.2, linestyle=':')
                        
                        last_graph_update = now

                fig.canvas.draw_idle()
                fig.canvas.flush_events()

            time.sleep(0.1)

        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"Aviso de Debug: {e}")
            time.sleep(1)

    print("\nMonitorização terminada.")

if __name__ == "__main__":
    main()