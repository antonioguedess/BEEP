import numpy as np
import pandas as pd
import json
import os
from datetime import datetime


# --- Dimensionless reference values ---
# Replace these constants with the admissible values adopted for the gear material.
P_HZ_ADMISSIBLE_MPA = 65.0 
T_FLOW_C = -75.0
T_MELTING_C = 166.0
SURFACE_POLYNOMIAL_DEGREE = 4


def find_temperature_column(dataframe):
    """Find the temperature column even if the CSV encoding is inconsistent."""
    for column in dataframe.columns:
        if column.startswith("Temp"):
            return column
    raise KeyError("Could not identify the temperature column in the CSV.")


def calculate_metrics(actual_wear, predicted_wear):
    """Calculate basic fit quality metrics."""
    mse = np.mean((actual_wear - predicted_wear) ** 2)
    rmse = np.sqrt(mse)
    ss_res = np.sum((actual_wear - predicted_wear) ** 2)
    ss_tot = np.sum((actual_wear - np.mean(actual_wear)) ** 2)
    r2 = 1 - (ss_res / ss_tot)
    return mse, rmse, r2


def enforce_monotonic_wear_by_condition(dataframe, predicted_wear, condition_columns):
    """Force the predicted wear to be non-decreasing with N within each condition."""
    prediction_frame = dataframe[condition_columns + ["N"]].copy()
    prediction_frame["predicted_wear_raw"] = predicted_wear
    prediction_frame["row_id"] = np.arange(len(prediction_frame))

    corrected_groups = []
    for _, group in prediction_frame.groupby(condition_columns, sort=False):
        ordered_group = group.sort_values("N").copy()
        ordered_group["predicted_wear"] = np.maximum.accumulate(ordered_group["predicted_wear_raw"].values)
        corrected_groups.append(ordered_group[["row_id", "predicted_wear"]])

    corrected_frame = pd.concat(corrected_groups, ignore_index=True).sort_values("row_id")
    return corrected_frame["predicted_wear"].to_numpy()


def fit_power_law_model(N_values, P_hz_values, v_slip_values, mold_temperature_values, wear_values):
    """Fit a power-law model a = A * N^b * P_hz^c * v_slip^d * T_mold^e."""
    positive_mask = (
        (N_values > 0)
        & (P_hz_values > 0)
        & (v_slip_values > 0)
        & (mold_temperature_values > 0)
        & (wear_values > 0)
    )
    if not np.any(positive_mask):
        return None

    log_design = np.column_stack(
        [
            np.ones(np.sum(positive_mask)),
            np.log(N_values[positive_mask]),
            np.log(P_hz_values[positive_mask]),
            np.log(v_slip_values[positive_mask]),
            np.log(mold_temperature_values[positive_mask]),
        ]
    )
    log_wear = np.log(wear_values[positive_mask])
    coefficients = np.linalg.lstsq(log_design, log_wear, rcond=None)[0]

    fitted = {
        "a": float(np.exp(coefficients[0])),
        "b_N": float(coefficients[1]),
        "c_Phz": float(coefficients[2]),
        "d_vslip": float(coefficients[3]),
        "e_Tmolde": float(coefficients[4]),
    }
    predicted_wear = (
        fitted["a"]
        * (N_values[positive_mask] ** fitted["b_N"])
        * (P_hz_values[positive_mask] ** fitted["c_Phz"])
        * (v_slip_values[positive_mask] ** fitted["d_vslip"])
        * (mold_temperature_values[positive_mask] ** fitted["e_Tmolde"])
    )
    mse, rmse, r2 = calculate_metrics(wear_values[positive_mask], predicted_wear)
    fitted["positive_rows"] = int(np.sum(positive_mask))
    fitted["predicted_wear"] = predicted_wear
    fitted["mse"] = float(mse)
    fitted["rmse"] = float(rmse)
    fitted["r2"] = float(r2)
    return fitted


