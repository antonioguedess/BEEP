// --- Includes ---

#include <stdio.h> // printf
#include "driver/pulse_cnt.h" // PCNT
#include "driver/gpio.h" // GPIO
#include "esp_timer.h" // Timers
#include "esp_err.h" // ESP Error Codes
#include "esp_log.h" // ESP Logging
#include "esp_clk_tree.h" // ESP Clock Functions
#include "freertos/FreeRTOS.h" // FreeRTOS
#include "freertos/task.h" // FreeRTOS Task

// --- Hardware ---
#define ENC1_A 18
#define ENC1_B 19
#define ENC1_Z 21
#define ENC2_A 22
#define ENC2_B 23
#define ENC2_Z 25
#define PPR 3840 // Pulses Per Revolution (with 4x decoding)

// --- Frequencies ---
#define FREQUENCIA_MEDICAO_HZ 1000  // Measuring frequency in Hz
#define FREQUENCIA_LOG_HZ     10    // Logging frequency in Hz

#define PERIOD_US_MEDICAO     (1000000 / FREQUENCIA_MEDICAO_HZ) // Measuring period in microseconds
#define PERIOD_MS_LOG         (1000 / FREQUENCIA_LOG_HZ) // Logging period in milliseconds

// --- Global Variables ---
volatile int pos1 = 0, pos2 = 0; // Initial definition of position variables
volatile float vel1 = 0, vel2 = 0; // Initial definition of velocity variables
int lastPos1 = 0, lastPos2 = 0, lastZ1 = 0, lastZ2 = 0; // Last position and Z auxiliary variables
pcnt_unit_handle_t pcntHandle1, pcntHandle2, pcntHandleZ1, pcntHandleZ2; // PCNT Handles
const float delta_t_min = (1.0f / FREQUENCIA_MEDICAO_HZ) / 60.0f; // Time delta in minutes

// --- High Precision Callback ---
// This function is triggered by a hardware timer interrupt at a fixed frequency (e.g., 1kHz)
static void periodic_timer_callback(void* arg) {
    int z1 = 0, z2 = 0;
    int current_pos1 = 0, current_pos2 = 0;

    // 1. Fetch raw pulse counts directly from the hardware PCNT units
    // current_pos stores the quadrature count (position)
    // z stores the cumulative count of Index pulses (rotations)
    pcnt_unit_get_count(pcntHandle1, &current_pos1);
    pcnt_unit_get_count(pcntHandle2, &current_pos2);
    pcnt_unit_get_count(pcntHandleZ1, &z1);
    pcnt_unit_get_count(pcntHandleZ2, &z2);

    // 2. CALCULATE VELOCITY (RPM) FIRST
    // We calculate RPM using the raw count BEFORE any reset logic.
    // This ensures continuity and avoids velocity spikes/drops when the Index (Z) resets the counter.
    // Formula: Delta_Position / (Resolution * 4 for quadrature) / Delta_Time_in_minutes
    vel1 = (float)(current_pos1 - lastPos1) / (PPR * 4.0f) / delta_t_min;
    vel2 = (float)(current_pos2 - lastPos2) / (PPR * 4.0f) / delta_t_min;

    // 3. INDEX (Z) RESET LOGIC for Absolute Position
    // Motor 1: Check if a new Index pulse was detected since the last execution
    if (z1 != lastZ1) { 
        pcnt_unit_clear_count(pcntHandle1); // Hardware reset of the pulse counter
        current_pos1 = 0;                  // Reset local variable to align with hardware
        lastPos1 = 0;                      // Prepare delta calculation for the next cycle
        lastZ1 = z1;                       // Update last known Index state
    } else {
        lastPos1 = current_pos1;           // Store current position for next RPM delta
    }

    // Motor 2: Same logic as Motor 1
    if (z2 != lastZ2) { 
        pcnt_unit_clear_count(pcntHandle2); 
        current_pos2 = 0; 
        lastPos2 = 0; 
        lastZ2 = z2; 
    } else {
        lastPos2 = current_pos2;
    }

    // 4. Update Global Variables
    // These volatile variables will be read by the main loop for logging/display
    pos1 = current_pos1;
    pos2 = current_pos2;
}

