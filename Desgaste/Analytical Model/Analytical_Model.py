import numpy as np
import pandas as pd
from pysr import PySRRegressor

# --- Carregar dados do CSV ---
df = pd.read_csv(r'C:\Prog_Diss\Desgaste\Dataset\Dataset.csv', delimiter=';', decimal=',')

# --- Geometria das Rodas (constantes) ---
m      = 2               # módulo (mm)
z      = 30              # número de dentes
b      = 15              # largura da face (mm)
alpha  = np.radians(20)  # ângulo de pressão (rad)
E_star = 3100            # módulo reduzido do POM (MPa)

# --- Raio primitivo ---
r = (m * z) / 2          # = 30 mm

# --- Variáveis por ensaio (extraídas do CSV) ---
N = df['N (x10^5)'].values * 1e5  # converter para ciclos
torque = df['Torque (Nm)'].values * 1000  # converter para N·mm
n = df['n (rpm)'].values  # velocidade de rotação (rpm)
T_molde = df['Temp (ºC)'].values  # temperatura (°C)
desgaste_linear = df['a (mm)'].values  # em mm

# --- Restrição: apenas dados com a <= 0.2*m ---
mascara = df['a (mm)'] <= 0.2 * m
N = N[mascara]
torque = torque[mascara]
n = n[mascara]
T_molde = T_molde[mascara]
desgaste_linear = desgaste_linear[mascara]

# --- Cálculos derivados ---
Ft     = torque / r                           # força tangencial (N)
omega  = 2 * np.pi * n / 60                   # velocidade angular (rad/s)
v_slip = omega * r * np.sin(alpha)            # velocidade de escorregamento (mm/s)
R_star = r * np.sin(alpha) / 2                # raio equivalente de curvatura (mm)
P_hz   = np.sqrt((Ft * E_star) /
                 (np.pi * b * R_star))        # pressão de Hertz (MPa)

# --- Matriz de entradas (sem normalização) ---
X = np.column_stack([N, P_hz, v_slip, T_molde])
y = desgaste_linear   # (µm) — variável alvo

# --- PySR ---
model = PySRRegressor(
    niterations=500,
    binary_operators=["+", "-", "*", "/"],
    unary_operators=["sqrt", "square", "log", "exp"],
    populations=50,
    parsimony=0.0005,
    maxsize=30,
    verbosity=0
)

model.fit(X, y, variable_names=["Ncycles", "Phz", "vslip", "Tmolde"])

# --- Resultados ---
print("\n" + "="*70)
print("RESULTADO DA ANÁLISE DE REGRESSÃO SIMBÓLICA (PySR)")
print("="*70)

# Melhor equação
melhor_eq = model.sympy()
y_pred = model.predict(X)
mse = np.mean((y - y_pred)**2)

# Substituir nomes internos pelos nomes legíveis
nomes_mapping = {"Ncycles": "N", "Phz": "P_hz", "vslip": "v_slip", "Tmolde": "T_molde"}
melhor_eq_str = str(melhor_eq)
for nome_interno, nome_legivel in nomes_mapping.items():
    melhor_eq_str = melhor_eq_str.replace(nome_interno, nome_legivel)

print(f"\n📊 MELHOR EQUAÇÃO ENCONTRADA:")
print(f"\n   a = {melhor_eq_str}")
print(f"\n   MSE: {mse:.6f}")

# Tabela com as melhores equações
print(f"\n📈 TODAS AS EQUAÇÕES (ordenadas por qualidade):\n")
print(model)

print("\n" + "="*70)