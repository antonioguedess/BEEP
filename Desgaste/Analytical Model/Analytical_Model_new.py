import numpy as np
import pandas as pd


def find_temperature_column(dataframe):
    """Find the temperature column even if the CSV encoding is inconsistent."""
    for column in dataframe.columns:
        if column.startswith("Temp"):
            return column
    raise KeyError("Could not identify the temperature column in the CSV.")


def estimate_final_wear_curve(a_cond):
    """
    Estimate the final plateau from the tail of the curve.
    Use the average of the last points to preserve the observed visual plateau.
    """
    if len(a_cond) == 0:
        raise ValueError("Curve has no points to estimate a_final.")

    tail_points = max(3, int(np.ceil(len(a_cond) * 0.15)))
    tail_points = min(tail_points, len(a_cond))
    return np.mean(a_cond[-tail_points:])


# --- Load CSV data ---
df = pd.read_csv(r"C:\Prog_Diss\Desgaste\Dataset\Dataset.csv", delimiter=";", decimal=",")
temperature_column = find_temperature_column(df)

# --- Gear geometry (constants) ---
m = 2
z = 30
b = 15
alpha = np.radians(20)
E_star = 3100

# --- Pitch radius ---
r = (m * z) / 2

# --- Per-test variables (extracted from CSV) ---
N = df["N (x10^5)"].values * 1e5
torque = df["Torque (Nm)"].values * 1000
n = df["n (rpm)"].values
T_molde = df[temperature_column].values
linear_wear = df["a (mm)"].values

# --- Derived calculations ---
Ft = torque / r
omega = 2 * np.pi * n / 60
v_slip = omega * r * np.sin(alpha)
R_star = r * np.sin(alpha) / 2
P_hz = np.sqrt((Ft * E_star) / (np.pi * b * R_star))

# --- Restriction: only data with a <= 0.2*m ---
mask = linear_wear <= 0.2 * m
N = N[mask]
P_hz = P_hz[mask]
v_slip = v_slip[mask]
T_molde = T_molde[mask]
linear_wear = linear_wear[mask]
torque = torque[mask]
n = n[mask]

# --- Filter positive values for log-space ---
positive_mask = (N > 0) & (P_hz > 0) & (v_slip > 0) & (linear_wear > 0)
N = N[positive_mask]
P_hz = P_hz[positive_mask]
v_slip = v_slip[positive_mask]
T_molde = T_molde[positive_mask]
linear_wear = linear_wear[positive_mask]
torque = torque[positive_mask]
n = n[positive_mask]

# --- Input matrix (without normalization) ---
inputs = np.column_stack([N, P_hz, v_slip, T_molde])
observed_wear = linear_wear

# --- Calculate geometric factor Y and Delta K ---
W = b
a_over_W = linear_wear / W
Y = 16.70 - 104.7 * a_over_W + 369.9 * a_over_W**2 - 573.8 * a_over_W**3 + 360.5 * a_over_W**4

sigma_max = P_hz
sigma_min = 0
delta_K = Y * (sigma_max - sigma_min) * np.sqrt(np.pi * linear_wear)

# --- Determine a_final (wear plateau) ---
# For each condition, use the final plateau observed in the tail of the curve.
processed_data = pd.DataFrame(
    {
        "N": N,
        "P_hz": P_hz,
        "v_slip": v_slip,
        "T_molde": T_molde,
        "wear": linear_wear,
        "torque": torque,
        "rpm": n,
    }
)

final_wear_by_condition = {}
condition_data = []

for (torque_value, rpm_value, temperature_value), group in processed_data.groupby(["torque", "rpm", "T_molde"]):
    sorted_group = group.sort_values("N")
    N_cond = sorted_group["N"].values
    a_cond = sorted_group["wear"].values

    if len(N_cond) >= 2:
        a_final_cond = estimate_final_wear_curve(a_cond)
        P_hz_ref = sorted_group["P_hz"].iloc[0]
        v_slip_ref = sorted_group["v_slip"].iloc[0]

        final_wear_by_condition[(torque_value, rpm_value, temperature_value)] = a_final_cond
        condition_data.append(
            {
                "P_hz": P_hz_ref,
                "v_slip": v_slip_ref,
                "T_molde": temperature_value,
                "a_final": a_final_cond,
                "torque": torque_value,
                "rpm": rpm_value,
            }
        )

condition_dataframe = pd.DataFrame(condition_data)

print(f"\nCondition analysis:")
print(f"   Total identified conditions: {len(condition_dataframe)}")
print(f"\n   First 5 conditions:")
print(condition_dataframe.head().to_string(index=False))

# --- Fit polynomial to the identified a_final values ---
P_hz_condition_array = condition_dataframe["P_hz"].values
v_slip_condition_array = condition_dataframe["v_slip"].values
T_molde_condition_array = condition_dataframe["T_molde"].values
a_final_observed = condition_dataframe["a_final"].values