def fit_kw_model(actual_wear, base_term):
    """Fit an equivalent scalar coefficient for a direct wear formulation."""
    best = None
    denominator = np.sum(base_term**2)
    if denominator <= 0:
        return None

    equivalent_coefficient = np.sum(base_term * actual_wear) / denominator
    if equivalent_coefficient <= 0:
        return None

    predicted_wear = equivalent_coefficient * base_term
    mse, rmse, r2 = calculate_metrics(actual_wear, predicted_wear)

    best = {
        "equivalent_coefficient": float(equivalent_coefficient),
        "predicted_wear": predicted_wear,
        "mse": float(mse),
        "rmse": float(rmse),
        "r2": float(r2),
        "ss_res": float(np.sum((actual_wear - predicted_wear) ** 2)),
        "ss_tot": float(np.sum((actual_wear - np.mean(actual_wear)) ** 2)),
    }

    return best


def build_condition_scaling(P_hz_values, N_values, mold_temperature_values):
    """Build scaling statistics for condition variables."""
    return {
        "P_hz_mean": float(np.mean(P_hz_values)),
        "P_hz_std": float(max(np.std(P_hz_values), 1e-12)),
        "N_mean": float(np.mean(N_values)),
        "N_std": float(max(np.std(N_values), 1e-12)),
        "T_mold_mean": float(np.mean(mold_temperature_values)),
        "T_mold_std": float(max(np.std(mold_temperature_values), 1e-12)),
    }


def build_condition_feature_matrix(P_hz_values, N_values, mold_temperature_values, scaling):
    """Build a scaled polynomial feature matrix for condition-dependent parameters."""
    p_scaled = (P_hz_values - scaling["P_hz_mean"]) / scaling["P_hz_std"]
    N_scaled = (N_values - scaling["N_mean"]) / scaling["N_std"]
    t_scaled = (mold_temperature_values - scaling["T_mold_mean"]) / scaling["T_mold_std"]

    feature_matrix = np.column_stack(
        [
            np.ones(len(P_hz_values)),
            p_scaled,
            N_scaled,
            t_scaled,
            p_scaled * N_scaled,
            p_scaled * t_scaled,
            N_scaled * t_scaled,
            p_scaled**2,
            N_scaled**2,
            t_scaled**2,
            p_scaled**3,
            N_scaled**3,
            t_scaled**3,
            p_scaled**2 * N_scaled,
            p_scaled**2 * t_scaled,
            N_scaled**2 * p_scaled,
            N_scaled**2 * t_scaled,
            t_scaled**2 * p_scaled,
            t_scaled**2 * N_scaled,
            p_scaled * N_scaled * t_scaled,
            p_scaled**4,
            N_scaled**4,
            t_scaled**4,
            p_scaled**3 * N_scaled,
            p_scaled**3 * t_scaled,
            N_scaled**3 * p_scaled,
            N_scaled**3 * t_scaled,
            t_scaled**3 * p_scaled,
            t_scaled**3 * N_scaled,
            p_scaled**2 * N_scaled**2,
            p_scaled**2 * t_scaled**2,
            N_scaled**2 * t_scaled**2,
            p_scaled**5,
            N_scaled**5,
            t_scaled**5,
            p_scaled**4 * N_scaled,
            p_scaled**4 * t_scaled,
            N_scaled**4 * p_scaled,
            N_scaled**4 * t_scaled,
            t_scaled**4 * p_scaled,
            t_scaled**4 * N_scaled,
            p_scaled**3 * N_scaled**2,
            p_scaled**3 * t_scaled**2,
            N_scaled**3 * p_scaled**2,
            N_scaled**3 * t_scaled**2,
            t_scaled**3 * p_scaled**2,
            t_scaled**3 * N_scaled**2,
            p_scaled * N_scaled * t_scaled * (p_scaled + N_scaled + t_scaled),
        ]
    )
    feature_names = [
        "1",
        "p_scaled",
        "N_scaled",
        "t_scaled",
        "p_scaled*N_scaled",
        "p_scaled*t_scaled",
        "N_scaled*t_scaled",
        "p_scaled^2",
        "N_scaled^2",
        "t_scaled^2",
        "p_scaled^3",
        "N_scaled^3",
        "t_scaled^3",
        "p_scaled^2*N_scaled",
        "p_scaled^2*t_scaled",
        "N_scaled^2*p_scaled",
        "N_scaled^2*t_scaled",
        "t_scaled^2*p_scaled",
        "t_scaled^2*N_scaled",
        "p_scaled*N_scaled*t_scaled",
        "p_scaled^4",
        "N_scaled^4",
        "t_scaled^4",
        "p_scaled^3*N_scaled",
        "p_scaled^3*t_scaled",
        "N_scaled^3*p_scaled",
        "N_scaled^3*t_scaled",
        "t_scaled^3*p_scaled",
        "t_scaled^3*N_scaled",
        "p_scaled^2*N_scaled^2",
        "p_scaled^2*t_scaled^2",
        "N_scaled^2*t_scaled^2",
        "p_scaled^5",
        "N_scaled^5",
        "t_scaled^5",
        "p_scaled^4*N_scaled",
        "p_scaled^4*t_scaled",
        "N_scaled^4*p_scaled",
        "N_scaled^4*t_scaled",
        "t_scaled^4*p_scaled",
        "t_scaled^4*N_scaled",
        "p_scaled^3*N_scaled^2",
        "p_scaled^3*t_scaled^2",
        "N_scaled^3*p_scaled^2",
        "N_scaled^3*t_scaled^2",
        "t_scaled^3*p_scaled^2",
        "t_scaled^3*N_scaled^2",
        "p_scaled*N_scaled*t_scaled*(p_scaled+N_scaled+t_scaled)",
    ]
    return feature_matrix, feature_names


