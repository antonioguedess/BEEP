import numpy as np
import pandas as pd


def find_temperature_column(dataframe):
    """Find the temperature column even if the CSV encoding is inconsistent."""
    for column in dataframe.columns:
        if column.startswith("Temp"):
            return column
    raise KeyError("Could not identify the temperature column in the CSV.")


def estimate_final_wear_curve(N_cond, a_cond):
    """
    Define the plateau using the point immediately before the slope increase
    at the end of the flat region of the curve.
    """
    if len(a_cond) == 0:
        raise ValueError("Curve has no points to estimate a_final.")
    if len(a_cond) < 4:
        return a_cond[-1]

    delta_N = np.diff(N_cond)
    delta_a = np.diff(a_cond)
    valid_mask = delta_N > 0

    if not np.any(valid_mask):
        return a_cond[-1]

    slopes = delta_a[valid_mask] / delta_N[valid_mask]
    point_indices = np.flatnonzero(valid_mask)

    if len(slopes) < 4:
        return a_cond[point_indices[-1]]

    window = min(3, len(slopes))
    kernel = np.ones(window) / window
    smoothed_slopes = np.convolve(slopes, kernel, mode="same")

    search_start = len(smoothed_slopes) // 2
    search_slopes = smoothed_slopes[search_start:]
    positive_slopes = search_slopes[search_slopes > 0]

    if len(positive_slopes) == 0:
        return a_cond[point_indices[-1]]

    n_base = max(3, int(np.ceil(len(positive_slopes) * 0.4)))
    baseline_slopes = np.sort(positive_slopes)[:n_base]
    baseline_slope = np.median(baseline_slopes)
    baseline_spread = np.std(baseline_slopes)
    rise_threshold = max(baseline_slope * 2.5, baseline_slope + 3 * baseline_spread, 1e-12)

    rise_index = None
    run_min = 3

    for idx in range(search_start, len(smoothed_slopes) - run_min + 1):
        segment = smoothed_slopes[idx : idx + run_min]
        if np.all(segment > rise_threshold) and np.all(np.diff(segment) >= -rise_threshold * 0.15):
            rise_index = idx
            break

    if rise_index is None:
        rise_index = search_start + np.argmax(search_slopes)

    plateau_point = point_indices[max(rise_index - 1, 0)]
    return a_cond[plateau_point]


def calculate_metrics(actual_wear, predicted_wear):
    """Calculate basic fit quality metrics."""
    mse = np.mean((actual_wear - predicted_wear) ** 2)
    rmse = np.sqrt(mse)
    ss_res = np.sum((actual_wear - predicted_wear) ** 2)
    ss_tot = np.sum((actual_wear - np.mean(actual_wear)) ** 2)
    r2 = 1 - (ss_res / ss_tot)
    return mse, rmse, r2


def fit_sigmoidal_model(actual_wear, a_final, base_term, candidate_exponents):
    """Test several exponents for a generating function and keep the best fit."""
    best = None
    epsilon = 1e-10
    wear_ratio = np.clip(actual_wear / a_final, epsilon, 1 - epsilon)
    transformed_wear = -np.log(1 - wear_ratio)

    for exponent in candidate_exponents:
        transformed_input = (base_term**exponent) / a_final
        denominator = np.sum(transformed_input**2)
        if denominator <= 0:
            continue

        zeta = np.sum(transformed_input * transformed_wear) / denominator
        predicted_wear = a_final * (1 - np.exp(-zeta * transformed_input))
        mse, rmse, r2 = calculate_metrics(actual_wear, predicted_wear)

        candidate = {
            "exponent": float(exponent),
            "zeta": float(zeta),
            "predicted_wear": predicted_wear,
            "mse": float(mse),
            "rmse": float(rmse),
            "r2": float(r2),
            "ss_res": float(np.sum((actual_wear - predicted_wear) ** 2)),
            "ss_tot": float(np.sum((actual_wear - np.mean(actual_wear)) ** 2)),
        }

        if best is None or candidate["r2"] > best["r2"]:
            best = candidate

    return best