// --- Setup Pulse Counter (PCNT) Unit ---
// This function initializes a hardware pulse counter unit for either Quadrature Encoders (4x) or simple Pulse Counting (Index/Z)
pcnt_unit_handle_t setupPCNT(gpio_num_t pinA, gpio_num_t pinB, bool quadrature) {
    
    // 1. Unit Configuration
    // Defines the 16-bit hardware counter limits and enables overflow accumulation (accum_count)
    pcnt_unit_config_t unit_config = { 
        .high_limit = 32767, 
        .low_limit = -32768, 
        .flags.accum_count = true // Allows the driver to track counts beyond 16-bit limits
    };
    pcnt_unit_handle_t unit = NULL;
    ESP_ERROR_CHECK(pcnt_new_unit(&unit_config, &unit));
    
    // 2. Glitch Filter Configuration (Signal Debouncing)
    // Ignores pulses shorter than 1000ns to eliminate high-frequency electromagnetic noise from motors
    pcnt_glitch_filter_config_t filter_config = {
        .max_glitch_ns = 1000 
    };
    ESP_ERROR_CHECK(pcnt_unit_set_glitch_filter(unit, &filter_config));
    ESP_ERROR_CHECK(pcnt_unit_enable_glitch_filter(unit)); // Hardware-level noise suppression enabled

    // 3. Channel Configuration
    // Assigns physical GPIOs to the PCNT unit
    pcnt_chan_config_t chan_config = {
        .edge_gpio_num = pinA, 
        .level_gpio_num = quadrature ? pinB : -1 // If quadrature, B level informs direction
    };
    pcnt_channel_handle_t chan = NULL;
    ESP_ERROR_CHECK(pcnt_new_channel(unit, &chan_config, &chan));

    if (quadrature) {
        // --- 4x Decoding Logic (Quadrature Mode) ---
        // Setup Channel A: Actions for rising/falling edges based on Channel B level
        pcnt_channel_set_edge_action(chan, PCNT_CHANNEL_EDGE_ACTION_DECREASE, PCNT_CHANNEL_EDGE_ACTION_INCREASE);
        pcnt_channel_set_level_action(chan, PCNT_CHANNEL_LEVEL_ACTION_KEEP, PCNT_CHANNEL_LEVEL_ACTION_INVERSE);
        
        // Setup Channel B: Required for full 4x resolution (decodes all transitions)
        pcnt_chan_config_t chanB_cfg = {
            .edge_gpio_num = pinB, 
            .level_gpio_num = pinA
        };
        pcnt_channel_handle_t chanB = NULL;
        ESP_ERROR_CHECK(pcnt_new_channel(unit, &chanB_cfg, &chanB));
        
        // Setup Channel B Actions: Inverse logic compared to Channel A to correctly increment/decrement
        pcnt_channel_set_edge_action(chanB, PCNT_CHANNEL_EDGE_ACTION_INCREASE, PCNT_CHANNEL_EDGE_ACTION_DECREASE);
        pcnt_channel_set_level_action(chanB, PCNT_CHANNEL_LEVEL_ACTION_KEEP, PCNT_CHANNEL_LEVEL_ACTION_INVERSE);
    } else {
        // --- Single Pulse Mode (Index/Z Pin) ---
        // Simply increments the counter on every rising edge of the Z signal
        pcnt_channel_set_edge_action(chan, PCNT_CHANNEL_EDGE_ACTION_INCREASE, PCNT_CHANNEL_EDGE_ACTION_HOLD);
    }

    // 4. Hardware Initialization and Startup
    ESP_ERROR_CHECK(pcnt_unit_enable(unit));  // Enable the PCNT unit power/clock
    ESP_ERROR_CHECK(pcnt_unit_clear_count(unit)); // Reset counter to zero
    ESP_ERROR_CHECK(pcnt_unit_start(unit));   // Begin autonomous hardware counting
    
    return unit; // Return the handle for future hardware interaction
}

void app_main(void) {
    // 1. Disable system logs to prevent non-CSV data from corrupting the Serial output
    esp_log_level_set("*", ESP_LOG_NONE); 

    // 2. Hardware Initialization
    // Initialize PCNT units for position (quadrature) and rotation reference (Z-index)
    pcntHandle1  = setupPCNT(ENC1_A, ENC1_B, true);  // Motor 1 Position
    pcntHandle2  = setupPCNT(ENC2_A, ENC2_B, true);  // Motor 2 Position
    pcntHandleZ1 = setupPCNT(ENC1_Z, -1, false);     // Motor 1 Index/Reset
    pcntHandleZ2 = setupPCNT(ENC2_Z, -1, false);     // Motor 2 Index/Reset

    // 3. High-Precision Background Timer
    // Create and start a hardware timer to execute the measurement callback at a fixed frequency
    const esp_timer_create_args_t timer_args = { .callback = &periodic_timer_callback, .name = "m" };
    esp_timer_handle_t timer;
    esp_timer_create(&timer_args, &timer);
    esp_timer_start_periodic(timer, PERIOD_US_MEDICAO);

    // 4. CSV Header Output
    // Prints the column labels for easy parsing by Python/Excel
    printf("timestamp_ms,pos1,pos2,vel1_rpm,vel2_rpm,erro_graus\n");

    // 5. Main Logging Loop
    // Periodically captures snapshots of the global data and exports them via Serial
    while (1) {
        // Capture a local "snapshot" of volatile variables to ensure data atomicity
        int p1 = pos1, p2 = pos2;
        float v1 = vel1, v2 = vel2;

        // Calculate the angular error in degrees between the two shafts
        // Formula: (Delta_Pulses) * 360 / (Resolution * 4)
        float erro_g = (float)(p2 - p1) * 360.0f / (PPR * 4.0f);
        
        // Export data in CSV format: Time, Positions, Velocities, and Synchronism Error
        printf("%llu,%d,%d,%.2f,%.2f,%.2f\n", 
                esp_timer_get_time() / 1000, p1, p2, v1, v2, erro_g);

        // Block the task for a predefined logging period to free CPU resources
        vTaskDelay(pdMS_TO_TICKS(PERIOD_MS_LOG));
    }
}