def evaluate_parameter_surface(coefficients, P_hz_values, N_values, mold_temperature_values, scaling):
    """Evaluate a polynomial parameter surface."""
    features, _ = build_condition_feature_matrix(P_hz_values, N_values, mold_temperature_values, scaling)
    return features @ coefficients


def format_surface_expression(coefficients, feature_names, scaling):
    """Format a polynomial parameter expression for display."""
    scale_text = (
        f"p_scaled=(P_hz-{scaling['P_hz_mean']:.6e})/{scaling['P_hz_std']:.6e}, "
        f"N_scaled=(N-{scaling['N_mean']:.6e})/{scaling['N_std']:.6e}, "
        f"t_scaled=(T_mold-{scaling['T_mold_mean']:.6e})/{scaling['T_mold_std']:.6e}"
    )
    terms = [f"{coefficient:.6e}*{name}" for coefficient, name in zip(coefficients, feature_names)]
    return scale_text + "; " + " + ".join(terms)


def build_condition_scaling(P_hz_values=None, N_values=None, mold_temperature_values=None):
    """Build reference values for dimensionless condition variables."""
    if P_HZ_ADMISSIBLE_MPA <= 0:
        raise ValueError("P_HZ_ADMISSIBLE_MPA must be positive.")
    if T_MELTING_C == T_FLOW_C:
        raise ValueError("T_MELTING_C and T_FLOW_C must be different.")

    return {
        "P_hz_admissible": float(P_HZ_ADMISSIBLE_MPA),
        "T_flow": float(T_FLOW_C),
        "T_melting": float(T_MELTING_C),
    }


def build_condition_feature_matrix(P_hz_values, N_values, v_slip_values, mold_temperature_values, scaling):
    """Build low-order features from dimensionless groups and slip velocity in m/s."""
    pressure_ratio = P_hz_values / scaling["P_hz_admissible"]
    temperature_ratio = (mold_temperature_values - scaling["T_flow"]) / (
        scaling["T_melting"] - scaling["T_flow"]
    )
    v_slip_ms = v_slip_values / 1000.0

    variables = [
        ("P_hz/P_hz_adm", pressure_ratio),
        ("theta_T", temperature_ratio),
        ("v_slip_m_s", v_slip_ms),
    ]
    feature_columns = [np.ones(len(P_hz_values))]
    feature_names = ["1"]

    for degree in range(1, SURFACE_POLYNOMIAL_DEGREE + 1):
        for start_index in range(len(variables)):
            name, values = variables[start_index]
            terms = [(name, values, start_index)]
            for _ in range(degree - 1):
                next_terms = []
                for term_name, term_values, min_index in terms:
                    for variable_index in range(min_index, len(variables)):
                        variable_name, variable_values = variables[variable_index]
                        next_terms.append(
                            (
                                f"{term_name}*{variable_name}",
                                term_values * variable_values,
                                variable_index,
                            )
                        )
                terms = next_terms

            for term_name, term_values, _ in terms:
                feature_names.append(term_name)
                feature_columns.append(term_values)

    return np.column_stack(feature_columns), feature_names