def build_condition_scaling(P_hz_values, v_slip_values, mold_temperature_values):
    """Build scaling statistics for condition variables."""
    return {
        "P_hz_mean": float(np.mean(P_hz_values)),
        "P_hz_std": float(max(np.std(P_hz_values), 1e-12)),
        "v_slip_mean": float(np.mean(v_slip_values)),
        "v_slip_std": float(max(np.std(v_slip_values), 1e-12)),
        "T_mold_mean": float(np.mean(mold_temperature_values)),
        "T_mold_std": float(max(np.std(mold_temperature_values), 1e-12)),
    }


def build_condition_feature_matrix(P_hz_values, v_slip_values, mold_temperature_values, scaling):
    """Build a scaled polynomial feature matrix for condition-dependent parameters."""
    p_scaled = (P_hz_values - scaling["P_hz_mean"]) / scaling["P_hz_std"]
    v_scaled = (v_slip_values - scaling["v_slip_mean"]) / scaling["v_slip_std"]
    t_scaled = (mold_temperature_values - scaling["T_mold_mean"]) / scaling["T_mold_std"]

    feature_matrix = np.column_stack(
        [
            np.ones(len(P_hz_values)),
            p_scaled,
            v_scaled,
            t_scaled,
            p_scaled * v_scaled,
            p_scaled * t_scaled,
            v_scaled * t_scaled,
            p_scaled**2,
            v_scaled**2,
            t_scaled**2,
        ]
    )
    feature_names = [
        "1",
        "p_scaled",
        "v_scaled",
        "t_scaled",
        "p_scaled*v_scaled",
        "p_scaled*t_scaled",
        "v_scaled*t_scaled",
        "p_scaled^2",
        "v_scaled^2",
        "t_scaled^2",
    ]
    return feature_matrix, feature_names


def evaluate_parameter_surface(coefficients, P_hz_values, v_slip_values, mold_temperature_values, scaling):
    """Evaluate a polynomial parameter surface."""
    features, _ = build_condition_feature_matrix(P_hz_values, v_slip_values, mold_temperature_values, scaling)
    return features @ coefficients


def format_surface_expression(coefficients, feature_names, scaling):
    """Format a polynomial parameter expression for display."""
    scale_text = (
        f"p_scaled=(P_hz-{scaling['P_hz_mean']:.6e})/{scaling['P_hz_std']:.6e}, "
        f"v_scaled=(v_slip-{scaling['v_slip_mean']:.6e})/{scaling['v_slip_std']:.6e}, "
        f"t_scaled=(T_mold-{scaling['T_mold_mean']:.6e})/{scaling['T_mold_std']:.6e}"
    )
    terms = [f"{coefficient:.6e}*{name}" for coefficient, name in zip(coefficients, feature_names)]
    return scale_text + "; " + " + ".join(terms)


# --- Load CSV data ---
df = pd.read_csv(r"C:\Prog_Diss\Desgaste\Dataset\Dataset.csv", delimiter=";", decimal=",")
temperature_column = find_temperature_column(df)

# --- Gear geometry (constants) ---
m = 2 # module in mm
z = 30 # number of teeth
b = 15 # tooth width in mm
alpha = np.radians(20) # pressure angle in radians
E_star = 3100 # effective Young's modulus in MPa (converted from GPa)
a_filter = 0.2 * m

# --- Pitch radius ---
r_mm = (m * z) / 2 # in mm
r_m = r_mm / 1000.0 # in meters

# --- Per-test variables (extracted from CSV) ---
N = df["N (x10^5)"].values * 1e5 # in x10^5
torque = df["Torque (Nm)"].values # in Nm
n = df["n (rpm)"].values # in rpm
T_molde = df[temperature_column].values # in °C
desgaste_linear = df["a (mm)"].values # in mm

# --- Derived calculations ---
# Torque remains in N.m; r_m is used to obtain Ft in N.
Ft = torque / r_m # in N
omega = 2 * np.pi * n / 60 # in rad/s
# v_slip and R_star remain in mm/s and mm, respectively.
v_slip = omega * r_mm * np.sin(alpha) # in mm/s
R_star = r_mm * np.sin(alpha) / 2 # in mm
P_hz = np.sqrt((Ft * E_star) / (np.pi * b * R_star)) # in MPa