# The plateau depends on the test condition, not on cycle N.
condition_features = np.column_stack(
    [
        np.ones(len(P_hz_condition_array)),
        P_hz_condition_array,
        v_slip_condition_array,
        T_molde_condition_array,
        P_hz_condition_array**2,
        v_slip_condition_array**2,
        T_molde_condition_array**2,
    ]
)

final_wear_coefficients = np.linalg.lstsq(condition_features, a_final_observed, rcond=None)[0]

# Preserve the observed plateau in each curve during model fitting.
condition_keys = list(zip(torque, n, T_molde))
a_final_array = np.array([final_wear_by_condition[key] for key in condition_keys], dtype=float)

# Auxiliary polynomial only for reference or future extrapolation.
all_features = np.column_stack(
    [
        np.ones(len(P_hz)),
        P_hz,
        v_slip,
        T_molde,
        P_hz**2,
        v_slip**2,
        T_molde**2,
    ]
)
a_final_poly_array = all_features @ final_wear_coefficients

# Ensure physical consistency: a_final must remain above the observed wear.
a_final_array = np.maximum(a_final_array, linear_wear * 1.01)
a_final_poly_array = np.maximum(a_final_poly_array, linear_wear * 1.01)


def calculate_m(N, P_hz, v_slip, T_molde):
    """
    Calculate the exponent m of the sigmoidal formula.
    It may be a constant or a function of the inputs.
    """
    return 1.0


m_values = np.array([calculate_m(N[i], P_hz[i], v_slip[i], T_molde[i]) for i in range(len(N))])

# --- Fit sigmoidal formula: a(N) = a_final * (1 - exp(-zeta*(Delta K)^m * N / a_final)) ---
epsilon = 1e-10
wear_ratio = linear_wear / a_final_array
wear_ratio = np.clip(wear_ratio, epsilon, 1 - epsilon)

transformed_wear = -np.log(1 - wear_ratio)
transformed_input = (delta_K**m_values) * N / a_final_array

zeta = np.sum(transformed_input * transformed_wear) / np.sum(transformed_input**2)

predicted_wear = a_final_array * (1 - np.exp(-zeta * (delta_K**m_values) * N / a_final_array))
mse = np.mean((linear_wear - predicted_wear) ** 2)
rmse = np.sqrt(mse)

ss_res = np.sum((linear_wear - predicted_wear) ** 2)
ss_tot = np.sum((linear_wear - np.mean(linear_wear)) ** 2)
r2 = 1 - (ss_res / ss_tot)

# --- Results ---
print("\n" + "=" * 70)
print("SIGMOIDAL WEAR MODEL")
print("=" * 70)
print("\nFormula: a(N) = a_final * (1 - exp(-zeta*(Delta K)^m * N / a_final))")

print(f"\nCalculated coefficients:")
print(f"   zeta: {zeta:.6e}")
print(f"   m (exponent): {m_values[0]:.6f}")

print(f"\nParameters:")
print(f"   a_final used in fitting (per observed condition) - Statistics:")
print(f"      Min: {np.min(a_final_array):.6f} mm")
print(f"      Mean: {np.mean(a_final_array):.6f} mm")
print(f"      Max: {np.max(a_final_array):.6f} mm")
print(f"   observed a_final (curve tails) - Statistics:")
print(f"      Min: {np.min(a_final_observed):.6f} mm")
print(f"      Mean: {np.mean(a_final_observed):.6f} mm")
print(f"      Max: {np.max(a_final_observed):.6f} mm")
print(f"   auxiliary polynomial a_final - Statistics:")
print(f"      Min: {np.min(a_final_poly_array):.6f} mm")
print(f"      Mean: {np.mean(a_final_poly_array):.6f} mm")
print(f"      Max: {np.max(a_final_poly_array):.6f} mm")
print(f"   W (tooth width): {W} mm")

print(f"\nPolynomial coefficients for a_final(condition):")
coefficient_names = ["const", "P_hz", "v_slip", "T_molde", "P_hz^2", "v_slip^2", "T_molde^2"]
for name, coefficient in zip(coefficient_names, final_wear_coefficients):
    print(f"      {name}: {coefficient:.6e}")

print(f"\nDelta K:")
print(f"   Min: {np.min(delta_K):.6e}")
print(f"   Mean: {np.mean(delta_K):.6e}")
print(f"   Max: {np.max(delta_K):.6e}")

print(f"\nQuality metrics:")
print(f"   MSE: {mse:.6f}")
print(f"   RMSE: {rmse:.6f}")
print(f"   R^2: {r2:.6f}")

print(f"\nFinal equation:")
print(f"   a(N) = a_final(condition) * (1 - exp(-{zeta:.6e}*(Delta K)^{m_values[0]:.2f} * N / a_final(condition)))")
print("   where a_final(condition) is obtained from the observed plateau in the tail of each curve")

print("\n" + "=" * 70)