def evaluate_parameter_surface(coefficients, P_hz_values, N_values, v_slip_values, mold_temperature_values, scaling):
    """Evaluate a polynomial parameter surface."""
    features, _ = build_condition_feature_matrix(P_hz_values, N_values, v_slip_values, mold_temperature_values, scaling)
    return features @ coefficients


def format_surface_expression(coefficients, feature_names, scaling):
    """Format a polynomial parameter expression for display."""
    scale_text = (
        f"pi_p=P_hz/{scaling['P_hz_admissible']:.6e}, "
        f"theta_T=(T_mold-{scaling['T_flow']:.6e})/"
        f"({scaling['T_melting']:.6e}-{scaling['T_flow']:.6e}), "
        "v_slip_m_s=v_slip/1000"
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
z1 = z # constant number of teeth of gear 1
z2 = 30 # number of teeth of gear 2
u = z2 / z1 # transmission ratio
alpha_n = alpha # normal pressure angle in radians
beta = np.radians(0.0) # helix angle in radians
beta_b = np.arcsin(np.sin(beta) * np.cos(alpha_n)) # base helix angle in radians
alpha_t = np.arctan(np.tan(alpha_n) / np.cos(beta)) # transverse pressure angle in radians
m_n = m # normal module in mm
m_t = m_n / np.cos(beta) # transverse module in mm
d1 = m_t * z1 # reference diameter of gear 1 in mm
d2 = m_t * z2 # reference diameter of gear 2 in mm
d_b1 = d1 * np.cos(alpha_t) # base diameter of gear 1 in mm
d_b2 = d2 * np.cos(alpha_t) # base diameter of gear 2 in mm
a = (d1 + d2) / 2.0 # center distance in mm
d_w1 = (2 * a * z1) / (z1 + z2) # working pitch diameter of gear 1 in mm
d_w2 = (2 * a * z2) / (z1 + z2) # working pitch diameter of gear 2 in mm
alpha_wt = np.arccos(np.clip(d_b1 / d_w1, -1.0, 1.0)) # operating transverse pressure angle in radians
h_aP0 = m_n # standard addendum height in mm
d_a1 = d1 + 2 * h_aP0 # addendum diameter of gear 1 in mm
d_a2 = d2 + 2 * h_aP0 # addendum diameter of gear 2 in mm
x_sum = ((np.tan(alpha_wt) - alpha_wt - np.tan(alpha_t) + alpha_t) * (z1 + z2)) / (2 * np.tan(alpha_n))
epsilon_1 = (z1 / (2 * np.pi)) * (np.sqrt((d_a1 / d_b1) ** 2 - 1) - np.tan(alpha_wt))
epsilon_2 = (z2 / (2 * np.pi)) * (np.sqrt((d_a2 / d_b2) ** 2 - 1) - np.tan(alpha_wt))
epsilon_alpha = epsilon_1 + epsilon_2
epsilon_beta = (b * np.sin(beta)) / (m_n * np.pi)
d_Nf1 = np.sqrt((2 * a * np.sin(alpha_wt) - np.sqrt(d_a2**2 - d_b2**2)) ** 2 + d_b1**2)
d_Nf2 = np.sqrt((2 * a * np.sin(alpha_wt) - np.sqrt(d_a1**2 - d_b1**2)) ** 2 + d_b2**2)
l_f = (((d_a1 / 2.0) ** 2) - ((d_Nf1 / 2.0) ** 2)) / d_b1 # contact length in mm
H_v = (
    (np.pi * (u + 1.0)) / (z2 * np.cos(beta_b))
    * (1 - epsilon_1 - epsilon_2 + epsilon_1**2 + epsilon_2**2)
)

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
T1 = torque
wk_multiplier = ((2 * np.pi * T1 * H_v) * N) / (b * l_f * z1)

# --- Input matrix (without normalization) ---
inputs = np.column_stack([N, P_hz, v_slip, T_molde])
observed_wear = desgaste_linear

processed_data = pd.DataFrame(
    {
        "N": N,
        "P_hz": P_hz,
        "v_slip": v_slip,
        "wk_multiplier": wk_multiplier,
        "T_mold": T_molde,
        "linear_wear": desgaste_linear,
        "torque": torque,
        "rpm": n,
    }
)

# --- Apply the N-based filter ---
condition_columns = ["torque", "rpm", "T_mold"]
condition_n_max = processed_data.groupby(condition_columns)["N"].transform("max")
filtered_data = processed_data[processed_data["N"] < 0.99 * condition_n_max].copy()
scaling = build_condition_scaling(
    filtered_data["P_hz"].values,
    filtered_data["N"].values,
    filtered_data["T_mold"].values,
)
row_features, surface_feature_names = build_condition_feature_matrix(
    filtered_data["P_hz"].values,
    filtered_data["N"].values,
    filtered_data["v_slip"].values,
    filtered_data["T_mold"].values,
    scaling,
)
N_filtered = filtered_data["N"].values
P_hz_filtered = filtered_data["P_hz"].values
v_slip_filtered = filtered_data["v_slip"].values
T_mold_filtered = filtered_data["T_mold"].values
wk_multiplier_filtered = filtered_data["wk_multiplier"].values
wear_filtered = filtered_data["linear_wear"].values
N_scale = np.max(N_filtered)
N_scaled = N_filtered / N_scale
power_law_fit = fit_power_law_model(
    N_filtered,
    P_hz_filtered,
    v_slip_filtered,
    T_mold_filtered,
    wear_filtered,
)

# a = wk_multiplier * (C0(inputs) + C1(inputs) * N^3 + C2(inputs) * N^2 + C3(inputs) * N)
# With C0, C1, C2 and C3 modeled as surfaces over the inputs, this is linear in the
# unknown surface coefficients and can be fit directly over all filtered rows.
design_matrix = np.column_stack(
    [
        wk_multiplier_filtered[:, None] * row_features,
        wk_multiplier_filtered[:, None] * (N_scaled**3)[:, None] * row_features,
        wk_multiplier_filtered[:, None] * (N_scaled**2)[:, None] * row_features,
        wk_multiplier_filtered[:, None] * N_scaled[:, None] * row_features,
    ]
)
combined_coefficients = np.linalg.lstsq(design_matrix, wear_filtered, rcond=None)[0]
feature_count = row_features.shape[1]
C0_coefficients = combined_coefficients[:feature_count]
C1_coefficients = combined_coefficients[feature_count : 2 * feature_count] / (N_scale**3)
C2_coefficients = combined_coefficients[2 * feature_count : 3 * feature_count] / (N_scale**2)
C3_coefficients = combined_coefficients[3 * feature_count :] / N_scale

C0_array = row_features @ C0_coefficients
C1_array = row_features @ C1_coefficients
C2_array = row_features @ C2_coefficients
C3_array = row_features @ C3_coefficients
k_w_array = (
    C0_array
    + C1_array * N_filtered**3
    + C2_array * N_filtered**2
    + C3_array * N_filtered
)
W_k_array = k_w_array * wk_multiplier_filtered
predicted_wear_raw = W_k_array
predicted_wear = enforce_monotonic_wear_by_condition(filtered_data, predicted_wear_raw, condition_columns)
raw_mse, raw_rmse, raw_r2 = calculate_metrics(wear_filtered, predicted_wear_raw)
mse, rmse, r2 = calculate_metrics(wear_filtered, predicted_wear)

condition_records = []
for (_, _, _), group in filtered_data.assign(predicted_wear=predicted_wear).groupby(["torque", "rpm", "T_mold"]):
    _, group_rmse, group_r2 = calculate_metrics(group["linear_wear"].values, group["predicted_wear"].values)
    condition_records.append({"rmse": group_rmse, "r2": group_r2})
condition_dataframe = pd.DataFrame(condition_records)

print("\n" + "=" * 70)
print("ANALYTICAL WEAR MODEL")
print("=" * 70)
print(f"\nModel:")
print("   Generic expression: a(inputs, N) = k_w(inputs, N) * ((2 * pi * T1 * H_v) * N) / (b * l_f * z1)")
print("   a(inputs, N) = (C0(inputs) + C1(inputs) * N^3 + C2(inputs) * N^2 + C3(inputs) * N) * ((2 * pi * T1 * H_v) * N) / (b * l_f * z1)")
print("   k_w(inputs, N) = C0(inputs) + C1(inputs) * N^3 + C2(inputs) * N^2 + C3(inputs) * N")
if power_law_fit is not None:
    print(
        "   power_law(inputs) = "
        f"{power_law_fit['a']:.6e} * N^{power_law_fit['b_N']:.6f} * P_hz^{power_law_fit['c_Phz']:.6f} "
        f"* v_slip^{power_law_fit['d_vslip']:.6f} * T_mold^{power_law_fit['e_Tmolde']:.6f}"
    )
print(f"   C0(inputs) = {format_surface_expression(C0_coefficients, surface_feature_names, scaling)}")
print(f"   C1(inputs) = {format_surface_expression(C1_coefficients, surface_feature_names, scaling)}")
print(f"   C2(inputs) = {format_surface_expression(C2_coefficients, surface_feature_names, scaling)}")
print(f"   C3(inputs) = {format_surface_expression(C3_coefficients, surface_feature_names, scaling)}")
print(f"   H_v = (pi * (u + 1) / (z2 * cos(beta_b))) * (1 - epsilon_1 - epsilon_2 + epsilon_1^2 + epsilon_2^2) = {H_v:.6f}")
print(f"   alpha_t = arctan(tan(alpha_n) / cos(beta)) = {alpha_t:.6f}")
print(f"   beta_b = arcsin(sin(beta) * cos(alpha_n)) = {beta_b:.6f}")
print(f"   m_t = m_n / cos(beta) = {m_t:.6f}")
print(f"   alpha_wt = arccos(d_bi / d_wi) = {alpha_wt:.6f}")
print(f"   epsilon_1 = (z1 / (2 * pi)) * (sqrt((d_a1 / d_b1)^2 - 1) - tan(alpha_wt)) = {epsilon_1:.6f}")
print(f"   epsilon_2 = (z2 / (2 * pi)) * (sqrt((d_a2 / d_b2)^2 - 1) - tan(alpha_wt)) = {epsilon_2:.6f}")
print(f"   epsilon_alpha = epsilon_1 + epsilon_2 = {epsilon_alpha:.6f}")
print(f"   epsilon_beta = (b * sin(beta)) / (m_n * pi) = {epsilon_beta:.6f}")
print(f"   l_f = (((d_a1 / 2)^2) - ((d_Nf1 / 2)^2)) / d_b1 = {l_f:.6f}")
print(f"   a(inputs, N) = k_w(inputs, N) * ((2 * pi * T1 * H_v) * N) / (b * l_f * z1)")

print(f"\nGeometry:")
print(f"   z1 = {z1}, z2 = {z2}, u = {u:.6f}, beta = {beta:.6f}, alpha_n = {alpha_n:.6f}")
print(f"   d1 = {d1:.6f}, d2 = {d2:.6f}, d_w1 = {d_w1:.6f}, d_w2 = {d_w2:.6f}")
print(f"   d_a1 = {d_a1:.6f}, d_a2 = {d_a2:.6f}, d_b1 = {d_b1:.6f}, d_b2 = {d_b2:.6f}")
print(f"   a = {a:.6f}, h_aP0 = {h_aP0:.6f}, x1+x2 = {x_sum:.6f}, d_Nf1 = {d_Nf1:.6f}, d_Nf2 = {d_Nf2:.6f}")

print(f"\nStats:")
print(f"   C0[min/mean/max] = {np.min(C0_array):.6e} / {np.mean(C0_array):.6e} / {np.max(C0_array):.6e}")
print(f"   C1[min/mean/max] = {np.min(C1_array):.6e} / {np.mean(C1_array):.6e} / {np.max(C1_array):.6e}")
print(f"   C2[min/mean/max] = {np.min(C2_array):.6e} / {np.mean(C2_array):.6e} / {np.max(C2_array):.6e}")
print(f"   C3[min/mean/max] = {np.min(C3_array):.6e} / {np.mean(C3_array):.6e} / {np.max(C3_array):.6e}")
print(f"   k_w[min/mean/max] = {np.min(k_w_array):.6e} / {np.mean(k_w_array):.6e} / {np.max(k_w_array):.6e}")
print(f"   a_pred[min/mean/max] = {np.min(predicted_wear):.6e} / {np.mean(predicted_wear):.6e} / {np.max(predicted_wear):.6e}")
print(f"   W_k[min/mean/max] = {np.min(W_k_array):.6e} / {np.mean(W_k_array):.6e} / {np.max(W_k_array):.6e}")
print(f"   T1[min/mean/max] = {np.min(T1):.6e} / {np.mean(T1):.6e} / {np.max(T1):.6e}")
print("   data_filter = N < 0.99 * Nmax")
print("   monotonic_constraint = predicted wear non-decreasing with N inside each condition")
if power_law_fit is not None:
    print(
        f"   power_law_rows = {power_law_fit['positive_rows']}, "
        f"power_law_rmse = {power_law_fit['rmse']:.6f}, power_law_r2 = {power_law_fit['r2']:.6f}"
    )

print(f"\nFit:")
print(f"   filtered_rows = {len(filtered_data)}")
print(f"   fitted_conditions = {len(condition_dataframe)}")
print(f"   mean_condition_rmse = {condition_dataframe['rmse'].mean():.6f}")
print(f"   mean_condition_r2 = {condition_dataframe['r2'].mean():.6f}")
print(f"   raw_MSE = {raw_mse:.6f}")
print(f"   raw_RMSE = {raw_rmse:.6f}")
print(f"   raw_R^2 = {raw_r2:.6f}")
print(f"   MSE = {mse:.6f}")
print(f"   RMSE = {rmse:.6f}")
print(f"   R^2 = {r2:.6f}")
if power_law_fit is not None:
    print(f"   power_law_MSE = {power_law_fit['mse']:.6f}")
    print(f"   power_law_RMSE = {power_law_fit['rmse']:.6f}")
    print(f"   power_law_R^2 = {power_law_fit['r2']:.6f}")

print("\n" + "=" * 70)

# ==================== EXPORT COEFFICIENTS FOR GEARWEARNN ====================
power_law_reference = None
if power_law_fit is not None:
    power_law_reference = {
        key: value
        for key, value in power_law_fit.items()
        if key != "predicted_wear"
    }

analytical_model_output = {
    "timestamp": datetime.now().isoformat(),
    "model_type": "analytical_cubic_polynomial",
    "description": "Cubic polynomial surfaces for wear prediction: a = wk * (C0 + C1*N^3 + C2*N^2 + C3*N)",
    
    # Polynomial surface coefficients
    "C0_coefficients": C0_coefficients.tolist(),
    "C1_coefficients": C1_coefficients.tolist(),
    "C2_coefficients": C2_coefficients.tolist(),
    "C3_coefficients": C3_coefficients.tolist(),
    "surface_feature_names": surface_feature_names,
    "surface_polynomial_degree": int(SURFACE_POLYNOMIAL_DEGREE),
    
    # Scaling parameters
    "scaling": scaling,
    
    # Geometry parameters
    "geometry": {
        "m": float(m),
        "z": int(z),
        "z1": int(z1),
        "z2": int(z2),
        "b": float(b),
        "alpha_rad": float(alpha),
        "E_star_MPa": float(E_star),
        "H_v": float(H_v),
        "l_f": float(l_f),
        "alpha_wt_rad": float(alpha_wt),
        "epsilon_1": float(epsilon_1),
        "epsilon_2": float(epsilon_2),
    },
    
    # Model performance metrics
    "metrics": {
        "filtered_rows": int(len(filtered_data)),
        "fitted_conditions": int(len(condition_dataframe)),
        "mean_condition_rmse": float(condition_dataframe['rmse'].mean()),
        "mean_condition_r2": float(condition_dataframe['r2'].mean()),
        "MSE": float(mse),
        "RMSE": float(rmse),
        "R_squared": float(r2),
    },
    
    # Power law reference (if available)
    "power_law_reference": power_law_reference,
    
    # Normalization reference
    "N_scale": float(N_scale),
    "N_max": float(np.max(N_filtered)),
    "P_hz_range": [float(np.min(P_hz_filtered)), float(np.max(P_hz_filtered))],
    "v_slip_range": [float(np.min(v_slip_filtered)), float(np.max(v_slip_filtered))],
    "T_mold_range": [float(np.min(T_mold_filtered)), float(np.max(T_mold_filtered))],
}

# Save to file adjacent to this script
output_dir = os.path.dirname(os.path.abspath(__file__))
output_file = os.path.join(output_dir, "analytical_model_coefficients.json")

try:
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(analytical_model_output, f, indent=2)
    print(f"\n[OK] Analytical model coefficients exported to: {output_file}")
except Exception as e:
    print(f"\n[ERRO] Error saving coefficients: {e}")