# --- Filter positive values for log-space ---
positive_mask = (N > 0) & (P_hz > 0) & (v_slip > 0) & (desgaste_linear > 0)
N = N[positive_mask]
P_hz = P_hz[positive_mask]
v_slip = v_slip[positive_mask]
T_molde = T_molde[positive_mask]
desgaste_linear = desgaste_linear[positive_mask]
torque = torque[positive_mask]
n = n[positive_mask]

# --- Input matrix (without normalization) ---
inputs = np.column_stack([N, P_hz, v_slip, T_molde])
observed_wear = desgaste_linear

# --- Calculate geometric factor Y and Delta K ---
W = b
a_over_W = desgaste_linear / W
Y = 16.70 - 104.7 * a_over_W + 369.9 * a_over_W**2 - 573.8 * a_over_W**3 + 360.5 * a_over_W**4

sigma_max = P_hz
sigma_min = 0
delta_K = Y * (sigma_max - sigma_min) * np.sqrt(np.pi * desgaste_linear)

processed_data = pd.DataFrame(
    {
        "N": N,
        "P_hz": P_hz,
        "v_slip": v_slip,
        "T_mold": T_molde,
        "linear_wear": desgaste_linear,
        "delta_K": delta_K,
        "torque": torque,
        "rpm": n,
    }
)

# --- Define a_final on full positive curves before applying the CSV filter ---
a_final_by_condition = {}

for (torque_value, rpm_value, temperature_value), group in processed_data.groupby(["torque", "rpm", "T_mold"]):
    sorted_group = group.sort_values("N")
    N_cond = sorted_group["N"].values
    wear_cond = sorted_group["linear_wear"].values

    if len(N_cond) < 3:
        continue

    a_final_cond = estimate_final_wear_curve(N_cond, wear_cond)
    a_final_cond = max(a_final_cond, np.max(wear_cond) * 1.01)
    a_final_by_condition[(torque_value, rpm_value, temperature_value)] = a_final_cond

# --- Apply the CSV filter only after a_final has been defined ---
filtered_data = processed_data[processed_data["linear_wear"] <= a_filter].copy()

candidate_models = {
    "DeltaK_N": {
        "exponents": np.linspace(0.25, 1.25, 21),
        "description": "DeltaK * N",
    },
    "N": {
        "exponents": np.linspace(0.25, 1.25, 21),
        "description": "N",
    },
    "P_hz_v_slip_N": {
        "exponents": np.linspace(0.20, 0.80, 25),
        "description": "P_hz * v_slip * N",
    },
}

condition_records_by_model = {model_name: [] for model_name in candidate_models}

for (torque_value, rpm_value, temperature_value), group in filtered_data.groupby(["torque", "rpm", "T_mold"]):
    sorted_group = group.sort_values("N")
    N_cond = sorted_group["N"].values
    wear_cond = sorted_group["linear_wear"].values
    delta_K_cond = sorted_group["delta_K"].values
    P_hz_cond = sorted_group["P_hz"].iloc[0]
    v_slip_cond = sorted_group["v_slip"].iloc[0]

    if len(N_cond) < 3:
        continue

    a_final_cond = a_final_by_condition.get((torque_value, rpm_value, temperature_value))
    if a_final_cond is None:
        continue
    a_final_cond_array = np.full_like(wear_cond, a_final_cond, dtype=float)

    base_terms = {
        "DeltaK_N": delta_K_cond * N_cond,
        "N": N_cond,
        "P_hz_v_slip_N": sorted_group["P_hz"].values * sorted_group["v_slip"].values * N_cond,
    }

    for model_name, config in candidate_models.items():
        result = fit_sigmoidal_model(
            wear_cond,
            a_final_cond_array,
            base_terms[model_name],
            config["exponents"],
        )
        if result is None:
            continue

        condition_records_by_model[model_name].append(
            {
                "P_hz": P_hz_cond,
                "v_slip": v_slip_cond,
                "T_mold": temperature_value,
                "a_final": a_final_cond,
                "zeta": result["zeta"],
                "gamma": result["exponent"],
                "rmse": result["rmse"],
                "r2": result["r2"],
                "ss_res": result["ss_res"],
                "ss_tot": result["ss_tot"],
            }
        )
