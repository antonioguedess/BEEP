import os
import shutil
import time
import pandas as pd
import matplotlib
import sys

# --- GRAPHICAL ENGINE SETUP ---
# Forces Matplotlib to use 'Qt5Agg', which is a high-performance backend.
# This is crucial for real-time plotting as it allows for interactive windows 
# and smoother frame updates compared to the default static backends.
try:
    matplotlib.use('Qt5Agg')
    import matplotlib.pyplot as plt
except ImportError:
    # Error handling if the required PyQt5 library is missing
    print("Error: PyQt5 not found. Please install it using 'pip install PyQt5'")
    sys.exit(1)

# --- SYSTEM CONFIGURATIONS ---
# FILE_PATH: The destination file where all your synchronized motor data will be saved.
# Using a descriptive name like '1h' suggests a long-duration endurance test.
FILE_PATH = 'teste_final_1h.csv'

# TEMP_PATH: A temporary buffer file, often used to prevent data loss 
# or for debugging purposes while the main file is being written.
TEMP_PATH = 'temp_plot_debug.csv'

# COLUMNS: Definitive header for the CSV file. 
# It MUST match the order of the 'printf' statement in your ESP32 C code.
# Includes: Time, Raw Positions (Pulse counts), Calculated RPMs, and the Sync Error (Degrees).
COLUMNS = ['timestamp_ms', 'pos1', 'pos2', 'vel1_rpm', 'vel2_rpm', 'erro_graus']

# Scale Parameters
REF_RPM, REF_ERRO = 200, 5
max_rpm_h, max_erro_h = REF_RPM, REF_ERRO

def setup_plots():
    # 1. Enable Matplotlib's Interactive Mode
    # This allows the GUI to update its frames dynamically without blocking 
    # the execution of the main data-acquisition loop.
    plt.ion()
    
    # 2. Layout Initialization
    # Creates a figure with two vertical subplots (axes).
    # ax1: Usually dedicated to high-level metrics (e.g., RPM or Position).
    # ax2: Dedicated to delta metrics (e.g., Synchronization Error in degrees).
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 8))
    fig.canvas.manager.set_window_title('BEEP Monitor - Debug Mode')
    
    # 3. Dynamic Text Overlay Initialization
    # These text objects act as digital "displays" within the charts.
    # 'transform=ax1.transAxes' pins the text to the plot's coordinate system (0.5 = middle).
    # 'bbox' creates a solid background (yellow) to ensure readability over moving lines.
    # 'zorder=10' ensures the text always stays on top of the plotted data.
    
    # Text display for the Upper Plot (ax1)
    t1 = ax1.text(0.5, 0.9, '', transform=ax1.transAxes, ha='center', 
                  bbox=dict(facecolor='yellow', alpha=0.9), weight='bold', zorder=10)
    
    # Text display for the Lower Plot (ax2)
    t2 = ax2.text(0.5, 0.9, '', transform=ax2.transAxes, ha='center', 
                  bbox=dict(facecolor='yellow', alpha=0.9), weight='bold', zorder=10)
    
    # Return the handles to the main loop so they can be modified with new data
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

            # 1. SAFE COPY: Copy the live CSV to a temp file to avoid access conflicts
            # with the process currently writing to the original file.
            try:
                shutil.copy2(FILE_PATH, TEMP_PATH)
            except IOError:
                time.sleep(0.05)
                continue

            # 2. OPTIMIZED FAST READ: Get only the last 5 rows for real-time text display
            # Usamos low_memory=False para performance
            df_recent = pd.read_csv(TEMP_PATH, names=COLUMNS, encoding='utf16', 
                                    engine='python', on_bad_lines='skip').tail(5)
            df_recent = df_recent.apply(pd.to_numeric, errors='coerce').dropna()

            if not df_recent.empty:
                ultima = df_recent.iloc[-1]
                t_s, v1, v2, err = ultima['timestamp_ms']/1000, ultima['vel1_rpm'], ultima['vel2_rpm'], ultima['erro_graus']

                # Update the on-screen digital displays (Yellow boxes)
                txt_v.set_text(f"M1: {v1:.2f} | M2: {v2:.2f} RPM")
                txt_e.set_text(f"ERRO: {err:.3f}º | T: {t_s:.1f}s")

                # 3. HISTORICAL PLOT UPDATE: Dynamic refresh rate to prevent lag
                # If test duration > 10min, slow down updates to handle large datasets
                now = time.time()
                update_interval = 3.0 if t_s < 600 else 10.0 # If >10min, lesser updates

                if now - last_graph_update > update_interval:
                    # Load full dataset for the complete trend lines
                    df_full = pd.read_csv(TEMP_PATH, names=COLUMNS, encoding='utf16', 
                                          engine='python', on_bad_lines='skip')
                    df_full = df_full.apply(pd.to_numeric, errors='coerce').dropna()
                    
                    if not df_full.empty:
                        # Dynamic Auto-scaling for Y-axis based on historical peaks
                        global max_rpm_h, max_erro_h
                        max_rpm_h = max(max_rpm_h, df_full['vel1_rpm'].abs().max(), df_full['vel2_rpm'].abs().max())
                        max_erro_h = max(max_erro_h, df_full['erro_graus'].abs().max())

                        # Clear old lines before redrawing (Optimized refresh)
                        for ax in [ax1, ax2]:
                            for line in ax.get_lines(): line.remove()
                        
                        # Plot 1: Velocity Comparison (Motor 1 vs Motor 2)
                        ax1.plot(df_full['timestamp_ms']/1000, df_full['vel1_rpm'], 'b-', alpha=0.5, linewidth=0.8)
                        ax1.plot(df_full['timestamp_ms']/1000, df_full['vel2_rpm'], 'r-', alpha=0.5, linewidth=0.8)
                        ax1.set_ylim(0, max_rpm_h * 1.2)
                        ax1.set_xlim(0, t_s * 1.1)
                        ax1.minorticks_on()
                        ax1.grid(True, which='major', alpha=0.7)
                        ax1.grid(True, which='minor', alpha=0.2, linestyle=':')

                        # Plot 2: Synchronization Error (Degrees)
                        ax2.plot(df_full['timestamp_ms']/1000, df_full['erro_graus'], 'g-', linewidth=0.8)
                        ax2.set_ylim(-max_erro_h * 1.2, max_erro_h * 1.2)
                        ax2.set_xlim(0, t_s * 1.1)
                        ax2.minorticks_on()
                        ax2.grid(True, which='major', alpha=0.7)
                        ax2.grid(True, which='minor', alpha=0.2, linestyle=':')
                        
                        last_graph_update = now

                # Force the canvas to update with new data
                fig.canvas.draw_idle()
                fig.canvas.flush_events()

            time.sleep(0.1) # Small delay to reduce CPU load

        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"Aviso de Debug: {e}")
            time.sleep(1)

    print("\nMonitorização terminada.")

if __name__ == "__main__":
    main()