best_model_name = None
best_model_payload = None
best_model_r2 = -np.inf

base_terms_all = {
    "DeltaK_N": filtered_data["delta_K"].values * filtered_data["N"].values,
    "N": filtered_data["N"].values,
    "P_hz_v_slip_N": filtered_data["P_hz"].values * filtered_data["v_slip"].values * filtered_data["N"].values,
}

for model_name, records in condition_records_by_model.items():
    if not records:
        continue

    condition_dataframe = pd.DataFrame(records)
    scaling = build_condition_scaling(
        condition_dataframe["P_hz"].values,
        condition_dataframe["v_slip"].values,
        condition_dataframe["T_mold"].values,
    )
    condition_features, surface_feature_names = build_condition_feature_matrix(
        condition_dataframe["P_hz"].values,
        condition_dataframe["v_slip"].values,
        condition_dataframe["T_mold"].values,
        scaling,
    )

    log_a_final_coefficients = np.linalg.lstsq(
        condition_features,
        np.log(np.maximum(condition_dataframe["a_final"].values, 1e-12)),
        rcond=None,
    )[0]
    log_zeta_coefficients = np.linalg.lstsq(
        condition_features,
        np.log(np.maximum(condition_dataframe["zeta"].values, 1e-18)),
        rcond=None,
    )[0]
    gamma_coefficients = np.linalg.lstsq(
        condition_features,
        condition_dataframe["gamma"].values,
        rcond=None,
    )[0]

    log_a_final_array = evaluate_parameter_surface(
        log_a_final_coefficients,
        filtered_data["P_hz"].values,
        filtered_data["v_slip"].values,
        filtered_data["T_mold"].values,
        scaling,
    )
    log_zeta_array = evaluate_parameter_surface(
        log_zeta_coefficients,
        filtered_data["P_hz"].values,
        filtered_data["v_slip"].values,
        filtered_data["T_mold"].values,
        scaling,
    )
    gamma_array = evaluate_parameter_surface(
        gamma_coefficients,
        filtered_data["P_hz"].values,
        filtered_data["v_slip"].values,
        filtered_data["T_mold"].values,
        scaling,
    )

    a_final_array = np.exp(log_a_final_array)
    zeta_array = np.exp(log_zeta_array)
    gamma_array = np.clip(gamma_array, 0.20, 2.50)
    a_final_array = np.maximum(a_final_array, filtered_data["linear_wear"].values * 1.01)

    selected_base_term = base_terms_all[model_name]
    predicted_wear = a_final_array * (
        1 - np.exp(-zeta_array * ((selected_base_term**gamma_array) / a_final_array))
    )
    mse, rmse, r2 = calculate_metrics(filtered_data["linear_wear"].values, predicted_wear)

    if r2 > best_model_r2:
        best_model_r2 = r2
        best_model_name = model_name
        best_model_payload = {
            "condition_dataframe": condition_dataframe,
            "surface_feature_names": surface_feature_names,
            "scaling": scaling,
            "a_final_coefficients": log_a_final_coefficients,
            "zeta_coefficients": log_zeta_coefficients,
            "gamma_coefficients": gamma_coefficients,
            "a_final_array": a_final_array,
            "zeta_array": zeta_array,
            "gamma_array": gamma_array,
            "predicted_wear": predicted_wear,
            "mse": mse,
            "rmse": rmse,
            "r2": r2,
        }

if best_model_name is None or best_model_payload is None:
    raise RuntimeError("Could not fit any sigmoidal model to the available conditions.")

condition_dataframe = best_model_payload["condition_dataframe"]
surface_feature_names = best_model_payload["surface_feature_names"]
scaling = best_model_payload["scaling"]
a_final_coefficients = best_model_payload["a_final_coefficients"]
zeta_coefficients = best_model_payload["zeta_coefficients"]
gamma_coefficients = best_model_payload["gamma_coefficients"]
a_final_array = best_model_payload["a_final_array"]
zeta_array = best_model_payload["zeta_array"]
gamma_array = best_model_payload["gamma_array"]
predicted_wear = best_model_payload["predicted_wear"]
mse = best_model_payload["mse"]
rmse = best_model_payload["rmse"]
r2 = best_model_payload["r2"]

print("\n" + "=" * 70)
print("SIGMOIDAL WEAR MODEL")
print("=" * 70)
print("\nGeneral formula:")
print("   a(N) = a_final(inputs) * (1 - exp(-zeta(inputs) * G(inputs)^gamma(inputs) / a_final(inputs)))")

print(f"\nSelected generating function:")
print(f"   {best_model_name}: {candidate_models[best_model_name]['description']}")

print(f"\na_final(inputs):")
print("   log(a_final) = " + format_surface_expression(a_final_coefficients, surface_feature_names, scaling))
print("   a_final = exp(log(a_final))")

print(f"\nzeta(inputs):")
print("   log(zeta) = " + format_surface_expression(zeta_coefficients, surface_feature_names, scaling))
print("   zeta = exp(log(zeta))")

print(f"\ngamma(inputs):")
print("   gamma = " + format_surface_expression(gamma_coefficients, surface_feature_names, scaling))

print(f"\nDelta K:")
print("   DeltaK = Y * (sigma_max - sigma_min) * sqrt(pi * a)")
print("   sigma_max = P_hz")
print("   sigma_min = 0")
print("   Y = 16.70 - 104.7*(a/W) + 369.9*(a/W)^2 - 573.8*(a/W)^3 + 360.5*(a/W)^4")
print(f"   W = {W} mm")

print(f"\nParameter statistics:")
print(f"   a_final from fitted expression:")
print(f"      Min: {np.min(a_final_array):.6f} mm")
print(f"      Mean: {np.mean(a_final_array):.6f} mm")
print(f"      Max: {np.max(a_final_array):.6f} mm")
print(f"   zeta from fitted expression:")
print(f"      Min: {np.min(zeta_array):.6e}")
print(f"      Mean: {np.mean(zeta_array):.6e}")
print(f"      Max: {np.max(zeta_array):.6e}")
print(f"   gamma from fitted expression:")
print(f"      Min: {np.min(gamma_array):.6f}")
print(f"      Mean: {np.mean(gamma_array):.6f}")
print(f"      Max: {np.max(gamma_array):.6f}")
print(f"   DeltaK:")
print(f"      Min: {np.min(filtered_data['delta_K'].values):.6e}")
print(f"      Mean: {np.mean(filtered_data['delta_K'].values):.6e}")
print(f"      Max: {np.max(filtered_data['delta_K'].values):.6e}")
print(f"   CSV filtering limit: a_filter = 0.2 * m = {a_filter:.6f} mm")

print(f"\nCondition-level fit statistics for the selected model:")
print(f"   Total fitted conditions: {len(condition_dataframe)}")
print(f"   Mean condition RMSE: {condition_dataframe['rmse'].mean():.6f}")
print(f"   Mean condition R^2: {condition_dataframe['r2'].mean():.6f}")

print(f"\nGlobal quality metrics:")
print(f"   MSE: {mse:.6f}")
print(f"   RMSE: {rmse:.6f}")
print(f"   R^2: {r2:.6f}")

print(f"\nFinal equation with explicit parameter functions:")
print("   a(N) = a_final(inputs) * (1 - exp(-zeta(inputs) * G(inputs)^gamma(inputs) / a_final(inputs)))")
print("   with")
print("   log(a_final(inputs)) = " + format_surface_expression(a_final_coefficients, surface_feature_names, scaling))
print("   a_final(inputs) = exp(log(a_final(inputs)))")
print("   log(zeta(inputs)) = " + format_surface_expression(zeta_coefficients, surface_feature_names, scaling))
print("   zeta(inputs) = exp(log(zeta(inputs)))")
print("   gamma(inputs) = " + format_surface_expression(gamma_coefficients, surface_feature_names, scaling))
print(f"   G(inputs) = {candidate_models[best_model_name]['description']}")

print("\n" + "=" * 70)
